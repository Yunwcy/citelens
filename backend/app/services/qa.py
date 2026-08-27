"""問答服務：檢索 → 組裝脈絡 → 生成 → 附上引用。

這一層是「模型只做 text-to-text」這條限制的實際落點：
送出去的是一段由本地流程組好的純文字，收回來的也是純文字。
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import asdict, dataclass, field
from typing import AsyncIterator

from app.config import settings
from app.llm import client, prompts
from app.models import Chunk
from app.observability import metrics
from app.retrieval.budget import PackedContext, pack
from app.retrieval.hybrid import rrf
from app.retrieval.index import DocumentIndex
from app.router import query_router
from app.services.limits import QUERY as QUERY_SEM
from app.summarization import hierarchical
from app.util import tokens

_CITE = re.compile(r"\[(\d+)\]")

# 依提示詞要求，文件未涵蓋該問題時模型應如實說明。
# 這類回答本來就沒有可標註的來源，計為「未標註引用」等於把正確行為算成失敗。
_DECLINED = re.compile(
    r"未提及|沒有提及|未提到|沒有提到|未說明|沒有說明|未包含|沒有包含|"
    r"未提供|沒有提供|無法回答|沒有相關(資訊|內容)|文件中(找不到|沒有)|"
    r"does not (mention|contain|provide|include)|not mentioned|"
    r"no information|cannot answer|is not (mentioned|provided|discussed)",
    re.I,
)


_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")


def language_of(text: str) -> str:
    """判斷提問語言。CJK 字元佔比超過一成即視為中文。"""
    stripped = re.sub(r"\s", "", text)
    if not stripped:
        return "zh"
    return "zh" if len(_CJK.findall(stripped)) / len(stripped) > 0.1 else "en"


def declined(text: str) -> bool:
    """判斷回答是否為「文件未涵蓋」。

    只在答案偏短時才判定：長篇回答即使出現「文件未提及某細節」的字樣，
    整體仍是有實質內容的作答。
    """
    return bool(_DECLINED.search(text)) and len(text) < 400


@dataclass(slots=True)
class Source:
    n: int
    page: int
    section: str
    kind: str
    chunk_id: str
    score: float
    text: str
    cited: bool = True


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
    hits = await retrieve(index, question, r, top_k)
    retrieval_ms = (time.perf_counter() - t) * 1000

    chunks = [index.chunk(h.index) for h in hits]
    if r.name == "table_lookup" and r.table_id:
        chunks = _table_first(index, r.table_id, chunks)
    if r.name == "comparison" and r.entities:
        chunks = _drop_unrelated_table_rows(chunks, r.entities)
    scores = {index.chunk(h.index).chunk_id: h.score for h in hits}
    ctx = pack(chunks, all_chunks=index.chunks)

    route = r.name
    system = prompts.COMPARISON_SYSTEM if route == "comparison" else prompts.ANSWER_SYSTEM
    system += prompts.LANGUAGE_DIRECTIVE[language_of(question)]
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
        cited=bool(sources and sources[0].cited),
        declined=declined(result.text),
        **debug,
    )
    return Answer(text=result.text, sources=sources, debug=debug)


async def retrieve(index: DocumentIndex, question: str, r, top_k: int | None) -> list:
    """檢索一律移出事件迴圈：向量化是同步的 CPU 工作，
    直接在 async handler 內執行會讓整個服務停住。"""
    async with QUERY_SEM:
        if r.name == "comparison":
            # 比較類問題放寬名額：需要涵蓋雙方的架構、效能與成本三個面向，
            # 而實測脈絡僅用掉約四成預算，額度充足。
            return await asyncio.to_thread(
                _multi_search, index, query_router.subqueries(question, r.entities),
                (top_k or settings.top_k) + 6,
            )
        return await asyncio.to_thread(index.search, question, top_k)


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


def _drop_unrelated_table_rows(chunks: list[Chunk], entities: list[str]) -> list[Chunk]:
    """比較類問題：移除未提及任一被比較對象的表格列。

    論文的表格常把同一組指標對多個基準方法各列一次。以 LightRAG 為例，
    Table 1 的第一個區塊比的是 NaiveRAG，最後一個區塊才是 GraphRAG，
    而兩者的列標題都只是「Empowerment」—— 差別只在欄名。

    實測模型會把 NaiveRAG 區塊的數字當成 GraphRAG 的來回答。
    資料本身沒有錯、引用也指得到，但歸屬錯了 ——
    這種錯誤看起來完全合理，因此特別危險。

    判準是「同時提到兩個對象」而非「提到任一個」：比較 A 與 B 時，
    只提到 A 的列無法說明兩者的差異。以此例而言，NaiveRAG 區塊的列
    雖然含有 LightRAG，卻沒有 GraphRAG，因此不該用來回答這個問題。

    整表片段不移除：它保有完整的欄列對應，模型可據以核對。
    """
    names = [e.lower() for e in entities if len(e) > 2]
    if len(names) < 2:
        return chunks
    return [
        c for c in chunks
        if c.kind != "table_row" or all(n in c.text.lower() for n in names)
    ]


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
    """摘要走快取，不走檢索。快取依語言分別存放。"""
    lang = language_of(question)
    data = hierarchical.load(index.doc_id, lang)
    cached = data is not None
    if data is None:
        data = await hierarchical.build(index.doc_id, index.sections, lang)

    # 摘要沒有可指的片段，但仍應說明涵蓋範圍 ——
    # 「答案附出處」對摘要而言就是「這份摘要讀過哪些章節」。
    sources = [
        Source(n=i + 1, page=s.get("page", 0), section=s["section"], kind="summary",
               chunk_id=f"sum-{i:02d}", score=0.0, text=s["summary"][:600])
        for i, s in enumerate(data.get("section_summaries", []))
    ]
    debug = {
        "route": "summary",
        "route_reason": r.reason,
        "cached": cached,
        "lang": lang,
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
                   n_sources=len(sources), **debug)
    return Answer(text=data["summary"], sources=sources, debug=debug)


def _cite_label(c: Chunk) -> str:
    label = f"第 {c.page} 頁"
    if c.section_title:
        label += f" · {c.section_title}"
    if c.kind != "text" and c.meta.get("table_id"):
        label += f" · {c.meta['table_id']}"
    return label


def _used_sources(text: str, ctx: PackedContext, scores: dict[str, float]) -> list[Source]:
    """回傳答案實際引用到的來源。

    模型未標註任何引用時，回傳全部片段但標記為未引用 ——
    介面必須據此改變面板標題，否則使用者會以為那些片段支撐了答案，
    但答案裡根本沒有對應的標記可循。
    """
    used = {int(n) for n in _CITE.findall(text)}
    cited = bool(used)
    out = []
    for n, chunk, shown in ctx.blocks:
        if used and n not in used:
            continue
        out.append(
            Source(
                n=n, page=chunk.page, section=chunk.section_title, kind=chunk.kind,
                chunk_id=chunk.chunk_id, score=round(scores.get(chunk.chunk_id, 0.0), 4),
                text=" ".join(shown.split())[:600], cited=cited,
            )
        )
    return out


async def answer_stream(
    index: DocumentIndex,
    question: str,
    top_k: int | None = None,
) -> AsyncIterator[dict]:
    """串流版本，供 SSE 使用。

    先送出階段訊息再送內容：使用者等待的七秒幾乎全在模型生成，
    期間若畫面完全空白，會被誤認為系統當掉。
    """
    started = time.perf_counter()
    r = query_router.route(question, index.tables)
    yield {"type": "route", "route": r.name, "reason": r.reason,
           "entities": r.entities, "table_id": r.table_id}

    if r.name == "summary":
        res = await _summary(index, question, r, started)
        yield {"type": "token", "text": res.text}
        yield {"type": "done", "sources": [asdict(s) for s in res.sources], "debug": res.debug}
        return

    yield {"type": "stage", "stage": "retrieving"}
    t = time.perf_counter()
    hits = await retrieve(index, question, r, top_k)
    retrieval_ms = (time.perf_counter() - t) * 1000

    chunks = [index.chunk(h.index) for h in hits]
    if r.name == "table_lookup" and r.table_id:
        chunks = _table_first(index, r.table_id, chunks)
    if r.name == "comparison" and r.entities:
        chunks = _drop_unrelated_table_rows(chunks, r.entities)
    scores = {index.chunk(h.index).chunk_id: h.score for h in hits}
    ctx = pack(chunks, all_chunks=index.chunks)
    yield {"type": "stage", "stage": "packing",
           "packed": len(ctx.blocks), "tokens": ctx.used_tokens, "budget": ctx.budget}

    system = prompts.COMPARISON_SYSTEM if r.name == "comparison" else prompts.ANSWER_SYSTEM
    system += prompts.LANGUAGE_DIRECTIVE[language_of(question)]
    prompt = prompts.ANSWER_USER.format(context=ctx.render(_cite_label), question=question)

    yield {"type": "stage", "stage": "generating"}
    llm_started = time.perf_counter()
    parts: list[str] = []
    async for piece in client.generate_stream(prompt, system=system):
        parts.append(piece)
        yield {"type": "token", "text": piece}

    text = "".join(parts)
    llm_ms = int((time.perf_counter() - llm_started) * 1000)
    sources = _used_sources(text, ctx, scores)

    debug = {
        "route": r.name, "route_reason": r.reason, "entities": r.entities,
        "table_id": r.table_id, "retrieved": len(hits), "packed": len(ctx.blocks),
        "context_tokens": ctx.used_tokens, "context_budget": ctx.budget,
        "dropped": ctx.dropped, "truncated": ctx.truncated,
        # 串流模式取不到 usage，以 tiktoken 估算，欄位語意與非串流一致
        "prompt_tokens": tokens.count(prompt) + tokens.count(system),
        "completion_tokens": tokens.count(text),
        "retrieval_ms": round(retrieval_ms, 1), "llm_ms": llm_ms,
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    debug["cost_usd"] = round(
        (debug["prompt_tokens"] * settings.price_input_per_1m
         + debug["completion_tokens"] * settings.price_output_per_1m) / 1_000_000, 6
    )
    metrics.record("query", doc_id=index.doc_id, question=question,
                   model=settings.llm_model, embedding_backend=index.backend,
                   streamed=True, n_sources=len(sources),
                   cited=bool(sources and sources[0].cited),
                   declined=declined(text), **debug)

    yield {"type": "done",
           "sources": [asdict(s) for s in sources],
           "debug": debug}
