import os

from dotenv import load_dotenv

load_dotenv()

# 各階段用哪個 LLM：claude | ollama
# EXTRACT_PROVIDER：建圖譜（抽取實體與關係）
# QA_PROVIDER：問答。ollama 時只能用 --simple 模式（Smart Search 需要 Claude）
EXTRACT_PROVIDER = os.getenv("EXTRACT_PROVIDER", "claude").lower()
QA_PROVIDER = os.getenv("QA_PROVIDER", "claude").lower()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "taide")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

# Smart Search 問答（多輪工具呼叫、寫 Cypher）
QA_MODEL = os.getenv("QA_MODEL", "claude-opus-5-5")
QA_EFFORT = os.getenv("QA_EFFORT", "high")
# --simple 問答（單次檢索 + 單次生成）
SIMPLE_QA_MODEL = os.getenv("SIMPLE_QA_MODEL", QA_MODEL)
SIMPLE_QA_EFFORT = os.getenv("SIMPLE_QA_EFFORT", "medium")
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "claude-opus-5-5")
EXTRACT_EFFORT = os.getenv("EXTRACT_EFFORT", "low")

EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))
# 空白 = 自動（有 CUDA 用 GPU）；和本地 LLM 搶顯存時設成 cpu
EMBED_DEVICE = os.getenv("EMBED_DEVICE") or None

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "graphrag-password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "100"))

# 索引名稱
CHUNK_VECTOR_INDEX = "chunk_embedding"
CHUNK_FULLTEXT_INDEX = "chunk_fulltext"
ENTITY_FULLTEXT_INDEX = "entity_fulltext"
