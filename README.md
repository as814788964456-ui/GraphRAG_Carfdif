# GraphRAG_Carfdif

以**本地 LLM + Neo4j 知識圖譜**實作的 GraphRAG 問答系統，目標資料為 Cardif InsurHack 題目 4-1 的虛構保險公司知識庫（曦光人壽）。建圖譜與問答全部在本機執行，不呼叫任何雲端 API。

```
使用者問題 ─▶ 本地 LLM（Ollama：Qwen3-4B）檢索 ─▶ 問題 + 相關資訊 ─▶ 回答（標註出處）
                     │  Query            ▲ Relevant Info
                     ▼                   │
                  Neo4j 知識圖譜（向量索引 + 全文索引 + 圖結構）
```

> 最後更新：2026-10-23

---

## 架構（已固定）

目前架構固定如下，不做模型比較：

| 元件 | 使用 | 說明 |
|---|---|---|
| LLM（建圖譜、問答） | 原版 [Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B)，Ollama 標籤 `qwen3:4b-q4_K_M` | 4-bit 量化 2.6 GB，上下文 40K，支援工具呼叫，關閉思考模式 |
| Embedding | `BAAI/bge-m3` | 本地 GPU 執行，1024 維 |
| 圖資料庫 | Neo4j 2026.09.0 ＋ APOC 2026.09.0 | Neo4j Desktop |
| 推論服務 | Ollama 0.40.8 | 本機 `http://localhost:11434` |
| 硬體 | RTX 3070 8 GB | Qwen3-4B 約 5.2 GB（上下文 16384）＋ bge-m3 約 2.5 GB |

> **Ollama 標籤要用 `qwen3:4b-q4_K_M`**，不要用 `qwen3:4b`：後者現在指向 Qwen3-4B-Thinking-2507（只會思考），是另一個模型。

## 進度總覽

| 階段 | 狀態 |
|---|---|
| 環境（Python、CUDA、Ollama、Neo4j ＋ APOC） | ✅ 完成 |
| 讀取並分析曦光人壽知識庫 | ✅ 完成 |
| 父子切塊、metadata、版本、schema | ✅ 完成（新版知識庫，62 份） |
| 檢索（混合搜尋＋附加父塊＋圖譜） | ✅ 完成 |
| 本地 LLM 建圖譜（全部 62 份） | ✅ 完成（2714 個實體、1681 條關係） |
| 本地 LLM 問答（Smart Search／`--simple`） | 🟡 可用；Smart Search 會在無答案題編造答案 |
| 新版知識庫的測試集 | ⬜ 待辦 |
| 評估程式 | ⬜ 待辦（需要測試集） |
| 檢索品質優化 | ⬜ 待辦 |

---

## ✅ 已完成

### 1. 環境

- **Python**：`.venv` 已安裝所有套件，並安裝 CUDA 版 torch（`2.10.0+cu128`）。bge-m3 在 RTX 3070 上每秒處理 205 個片段，CPU 只有每秒 8 個，約快 25 倍。
- **Ollama 0.40.8**：`ollama pull qwen3:4b-q4_K_M`。
- **Neo4j Desktop**：資料庫 `graphrag`（Neo4j 2026.09.0），已安裝 APOC。

### 2. 知識庫

有兩個版本，**以新版為準**：

| | 舊版（模擬資料） | **新版（目前使用）** |
|---|---|---|
| 文件 | 39 份 Markdown | 50 份現行文件＋12 張流程圖，從 PDF 轉換 |
| 版本 | 例如 IT-FAQ-001 v3.2 | 例如 IT-FAQ-001 v4.0；舊版記錄為「被取代」 |
| 章節格式 | 各文件不同（Q1、第一條…） | 統一為「一、目的／二、依據／三、作業流程 › 3.x／四、控制重點／五、使用表單」 |
| 測試集 | 68 題 `golden_set.jsonl` | **沒有** |
| 本機位置 | `C:\Users\Ed\TAIDE\data\knowledge-base` | 切好的資料在 `data/chunks_structure_bge-m3/` |

兩個版本的知識庫都不放在這個 repo（已用 `.gitignore` 排除）。

### 3. 父子切塊與匯入

- **切塊**（`data/chunks_structure_bge-m3/`）：依文件結構切成子塊和父塊兩層。
  - 子塊 611 個：用來產生向量、建全文索引、抽取實體。長度依 bge-m3 的實際分詞計算，最多 449 token。
  - 父塊 191 個：找到子塊後，交給 LLM 閱讀的完整段落。
  - 每個子塊前面加上位置標頭 `【文件編號 標題 版本｜章節路徑】`。
