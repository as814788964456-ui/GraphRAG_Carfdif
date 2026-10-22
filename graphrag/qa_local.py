"""本地 LLM 問答（Ollama）：和 qa.py 相同的兩種模式，但不呼叫 Claude API。

  - agent（預設）：Ollama tool calling，模型自己決定要不要再查。
    2–4B 小模型常常不呼叫工具就直接回答，所以第一步固定先用使用者問題做一次混合搜尋，
    把結果放進對話，模型覺得不夠再換關鍵字或換工具查。
  - simple：neo4j-graphrag 內建的 GraphRAG 類別，一次檢索 + 一次生成（和 qa.py 共用流程，只換 LLM）。

小模型的上下文有限，工具回傳內容會截斷在 LOCAL_TOOL_MAX_CHARS 字以內。
"""
import json
import os

from neo4j_graphrag.generation import GraphRAG as LibGraphRAG
from neo4j_graphrag.generation import RagTemplate

from .graph_store import GraphStore
from .kg_schema import SCHEMA
from .local_llm import QA_MODEL, chat, make_local_qa_llm
from .qa import SIMPLE_SYSTEM, Answer

MAX_STEPS = int(os.getenv("LOCAL_AGENT_MAX_STEPS", "6"))
TOOL_MAX_CHARS = int(os.getenv("LOCAL_TOOL_MAX_CHARS", "6000"))

# 小模型看不完 APOC 產生的完整 schema（Document 有十幾個屬性），改用手寫的精簡版
_GRAPH_SCHEMA = (
    "(:Chunk {id, text})-[:CHILD_OF]->(:ParentChunk {id, heading, text})-[:PART_OF]->(:Document {doc_id, title, version, superseded})\n"
    "(:Chunk)-[:FROM_DOCUMENT]->(:Document)；(:Document)-[:SUPERSEDES]->(舊版 :Document)；(:Document)-[:REFERENCES]->(:Document)\n"
    "(:__Entity__ {name, description})-[:FROM_CHUNK]->(:Chunk)\n"
    f"實體類型：{', '.join(n['label'] for n in SCHEMA['node_types'])}\n"
    f"實體關係：{', '.join(f'({s})-[:{r}]->({o})' for s, r, o in SCHEMA['patterns'])}"
)

SYSTEM = f"""你是企業知識庫的問答助理，資料存在 Neo4j 知識圖譜裡。

規則：
1. 系統已經先用使用者的問題搜尋過一次原文片段（第一個 search_chunks 的結果）。內容足夠就直接回答。
2. 不夠的話再呼叫工具：換關鍵字用 search_chunks；找部門、系統、表單等實體用 search_entities；
   問兩件事的關聯用 expand_entity；計數或列清單用 run_cypher。同一個查詢不要重複呼叫。
3. 只根據查到的內容回答，用繁體中文，在句末用 [chunk_id] 標註出處。
4. 文件有新舊版時以新版為準（Document.superseded = true 是舊版）。
5. 查不到答案就說找不到，不要自己編。

圖譜結構：
{_GRAPH_SCHEMA}"""


def _tool(name: str, description: str, params: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": params, "required": required},
        },
    }


TOOLS = [
    _tool(
        "search_chunks",
        "混合搜尋原文片段（語意 + 關鍵字），回傳片段原文、出處與所屬段落全文。",
        {"query": {"type": "string", "description": "搜尋內容，可以是問題或關鍵字"},
         "k": {"type": "integer", "description": "回傳筆數 1-5，預設 3"}},
        ["query"],
    ),
    _tool(
        "search_entities",
        "用關鍵字搜尋實體（部門、系統、表單、規定等），回傳類型、描述與相連的關係。",
        {"keyword": {"type": "string", "description": "名稱或描述中的關鍵字，例如 eFlow、人力資源處"}},
        ["keyword"],
    ),
    _tool(
        "expand_entity",
        "從一個實體沿關係往外展開 1-3 跳，回傳路徑。",
        {"name": {"type": "string", "description": "實體名稱"},
         "hops": {"type": "integer", "description": "展開跳數 1-3，預設 2"}},
        ["name"],
    ),
    _tool(
        "run_cypher",
        "執行唯讀 Cypher 查詢（只能 MATCH/RETURN，最多 50 列），用於計數、列清單。",
        {"cypher": {"type": "string", "description": "完整的唯讀 Cypher 查詢"}},
        ["cypher"],
    ),
]


