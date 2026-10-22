# GraphRAG_Carfdif

以 **Claude + Neo4j 知識圖譜**實作的 GraphRAG 問答系統，目標資料為 Cardif InsurHack 題目 4-1 的虛構保險公司知識庫（曦光人壽）。另有**全本地版本**（Ollama 上的 2–4B 小模型，不呼叫 Claude API），見「7. 本地 LLM 版本」。

```
使用者問題 ─▶ LLM Smart Search（Claude 用工具自主檢索）─▶ LLM 問題 + 相關資訊 ─▶ 加強後的回答
                     │  Query            ▲ Relevant Info
                     ▼                   │
                  Neo4j 知識圖譜（向量索引 + 全文索引 + 圖結構）
```

> 最後更新：2026-10-23

---

## 進度總覽

| 階段 | 狀態 |
|---|---|
| 架構設計與模型選型 | ✅ 完成 |
| 程式骨架（建圖譜、檢索、兩種問答模式） | ✅ 完成 |
| 改用 `neo4j-graphrag` 函式庫並修正其 bug | ✅ 完成 |
| Python 環境與 GPU 加速 | ✅ 完成 |
| 讀取並分析曦光人壽知識庫 | ✅ 完成 |
| 針對知識庫調整（父子切塊、metadata、版本、schema） | ✅ 完成（新版知識庫，62 份） |
| 新版知識庫的測試集 | ⬜ 待辦（目前沒有） |
| 評估程式 | ⬜ 待辦（需要測試集） |
| 端到端實際執行（需 API key + Neo4j） | 🟡 Neo4j 與檢索已通，待 API key 抽實體與問答（目前先走本地版本） |
| 本地 LLM 版本（Ollama，2–4B） | 🟡 問答與抽實體都已實測可用，抽取 prompt 已改善；待跑完全部 62 份 |
| 檢索品質優化 | ⬜ 待辦 |

## ✅ 已完成

### 1. 架構與模型選型

全部使用 Claude API。每個步驟的模型可以在 `.env` 分別設定：

| 步驟 | 模型 | effort | 說明 |
|---|---|---|---|
| 建圖譜（抽取實體與關係） | `claude-opus-5-5` | `low` | 用量最大，確認品質後可試 Sonnet 5.5 / Haiku 5.5 |
| Smart Search 問答 | `claude-opus-5-5` | `high` | 多輪檢索、寫 Cypher、多跳推理 |
| `--simple` 問答 | `claude-opus-5-5` | `medium` | 單次檢索 + 單次生成 |
| Embedding | `BAAI/bge-m3`（本地） | — | Claude 沒有 embedding 模型；本地執行，免費 |

也支援把建圖譜或 `--simple` 問答改用本地 Llama（TAIDE，透過 Ollama），設定方式見下方「設定」。

### 2. 程式實作

- **建圖譜**（[ingest.py](ingest.py)）：使用 `neo4j-graphrag` 的 `SimpleKGPipeline`，依序切塊、產生向量、由 Claude 抽取實體與關係、寫入 Neo4j、合併同名實體。
- **檢索**（[graphrag/graph_store.py](graphrag/graph_store.py)）：
  - `HybridCypherRetriever`：向量搜尋加全文搜尋（中文用 `cjk` analyzer），找到片段後沿圖譜帶出相關的實體與關係。
  - 實體搜尋、多跳展開、唯讀 Cypher 查詢。
- **問答**（[graphrag/qa.py](graphrag/qa.py)）：
  - Smart Search（預設）：Claude 用 4 個檢索工具自主決定要查幾次、查什麼。
  - `--simple`：函式庫內建的 `GraphRAG` 類別。
- **安全**：Cypher 只在唯讀交易中執行，並擋下寫入關鍵字；關係類型名稱會先清理，避免 Cypher injection。
- **拒答備援**：支援的模型會啟用 `fallbacks: "default"`。Haiku 5.5 不支援，會自動略過這個參數。

### 3. 對 `neo4j-graphrag` 1.22 的修正

