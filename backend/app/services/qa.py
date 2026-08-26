"""問答服務：檢索 → 組裝脈絡 → 生成 → 附上引用。

這一層是「模型只做 text-to-text」這條限制的實際落點：
送出去的是一段由本地流程組好的純文字，收回來的也是純文字。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from app.config import settings
from app.llm import client, prompts
from app.models import Chunk
from app.observability import metrics
from app.retrieval.budget import PackedContext, pack
from app.retrieval.index import DocumentIndex

_CITE = re.compile(r"\[(\d+)\]")


@dataclass(slots=True)
class Source:
    n: int
    page: int
    section: str
    kind: str
    chunk_id: str
    score: float
    text: str


@dataclass(slots=True)
class Answer:
    text: str
    sources: list[Source] = field(default_factory=list)
    debug: dict = field(default_factory=dict)


async def answer(
    index: DocumentIndex,
    question: str,
    top_k: int | None = None,
    route: str = "qa",
) -> Answer:
    started = time.perf_counter()

    t = time.perf_counter()
    hits = index.search(question, top_k=top_k)
    retrieval_ms = (time.perf_counter() - t) * 1000

    chunks = [index.chunk(h.index) for h in hits]
    scores = {index.chunk(h.index).chunk_id: h.score for h in hits}
    ctx = pack(chunks, all_chunks=index.chunks)

    system = prompts.COMPARISON_SYSTEM if route == "comparison" else prompts.ANSWER_SYSTEM
    prompt = prompts.ANSWER_USER.format(
        context=ctx.render(lambda c: _cite_label(c)), question=question
    )

    result = await client.generate(prompt, system=system)

    sources = _used_sources(result.text, ctx, scores)
    debug = {
        "route": route,
        "retrieved": len(hits),
        "packed": len(ctx.blocks),
        "context_tokens": ctx.used_tokens,
        "context_budget": ctx.budget,
        "dropped": ctx.dropped,
        "truncated": ctx.truncated,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "cost_usd": round(result.cost_usd, 6),
        "retrieval_ms": round(retrieval_ms, 1),
        "llm_ms": result.latency_ms,
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    metrics.record(
        "query",
        doc_id=index.doc_id,
        question=question,
        model=settings.llm_model,
        embedding_backend=index.backend,
        n_sources=len(sources),
        **debug,
    )
    return Answer(text=result.text, sources=sources, debug=debug)


def _cite_label(c: Chunk) -> str:
    label = f"第 {c.page} 頁"
    if c.section_title:
        label += f" · {c.section_title}"
    if c.kind != "text" and c.meta.get("table_id"):
        label += f" · {c.meta['table_id']}"
    return label


def _used_sources(text: str, ctx: PackedContext, scores: dict[str, float]) -> list[Source]:
    """只回傳答案實際引用到的來源。

    把沒被引用的片段也列出來，會讓右側面板看起來像是「系統找到很多東西」，
    但使用者無法分辨哪些真的支撐了答案。引用面板的價值在於可查核，不在於數量。
    """
    used = {int(n) for n in _CITE.findall(text)}
    out = []
    for n, chunk, shown in ctx.blocks:
        if used and n not in used:
            continue
        out.append(
            Source(
                n=n, page=chunk.page, section=chunk.section_title, kind=chunk.kind,
                chunk_id=chunk.chunk_id, score=round(scores.get(chunk.chunk_id, 0.0), 4),
                text=" ".join(shown.split())[:600],
            )
        )
    return out
