"""用本地 LLM（Ollama）抽實體建圖譜，不呼叫 Claude API。參數和 ingest_chunks.py 完全相同：

    python ingest_chunks_local.py data/chunks_structure_bge-m3 --docs HR-002 IT-FAQ-002   # 先試 2 份
    python ingest_chunks_local.py data/chunks_structure_bge-m3                            # 全部 62 份

模型由 .env 的 LOCAL_EXTRACT_MODEL 決定（預設原版 Qwen3-4B：qwen3:4b-q4_K_M），輸出會被 JSON schema 限制在
kg_schema.py 定義的實體與關係類型內，抽取 prompt 用 local_kg_prompt.py 的中文規則與範例。
和 Claude 版一樣，資料庫已有的文件會被略過，跑過 --lexical-only 後要先清空資料庫（MATCH (n) DETACH DELETE n）。
"""
import asyncio

import graphrag.llm
import ingest_chunks
from graphrag.local_kg_prompt import LocalERTemplate
from graphrag.local_llm import make_local_extract_llm
from neo4j_graphrag.experimental.pipeline import kg_builder


class LocalKGPipeline(kg_builder.SimpleKGPipeline):
    """預設改用給小模型的中文抽取 prompt。"""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("prompt_template", LocalERTemplate())
        super().__init__(*args, **kwargs)


# ingest_chunks.main() 在執行時才 import make_extract_llm 和 SimpleKGPipeline，這裡換成本地版即可共用整個匯入流程
graphrag.llm.make_extract_llm = make_local_extract_llm
kg_builder.SimpleKGPipeline = LocalKGPipeline

if __name__ == "__main__":
    asyncio.run(ingest_chunks.main())