| 問題 | 影響 | 修正（[graphrag/llm.py](graphrag/llm.py)） |
|---|---|---|
| `AnthropicLLM` 只讀 `content[0]` | Opus 5.5 的第一個區塊常是 thinking block，會被誤判為錯誤 | `ClaudeLLM` 改成串接所有 text 區塊 |
| 模型拒答時拋出的例外沒人攔截 | 一個片段被拒答，整個建圖譜流程就中斷 | 拒答時回傳空圖或提示文字 |
| `OllamaLLM` 非同步路徑把參數多包一層 `options` | `format: json` 和 `num_ctx` 失效，長 prompt 被截斷 | `FixedOllamaLLM` 改走同步路徑 |
| `AnthropicLLM` 沒有實作 `invoke_with_tools` | 不能用函式庫的 `ToolsRetriever` 驅動 Claude | Smart Search 改用 Anthropic SDK 的 tool runner |

### 4. 環境

- `.venv`：已安裝所有套件，並沿用系統上的 torch。
- 在 `.venv` 安裝 CUDA 版 torch（`2.10.0+cu128`）。bge-m3 在 RTX 3070 上每秒處理 205 個片段，CPU 只有每秒 8 個，約快 25 倍，顯存峰值約 2.5 GB。

### 5. 知識庫

有兩個版本，**之後以新版為準**：

| | 舊版（模擬資料） | **新版（目前使用）** |
|---|---|---|
| 文件 | 39 份 Markdown | 50 份現行文件＋12 張流程圖，從 PDF 轉換 |
| 版本 | 例如 IT-FAQ-001 v3.2 | 例如 IT-FAQ-001 v4.0；舊版記錄為「被取代」 |
| 章節格式 | 各文件不同（Q1、第一條…） | 統一為「一、目的／二、依據／三、作業流程 › 3.x／四、控制重點／五、使用表單」 |
| 測試集 | 68 題 `golden_set.jsonl` | **沒有** |
| 本機位置 | `C:\Users\Ed\TAIDE\data\knowledge-base` | 切好的資料在 `data/chunks_structure_bge-m3/` |

兩個版本的知識庫都不放在這個 repo（已用 `.gitignore` 排除）。

### 6. 父子切塊與新版匯入流程

切塊資料和匯入程式來自另一個工作階段，已整合進本 repo。

- **切塊**（`data/chunks_structure_bge-m3/`）：依文件結構切成子塊和父塊兩層。
  - 子塊 611 個：用來產生向量、建全文索引、抽取實體。長度依 bge-m3 的實際分詞計算，最多 449 token。
  - 父塊 191 個：找到子塊後，交給 LLM 閱讀的完整段落。
  - 每個子塊前面加上位置標頭 `【文件編號 標題 版本｜章節路徑】`。
- **匯入**（[ingest_chunks.py](ingest_chunks.py)、[graphrag/chunk_loader.py](graphrag/chunk_loader.py)）：把切好的子塊直接交給 `SimpleKGPipeline`，再補上這些關係：
  - `CHILD_OF`、`PART_OF`：子塊、父塊、文件之間的層級
  - `SUPERSEDES`：新版取代舊版（38 條）
  - `REFERENCES`：文件之間的引用（309 條）
  - `FLOWCHART_OF`：流程圖對應的主文件（12 條）
- **Schema**（[graphrag/kg_schema.py](graphrag/kg_schema.py)）：改成企業知識庫的領域，共 12 種實體和 14 種關係。
  - 實體：部門、角色、系統、流程、表單、規定、術語、問題、解法、窗口、專案、經驗教訓
- **附加父塊**（`ParentExpandingRetriever`）：找到子塊後附上所屬父塊的全文，同一個父塊只附一次；`EXPAND_PARENT=0` 可以關閉。這做在檢索器裡，所以 Smart Search 和 `--simple` 都會拿到父塊。
- **來源顯示**：檢索結果改成顯示「文件編號＋標題＋版本」，不再只顯示檔案路徑。8 份文件沒有版本號（如 SYS-002～005、DOC-003），只顯示編號＋標題。
- **答案位置比對**（[graphrag/source_match.py](graphrag/source_match.py)）：給評估程式用，能處理縮寫（`Q2`）和新舊版的編號差異。用舊測試集對新版，可以對上 79/93 個答案位置。

