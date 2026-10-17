"""問答流程（對應架構圖）：

  User Question
      → LLM Smart Search：Claude 自行決定呼叫哪些檢索工具（混合搜尋 / 實體查詢 / 圖展開 / Cypher）
      ⇄ Neo4j Knowledge Graph：回傳 Relevant Info
      → LLM Question + Relevant Info
      → Enriched Answer

兩種模式：
  - agent（預設）：Claude tool use，多輪自主檢索，適合多跳、彙總類問題
  - simple：neo4j-graphrag 內建的 GraphRAG 類別，一次檢索 + 一次生成，便宜快速
"""
import json
from dataclasses import dataclass, field

from anthropic import beta_tool
from neo4j_graphrag.generation import GraphRAG as LibGraphRAG
from neo4j_graphrag.generation import RagTemplate

from . import config
from .graph_store import GraphStore
from .llm import FALLBACK_BETA, FALLBACK_MODELS, claude_client, make_qa_llm

SYSTEM_TEMPLATE = """你是一個 GraphRAG 問答助理，知識來源是一個 Neo4j 知識圖譜。

回答流程：
1. 先用檢索工具蒐集證據，不要憑記憶回答。通常先用 search_chunks 與 search_entities，
   需要多跳推理（A 和 C 有什麼關聯）時用 expand_entity，需要計數、排序、彙總或特定路徑時用 run_cypher。
2. 證據不足時換個關鍵字或換個工具再查；同一個查詢不要重複呼叫。
3. 用使用者的語言回答。只根據檢索到的內容作答，在句末用 [chunk_id] 或 [實體名稱] 標註出處。
4. 如果圖譜裡找不到答案，直接說找不到，並說明查過哪些方向。

<graph_schema>
{schema}
</graph_schema>"""

SIMPLE_SYSTEM = "根據 Context 回答問題。只使用 Context 內的資訊，找不到就說找不到。用使用者的語言回答。"


@dataclass
class Answer:
    text: str
    evidence: list[dict] = field(default_factory=list)


def _build_tools(store: GraphStore, evidence: list[dict]):
    def run(tool: str, args: dict, fn):
        try:
            result = fn()
        except Exception as e:
            return json.dumps({"error": str(e), "hint": "檢查參數或改用其他工具"}, ensure_ascii=False)
        evidence.append({"tool": tool, "args": args, "result": result})
        return json.dumps(result, ensure_ascii=False, default=str)

    @beta_tool
    def search_chunks(query: str, k: int = 5) -> str:
        """混合搜尋原文片段（向量語意 + 全文關鍵字），回傳片段原文、出處，以及片段中的實體和它們的關係。適合找事實描述與上下文。

        Args:
            query: 搜尋內容，可直接用使用者問題，或改寫成關鍵敘述。
            k: 回傳筆數，1-10。
        """
        return run("search_chunks", {"query": query}, lambda: store.search_chunks(query, max(1, min(k, 10))))

    @beta_tool
    def search_entities(keyword: str, k: int = 5) -> str:
        """用名稱或描述的關鍵字搜尋實體（人、組織、產品等），回傳實體類型、描述與直接相連的關係。

        Args:
            keyword: 名稱或描述中的關鍵字，例如「星河科技」、「技術長」。
            k: 回傳實體數，1-10。
        """
        return run("search_entities", {"keyword": keyword}, lambda: store.search_entities(keyword, max(1, min(k, 10))))

    @beta_tool
    def expand_entity(name: str, hops: int = 2) -> str:
        """從一個已知實體出發，沿關係往外展開 1-3 跳，回傳路徑。適合多跳推理問題。

        Args:
            name: 實體名稱（最好用 search_entities 回傳的 name）。
            hops: 展開跳數，1-3。
        """
        return run("expand_entity", {"name": name, "hops": hops}, lambda: store.expand_entity(name, hops))

    @beta_tool
    def run_cypher(cypher: str) -> str:
        """在 Neo4j 執行唯讀 Cypher 查詢（最多回傳 50 列）。用於計數、排序、彙總、指定路徑等結構化問題。
        只能 MATCH/RETURN，禁止任何寫入。字串比對建議用 toLower(e.name) CONTAINS '...'。不要回傳 embedding 屬性。

        Args:
            cypher: 完整的唯讀 Cypher 查詢，結構請參考 system prompt 中的 graph_schema。
        """
        return run("run_cypher", {"cypher": cypher}, lambda: store.run_readonly_cypher(cypher))

    return [search_chunks, search_entities, expand_entity, run_cypher]


def _fallback_params(model: str) -> dict:
    # 拒答備援只有部分模型支援（例如 Haiku 5.5 沒有），不支援的模型不送這個參數
    if model in FALLBACK_MODELS:
        return {"betas": [FALLBACK_BETA], "fallbacks": "default"}
    return {}


class GraphRAGAgent:
    def __init__(self, store: GraphStore | None = None):
        self.store = store or GraphStore()
        self._system: str | None = None
        self._simple: LibGraphRAG | None = None

    @property
    def system(self) -> str:
        # 每個 session 只產生一次 schema，維持 system prompt 穩定以命中 prompt cache
        if self._system is None:
            self._system = SYSTEM_TEMPLATE.format(schema=self.store.schema_summary())
        return self._system

    def ask(self, question: str) -> Answer:
        """Agent 模式：Claude 自己決定怎麼查。"""
        evidence: list[dict] = []
        runner = claude_client().beta.messages.tool_runner(
            model=config.QA_MODEL,
            max_tokens=16000,
            max_iterations=12,
            system=self.system,
            tools=_build_tools(self.store, evidence),
            output_config={"effort": config.QA_EFFORT},
            cache_control={"type": "ephemeral"},
            messages=[{"role": "user", "content": question}],
            **_fallback_params(config.QA_MODEL),
        )
        final = runner.until_done()

        if final.stop_reason == "refusal":
            return Answer(text="（模型拒絕回答此問題）", evidence=evidence)
        text = "".join(b.text for b in final.content if b.type == "text").strip()
        if final.stop_reason == "max_tokens":
            text += "\n\n（回答因長度上限被截斷）"
        return Answer(text=text, evidence=evidence)

    def ask_simple(self, question: str, top_k: int = 5) -> Answer:
        """Simple 模式：neo4j-graphrag 的 GraphRAG 類別（一次檢索 + 一次生成）。"""
        if self._simple is None:
            self._simple = LibGraphRAG(
                retriever=self.store.chunk_retriever,
                llm=make_qa_llm(),
                prompt_template=RagTemplate(system_instructions=SIMPLE_SYSTEM),
            )
        result = self._simple.search(query_text=question, retriever_config={"top_k": top_k}, return_context=True)
        items = [item.content for item in result.retriever_result.items] if result.retriever_result else []
        return Answer(text=result.answer, evidence=[{"tool": "hybrid_cypher_retriever", "args": {"query": question}, "result": items}])
