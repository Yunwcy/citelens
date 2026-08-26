"""全專案唯一允許呼叫 LLM 的位置。

作業限制：LLM 只做 text-to-text。因此這個模組刻意只暴露「字串進、字串出」
的介面，不接受檔案、圖片或任何 multimodal 輸入。要驗證這條限制，
只需確認整個 repo 中 `AsyncOpenAI` 僅出現在本檔案。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator

from openai import AsyncOpenAI, APIError, RateLimitError

from app.config import settings

log = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(settings.llm_concurrency)
_client: AsyncOpenAI | None = None

MAX_RETRIES = 4


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY 未設定，請複製 .env.example 為 .env 後填入")
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


@dataclass(slots=True)
class LLMResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int

    @property
    def cost_usd(self) -> float:
        return (
            self.prompt_tokens * settings.price_input_per_1m
            + self.completion_tokens * settings.price_output_per_1m
        ) / 1_000_000


async def generate(
    prompt: str,
    system: str | None = None,
    max_tokens: int | None = None,
) -> LLMResult:
    """送出純文字 prompt，取回純文字回應。

    回傳值附帶用量資訊供 metrics 使用；模型本身收到的仍只有文字。
    """
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    started = time.perf_counter()

    async with _semaphore:
        for attempt in range(MAX_RETRIES):
            try:
                rsp = await _get_client().chat.completions.create(
                    model=settings.llm_model,
                    temperature=settings.llm_temperature,
                    max_tokens=max_tokens or settings.answer_reserved,
                    messages=messages,
                )
                break
            except (RateLimitError, APIError) as exc:
                if attempt == MAX_RETRIES - 1:
                    log.error("LLM 呼叫失敗，已重試 %d 次: %s", MAX_RETRIES, exc)
                    raise
                backoff = 2**attempt
                log.warning("LLM 呼叫失敗（第 %d 次），%d 秒後重試: %s", attempt + 1, backoff, exc)
                await asyncio.sleep(backoff)

    usage = rsp.usage
    return LLMResult(
        text=rsp.choices[0].message.content or "",
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


async def generate_stream(
    prompt: str,
    system: str | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    """串流版本，供 SSE 使用。逐段吐出文字片段。"""
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    async with _semaphore:
        stream = await _get_client().chat.completions.create(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            max_tokens=max_tokens or settings.answer_reserved,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
