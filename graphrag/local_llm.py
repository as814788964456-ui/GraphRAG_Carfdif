"""本地 LLM（Ollama，2–4B 小模型）：建圖譜與問答都不呼叫 Claude API。

預設模型是原版 Qwen3-4B（https://huggingface.co/Qwen/Qwen3-4B，思考／不思考可切換）。
Ollama 的標籤要用 qwen3:4b-q4_K_M：qwen3:4b 現在指向 Qwen3-4B-Thinking-2507（只會思考），不是同一個模型。

直接呼叫 Ollama 的 REST API，不用 ollama 套件：neo4j-graphrag 1.22 把 ollama 套件限制在 0.5 以下，
舊版不能傳 think 參數，Qwen3 這類會思考的模型就關不掉思考，小模型會花很多 token 在思考上。

設定（.env，全部可省略）：
  LOCAL_EXTRACT_MODEL  建圖譜用的模型（預設 qwen3:4b-q4_K_M）
  LOCAL_QA_MODEL       問答用的模型（預設同上）
  LOCAL_HOST           Ollama 位址（預設同 OLLAMA_HOST）
  LOCAL_NUM_CTX        上下文長度（預設 16384；檢索結果附父塊時 8192 容易不夠；Qwen3-4B 上限 40960）
  LOCAL_THINK          false（預設）/ true / 空白 = 不送（模型不支援思考時）
  LOCAL_KEEP_ALIVE     模型閒置多久後從顯存卸載（預設 10m）
  LOCAL_TIMEOUT        單次請求逾時秒數（預設 300）
  LOCAL_NUM_PREDICT    單次最多輸出幾個 token（預設 2048）。temperature 0 時小模型偶爾會重複輸出停不下來，
                       JSON 格式又允許陣列無限延長，沒有上限的話一個子塊就會卡到逾時
"""
import asyncio
import logging
import os
import re

import httpx
from json_repair import repair_json
from neo4j_graphrag.exceptions import LLMGenerationError
from neo4j_graphrag.llm import LLMInterface
from neo4j_graphrag.llm.types import LLMResponse
from neo4j_graphrag.message_history import MessageHistory
from neo4j_graphrag.utils.rate_limit import DEFAULT_RATE_LIMIT_HANDLER

from . import config
from .kg_schema import SCHEMA

HOST = os.getenv("LOCAL_HOST", config.OLLAMA_HOST).rstrip("/")
EXTRACT_MODEL = os.getenv("LOCAL_EXTRACT_MODEL", "qwen3:4b-q4_K_M")
QA_MODEL = os.getenv("LOCAL_QA_MODEL", EXTRACT_MODEL)
NUM_CTX = int(os.getenv("LOCAL_NUM_CTX", "16384"))
THINK = os.getenv("LOCAL_THINK", "false").strip().lower()
KEEP_ALIVE = os.getenv("LOCAL_KEEP_ALIVE", "10m")
TIMEOUT = float(os.getenv("LOCAL_TIMEOUT", "300"))
NUM_PREDICT = int(os.getenv("LOCAL_NUM_PREDICT", "2048"))

EMPTY_GRAPH = '{"nodes": [], "relationships": []}'
_THINK_TAG = re.compile(r"<think>.*?</think>", re.DOTALL)
log = logging.getLogger(__name__)


def chat(model: str, messages: list[dict], tools: list[dict] | None = None, fmt: dict | str | None = None) -> dict:
    """呼叫 Ollama /api/chat，回傳 message（role、content、可能有 tool_calls）。"""
    body: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0, "num_ctx": NUM_CTX, "num_predict": NUM_PREDICT},
    }
    if THINK in ("true", "false"):
        body["think"] = THINK == "true"
    if tools:
        body["tools"] = tools
    if fmt is not None:
        body["format"] = fmt  # "json" 或 JSON schema：Ollama 會限制輸出必須符合格式
    try:
        r = httpx.post(f"{HOST}/api/chat", json=body, timeout=TIMEOUT)
    except httpx.ConnectError as e:
        raise LLMGenerationError(f"連不上 Ollama（{HOST}）：請先啟動 Ollama") from e
    except httpx.TimeoutException as e:
        raise LLMGenerationError(f"Ollama 超過 {TIMEOUT:.0f} 秒沒有回應（LOCAL_TIMEOUT）") from e
    if r.status_code == 404:
        raise LLMGenerationError(f"Ollama 沒有模型 {model}：請先執行 ollama pull {model}")
    if r.status_code != 200:
        raise LLMGenerationError(f"Ollama 錯誤 {r.status_code}：{r.text[:300]}")
    data = r.json()
    msg = data["message"]
    # 不支援 think 參數的舊版 Ollama 會把思考內容留在 content 裡
    msg["content"] = _THINK_TAG.sub("", msg.get("content") or "").strip()
    if data.get("done_reason") == "length":
        log.warning("輸出達到 %d token 上限被截斷（LOCAL_NUM_PREDICT）", NUM_PREDICT)
        if fmt is None:
            msg["content"] += "\n\n（回答因長度上限被截斷）"
        else:  # JSON 寫到一半：補齊結尾，保留已經輸出的部分
            msg["content"] = repair_json(msg["content"])
    return msg