- **匯入**（[ingest_chunks_local.py](ingest_chunks_local.py)、[graphrag/chunk_loader.py](graphrag/chunk_loader.py)）：把切好的子塊交給 `neo4j-graphrag` 的 `SimpleKGPipeline`（產生向量、由本地 LLM 抽實體、寫入 Neo4j、合併同名實體），再補上這些關係：
  - `CHILD_OF`、`PART_OF`：子塊、父塊、文件之間的層級
  - `SUPERSEDES`：新版取代舊版（38 條）
  - `REFERENCES`：文件之間的引用（309 條）
  - `FLOWCHART_OF`：流程圖對應的主文件（12 條）
- **Schema**（[graphrag/kg_schema.py](graphrag/kg_schema.py)）：企業知識庫領域，12 種實體、14 種關係。
  - 實體：部門、角色、系統、流程、表單、規定、術語、問題、解法、窗口、專案、經驗教訓

### 4. 檢索（[graphrag/graph_store.py](graphrag/graph_store.py)）

- **混合搜尋**：向量搜尋加全文搜尋（中文用 `cjk` analyzer），找到子塊後沿圖譜帶出子塊中的實體與關係。
- **附加父塊**（`ParentExpandingRetriever`）：找到子塊後附上所屬父塊的全文，同一個父塊只附一次；`EXPAND_PARENT=0` 可以關閉。
- **來源顯示**：「文件編號＋標題＋版本」。8 份文件沒有版本號（如 SYS-002～005、DOC-003），只顯示編號＋標題。
- **其他工具**：實體搜尋、多跳展開、唯讀 Cypher 查詢。
- **安全**：Cypher 只在唯讀交易中執行，並擋下寫入關鍵字；關係類型名稱會先清理，避免 Cypher injection。
- **答案位置比對**（[graphrag/source_match.py](graphrag/source_match.py)）：給評估程式用，能處理縮寫（`Q2`）和新舊版的編號差異。用舊測試集對新版，可以對上 79/93 個答案位置。
- 實測時修正的 bug：
  - `_read` 在交易函式裡把 `Query` 物件傳給 `tx.run`，新版 driver 不接受，寫 Cypher、搜尋實體、展開關係三個工具都會失敗。改用 `neo4j.unit_of_work(timeout=15)`。
  - 沒有版本號的文件，來源會因為字串串接遇到 null 而退回顯示檔案路徑。
  - `search_entities` 回傳的實體類型都是 `__KGBuilder__`，已改成排除函式庫的內部標籤。

### 5. 本地 LLM（[graphrag/local_llm.py](graphrag/local_llm.py)）

- **直接呼叫 Ollama REST API**（`/api/chat`）：`neo4j-graphrag` 1.22 把 `ollama` 套件限制在 0.5 以下，舊版不能傳 `think` 參數，關不掉 Qwen3 的思考模式。改用 `httpx` 直接呼叫，預設 `think=false`。
- **抽實體**：
  - 用 JSON schema 限制輸出（Ollama 的 `format`）：實體與關係類型只能從 schema 的清單選，`description` 必填，不會輸出壞掉的 JSON。
  - 中文抽取 prompt（[graphrag/local_kg_prompt.py](graphrag/local_kg_prompt.py)）：把試抽發現的問題逐條寫成規則（關係方向、文件編號不是實體、系統一律用 System、規定用 Rule 等），並附 2 個本知識庫的範例（取自 IT-FAQ-001、SYS-002）。
  - 小模型常把關係方向抽反；函式庫的圖譜修剪（`GraphPruning`）會把反向但符合 schema 的關係翻正，刪掉不符合 schema 的關係。
- **防止卡住**：temperature 0 時小模型偶爾會重複輸出停不下來。每次最多輸出 2048 token（`LOCAL_NUM_PREDICT`），被截斷的 JSON 用 `json_repair` 補齊；抽實體逾時或出錯時回傳空圖並記錄警告，不中斷整批匯入（問答仍會報錯）。
- **問答**（[graphrag/qa_local.py](graphrag/qa_local.py)、[ask_local.py](ask_local.py)）：
  - Smart Search（預設）：Ollama 工具呼叫，4 個工具（混合搜尋、實體搜尋、關係展開、唯讀 Cypher）。小模型常常不查就回答，所以第一步固定先用問題做一次混合搜尋。圖譜結構用手寫的精簡版放進 system prompt。每次工具回傳最多 6000 字、最多 6 輪工具呼叫。
  - `--simple`：`neo4j-graphrag` 的 `GraphRAG` 類別，一次檢索＋一次生成。

