"""讀取 chunks 資料夾（documents／parents／children .jsonl），提供：

1. PrecomputedChunkSplitter：讓 SimpleKGPipeline 直接使用切好的子塊（取代 FixedSizeSplitter）。
2. chunk_metadata() / document_metadata()：寫進 Chunk／Document 節點的屬性（只放 Neo4j 收得了的型別）。
3. link_graph()：子塊寫完後補上 ParentChunk、CHILD_OF、PART_OF、SUPERSEDES、REFERENCES、FLOWCHART_OF。

這個檔案不依賴 graphrag 套件的其他模組，可以單獨測試。
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Iterator

try:  # neo4j-graphrag 1.22 起元件搬到 neo4j_graphrag.components；舊路徑仍是相容殼，兩邊都試
    from neo4j_graphrag.experimental.components.text_splitters.base import TextSplitter
    from neo4j_graphrag.experimental.components.types import TextChunk, TextChunks
except ImportError:  # pragma: no cover
    from neo4j_graphrag.components.text_splitters.base import TextSplitter
    from neo4j_graphrag.components.types import TextChunk, TextChunks


# ---------------------------------------------------------------- 讀檔
def read_jsonl(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def load_contexts(path: str | None) -> dict:
    if not path:
        return {}
    return {r["child_id"]: r["context"] for r in read_jsonl(path) if r.get("context")}


def load_run(run_dir: str) -> dict:
    run = {n: read_jsonl(os.path.join(run_dir, f"{n}.jsonl")) for n in ("documents", "parents", "children")}
    info = os.path.join(run_dir, "run_info.json")
    run["info"] = json.load(open(info, encoding="utf-8")) if os.path.isfile(info) else {}
    run["children_by_doc"] = defaultdict(list)
    for c in run["children"]:
        run["children_by_doc"][c["doc_uid"]].append(c)
    for v in run["children_by_doc"].values():
        v.sort(key=lambda c: c["ordinal_in_doc"])
    run["parents_by_doc"] = defaultdict(list)
    for p in run["parents"]:
        run["parents_by_doc"][p["doc_uid"]].append(p)
    for v in run["parents_by_doc"].values():
        v.sort(key=lambda p: p["ordinal"])
    return run


# ---------------------------------------------------------------- 屬性（只放 Neo4j 收得了的型別：字串／數字／布林／同型別的清單；None 要拿掉）
def _clean(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, list) and not all(isinstance(x, (str, int, float, bool)) for x in v):
            continue
        if isinstance(v, (dict, tuple, set)):
            continue
        out[k] = v
    return out


def chunk_metadata(c: dict, use_header: bool) -> dict:
    """Chunk 節點屬性。`id` 放子塊編號，這樣 graph_store.py 現有的 `node.id` 查詢不用改。"""
    return _clean(
        {
            "id": c["child_id"],
            "child_id": c["child_id"],
            "parent_id": c["parent_id"],
            "doc_uid": c["doc_uid"],
            "doc_id": c["doc_id"] or None,
            "part": c["part"],
            "ordinal_in_doc": c["ordinal_in_doc"],
            "ordinal_in_parent": c["ordinal_in_parent"],
            "section_path": c["section_path"],
            "section_label": c["section_label"],
            "kind": c["kind"],
            "token_count": c["token_count"] if use_header else c["token_count_raw"],
            "overlap_tokens": c["overlap_tokens"],
            "context_header": c["context_header"] or None,
            "has_header": bool(use_header and c["context_header"]),
            "strategy": c["strategy"],
            "token_method": c["token_method"],
            "flow_id": c.get("flow_id"),
        }
    )


def document_metadata(d: dict) -> dict:
    """Document 節點屬性。DocumentInfo.metadata 的值一律要是字串。"""
    keys = ["doc_uid", "doc_id", "doc_id_source", "part", "title", "version", "effective_date", "status", "owner_dept", "confidentiality", "source_file", "parent_doc", "flow_id"]
    return {k: str(d[k]) for k in keys if d.get(k)}


# ---------------------------------------------------------------- 切塊器
class PrecomputedChunkSplitter(TextSplitter):
    """回傳事先切好的子塊（一份文件一個實例）。傳進來的 text 不使用。

    use_header=True：Chunk.text = 脈絡標頭＋原文（embed_text），向量、全文索引、實體抽取都看得到標頭；
    use_header=False：Chunk.text = 原文，標頭只留在 context_header 屬性。
    """

    def __init__(self, children: list, use_header: bool = True, contexts: dict | None = None):
        self.children = children
        self.use_header = use_header
        self.contexts = contexts or {}  # {child_id: contextualize_llm.py 產生的說明}，接在標頭後面

    def chunk_text(self, c: dict) -> str:
        ctx = self.contexts.get(c["child_id"])
        if not self.use_header:
            return c["text"]
        if not ctx:
            return c["embed_text"]
        head = c["context_header"]
        return (head + "\n" if head else "") + ctx + "\n" + c["text"]

    def iter_chunks(self, text: str = "") -> Iterator[TextChunk]:
        for i, c in enumerate(self.children):
            meta = chunk_metadata(c, self.use_header)
            if self.contexts.get(c["child_id"]) and self.use_header:
                meta["llm_context"] = self.contexts[c["child_id"]]
            yield TextChunk(text=self.chunk_text(c), index=i, uid=c["child_id"], metadata=meta)

    async def run(self, text: str = "") -> TextChunks:
        return TextChunks(chunks=list(self.iter_chunks(text)))


# ---------------------------------------------------------------- 父子／版本／引用關係
INDEXES = [
    "CREATE INDEX chunk_child_id IF NOT EXISTS FOR (n:Chunk) ON (n.id)",
    "CREATE INDEX parentchunk_id IF NOT EXISTS FOR (n:ParentChunk) ON (n.id)",
    "CREATE INDEX document_uid IF NOT EXISTS FOR (n:Document) ON (n.doc_uid)",
    "CREATE INDEX document_doc_id IF NOT EXISTS FOR (n:Document) ON (n.doc_id)",
]

CYPHER_PARENTS = """
UNWIND $rows AS p
MERGE (pc:ParentChunk {id: p.parent_id})
SET pc.text = p.text, pc.heading = p.heading, pc.section_path = p.section_path,
    pc.section_label = p.section_label, pc.token_count = p.token_count, pc.ordinal = p.ordinal,
    pc.doc_uid = p.doc_uid, pc.doc_id = p.doc_id, pc.part = p.part, pc.strategy = p.strategy