已驗證：
- 另一個工作階段已把 `--lexical-only` 對本地 Neo4j 5.26.12 ＋ APOC 匯入全部 62 份。
- 在本機測過：`--dry-run`、新 schema 通過驗證、兩種問答模式都會附加父塊。
- **2026-10-19 本機 Neo4j 2026.09.0 ＋ APOC 實測**：
  - `--lexical-only --embed` 匯入全部 62 份：611 個子塊、191 個父塊、100 個文件節點（含 38 份被取代的舊版），補上 309 條引用、38 條版本、12 條流程圖關係。
  - 向量索引、子塊全文索引、實體全文索引都是 ONLINE；混合檢索實測可找到正確段落並附上父塊（例如「VPN 權限要怎麼申請？」第 1 名即為 IT-FAQ-002 Q1）。
  - 唯讀 Cypher 會擋下寫入語句；`schema_summary()`（APOC）可正常取得圖譜結構。
- 實測時修正的 bug（[graphrag/graph_store.py](graphrag/graph_store.py)）：
  - `GraphStore._read` 在交易函式裡把 `Query` 物件傳給 `tx.run`，新版 driver 不接受，Smart Search 的寫 Cypher、搜尋實體、展開關係三個工具都會失敗。改用 `neo4j.unit_of_work(timeout=15)` 設定逾時。
  - 沒有版本號的文件，來源會因為字串串接遇到 null 而退回顯示檔案路徑。

**尚未驗證：Claude 抽取實體、兩種問答模式的實際回答（需要 API key）。**

### 7. 本地 LLM 版本（不呼叫 Claude API）

計畫先全部改用本地 2–4B 小模型。Claude 版的檔案都保留不動，本地版是另外的檔案，共用 Neo4j、bge-m3 與檢索器：

| Claude 版 | 本地版 | 說明 |
|---|---|---|
| [graphrag/llm.py](graphrag/llm.py) | [graphrag/local_llm.py](graphrag/local_llm.py) | LLM 與設定 |
| [graphrag/qa.py](graphrag/qa.py) | [graphrag/qa_local.py](graphrag/qa_local.py) | Smart Search 與 `--simple` |
| [ask.py](ask.py) | [ask_local.py](ask_local.py) | 問答 CLI |
| [ingest_chunks.py](ingest_chunks.py) | [ingest_chunks_local.py](ingest_chunks_local.py) | 建圖譜（參數相同，只換抽實體的 LLM 與 prompt） |
| （函式庫預設英文 prompt） | [graphrag/local_kg_prompt.py](graphrag/local_kg_prompt.py) | 給小模型的中文抽取 prompt（規則＋範例） |

- **模型**：預設原版 [Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B)（思考／不思考可切換，支援工具呼叫），Ollama 標籤 `qwen3:4b-q4_K_M`（2.6 GB，上下文 40K）。
  - **不要用 `qwen3:4b`**：它現在指向 Qwen3-4B-Thinking-2507（雜湊值和 `qwen3:4b-thinking-2507-q4_K_M` 相同），只會思考，是另一個模型。
  - 可用 `.env` 的 `LOCAL_EXTRACT_MODEL`、`LOCAL_QA_MODEL` 換成其他模型。注意 Gemma 3 不支援工具呼叫，要用 Gemma 4。