### 6. 實測結果（2026-10-23）

#### 抽取 prompt 改善

同樣 32 個子塊（HR-002、IT-FAQ-002、HR-002 流程圖），只在記憶體中抽取＋修剪、依「類型＋名稱」合併後比較：

| 指標 | 函式庫預設英文 prompt、描述選填 | 中文規則＋範例、描述必填（採用） |
|---|---|---|
| 實體 | 122 | 146 |
| 實體關係（修剪後） | 58 | **84**（+45%） |
| 孤立實體 | 72（59%） | 67（**46%**） |
| 沒有描述 | 58（48%） | **0（0%）** |
| 文件編號被當成實體 | 9 個 | 4 個 |
| 修剪刪掉的關係 | 102/161（63%） | 91/176（52%） |
| 耗時 | 142 秒 | 172 秒 |

#### 完整匯入（全部 62 份）

`ingest_chunks_local.py data/chunks_structure_bge-m3`，611 個子塊，**66 分鐘**跑完，沒有中斷。

- 6 個子塊重複輸出到上限被截斷：5 個補齊 JSON 後保留已抽到的實體，1 個仍無法解析（`OPS-INT-002-P01-C03` 沒有實體）。沒有逾時。
- 圖譜：**2714 個實體、1681 條實體關係**，另有 611 個子塊、191 個父塊、100 個文件節點。
  - 實體類型：Rule 810、Term 544、Process 444、Form 175、System 162、Issue 152、Department 144、Role 91、Contact 82、Solution 72、Project 19、Lesson 19。
  - 關係：RESPONSIBLE_FOR 291、GOVERNED_BY 278、PERFORMED_IN 216、REFERS_TO 186、APPLIES_TO 156、BELONGS_TO 131、APPROVES 113、PRECEDES 96、REQUIRES 76、RESOLVED_BY 62、ESCALATE_TO 44、AFFECTS 22、LEARNED 9、DELIVERED 2。
  - 連結最多的實體：人力資源處（74）、eFlow（70）、總務處（65）、iService（47）、直屬主管（44）、LIFE-Core（38）。
- 品質：
  - 描述 100% 都有；同名同類型的實體都有合併。
  - 孤立實體 45%（1215/2714）。
  - **同名不同類型 169 組**，例如 Teams、iService、LIFE-Core、VPN、AuroraLink 都同時是 System 和 Term。
  - 仍有錯誤，例如「文件編號規則」被當成 System、`申請人 -[APPROVES]-> VPN 權限申請流程`。

#### 問答（完整圖譜，同一個程式連續問，每題 4–11 秒）

| 類型 | 問題 | Smart Search | `--simple` |
|---|---|---|---|
| 單一事實 | VPN 權限要怎麼申請？ | ✅ eFlow 填 IT-02、簽核流程、開通時間 | |
| 單一事實 | 事假一年可以請幾天？ | ✅ 14 天 | |
| 圖譜彙總 | eFlow 由哪個部門負責？哪些流程要在 eFlow 辦理？ | ⚠️ 列出一長串流程，混入不相關項目，負責部門不精確 | |
| 圖譜彙總 | 人力資源處負責哪些流程？ | ✅ 8 項，大致正確並標註出處 | |
| 版本衝突 | 公司的 VPN 連線軟體是哪一個？ | ✅ FortiClient VPN（沒有答舊版的 AuroraLink） | ✅ |
| 無答案 | 公司今年的尾牙在哪裡舉辦？ | ❌ **編造**：把 GEN-001 的公司大樓地址當成尾牙地點 | ✅ 找不到 |

- **模型沒有主動呼叫任何工具**：6 題都只用了第一步自動搜尋的結果。圖譜資訊只透過搜尋結果附帶的「片段中的實體／相關關係」間接用到。
- 每次執行 `ask_local.py` 都要重新載入 bge-m3 和模型（約 30 秒）；互動模式（不帶問題）只載入一次。

---

## ⬜ 待辦事項

### 優先：測試集與評估