def extraction_format(require_description: bool = True) -> dict:
    """抽實體用的 JSON schema。實體類型與關係類型限定為 kg_schema 的清單，小模型才不會自創類型。
    欄位對應 neo4j-graphrag 的 Neo4jGraph，可以直接交給函式庫驗證。
    require_description：試抽時 38% 的實體沒有描述，所以預設設成必填。"""
    labels = [n["label"] for n in SCHEMA["node_types"]]
    rel_types = [r["label"] for r in SCHEMA["relationship_types"]]
    node = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "label": {"type": "string", "enum": labels},
            "properties": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "description": {"type": "string"}, "code": {"type": "string"}},
                "required": ["name", "description"] if require_description else ["name"],
            },
        },
        "required": ["id", "label", "properties"],
    }
    rel = {
        "type": "object",
        "properties": {
            "start_node_id": {"type": "string"},
            "end_node_id": {"type": "string"},
            "type": {"type": "string", "enum": rel_types},
        },
        "required": ["start_node_id", "end_node_id", "type"],
    }
    return {
        "type": "object",
        "properties": {"nodes": {"type": "array", "items": node}, "relationships": {"type": "array", "items": rel}},
        "required": ["nodes", "relationships"],
    }


class LocalLLM(LLMInterface):
    """給 neo4j-graphrag 用的本地 LLM（SimpleKGPipeline 抽實體、GraphRAG 的 --simple 問答）。

    fmt 有值時宣告支援結構化輸出：抽取器會改走 response_format 路徑，這裡一律用 fmt 限制輸出格式。
    fallback：呼叫失敗（逾時、Ollama 錯誤）時改回傳的內容。函式庫不會攔截 LLM 拋出的錯誤，
    建圖譜時一個子塊失敗就會讓整個 pipeline 中斷，所以抽實體時改成回傳空圖（同 llm.py 的 refusal_text）。
    """

    def __init__(self, model: str, fmt: dict | str | None = None, fallback: str | None = None):
        # 不呼叫 LLMInterface.__init__：它每次建立都會印「LLMInterface 已棄用」的警告
        self.model_name = model
        self.model_params = {}
        self._rate_limit_handler = DEFAULT_RATE_LIMIT_HANDLER
        self.fmt = fmt
        self.fallback = fallback
        self.supports_structured_output = fmt is not None

    def invoke(self, input, message_history=None, system_instruction=None, response_format=None, **kwargs) -> LLMResponse:
        if isinstance(input, list):  # 結構化輸出路徑傳進來的是 LLMMessage 清單
            messages = [dict(m) for m in input]
        else:
            if isinstance(message_history, MessageHistory):
                message_history = message_history.messages
            messages = [{"role": "system", "content": system_instruction}] if system_instruction else []
            messages += [dict(m) for m in message_history or []]
            messages.append({"role": "user", "content": input})
        try:
            return LLMResponse(content=chat(self.model_name, messages, fmt=self.fmt)["content"])
        except LLMGenerationError as e:
            if self.fallback is None:
                raise
            log.warning("本地模型呼叫失敗，改回傳替代內容：%s", e)
            return LLMResponse(content=self.fallback)

    async def ainvoke(self, input, message_history=None, system_instruction=None, response_format=None, **kwargs) -> LLMResponse:
        return await asyncio.to_thread(self.invoke, input, message_history, system_instruction, response_format)


def make_local_extract_llm() -> LocalLLM:
    """建圖譜用的本地 LLM。"""
    # 失敗的子塊回傳空圖，略過而不中斷整個 pipeline
    return LocalLLM(EXTRACT_MODEL, fmt=extraction_format(), fallback=EMPTY_GRAPH)


def make_local_qa_llm() -> LocalLLM:
    """--simple 問答用的本地 LLM。"""
    return LocalLLM(QA_MODEL)
