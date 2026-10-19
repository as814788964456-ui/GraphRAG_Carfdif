"""Neo4j 連線、索引與檢索。

SimpleKGPipeline 產生的圖譜結構：
  (:Chunk {id, index, text, embedding})-[:FROM_DOCUMENT]->(:Document {path})
  (:Chunk)-[:NEXT_CHUNK]->(:Chunk)
  （用 ingest_chunks.py 建圖時另有）
  (:Chunk)-[:CHILD_OF]->(:ParentChunk {id, heading, text})-[:PART_OF]->(:Document)
  (:Document)-[:REFERENCES | SUPERSEDES]->(:Document)、(:Document 流程圖)-[:FLOWCHART_OF]->(:Document)
  (:__Entity__:Process {name, description})-[:FROM_CHUNK]->(:Chunk)
  (:__Entity__)-[:PERFORMED_IN | REQUIRES | GOVERNED_BY | ...]->(:__Entity__)（見 kg_schema.py）
"""
import re
from typing import Any

import neo4j
from neo4j import GraphDatabase
from neo4j_graphrag.embeddings import SentenceTransformerEmbeddings
from neo4j_graphrag.indexes import create_vector_index
from neo4j_graphrag.retrievers import HybridCypherRetriever
from neo4j_graphrag.schema import get_schema
from neo4j_graphrag.types import RetrieverResult, RetrieverResultItem

from . import config

LEXICAL = {"Chunk", "Document", "__KGBuilder__"}
STRUCTURAL_RELS = ["FROM_CHUNK", "FROM_DOCUMENT", "NEXT_CHUNK"]
_WRITE_PATTERN = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b|CALL\s+(dbms|db\.create|apoc)",
    re.IGNORECASE,
)
MAX_ROWS = 50

# 向量/全文找到 Chunk 後，順著圖譜帶出片段中的實體與它們的關係（GraphRAG 的「Graph」部分）
CHUNK_RETRIEVAL_QUERY = """
OPTIONAL MATCH (node)-[:FROM_DOCUMENT]->(d:Document)
OPTIONAL MATCH (node)-[:CHILD_OF]->(p:ParentChunk)
OPTIONAL MATCH (e:__Entity__)-[:FROM_CHUNK]->(node)
OPTIONAL MATCH (e)-[r]-(o:__Entity__)
RETURN node.id AS chunk_id, coalesce(d.doc_id + ' ' + d.title + coalesce(' ' + d.version, ''), d.path) AS source, node.text AS text, score,
       p.id AS parent_id, p.heading AS parent_heading, p.text AS parent_text,
       collect(DISTINCT e.name)[..20] AS entities,
       collect(DISTINCT startNode(r).name + ' -[' + type(r) + ']-> ' + endNode(r).name)[..30] AS relations
"""


def format_chunk_record(record: neo4j.Record) -> RetrieverResultItem:
    content = (
        f"[{record['chunk_id']}] 來源：{record['source']}\n{record['text']}\n"
        f"片段中的實體：{', '.join(record['entities']) or '（無）'}\n"
        f"相關關係：{'; '.join(record['relations']) or '（無）'}"
    )
    return RetrieverResultItem(
        content=content,
        metadata={
            "chunk_id": record["chunk_id"],
            "score": record["score"],
            "parent_id": record["parent_id"],
            "parent_heading": record["parent_heading"],
            "parent_text": record["parent_text"],
        },
    )


class ParentExpandingRetriever(HybridCypherRetriever):
    """父子文檔：用子塊比對，再把同一個父塊（所屬段落全文）附上一次，讓 LLM 看到完整脈絡。

    做在檢索器裡，Smart Search（search_chunks）和 --simple（函式庫的 GraphRAG）才會拿到一樣的內容。
    """

    def search(self, *args, **kwargs) -> RetrieverResult:
        result = super().search(*args, **kwargs)
        if not config.EXPAND_PARENT:
            return result
        seen = set()
        for item in result.items:
            meta = item.metadata or {}
            pid, ptext = meta.get("parent_id"), meta.get("parent_text")
            if pid and ptext and pid not in seen:
                seen.add(pid)
                item.content += f"\n【所屬段落：{meta.get('parent_heading')}】\n{ptext}"
        return result


def driver() -> neo4j.Driver:
    return GraphDatabase.driver(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))


def embedder() -> SentenceTransformerEmbeddings:
    return SentenceTransformerEmbeddings(model=config.EMBED_MODEL, device=config.EMBED_DEVICE)