- **直接呼叫 Ollama REST API**：`neo4j-graphrag` 把 `ollama` 套件限制在 0.5 以下，舊版不能傳 `think` 參數，關不掉 Qwen3 的思考模式。本地版改用 `httpx` 直接呼叫 `/api/chat`，預設 `think=false`。
- **針對小模型的調整**：
  - 抽實體時用 JSON schema 限制輸出（Ollama 的 `format`），實體與關係類型只能從 `kg_schema.py` 的清單選，不會自創類型或輸出壞掉的 JSON；`description` 設為必填。
  - 抽實體改用中文 prompt（[graphrag/local_kg_prompt.py](graphrag/local_kg_prompt.py)），把試抽發現的問題逐條寫成規則，並附 2 個本知識庫的範例（取自 IT-FAQ-001、SYS-002，避開評估用的文件）。比較結果見下方「抽取 prompt 改善」。
  - **防止卡住**：temperature 0 時小模型偶爾會重複輸出停不下來（實測有一個子塊超過 300 秒逾時，整批中斷）。現在每次最多輸出 2048 token（`LOCAL_NUM_PREDICT`），被截斷的 JSON 用 `json_repair` 補齊；抽實體逾時或出錯時回傳空圖並記錄警告，不會中斷整批匯入（問答仍會報錯）。
  - Smart Search 第一步固定先用使用者問題做一次混合搜尋，小模型常常不呼叫工具就直接回答。
  - 圖譜結構改用手寫的精簡版放進 system prompt，不用 APOC 產生的完整 schema。
  - 每次工具回傳最多 6000 字（`LOCAL_TOOL_MAX_CHARS`），最多 6 輪工具呼叫（`LOCAL_AGENT_MAX_STEPS`）；上下文預設 16384（`LOCAL_NUM_CTX`）。
- **顯存**：RTX 3070 8 GB。4B 量化模型約 3 GB，加上 bge-m3 約 2.5 GB，放得下。
- **已驗證（離線）**：用假的 Ollama 回應測過抽實體的 schema 與 `think=false`、一般呼叫、Smart Search 的工具迴圈（自動先搜尋、再查一次、截斷、未知工具、步數上限），以及 `ingest_chunks_local.py --dry-run`。
- **已驗證（真模型，2026-10-23，Ollama 0.40.8 ＋ `qwen3:4b-q4_K_M`）**：
  - `think=false` 有效；載入後一般回答約 3 秒（第一次載入模型約 70 秒）。顯存佔用 5.2 GB（上下文 16384）。
  - 工具呼叫正常：問「VPN 權限要怎麼申請？」會呼叫 `search_entities(keyword="VPN 權限申請")`。
  - 抽實體：每個子塊 4–6 秒（611 個子塊估計約 50 分鐘）。例如 IT-FAQ-002 Q1 抽出資訊安全科、eFlow、IT-02 申請單、VPN 權限申請流程。
  - 小模型常把**關係方向抽反**（例如 eFlow -[PERFORMED_IN]-> 流程），也會把文件編號當成術語。函式庫的圖譜修剪（`GraphPruning`）會把反向但符合 schema 的關係翻正，刪掉不符合 schema 的關係，實測修剪後 3 條關係都正確。
  - 待觀察：實體的 `description` 常是空的；一般對話偶爾夾雜簡體字。
  - **問答（`ask_local.py`，資料庫為 `--lexical-only` 的資料，還沒有實體）**：5 題全部答對。
    - `--simple`：「VPN 權限要怎麼申請？」答出 eFlow、IT-02、簽核流程與開通時間；「事假一年可以請幾天？」答 14 天。
    - Smart Search：上面兩題加上「員工請假要在哪個系統申請？要誰核准？」（答 HR Portal、直屬主管，3 天以上加處級主管），都有標註 `[chunk_id]`。三題第一次自動搜尋就足夠，模型沒有再呼叫工具。
    - 每題約 32–35 秒，大部分是每次啟動都要重新載入 bge-m3 和模型；互動模式（`python ask_local.py` 不帶問題）只會載入一次。
  - **試抽 2 份（`ingest_chunks_local.py --docs HR-002 IT-FAQ-002`）**：含 HR-002 流程圖共 3 份、32 個子塊，3.3 分鐘（平均每子塊約 4.5 秒，全部 611 個估計 45–60 分鐘）。
    - 每個子塊都有抽到實體。共 139 個實體、67 條實體關係；類型以 Rule（46）、Form（23）、Process（15）、System（13）為主。
    - 抽對的例子：`請假 -[PERFORMED_IN]-> HR Portal`、`VPN 權限申請流程 -[REQUIRES]-> IT-02 申請單`、`VPN 連不上 -[ESCALATE_TO]-> 分機 5000`。
    - 問題：
      - 49% 的實體沒有任何實體關係（68/139），只透過 `FROM_CHUNK` 連到子塊。
      - 38% 的實體沒有描述（53/139）。
      - 同名實體被抽成不同類型，合併不起來：VPN（System／Term）、人力資源處（Department／Contact）。
      - 部分關係語意錯誤：`直屬主管 -[BELONGS_TO]-> 人力資源處`；規定被當成問題（Issue），`RESOLVED_BY` 指向同一句話的改寫。
    - `search_entities`、`expand_entity` 在真實圖譜上可以用。
  - 實測時修正的 bug（[graphrag/graph_store.py](graphrag/graph_store.py)）：`search_entities` 回傳的實體類型都是 `__KGBuilder__`（只排除了 `__Entity__` 標籤），已改成兩個都排除。Claude 版也共用這段。