WITH pc, p
MATCH (d:Document {doc_uid: p.doc_uid})
MERGE (pc)-[:PART_OF]->(d)
"""

CYPHER_CHILD_OF = """
UNWIND $rows AS r
MATCH (c:Chunk {id: r.child_id})
MATCH (pc:ParentChunk {id: r.parent_id})
MERGE (c)-[:CHILD_OF]->(pc)
"""

CYPHER_NEXT_PARENT = """
UNWIND $rows AS r
MATCH (a:ParentChunk {id: r.a})
MATCH (b:ParentChunk {id: r.b})
MERGE (a)-[:NEXT_PARENT]->(b)
"""

CYPHER_DOC_LISTS = """
UNWIND $rows AS r
MATCH (d:Document {doc_uid: r.doc_uid})
SET d.references = r.references, d.dropped_boilerplate = r.dropped_boilerplate
"""

CYPHER_REFERENCES = """
UNWIND $rows AS r
MATCH (a:Document {doc_uid: r.src})
MATCH (b:Document {doc_id: r.dst, part: 'main'}) WHERE b.superseded IS NULL  // 不要連到同編號的舊版節點
MERGE (a)-[:REFERENCES]->(b)
"""

CYPHER_SUPERSEDES = """
UNWIND $rows AS r
MATCH (d:Document {doc_uid: r.doc_uid})
MERGE (o:Document {doc_uid: r.old_uid})
  ON CREATE SET o.path = r.old_path, o.doc_id = r.doc_id, o.part = 'main', o.title = d.title,
                o.version = r.version, o.effective_date = r.effective_date, o.status = r.status, o.superseded = true