- [ ] **新版知識庫的測試集**：把舊版 68 題（single 51、multi_doc 7、unanswerable 7、version_conflict 3，含口語與錯字變體）對應到新版。79/93 個答案位置可以自動對上，剩下 14 個（主要是 GLO-001 的術語）人工對應到子塊編號，並逐題核對標準答案在新版是否改過
- [ ] **評估程式**：
  - 有沒有找到正確的段落（hit@k，用 `source_match.py` 比對）
  - 無答案題是否回答「找不到」
  - 版本衝突題是否避開舊版
  - 口語和錯字變體的表現
  - 回答是否正確：4B 模型當評審不可靠，改為產出答案報告人工評分
  - 分別跑 Smart Search 和 `--simple` 兩種模式

### 問答品質

- [ ] **修正 Smart Search 在無答案題編造答案**（用測試集評估，不要只針對單一題目調整）：
  - system prompt 加強「查到的內容沒有直接回答問題就說找不到」，並給一個反例
  - 回答前再確認一次「上面的內容有沒有直接回答這個問題」，否就回答找不到
  - 預設改用 `--simple`，Smart Search 只在需要時使用
- [ ] **讓模型用到圖譜工具**：把 `search_entities` 也放進第一步自動執行，或在彙總類問題（「哪些」「負責」）自動加上 `expand_entity`
- [ ] **調整取樣參數**：目前 `temperature=0`。Qwen 官方建議不思考模式用 temperature 0.7、top_p 0.8、top_k 20，並提醒貪婪解碼可能重複輸出（完整匯入時有 6 個子塊發生）

### 圖譜品質

- [ ] **合併同名不同類型的實體**（169 組，例如 Teams、iService、LIFE-Core、VPN、AuroraLink 的 System／Term）
- [ ] **孤立實體 45%**：考慮刪掉沒有任何關係、名稱又是長句的 Rule，或保留（它們仍連到子塊、可被 `search_entities` 找到）

### 檢索品質優化

- [ ] **Reranker**：用 `bge-reranker-v2-m3`（本地）從 top-20 精選出 top-5。實測例子：「事假一年可以請幾天？」的答案（HR-002 3.5 事假）排第 2，第 1 名是 SYS-003 的 FAQ 片段
- [ ] **實體向量**：目前實體只能用關鍵字搜尋。幫實體的名稱和描述產生向量並建立向量索引，就能用語意描述找到實體
- [ ] **Neo4j 2026.x 的棄用警告**：`db.index.vector.queryNodes` 已被標為棄用、建議改用 `SEARCH` 語法。目前仍可正常運作，警告來自 `neo4j-graphrag` 的檢索器；之後升級函式庫或 Neo4j 時要留意

### 自動分流（Simple ↔ Smart Search）

前置條件：評估程式（需要測試集與兩種模式的分數才能調整門檻）。

- [ ] **先比較兩種模式**：分別以「全 Simple」和「全 Smart Search」跑完測試集，比較分數與時間。差距不大就不需要分流
- [ ] **檢索訊號判斷**：Simple 的混合搜尋結果出現以下任一情況，就轉給 Smart Search
  - 最相關片段的分數低於門檻（可能是無答案題，或問法太口語）
  - 前幾名片段來自 2 份以上文件，且分數接近（可能是跨文件題）
  - 片段內容提到舊版或「已廢止」（例如 IT-FAQ-002 寫明 AuroraLink 已改為 FortiClient）
- [ ] **自我評估**：Simple 回答時用結構化輸出多回報 `sufficient: true/false`，`false` 就轉給 Smart Search
- [ ] **評估分流準確率**：以測試集的 `question_type` 作為標準答案（`single` 應留在 Simple，其他應轉出），計算漏轉率

---

## 專案結構