#### 抽取 prompt 改善（2026-10-23）

同樣 32 個子塊（HR-002、IT-FAQ-002、HR-002 流程圖），只在記憶體中抽取＋修剪、依「類型＋名稱」合併後比較，不寫入資料庫：

| 指標 | 舊版（函式庫預設英文 prompt、描述選填） | 新版（中文規則＋範例、描述必填） |
|---|---|---|
| 實體 | 122 | 146 |
| 實體關係（修剪後） | 58 | **84**（+45%） |
| 孤立實體 | 72（59%） | 67（**46%**） |
| 沒有描述 | 58（48%） | **0（0%）** |
| 文件編號被當成實體 | 9 個 | 4 個 |
| 修剪刪掉的關係 | 102/161（63%） | 91/176（52%） |
| Issue／Solution | 2／2 | 10／3 |
| 同名不同類型 | 3 組 | 5 組（VPN、AuroraLink 仍是 System／Term） |
| 耗時 | 142 秒 | 172 秒（prompt 較長，+20%） |

- 新版抽對的例子：`產檢假申請 -[ESCALATE_TO]-> 人資薪酬組分機 2100`、`境外連線申請 -[PERFORMED_IN]-> iService`、`VPN 遠端連線申請 -[PERFORMED_IN]-> FortiClient VPN`、`薪酬組 -[RESPONSIBLE_FOR]-> 留職停薪申請`。
- 仍有錯誤：`申請人 -[APPROVES]-> VPN 權限申請流程`（申請人不是核准者）、`人力資源處` 有時被當成 Role。
- 結論：新版關係多、描述完整、雜訊少，`ingest_chunks_local.py` 已改用新版。孤立實體與同名不同類型還需要後處理（見待辦）。
- 注意：目前資料庫裡的 3 份文件是**舊版 prompt** 試抽的結果，跑完整匯入前要先清空。

---

## ⬜ 待辦事項

### 優先：測試集與評估

- [ ] **新版知識庫的測試集**：目前沒有。有兩種做法：
  - 等官方或隊友提供
  - 把舊版 68 題對應到新版：79/93 個答案位置可以自動對上，剩下 14 個（主要是 GLO-001 的術語，新版已經不是獨立標題）要人工對應到子塊編號。答案內容要抽查新版是否改過
- [ ] **評估程式**：有了測試集後計算：
  - 有沒有找到正確的段落（hit@k，用 `source_match.py` 比對）
  - 回答是否正確（由 LLM 依標準答案評分）
  - 無答案題是否回答「找不到」
  - 版本衝突題是否避開舊版
  - 口語和錯字變體的表現
  - 並分別比較 Smart Search 和 `--simple` 兩種模式

### 本地 LLM 版本（目前優先）

- [x] 安裝 Ollama，下載模型：`ollama pull qwen3:4b-q4_K_M`（2026-10-23，Ollama 0.40.8）
- [x] 清空資料庫，用 `ingest_chunks_local.py --docs HR-002 IT-FAQ-002` 先試 2 份（結果見上方「7. 本地 LLM 版本」）
- [x] 改善抽取 prompt：中文規則＋範例＋描述必填（關係 +45%、描述 100%，見「抽取 prompt 改善」）
- [x] 防止小模型重複輸出卡住：輸出上限、JSON 修補、失敗回傳空圖
- [ ] **下一步**：清空資料庫（目前是舊版 prompt 試抽的 3 份），用新版跑完全部 62 份（611 個子塊，估計 55–70 分鐘）
- [ ] 抽取後處理：
  - 依名稱合併同名不同類型的實體（例如 VPN、AuroraLink 的 System／Term）
  - 孤立實體仍有 46%：考慮刪掉沒有任何關係、名稱又是長句的 Rule，或保留（它們仍連到子塊、可被 `search_entities` 找到）