def init_indexes(drv: neo4j.Driver):
    create_vector_index(
        drv, config.CHUNK_VECTOR_INDEX, label="Chunk", embedding_property="embedding",
        dimensions=config.EMBED_DIM, similarity_fn="cosine", neo4j_database=config.NEO4J_DATABASE,
    )
    # 全文索引用 cjk analyzer（bigram），中文關鍵字比對比預設 analyzer 準
    for name, label, props in (
        (config.CHUNK_FULLTEXT_INDEX, "Chunk", "n.text"),
        (config.ENTITY_FULLTEXT_INDEX, "__Entity__", "n.name, n.description"),
    ):
        drv.execute_query(
            f"CREATE FULLTEXT INDEX {name} IF NOT EXISTS FOR (n:`{label}`) ON EACH [{props}] "
            "OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}}",
            database_=config.NEO4J_DATABASE,
        )


class GraphStore:
    def __init__(self, drv: neo4j.Driver | None = None, emb: SentenceTransformerEmbeddings | None = None):
        self.driver = drv or driver()
        self.embedder = emb or embedder()
        self.db = config.NEO4J_DATABASE
        self.chunk_retriever = ParentExpandingRetriever(
            self.driver,
            vector_index_name=config.CHUNK_VECTOR_INDEX,
            fulltext_index_name=config.CHUNK_FULLTEXT_INDEX,
            retrieval_query=CHUNK_RETRIEVAL_QUERY,
            embedder=self.embedder,
            result_formatter=format_chunk_record,
            neo4j_database=self.db,
        )

    def close(self):
        self.driver.close()

    def schema_summary(self) -> str:
        """給 LLM 寫 Cypher 用的圖譜結構（需要 APOC）。"""
        schema = get_schema(self.driver, database=self.db)
        return (
            f"{schema}\n\n"
            "Notes:\n"
            "- Every extracted entity also has the label __Entity__ and properties `name`, `description`.\n"
            "- (:__Entity__)-[:FROM_CHUNK]->(:Chunk) links an entity to the text it came from.\n"
            "- (:Chunk)-[:CHILD_OF]->(:ParentChunk)-[:PART_OF]->(:Document): ParentChunk.text is the full section around a chunk.\n"
            "- Document nodes with superseded = true are old versions; (:Document)-[:SUPERSEDES]->(old). "
            "Prefer current documents (superseded IS NULL) when answering.\n"
            "- Never return the `embedding` property."
        )

    # ---------- 檢索 ----------
    def _read(self, cypher: str, **params) -> list[dict]:
        # 交易函式裡的 tx.run 不接受 Query 物件，timeout 要用 unit_of_work 設定
        @neo4j.unit_of_work(timeout=15)
        def work(tx):
            return tx.run(cypher, **params).data()

        with self.driver.session(database=self.db) as s:
            return s.execute_read(work)

    def search_chunks(self, query: str, k: int = 5) -> list[str]:
        result = self.chunk_retriever.search(query_text=query, top_k=k)
        return [item.content for item in result.items]

    def search_entities(self, keyword: str, k: int = 5) -> list[dict]:
        return self._read(
            """
            CALL db.index.fulltext.queryNodes($index, $kw, {limit: $k}) YIELD node, score
            OPTIONAL MATCH (node)-[r]-(o:__Entity__)
            WITH node, score, r, o ORDER BY o.name
            RETURN node.name AS name,
                   [l IN labels(node) WHERE l <> '__Entity__'][0] AS type,
                   node.description AS description, round(score, 3) AS score,
                   collect(startNode(r).name + ' -[' + type(r) + ']-> ' + endNode(r).name)[..20] AS relations
            ORDER BY score DESC
            """,
            index=config.ENTITY_FULLTEXT_INDEX, kw=_lucene_escape(keyword), k=k,
        )

    def expand_entity(self, name: str, hops: int = 2) -> list[dict]:
        hops = max(1, min(hops, 3))
        return self._read(
            f"""
            MATCH (e:__Entity__) WHERE toLower(e.name) = toLower($name)
               OR toLower(e.name) CONTAINS toLower($name)
            WITH e ORDER BY size(e.name) LIMIT 1
            MATCH p = (e)-[*1..{hops}]-(:__Entity__)
            WHERE all(n IN nodes(p) WHERE n:__Entity__)
            WITH p LIMIT 60
            RETURN [n IN nodes(p) | n.name] AS path_nodes,
                   [r IN relationships(p) | startNode(r).name + ' -[' + type(r) + ']-> ' + endNode(r).name] AS path_relations
            """,
            name=name.strip(),
        )

    def run_readonly_cypher(self, cypher: str) -> list[dict]:
        if _WRITE_PATTERN.search(cypher):
            raise ValueError("只允許唯讀查詢（偵測到寫入關鍵字）。")
        rows = self._read(cypher)
        return [_strip_embeddings(r) for r in rows[:MAX_ROWS]]


def _strip_embeddings(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_embeddings(v) for k, v in value.items() if k != "embedding"}
    if isinstance(value, list):
        return [_strip_embeddings(v) for v in value]
    return value


def _lucene_escape(text: str) -> str:
    escaped = re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", text).strip()
    return escaped or "__no_keyword__"