MERGE (d)-[:SUPERSEDES]->(o)
"""

CYPHER_FLOWCHART_OF = """
UNWIND $rows AS r
MATCH (f:Document {doc_uid: r.flow_uid})
MATCH (m:Document {doc_id: r.parent_doc, part: 'main'}) WHERE m.superseded IS NULL  // 不要連到同編號的舊版節點
MERGE (f)-[:FLOWCHART_OF]->(m)
"""


def _batches(rows: list, n: int = 500):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def link_rows(run: dict, doc_uids: set | None = None) -> dict:
    """把 jsonl 轉成各段 Cypher 的參數列（純資料，方便不連資料庫也能檢查）。"""
    keep = lambda uid: doc_uids is None or uid in doc_uids  # noqa: E731
    docs = [d for d in run["documents"] if keep(d["doc_uid"])]
    parents = [p for p in run["parents"] if keep(p["doc_uid"])]
    children = [c for c in run["children"] if keep(c["doc_uid"])]
    rows = {
        "parents": [{k: p[k] for k in ("parent_id", "text", "heading", "section_path", "section_label", "token_count", "ordinal", "doc_uid", "doc_id", "part", "strategy")} | {"doc_id": p["doc_id"] or ""} for p in parents],
        "child_of": [{"child_id": c["child_id"], "parent_id": c["parent_id"]} for c in children],
        "next_parent": [],
        "doc_lists": [{"doc_uid": d["doc_uid"], "references": d["references"], "dropped_boilerplate": d["dropped_boilerplate"]} for d in docs],
        "references": [{"src": d["doc_uid"], "dst": r} for d in docs for r in d["references"]],
        "supersedes": [],
        "flowchart_of": [{"flow_uid": d["doc_uid"], "parent_doc": d["parent_doc"]} for d in docs if d["part"] == "flowchart" and d.get("parent_doc")],
    }
    by_doc = defaultdict(list)
    for p in parents:
        by_doc[p["doc_uid"]].append(p)
    for ps in by_doc.values():
        ps.sort(key=lambda p: p["ordinal"])
        rows["next_parent"] += [{"a": a["parent_id"], "b": b["parent_id"]} for a, b in zip(ps, ps[1:])]
    for d in docs:
        for s in d.get("supersedes") or []:
            base = os.path.splitext(os.path.basename(s.get("source_file") or s.get("path") or "old"))[0]
            rows["supersedes"].append(
                {
                    "doc_uid": d["doc_uid"],
                    "old_uid": f"{d['doc_uid']}@old:{base}",
                    "old_path": s.get("path") or s.get("source_file") or "",
                    "doc_id": d["doc_id"] or "",
                    "version": s.get("version") or "",
                    "effective_date": s.get("effective_date") or "",
                    "status": s.get("status") or "舊版",
                }
            )
    return rows


def link_graph(driver, run: dict, database: str = "neo4j", doc_uids: set | None = None) -> dict:
    """在子塊都寫進 Neo4j 之後執行。每段 Cypher 都是 MERGE，重複執行不會產生重複節點／關係。"""
    rows = link_rows(run, doc_uids)
    for q in INDEXES:
        driver.execute_query(q, database_=database)
    steps = [
        ("parents", CYPHER_PARENTS),
        ("child_of", CYPHER_CHILD_OF),
        ("next_parent", CYPHER_NEXT_PARENT),
        ("doc_lists", CYPHER_DOC_LISTS),
        ("references", CYPHER_REFERENCES),
        ("supersedes", CYPHER_SUPERSEDES),
        ("flowchart_of", CYPHER_FLOWCHART_OF),
    ]
    counts = {}
    for name, cypher in steps:
        for batch in _batches(rows[name]):
            driver.execute_query(cypher, parameters_={"rows": batch}, database_=database)
        counts[name] = len(rows[name])
    return counts