- [ ] 比較其他 2–4B 模型的抽取品質
- [x] 用 `ask_local.py` 實測 Smart Search 和 `--simple`（`--lexical-only` 資料，5 題全對）
- [ ] 有實體之後再測一次 `ask_local.py`，看模型會不會用到 `search_entities`／`expand_entity`，以及問「找不到」的題目
- [ ] 比較 2B 與 4B（以及其他 2–4B 模型）的抽取品質、回答品質與速度
- [ ] 調整取樣參數：目前 `temperature=0`。Qwen 官方建議不思考模式用 temperature 0.7、top_p 0.8、top_k 20，並提醒貪婪解碼（temperature 0）可能出現重複輸出；實測若有重複再調整

### 端到端執行（Claude 版，需要使用者先準備）

- [x] 安裝 Neo4j Desktop，建立資料庫並安裝 APOC 外掛，把密碼填入 `.env`（本機為 Neo4j 2026.09.0 ＋ APOC 2026.09.0）
- [x] 先用 `--lexical-only --embed` 匯入：不需要 API key，可以先測試檢索（2026-10-19 完成，混合檢索已實測）
- [ ] **下一步**：到 [console.anthropic.com](https://console.anthropic.com) 建立 API key，填入 `.env`（取消 `ANTHROPIC_API_KEY` 那行的註解）
- [ ] 清空資料庫（`MATCH (n) DETACH DELETE n`）：目前資料庫裡是 `--lexical-only` 的資料，不清空的話抽實體時會跳過所有文件
- [ ] 用 `--docs` 先對 1～2 份文件執行 Claude 抽取實體，確認圖譜品質和實際費用，再跑全部 62 份
  - 每個子塊呼叫一次 Claude，共 611 次。如果太貴，可以改成每份文件呼叫一次（約 62 次），再依名稱把實體對應回子塊
- [ ] 在 Neo4j Browser 檢查圖譜：實體有沒有漏、同一個實體有沒有被拆成好幾個、關係是否正確
- [ ] 實際跑 `ask.py` 的 Smart Search 和 `--simple`，確認兩種模式端到端都正常

### 檢索品質優化（有 baseline 後逐項比較）

- [ ] **Reranker**：用 `bge-reranker-v2-m3` 從 top-20 精選出 top-5（本地，免費）。實測例子：「事假一年可以請幾天？」的答案（HR-002 3.5 事假）排第 2，第 1 名是 SYS-003 的 FAQ 片段
- [ ] **Neo4j 2026.x 的棄用警告**：`db.index.vector.queryNodes` 已被標為棄用、建議改用 `SEARCH` 語法。目前仍可正常運作，警告來自 `neo4j-graphrag` 的檢索器；之後升級函式庫或 Neo4j 時要留意
- [ ] **實體向量**：目前實體只能用關鍵字搜尋。幫實體的名稱和描述產生向量並建立向量索引，就能用語意描述找到實體
- [ ] **Contextual Retrieval**：產生向量前，由 Claude 替每個片段補上它在整份文件中的背景（用 Haiku 5.5，並把整份文件設成 prompt cache）
- [ ] **比較 embedding 模型**：bge-m3、xVITA-Embed-300M-zhTW（繁中特化，768 維，GGUF）、Qwen3-Embedding-0.6B
- [ ] **比較建圖譜的模型**：Opus 5.5 vs Sonnet 5.5 vs Haiku 5.5 的圖譜品質與成本
- [ ] **長上下文 baseline**：把整份知識庫放進 Claude 的上下文，並設成 prompt cache 直接回答。用它當作 GraphRAG 分數的參考上限

### 自動分流（Simple ↔ Smart Search）

前置條件：metadata（✅ 已完成）＋評估程式（需要測試集與 baseline 才能調整門檻）。

- [ ] **先比較兩種模式**：評估程式分別以「全 Simple」和「全 Smart Search」跑完測試集，比較分數與成本。差距不大就不需要分流
- [ ] **檢索訊號判斷（免費）**：Simple 的混合搜尋結果出現以下任一情況，就轉給 Smart Search
  - 最相關片段的分數低於門檻（可能是無答案題，或問法太口語）
  - 前幾名片段來自 2 份以上文件，且分數接近（可能是跨文件題）
  - 片段內容提到舊版或「已廢止」（新版知識庫的舊版文件不切塊，版本衝突要看內文；例如 IT-FAQ-002 寫明 AuroraLink 已改為 FortiClient）
- [ ] **Cascade 自我評估**：Simple 回答時用結構化輸出多回報 `sufficient: true/false`，`false` 就轉給 Smart Search
- [ ] **設計原則：寧可多轉也不要漏轉**：複雜題誤送 Simple 會降低品質，簡單題誤送 Smart Search 只是多花成本
- [ ] **評估分流準確率**：以測試集的 `question_type` 作為標準答案（`single` 應留在 Simple，`multi_doc`／`version_conflict`／`unanswerable` 應轉出），計算漏轉率，並比較三種方式（全 Simple／全 Smart Search／分流）的分數與成本
- [ ] （備選）**Haiku 5.5 前置分類**：只看問題就分類。看不到檢索結果，所以判斷不出版本衝突，只在上面兩種訊號不夠用時才考慮

---

## 專案結構

```
GraphRAG/
├── ingest_chunks.py       # 建圖譜（知識庫用）：切好的父子塊 → SimpleKGPipeline → 補上父子／版本／引用關係
├── ingest_chunks_local.py # 同上，抽實體改用本地 LLM
├── ingest.py              # 建圖譜（一般 .txt/.md）：固定字數切塊
├── ask.py                 # 問答 CLI（Smart Search / --simple）
├── ask_local.py           # 問答 CLI，本地 LLM 版
├── graphrag/
│   ├── config.py          # 讀取 .env
│   ├── llm.py             # ClaudeLLM / FixedOllamaLLM 與模型工廠
│   ├── local_llm.py       # 本地 LLM（Ollama REST API）與設定
│   ├── graph_store.py     # Neo4j 連線、索引、檢索器（含附加父塊）
│   ├── qa.py              # Smart Search agent 與 simple 模式
│   ├── qa_local.py        # 同上，本地 LLM 版
│   ├── local_kg_prompt.py # 本地小模型用的中文抽取 prompt（規則＋範例）
│   ├── kg_schema.py       # 實體與關係 schema（企業知識庫）
│   ├── chunk_loader.py    # 讀取切好的父子塊、補圖譜關係
│   └── source_match.py    # 評估用：判斷子塊是否命中答案位置
├── data/
│   ├── sample.txt                   # 範例文件
│   └── chunks_structure_bge-m3/     # 新版知識庫的父子塊（不進 git）
├── docker-compose.yml     # Neo4j 5.26 + APOC（可選）
├── requirements.txt
└── .env.example           # 設定範本
```

## 快速開始

```powershell
# 1. Neo4j 5.26 以上並啟用 APOC：Neo4j Desktop / AuraDB Free / docker compose up -d

# 2. Python 環境
python -m venv --system-site-packages .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# （可選）GPU 加速：
pip install --ignore-installed --no-deps torch==2.10.0 --index-url https://download.pytorch.org/whl/cu128

# 3. 設定：複製範本，填入 ANTHROPIC_API_KEY 與 NEO4J_PASSWORD
copy .env.example .env

# 4. 建圖譜（曦光人壽知識庫）
python ingest_chunks.py data/chunks_structure_bge-m3 --dry-run                   # 只看計畫
python ingest_chunks.py data/chunks_structure_bge-m3 --lexical-only --embed      # （可選）不呼叫 Claude，先測檢索
# 跑過 --lexical-only 後，要抽實體前先在 Neo4j Browser 清空：MATCH (n) DETACH DELETE n
# （已匯入的文件會被略過；用 --force 重寫則會產生重複節點）
python ingest_chunks.py data/chunks_structure_bge-m3 --docs HR-002 IT-FAQ-002    # 先試 2 份的實體抽取
python ingest_chunks.py data/chunks_structure_bge-m3                             # 其餘全部（已匯入的會略過）

# 5. 問答
python ask.py "VPN 權限要怎麼申請？" -v               # Smart Search
python ask.py "事假一年可以請幾天？" --simple -v      # 單次檢索
```

### 全本地版本（不需要 API key）

```powershell
ollama pull qwen3:4b-q4_K_M
python ingest_chunks_local.py data/chunks_structure_bge-m3 --docs HR-002 IT-FAQ-002   # 先試 2 份
python ingest_chunks_local.py data/chunks_structure_bge-m3                            # 其餘全部
python ask_local.py "VPN 權限要怎麼申請？" -v               # Smart Search（本地模型）
python ask_local.py "事假一年可以請幾天？" --simple -v      # 單次檢索（本地模型）
```

## 設定（`.env`）

| 變數 | 說明 |
|---|---|
| `EXTRACT_PROVIDER` / `QA_PROVIDER` | `claude` 或 `ollama`。`QA_PROVIDER=ollama` 時只能用 `--simple` |
| `EXTRACT_MODEL` / `EXTRACT_EFFORT` | 建圖譜的模型與 effort |
| `QA_MODEL` / `QA_EFFORT` | Smart Search 的模型與 effort |
| `SIMPLE_QA_MODEL` / `SIMPLE_QA_EFFORT` | `--simple` 的模型與 effort |
| `EMBED_MODEL` / `EMBED_DIM` / `EMBED_DEVICE` | Embedding 模型、維度、裝置（空白 = 自動偵測 GPU） |
| `OLLAMA_MODEL` / `OLLAMA_HOST` / `OLLAMA_NUM_CTX` | 本地 Llama 設定（Claude 版檔案的 `ollama` provider 用） |
| `LOCAL_EXTRACT_MODEL` / `LOCAL_QA_MODEL` | 本地版（`*_local.py`）建圖譜與問答的模型，預設 `qwen3:4b-q4_K_M`（原版 Qwen3-4B） |
| `LOCAL_NUM_CTX` / `LOCAL_THINK` / `LOCAL_AGENT_MAX_STEPS` / `LOCAL_TOOL_MAX_CHARS` | 本地版的上下文長度、思考開關、工具呼叫輪數與回傳長度上限 |
| `LOCAL_NUM_PREDICT` / `LOCAL_TIMEOUT` | 本地版單次最多輸出幾個 token（預設 2048）、單次請求逾時秒數（預設 300） |
| `NEO4J_*` | Neo4j 連線資訊 |
| `EXPAND_PARENT` | `1`（預設）：找到子塊時附上父塊全文；`0`：只回傳子塊 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 只有 `ingest.py`（一般文件）會用到 |

### 改用本地 Llama（可選）

```powershell
ollama pull hf.co/tetf/Llama-3.1-TAIDE-LX-8B-Chat-GGUF:Q4_K_M
ollama cp hf.co/tetf/Llama-3.1-TAIDE-LX-8B-Chat-GGUF:Q4_K_M taide
```

接著在 `.env` 設定 `QA_PROVIDER=ollama` 和 `EMBED_DEVICE=cpu`，把顯存留給 Llama。HuggingFace 上的 16-bit 原始權重約 16 GB，放不進 8 GB 顯存，所以要改用 4-bit 量化的 GGUF。

## 注意事項

- **更換 embedding 模型時**：`EMBED_DIM` 要跟著改，並刪除 `chunk_embedding` 索引後重建圖譜。
- **Cypher 安全**：正式環境建議另外建立唯讀的 Neo4j 帳號給問答使用。
- **Neo4j Desktop 與中文路徑**：Windows 使用者資料夾名稱含中文時，Neo4j 無法啟動（設定檔路徑解析錯誤、啟動指令過長），連帶建立資料庫時設定的密碼也不會生效，帳號會停在預設密碼 `neo4j`。請使用全英文路徑。
- **知識庫資料**：曦光人壽知識庫為競賽用的虛構資料，不包含在這個 repo 中。要重現時，請把 `chunks_structure_bge-m3` 資料夾放到 `data/` 底下。
