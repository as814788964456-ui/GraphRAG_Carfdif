# GraphRAG_Carfdif

以 **Claude + Neo4j 知識圖譜**實作的 GraphRAG 問答系統，目標資料為 Cardif InsurHack 題目 4-1 的虛構保險公司知識庫（曦光人壽）。

```
使用者問題 ─▶ LLM Smart Search（Claude 用工具自主檢索）─▶ LLM 問題 + 相關資訊 ─▶ 加強後的回答
                     │  Query            ▲ Relevant Info
                     ▼                   │
                  Neo4j 知識圖譜（向量索引 + 全文索引 + 圖結構）
```

> 最後更新：2026-10-19

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
| 端到端實際執行（需 API key + Neo4j） | ⬜ 待辦 |
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
| 本機位置 | `C:\Users\政德\TAIDE\data\knowledge-base` | 切好的資料在 `data/chunks_structure_bge-m3/` |

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
- **來源顯示**：檢索結果改成顯示「文件編號＋標題＋版本」，不再只顯示檔案路徑。
- **答案位置比對**（[graphrag/source_match.py](graphrag/source_match.py)）：給評估程式用，能處理縮寫（`Q2`）和新舊版的編號差異。用舊測試集對新版，可以對上 79/93 個答案位置。

已驗證：
- 另一個工作階段已把 `--lexical-only` 對本地 Neo4j 5.26.12 ＋ APOC 匯入全部 62 份。
- 在本機測過：`--dry-run`、新 schema 通過驗證、兩種問答模式都會附加父塊。

**尚未驗證：Claude 抽取實體。**

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

### 端到端執行（需要使用者先準備）

- [ ] 到 [console.anthropic.com](https://console.anthropic.com) 建立 API key，填入 `.env`
- [ ] 安裝 Neo4j Desktop，建立資料庫並安裝 APOC 外掛，把密碼填入 `.env`
- [ ] 先用 `--lexical-only --embed` 匯入：不需要 API key，可以先測試檢索
- [ ] 用 `--docs` 先對 1～2 份文件執行 Claude 抽取實體，確認圖譜品質和實際費用，再跑全部 62 份
  - 每個子塊呼叫一次 Claude，共 611 次。如果太貴，可以改成每份文件呼叫一次（約 62 次），再依名稱把實體對應回子塊
- [ ] 在 Neo4j Browser 檢查圖譜：實體有沒有漏、同一個實體有沒有被拆成好幾個、關係是否正確

### 檢索品質優化（有 baseline 後逐項比較）

- [ ] **Reranker**：用 `bge-reranker-v2-m3` 從 top-20 精選出 top-5（本地，免費）
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
├── ingest.py              # 建圖譜（一般 .txt/.md）：固定字數切塊
├── ask.py                 # 問答 CLI（Smart Search / --simple）
├── graphrag/
│   ├── config.py          # 讀取 .env
│   ├── llm.py             # ClaudeLLM / FixedOllamaLLM 與模型工廠
│   ├── graph_store.py     # Neo4j 連線、索引、檢索器（含附加父塊）
│   ├── qa.py              # Smart Search agent 與 simple 模式
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

## 設定（`.env`）

| 變數 | 說明 |
|---|---|
| `EXTRACT_PROVIDER` / `QA_PROVIDER` | `claude` 或 `ollama`。`QA_PROVIDER=ollama` 時只能用 `--simple` |
| `EXTRACT_MODEL` / `EXTRACT_EFFORT` | 建圖譜的模型與 effort |
| `QA_MODEL` / `QA_EFFORT` | Smart Search 的模型與 effort |
| `SIMPLE_QA_MODEL` / `SIMPLE_QA_EFFORT` | `--simple` 的模型與 effort |
| `EMBED_MODEL` / `EMBED_DIM` / `EMBED_DEVICE` | Embedding 模型、維度、裝置（空白 = 自動偵測 GPU） |
| `OLLAMA_MODEL` / `OLLAMA_HOST` / `OLLAMA_NUM_CTX` | 本地 Llama 設定 |
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
- **知識庫資料**：曦光人壽知識庫為競賽用的虛構資料，不包含在這個 repo 中。要重現時，請把 `chunks_structure_bge-m3` 資料夾放到 `data/` 底下。
