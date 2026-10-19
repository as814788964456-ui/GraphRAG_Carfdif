"""用事先切好的父子塊建立知識圖譜（取代 ingest.py 裡的 FixedSizeSplitter）。

在專案根目錄執行（需要 .env、Neo4j、Claude API key；和 ingest.py 一樣）：

    python ingest_chunks.py data/chunks_structure_bge-m3                   # 全部 62 份
    python ingest_chunks.py data/chunks_structure_bge-m3 --docs HR-002 IT-FAQ-002
    python ingest_chunks.py data/chunks_structure_bge-m3 --dry-run         # 只印計畫，不連資料庫、不呼叫 Claude
    python ingest_chunks.py data/chunks_structure_bge-m3 --links-only      # 子塊已經寫好，只補父子／版本／引用關係
    python ingest_chunks.py data/chunks_structure_bge-m3 --no-header       # Chunk.text 只放原文（脈絡標頭留在 context_header 屬性）
    python ingest_chunks.py data/chunks_structure_bge-m3 --lexical-only    # 只寫文件／子塊／父塊與關係，不算向量、不呼叫 Claude
    python ingest_chunks.py data/chunks_structure_bge-m3 --lexical-only --embed   # 同上，但子塊加上 bge-m3 向量（不抽實體）

流程：每份文件 → PrecomputedChunkSplitter（用切好的子塊）→ bge-m3 向量 → Claude 抽取實體與關係 →
寫入 Neo4j → 合併同名實體；全部文件完成後建立索引，並補上 ParentChunk、CHILD_OF、PART_OF、
SUPERSEDES、REFERENCES、FLOWCHART_OF（見 chunk_loader.link_graph）。

驗證狀態（2026-10-19）：--lexical-only 與 link_graph 已對本地 Neo4j 5.26.12＋APOC 跑過全部 62 份。
Claude 抽取實體還沒有對真的模型跑過，第一次請先用 --docs 跑 1–2 份確認。
"""
import argparse
import asyncio
import logging

from graphrag import chunk_loader as cl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest_chunks")


def select_docs(run: dict, wanted: list | None) -> list:
    docs = run["documents"]
    if not wanted:
        return docs
    w = set(wanted)
    return [d for d in docs if d["doc_uid"] in w or d["doc_id"] in w or d["doc_uid"].split("@")[0] in w]


