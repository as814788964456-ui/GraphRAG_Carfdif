import asyncio
from functools import lru_cache

import anthropic
from neo4j_graphrag.exceptions import LLMGenerationError
from neo4j_graphrag.llm import AnthropicLLM, LLMInterface

from . import config


@lru_cache(maxsize=1)
def claude_client() -> anthropic.Anthropic:
    # 從環境解析憑證：ANTHROPIC_API_KEY，或 `ant auth login` 的 profile
    # 延遲建立，讓全本地（Ollama）模式不需要 API key
    return anthropic.Anthropic(max_retries=4)

# 安全分類器拒答時，由 API 端自動改用建議的備援模型重跑（qa.py 的 Smart Search 使用）
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class ClaudeLLM(AnthropicLLM):
    """neo4j-graphrag 的 AnthropicLLM，修正回應解析。

    原版只讀 response.content[0]，但 Claude Opus 5.5 一定會思考，
    第一個區塊常是 thinking block，會被誤判為錯誤。這裡改成串接所有 text 區塊。

    refusal_text：Claude 拒答時回傳的內容。函式庫不會攔截 LLM 拋出的錯誤，
    建圖譜時一個片段拒答就會讓整個 pipeline 中斷，所以改成回傳替代文字。
    """

    def __init__(self, *args, refusal_text: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.refusal_text = refusal_text

    def _extract_text(self, response) -> str:
        if response.stop_reason == "refusal":
            if self.refusal_text is not None:
                return self.refusal_text
            raise LLMGenerationError("Claude declined this request (stop_reason=refusal).")
        text = "".join(b.text for b in response.content if b.type == "text")
        if not text:
            raise LLMGenerationError(f"No text in response (stop_reason={response.stop_reason}).")
        return text


def make_llm(model: str, effort: str, refusal_text: str | None = None, max_tokens: int = 16000) -> ClaudeLLM:
    return ClaudeLLM(
        model_name=model,
        model_params={"max_tokens": max_tokens, "output_config": {"effort": effort}},
        refusal_text=refusal_text,
        max_retries=4,
    )


def _ollama_llm(json_mode: bool) -> LLMInterface:
    from neo4j_graphrag.llm import OllamaLLM

    class FixedOllamaLLM(OllamaLLM):
        """neo4j-graphrag 1.22 的 OllamaLLM 非同步路徑會把參數多包一層 options，
        導致 format / num_ctx 被 Ollama 忽略（長 prompt 會被截斷）。
        同步路徑正確，所以非同步呼叫改成在執行緒中跑同步版本。"""

        async def ainvoke(self, input, message_history=None, system_instruction=None, response_format=None, **kwargs):
            return await asyncio.to_thread(
                self.invoke, input, message_history, system_instruction, response_format, **kwargs
            )

    params: dict = {"options": {"temperature": 0, "num_ctx": config.OLLAMA_NUM_CTX}}
    if json_mode:
        params["format"] = "json"  # 限制只輸出 JSON，小模型抽取時較不會格式錯誤
    return FixedOllamaLLM(model_name=config.OLLAMA_MODEL, model_params=params, host=config.OLLAMA_HOST)


def make_extract_llm() -> LLMInterface:
    """建圖譜用的 LLM。"""
    if config.EXTRACT_PROVIDER == "ollama":
        return _ollama_llm(json_mode=True)
    # 拒答的片段回傳空圖，略過而不中斷整個 pipeline
    return make_llm(config.EXTRACT_MODEL, config.EXTRACT_EFFORT, refusal_text='{"nodes": [], "relationships": []}')


def make_qa_llm() -> LLMInterface:
    """--simple 問答用的 LLM。"""
    if config.QA_PROVIDER == "ollama":
        return _ollama_llm(json_mode=False)
    return make_llm(config.SIMPLE_QA_MODEL, config.SIMPLE_QA_EFFORT, refusal_text="（模型拒絕回答此問題）")
