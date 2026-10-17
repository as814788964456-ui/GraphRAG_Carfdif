"""用 neo4j-graphrag 的 SimpleKGPipeline 建立知識圖譜：python ingest.py data/  （支援 .txt / .md）

Pipeline 流程：切塊 → bge-m3 產生片段向量 → Claude 抽取實體與關係 → 寫入 Neo4j → 合併同名實體
"""
import argparse
import asyncio
import logging
from pathlib import Path

from neo4j_graphrag.experimental.components.text_splitters.fixed_size_splitter import FixedSizeSplitter
from neo4j_graphrag.experimental.pipeline.kg_builder import SimpleKGPipeline

from graphrag import config
from graphrag.graph_store import driver, embedder, init_indexes
from graphrag.kg_schema import SCHEMA
from graphrag.llm import make_extract_llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest")


def iter_files(paths: list[str]):
    for p in map(Path, paths):
        if p.is_dir():
            yield from sorted(f for f in p.rglob("*") if f.suffix.lower() in (".txt", ".md"))
        elif p.is_file():
            yield p


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", help="檔案或資料夾")
    args = parser.parse_args()

    drv = driver()
    try:
        kg_builder = SimpleKGPipeline(
            llm=make_extract_llm(),
            driver=drv,
            embedder=embedder(),
            schema=SCHEMA,
            # approximate=False：中文沒有空白分詞，直接依字元數切
            text_splitter=FixedSizeSplitter(
                chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP, approximate=False
            ),
            from_file=False,
            on_error="IGNORE",
            perform_entity_resolution=True,
            neo4j_database=config.NEO4J_DATABASE,
        )
        for f in iter_files(args.paths):
            log.info("處理 %s", f)
            result = await kg_builder.run_async(text=f.read_text(encoding="utf-8"), file_path=str(f))
            log.info("  完成：%s", result.result)

        # 索引放在最後建立：全文索引需要 __Entity__ label 已存在才有意義（IF NOT EXISTS 可重複執行）
        init_indexes(drv)
        log.info("索引建立完成")
    finally:
        drv.close()


if __name__ == "__main__":
    asyncio.run(main())