def print_plan(run: dict, docs: list):
    kids = [c for d in docs for c in run["children_by_doc"][d["doc_uid"]]]
    toks = sorted(c["token_count"] for c in kids)
    log.info("策略 %s｜token 計法 %s｜文件 %d｜父塊 %d｜子塊 %d", run["info"].get("strategy"), run["info"].get("token_method"), len(docs), sum(len(run["parents_by_doc"][d["doc_uid"]]) for d in docs), len(kids))
    if toks:
        log.info("子塊 token（含標頭）：中位數 %d｜最大 %d", toks[len(toks) // 2], toks[-1])
    if run["info"].get("embedder_is_standin"):
        log.warning("這份語意切分用的是替代向量（%s），不是 bge-m3；正式使用前請用 --embedder bge-m3 重新產生。", run["info"].get("embedder"))
    if not str(run["info"].get("token_method", "")).startswith("tokenizer:"):
        log.warning("子塊長度是用估算的 token 數切的，不是 bge-m3 的 tokenizer；有網路時請加 --tokenizer BAAI/bge-m3 重切，或用 recount_tokens.py 檢查。")


async def write_lexical(drv, database: str, doc: dict, children: list, use_header: bool, contexts: dict, emb=None):
    """--lexical-only：用和 SimpleKGPipeline 相同的 LexicalGraphBuilder／Neo4jWriter 寫 Document、Chunk、
    FROM_DOCUMENT、NEXT_CHUNK，但不算向量、不抽實體。之後要補向量與實體，用一般模式加 --force 重跑即可。"""
    from neo4j_graphrag.components.kg_writer import Neo4jWriter
    from neo4j_graphrag.components.lexical_graph import LexicalGraphBuilder
    from neo4j_graphrag.components.types import DocumentInfo

    chunks = await cl.PrecomputedChunkSplitter(children, use_header=use_header, contexts=contexts).run()
    if emb is not None:  # 和 SimpleKGPipeline 相同的 TextChunkEmbedder，向量寫在 Chunk.embedding
        from neo4j_graphrag.components.embedder import TextChunkEmbedder

        chunks = await TextChunkEmbedder(embedder=emb).run(text_chunks=chunks)
    info = DocumentInfo(path=doc["source_path"], metadata=cl.document_metadata(doc))
    graph = (await LexicalGraphBuilder().run(chunks, info)).graph
    return await Neo4jWriter(drv, neo4j_database=database).run(graph)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", help="切好的 chunks 資料夾，例如 data/chunks_structure_bge-m3")
    ap.add_argument("--docs", nargs="*", help="只處理這些文件（doc_uid 或文件編號）")
    ap.add_argument("--no-header", action="store_true")
    ap.add_argument("--links-only", action="store_true")
    ap.add_argument("--lexical-only", action="store_true", help="不呼叫 Claude，只寫文件／子塊／父塊與關係（驗證圖譜結構用）")
    ap.add_argument("--embed", action="store_true", help="搭配 --lexical-only：子塊加上 bge-m3 向量")
    ap.add_argument("--contexts", help="contextualize_llm.py 產生的 jsonl（LLM 寫的子塊說明）；不給就只用確定性的脈絡標頭")
    ap.add_argument("--force", action="store_true", help="資料庫已經有的文件也重新寫入（會產生重複節點，除非先清掉）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    run = cl.load_run(args.run_dir)
    contexts = cl.load_contexts(args.contexts)
    docs = select_docs(run, args.docs)
    print_plan(run, docs)
    if args.dry_run:
        return

    from neo4j_graphrag.experimental.pipeline.kg_builder import SimpleKGPipeline

    from graphrag import config
    from graphrag.graph_store import driver, embedder, init_indexes
    from graphrag.kg_schema import SCHEMA
    from graphrag.llm import make_extract_llm

    drv = driver()
    try:
        if not args.links_only:
            llm = None if args.lexical_only else make_extract_llm()
            emb = embedder() if (args.embed or not args.lexical_only) else None
            recs, _, _ = drv.execute_query("MATCH (d:Document) WHERE d.doc_uid IS NOT NULL RETURN d.doc_uid AS uid", database_=config.NEO4J_DATABASE)
            existing = {r["uid"] for r in recs}
            for i, doc in enumerate(docs, 1):
                uid = doc["doc_uid"]
                if uid in existing and not args.force:
                    log.info("[%d/%d] 略過 %s（資料庫已有）", i, len(docs), uid)
                    continue
                log.info("[%d/%d] 處理 %s（%d 個子塊）", i, len(docs), uid, len(run["children_by_doc"][uid]))
                if args.lexical_only:
                    result = await write_lexical(drv, config.NEO4J_DATABASE, doc, run["children_by_doc"][uid], not args.no_header, contexts, emb)
                    log.info("  完成：%s", result)
                    continue
                kg = SimpleKGPipeline(
                    llm=llm,
                    driver=drv,
                    embedder=emb,
                    schema=SCHEMA,
                    text_splitter=cl.PrecomputedChunkSplitter(run["children_by_doc"][uid], use_header=not args.no_header, contexts=contexts),
                    from_file=False,
                    on_error="IGNORE",
                    perform_entity_resolution=True,
                    neo4j_database=config.NEO4J_DATABASE,
                )
                text = "\n".join(p["text"] for p in run["parents_by_doc"][uid])  # 切塊器不看它；只是 pipeline 需要有輸入
                result = await kg.run_async(text=text, file_path=doc["source_path"], document_metadata=cl.document_metadata(doc))
                log.info("  完成：%s", result.result)
        init_indexes(drv)
        counts = cl.link_graph(drv, run, config.NEO4J_DATABASE, {d["doc_uid"] for d in docs})
        log.info("父子／版本／引用關係已補上：%s", counts)
    finally:
        drv.close()


if __name__ == "__main__":
    asyncio.run(main())