def _int(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


class LocalGraphRAGAgent:
    def __init__(self, store: GraphStore | None = None):
        self.store = store or GraphStore()
        self._simple: LibGraphRAG | None = None

    def _run_tool(self, name: str, args: dict, evidence: list[dict]) -> str:
        s = self.store
        calls = {
            "search_chunks": lambda: s.search_chunks(str(args.get("query", "")), _int(args.get("k"), 3, 1, 5)),
            "search_entities": lambda: s.search_entities(str(args.get("keyword", "")), 5),
            "expand_entity": lambda: s.expand_entity(str(args.get("name", "")), _int(args.get("hops"), 2, 1, 3)),
            "run_cypher": lambda: s.run_readonly_cypher(str(args.get("cypher", ""))),
        }
        if name not in calls:
            return json.dumps({"error": f"沒有 {name} 這個工具"}, ensure_ascii=False)
        try:
            result = calls[name]()
        except Exception as e:
            return json.dumps({"error": str(e), "hint": "檢查參數或改用其他工具"}, ensure_ascii=False)
        evidence.append({"tool": name, "args": args, "result": result})
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        if isinstance(result, list) and result and all(isinstance(r, str) for r in result):
            text = "\n\n".join(result)  # search_chunks 回傳的是已排版的文字，不要再包成 JSON
        if len(text) > TOOL_MAX_CHARS:
            text = text[:TOOL_MAX_CHARS] + "\n…（內容過長，已截斷）"
        return text or "（沒有結果）"

    def ask(self, question: str) -> Answer:
        """Agent 模式：先自動搜尋一次，再讓本地模型決定要不要繼續查。"""
        evidence: list[dict] = []
        first = {"query": question, "k": 3}
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": question},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "search_chunks", "arguments": first}}]},
            {"role": "tool", "tool_name": "search_chunks", "content": self._run_tool("search_chunks", first, evidence)},
        ]
        for _ in range(MAX_STEPS):
            msg = chat(QA_MODEL, messages, tools=TOOLS)
            calls = msg.get("tool_calls") or []
            if not calls:
                return Answer(text=msg["content"], evidence=evidence)
            messages.append({"role": "assistant", "content": msg["content"], "tool_calls": calls})
            for call in calls:
                fn = call.get("function", {})
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                name = fn.get("name", "")
                messages.append({"role": "tool", "tool_name": name, "content": self._run_tool(name, args, evidence)})
        # 步數用完：不給工具，要求根據已查到的內容作答
        messages.append({"role": "user", "content": "請根據以上查到的內容直接回答，不要再呼叫工具。"})
        return Answer(text=chat(QA_MODEL, messages)["content"], evidence=evidence)

    def ask_simple(self, question: str, top_k: int = 5) -> Answer:
        """Simple 模式：neo4j-graphrag 的 GraphRAG 類別，LLM 換成本地模型。"""
        if self._simple is None:
            self._simple = LibGraphRAG(
                retriever=self.store.chunk_retriever,
                llm=make_local_qa_llm(),
                prompt_template=RagTemplate(system_instructions=SIMPLE_SYSTEM),
            )
        result = self._simple.search(query_text=question, retriever_config={"top_k": top_k}, return_context=True)
        items = [item.content for item in result.retriever_result.items] if result.retriever_result else []
        return Answer(text=result.answer, evidence=[{"tool": "hybrid_cypher_retriever", "args": {"query": question}, "result": items}])
