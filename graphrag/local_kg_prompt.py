"""本地小模型（2–4B）用的抽實體 prompt：中文規則 + 本知識庫的範例。

函式庫預設的 ERExtractionTemplate 是英文、規則籠統、沒有範例。2026-10-23 用 qwen3:4b 試抽 HR-002、IT-FAQ-002
發現的問題，這裡逐條寫成規則：
  - 關係方向抽反 → 直接列出允許的「(來源)-[關係]->(目標)」，說明方向要照抄
  - 一半實體沒有任何關係 → 要求每個實體都要連到其他實體；細節寫進 description，不要另外拆成孤立實體
  - 很多實體沒有描述 → 規則要求，且 local_llm.extraction_format() 把 description 設成必填
  - 同名實體被抽成不同類型（VPN 是 System 也是 Term）→ 系統、軟體一律 System
  - 文件編號（IT-FAQ-002）被當成實體 → 明講標頭的文件編號、章節標題不是實體
  - 規定被當成 Issue → 說明 Issue 只用於錯誤、異常、問題
範例取自 IT-FAQ-001、SYS-002，刻意避開試抽評估用的 HR-002、IT-FAQ-002。
"""
import json

from neo4j_graphrag.generation.prompts import ERExtractionTemplate

from .kg_schema import SCHEMA


def _escape(text: str) -> str:
    """ERExtractionTemplate 用 str.format 填入 {text}，其他大括號要寫成兩個。"""
    return text.replace("{", "{{").replace("}", "}}")


def _example(text: str, nodes: list[tuple], rels: list[tuple]) -> str:
    graph = {
        "nodes": [{"id": str(i), "label": label, "properties": {"name": name, "description": desc}}
                  for i, (label, name, desc) in enumerate(nodes)],
        "relationships": [{"start_node_id": str(s), "end_node_id": str(e), "type": t} for s, t, e in rels],
    }
    return f"輸入：\n{text}\n輸出：\n{json.dumps(graph, ensure_ascii=False)}"


EXAMPLES = [
    _example(
        "【IT-FAQ-001｜三、常見問題與處理方式 › Q3. 帳號被鎖住了？】\n"
        "• 連續輸入錯誤密碼 5 次，帳號會鎖定 30 分鐘，時間到自動解鎖\n"
        "• 急需使用可撥分機 5000 請服務台立即解鎖\n"
        "• 若您沒有輸錯卻反覆被鎖，常見原因是手機或舊電腦仍用舊密碼同步郵件，請更新手機 Outlook／Teams 上的密碼",
        [
            ("Issue", "帳號被鎖定", "連續輸入錯誤密碼 5 次，帳號會鎖定 30 分鐘"),
            ("Solution", "等待自動解鎖", "鎖定 30 分鐘後時間到自動解鎖"),
            ("Solution", "更新手機與舊電腦上的密碼", "沒有輸錯卻反覆被鎖，常因手機或舊電腦仍用舊密碼同步郵件，更新 Outlook／Teams 上的密碼"),
            ("Contact", "服務台分機 5000", "急需使用時撥分機 5000，請服務台立即解鎖"),
        ],
        [(0, "RESOLVED_BY", 1), (0, "RESOLVED_BY", 2), (0, "ESCALATE_TO", 3)],
    ),
    _example(
        "【SYS-002 eFlow 電子簽核系統使用手冊｜三、作業流程 › 3.2 簽核與退件】\n"
        "待簽核的表單會出現在「我的待辦」，同時寄 Email 及 Teams 通知。簽核動作：同意（送往下一關）、"
        "退件（退回申請人修改，必須填寫退件原因）、加簽、會簽。\n"
        "每一關簽核時限為 3 個工作天，逾期系統會每天催辦，逾期 5 個工作天會通知簽核人的上一級主管",
        [
            ("Process", "表單簽核", "待簽核表單出現在「我的待辦」，可同意、退件、加簽或會簽"),
            ("System", "eFlow", "電子簽核系統，待簽核時寄 Email 及 Teams 通知"),
            ("Process", "退件", "退回申請人修改，必須填寫退件原因"),
            ("Rule", "簽核時限", "每一關 3 個工作天；逾期每天催辦，逾期 5 個工作天通知簽核人的上一級主管"),
            ("Role", "簽核人", "負責各關卡的簽核，須在 3 個工作天內處理"),
        ],
        [(0, "PERFORMED_IN", 1), (2, "PERFORMED_IN", 1), (0, "GOVERNED_BY", 3), (3, "APPLIES_TO", 4), (4, "APPROVES", 0)],
    ),
]

_TYPES = "\n".join(f"- {n['label']}：{n['description']}" for n in SCHEMA["node_types"])
_PATTERNS = "\n".join(f"- ({s})-[{r}]->({o})" for s, r, o in SCHEMA["patterns"])

TEMPLATE = f"""你要從企業內部文件的一段文字中，抽出知識圖譜的實體與關係，輸出 JSON。

實體類型（label 只能用這些）：
{_TYPES}

允許的關係（方向一定要照抄：start_node 是左邊的類型，end_node 是右邊的類型）：
{_PATTERNS}

規則：
1. 開頭【】裡是文件編號、標題和章節，只用來理解上下文。文件編號（例如 HR-002、IT-FAQ-002）和章節標題不是實體。
2. 每個實體都要透過上面允許的關係連到至少一個其他實體。數字、期限、步驟等細節寫進 description，不要另外拆成沒有關係的實體。
3. 每個實體都要有 description：根據原文寫一句話，規定類要寫出具體的數字、期限或門檻。
4. 系統、軟體、App 一律用 System（例如 eFlow、HR Portal、VPN、Teams），不要用 Term。
5. Issue 只用於錯誤、異常或使用者遇到的問題；規定、限制、條件用 Rule。
6. name 用原文中最完整的寫法，要簡短（Rule 例如「事假天數上限」，不要把整句規定當名稱）。
7. 同一個實體只輸出一次，用 id 在關係中引用。只抽原文有寫的內容，不要自己推測。
8. 使用繁體中文。

範例：
{_escape(chr(10).join(EXAMPLES))}
{{examples}}
現在處理這段文字：
{{text}}
"""


class LocalERTemplate(ERExtractionTemplate):
    """SimpleKGPipeline 的 prompt_template。schema 參數不使用：類型與關係已經寫在模板裡。"""

    DEFAULT_TEMPLATE = TEMPLATE