```
GraphRAG/
├── ingest_chunks_local.py # 建圖譜：切好的父子塊 → SimpleKGPipeline（本地 LLM 抽實體）→ 補上父子／版本／引用關係
├── ask_local.py           # 問答 CLI（Smart Search / --simple）
├── ingest_chunks.py       # 共用：匯入流程主體（ingest_chunks_local.py 會呼叫）
├── graphrag/
│   ├── config.py          # 讀取 .env
│   ├── local_llm.py       # 本地 LLM（Ollama REST API）與設定
│   ├── local_kg_prompt.py # 中文抽取 prompt（規則＋範例）
│   ├── qa_local.py        # Smart Search agent 與 simple 模式
│   ├── graph_store.py     # Neo4j 連線、索引、檢索器（含附加父塊）
│   ├── kg_schema.py       # 實體與關係 schema（企業知識庫）
│   ├── chunk_loader.py    # 讀取切好的父子塊、補圖譜關係
│   ├── source_match.py    # 評估用：判斷子塊是否命中答案位置
│   ├── qa.py              # 共用：回答格式與 simple 模式的 prompt（qa_local.py 引用）
│   └── llm.py             # 共用：被 ingest_chunks.py 引用
├── data/
│   └── chunks_structure_bge-m3/     # 新版知識庫的父子塊（不進 git）
├── requirements.txt
└── .env.example           # 設定範本
```

## 快速開始

```powershell
# 1. Neo4j 2026.09（或 5.26 以上）並啟用 APOC：Neo4j Desktop，資料夾路徑要全英文

# 2. Ollama 與模型
winget install Ollama.Ollama
ollama pull qwen3:4b-q4_K_M

# 3. Python 環境
python -m venv --system-site-packages .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install --ignore-installed --no-deps torch==2.10.0 --index-url https://download.pytorch.org/whl/cu128   # GPU 加速

# 4. 設定：複製範本，填入 NEO4J_PASSWORD
copy .env.example .env

# 5. 建圖譜（曦光人壽知識庫，全部約 66 分鐘）
python ingest_chunks_local.py data/chunks_structure_bge-m3 --dry-run                   # 只看計畫
python ingest_chunks_local.py data/chunks_structure_bge-m3 --docs HR-002 IT-FAQ-002    # 先試 2 份
python ingest_chunks_local.py data/chunks_structure_bge-m3                             # 其餘全部（已匯入的會略過）
# 只想先測檢索、不抽實體：加上 --lexical-only --embed
# 要重建時先在 Neo4j 清空：MATCH (n) DETACH DELETE n（用 --force 重寫會產生重複節點）

# 6. 問答
python ask_local.py "VPN 權限要怎麼申請？" -v               # Smart Search
python ask_local.py "事假一年可以請幾天？" --simple -v      # 單次檢索
python ask_local.py                                         # 互動模式（模型只載入一次）
```

## 設定（`.env`）

| 變數 | 說明 |
|---|---|
| `LOCAL_EXTRACT_MODEL` / `LOCAL_QA_MODEL` | 建圖譜與問答的模型，預設 `qwen3:4b-q4_K_M` |
| `LOCAL_HOST` | Ollama 位址，預設 `http://localhost:11434` |
| `LOCAL_NUM_CTX` | 上下文長度，預設 16384 |
| `LOCAL_THINK` | `false`（預設）關閉思考 |
| `LOCAL_NUM_PREDICT` / `LOCAL_TIMEOUT` | 單次最多輸出幾個 token（預設 2048）、單次請求逾時秒數（預設 300） |
| `LOCAL_AGENT_MAX_STEPS` / `LOCAL_TOOL_MAX_CHARS` | Smart Search 最多幾輪工具呼叫（預設 6）、每次工具回傳最多幾個字（預設 6000） |
| `EMBED_MODEL` / `EMBED_DIM` / `EMBED_DEVICE` | Embedding 模型、維度、裝置（空白 = 自動偵測 GPU） |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` | Neo4j 連線資訊 |
| `EXPAND_PARENT` | `1`（預設）：找到子塊時附上父塊全文；`0`：只回傳子塊 |

## 注意事項

- **Neo4j Desktop 與中文路徑**：Windows 使用者資料夾名稱含中文時，Neo4j 無法啟動（設定檔路徑解析錯誤、啟動指令過長），連帶建立資料庫時設定的密碼也不會生效，帳號會停在預設密碼 `neo4j`。請使用全英文路徑。
- **顯存**：建圖譜與問答時 Ollama 和 bge-m3 會同時佔用顯卡（約 7.7 GB），執行期間避免同時跑其他吃顯卡的程式。
- **更換 embedding 模型時**：`EMBED_DIM` 要跟著改，並刪除 `chunk_embedding` 索引後重建圖譜。
- **Cypher 安全**：正式環境建議另外建立唯讀的 Neo4j 帳號給問答使用。
- **知識庫資料**：曦光人壽知識庫為競賽用的虛構資料，不包含在這個 repo 中。要重現時，請把 `chunks_structure_bge-m3` 資料夾放到 `data/` 底下。
