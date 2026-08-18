"""LLM 调用封装：openai SDK 指向可配的国产模型（DeepSeek 等，OpenAI 兼容协议）。

同步客户端（路由均为同步 def，由 FastAPI 线程池执行）。
"""
import logging

from openai import OpenAI

from app.config import settings

logger = logging.getLogger("uvicorn.error")

_client: OpenAI | None = None


def get_client() -> OpenAI:
    """惰性创建 OpenAI 客户端（单例）。超时防挂起：流式读超时 300s（块间隔），见各调用。"""
    global _client
    if _client is None:
        if not settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY 未配置，请在 backend/.env 中填写")
        _client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=300.0,
        )
    return _client


def stream_chat(messages: list[dict], *, temperature: float = 0.7, max_tokens: int = 2048):
    """流式对话：逐段产出文本增量（生成器）。读超时 300s：模型挂起时不会无限占用线程。"""
    stream = get_client().chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        stream=True,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=300.0,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


def chat(messages: list[dict], *, temperature: float = 0.3, max_tokens: int = 512) -> str:
    """非流式对话：返回完整文本（推荐标签等轻量任务，短超时）。"""
    resp = get_client().chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        stream=False,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=60.0,
    )
    return (resp.choices[0].message.content or "") if resp.choices else ""
