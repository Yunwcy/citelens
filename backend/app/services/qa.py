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
from app.retrieval.hybrid import rrf
from app.retrieval.index import DocumentIndex
from app.router import query_router
from app.summarization import hierarchical

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
    force_route: str | None = None,
) -> Answer:
    """依查詢類型走不同策略。三個指定問題正好對應三條不同的路徑。"""
    started = time.perf_counter()
    r = query_router.route(question, index.tables)
    if force_route:
        r = query_router.Route(force_route, "由呼叫端指定")

    if r.name == "summary":
        return await _summary(index, question, r, started)

    t = time.perf_counter()
    if r.name == "comparison":
        # 比較類問題放寬名額：需要涵蓋雙方的架構、效能與成本三個面向，
        # 而實測脈絡僅用掉約四成預算，額度充足。
        hits = _multi_search(
            index, query_router.subqueries(question, r.entities),
            (top_k or settings.top_k) + 6,
        )
    else:
        hits = index.search(question, top_k=top_k)
    retrieval_ms = (time.perf_counter() - t) * 1000

    chunks = [index.chunk(h.index) for h in hits]
    if r.name == "table_lookup" and r.table_id:
        chunks = _table_first(index, r.table_id, chunks)
    scores = {index.chunk(h.index).chunk_id: h.score for h in hits}
    ctx = pack(chunks, all_chunks=index.chunks)

    route = r.name
    system = prompts.COMPARISON_SYSTEM if route == "comparison" else prompts.ANSWER_SYSTEM
    prompt = prompts.ANSWER_USER.format(
        context=ctx.render(lambda c: _cite_label(c)), question=question
    )

    result = await client.generate(prompt, system=system)

    sources = _used_sources(result.text, ctx, scores)
    debug = {
        "route": route,
        "route_reason": r.reason,
        "entities": r.entities,
        "table_id": r.table_id,
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


def _multi_search(index: DocumentIndex, queries: list[str], top_k: int | None) -> list:
    """比較類問題：多路檢索，保障每個子查詢的名額後再依 RRF 排序。

    單一查詢容易只命中被比較的其中一方 —— 「compare A with B」的向量會偏向
    A 出現較多的段落，B 的架構描述可能整段缺席。

    但只做 RRF 仍然不夠：實測時五個子查詢的結果被最強的那組通吃，
    九個片段全部來自同一節，架構與成本兩個面向完全沒有進入脈絡。
    因此改為輪流各取一名（round-robin）填滿名額，再以 RRF 決定呈現順序。
    覆蓋面是比較類問題的正確性條件，不只是多樣性的偏好。
    """
    k = top_k or settings.top_k
    per_query = [index.search(q, top_k=k) for q in queries]
    rankings = {f"q{i}": [(h.index, h.score) for h in hits] for i, hits in enumerate(per_query)}

    picked: list[int] = []
    seen: set[int] = set()
    for depth in range(k):
        for hits in per_query:
            if len(picked) >= k:
                break
            if depth < len(hits) and hits[depth].index not in seen:
                seen.add(hits[depth].index)
                picked.append(hits[depth].index)
        if len(picked) >= k:
            break

    fused = {h.index: h for h in rrf(rankings)}
    return sorted(
        (fused[i] for i in picked if i in fused),
        key=lambda h: h.score, reverse=True,
    )


def _table_first(index: DocumentIndex, table_id: str, chunks: list[Chunk]) -> list[Chunk]:
    """查表路由：把命中的整張表放在最前面，確保模型一定看得到完整欄列對應。"""
    full = next(
        (c for c in index.chunks if c.kind == "table_full" and c.meta.get("table_id") == table_id),
        None,
    )
    if full is None:
        return chunks
    return [full] + [c for c in chunks if c.chunk_id != full.chunk_id]


async def _summary(index: DocumentIndex, question: str, r, started: float) -> Answer:
    """摘要走快取，不走檢索。"""
    data = hierarchical.load(index.doc_id)
    cached = data is not None
    if data is None:
        data = await hierarchical.build(index.doc_id, index.sections)

    debug = {
        "route": "summary",
        "route_reason": r.reason,
        "cached": cached,
        "sections_summarized": len(data.get("section_summaries", [])),
        "n_llm_calls": 0 if cached else data.get("n_llm_calls", 0),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": 0.0,
        "retrieval_ms": 0.0,
        "llm_ms": 0,
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    metrics.record("query", doc_id=index.doc_id, question=question,
                   model=settings.llm_model, embedding_backend=index.backend,
                   n_sources=0, **debug)
    return Answer(text=data["summary"], sources=[], debug=debug)


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
