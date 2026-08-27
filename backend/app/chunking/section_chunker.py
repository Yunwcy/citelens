"""切塊：結構優先，遞迴為輔。

策略由 config.CHUNK_STRATEGY 控制：
- section：先依章節切，章節內超過上限才遞迴切分（正式版）
- fixed  ：忽略章節，純固定長度（evaluation 的對照組）

兩者使用相同的目標 token 數，確保對照實驗只有「切法」一個變因。
"""
from __future__ import annotations

import re

from app.config import settings
from typing import Callable

from app.models import Block, Chunk, Section
from app.util import tokens

# 遞迴切分的分隔符，由粗到細
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ". ", "; ", "，", " "]


def chunk_sections(
    sections: list[Section],
    strategy: str | None = None,
    exclude: Callable[[Block], bool] | None = None,
) -> list[Chunk]:
    """exclude 用來排除已被表格抽取器接手的區塊，避免同一份內容重複入索引。"""
    strategy = strategy or settings.chunk_strategy
    keep = (lambda b: not exclude(b)) if exclude else (lambda b: True)
    if strategy == "fixed":
        return _fixed(sections, keep)
    return _section_aware(sections, keep)


def _section_aware(sections: list[Section], keep) -> list[Chunk]:
    out: list[Chunk] = []
    for sec in sections:
        blocks = [(b.page, b.text) for b in sec.blocks if keep(b)]
        for page, piece in _split_blocks(blocks):
            out.append(
                Chunk(
                    chunk_id=f"{sec.id}-{len([c for c in out if c.section_id == sec.id]):02d}",
                    text=piece,
                    page=page,
                    section_id=sec.id,
                    section_title=sec.title,
                    n_tokens=tokens.count(piece),
                )
            )
    return out


def _fixed(sections: list[Section], keep) -> list[Chunk]:
    """對照組：文獻上的 fixed-size chunking with overlap。

    把全文攤平成一條 token 串，以固定視窗滑動切分，**不看任何結構、
    也不在段落邊界停下**。視窗之間保留重疊，這是這個作法的標準配置 ——
    邊界會切斷句子，重疊是用來降低「答案剛好落在切點上」的機率。

    對照組必須是文獻上真正的那個作法，否則消融實驗只是在比較兩個自訂方案。
    """
    flat = [(b.page, b.text) for sec in sections for b in sec.blocks if keep(b)]
    if not flat:
        return []

    limit = settings.chunk_target_tokens
    stride = max(1, limit - settings.chunk_overlap_tokens)

    # 攤平成單一 token 串，同時記住每個 token 屬於哪一頁，讓引用仍能指到頁碼
    enc = tokens.encoding()
    ids: list[int] = []
    pages: list[int] = []
    for page, text in flat:
        piece = enc.encode(text + "\n")
        ids.extend(piece)
        pages.extend([page] * len(piece))

    out: list[Chunk] = []
    for i, start in enumerate(range(0, len(ids), stride)):
        window = ids[start:start + limit]
        if not window:
            break
        out.append(
            Chunk(
                chunk_id=f"f{i:04d}",
                text=enc.decode(window),
                page=pages[start],
                section_id="",
                section_title="",
                n_tokens=len(window),
            )
        )
        if start + limit >= len(ids):
            break
    return out


def _split_blocks(blocks: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """把 (頁碼, 文字) 序列組裝成接近目標長度的片段。

    以區塊為單位累積，超過上限才切；單一區塊本身過長時才遞迴切分。
    回傳片段的頁碼取其第一個區塊所在頁 —— 引用要指向段落開始的地方。
    """
    limit = settings.chunk_target_tokens
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    buf_tokens = 0
    buf_page = blocks[0][0] if blocks else 1

    def flush() -> None:
        nonlocal buf, buf_tokens
        if buf:
            out.append((buf_page, "\n".join(buf)))
            buf, buf_tokens = [], 0

    for page, text in blocks:
        n = tokens.count(text)

        if n > limit:                      # 單一區塊就超標，先清空再遞迴切
            flush()
            for piece in _recursive_split(text, limit):
                out.append((page, piece))
            buf_page = page
            continue

        if buf_tokens + n > limit:
            flush()
            buf_page = page
        if not buf:
            buf_page = page
        buf.append(text)
        buf_tokens += n

    flush()
    return out


def _recursive_split(text: str, limit: int) -> list[str]:
    """依分隔符由粗到細切，盡量在自然邊界斷開。"""
    if tokens.count(text) <= limit:
        return [text]

    for sep in _SEPARATORS:
        if sep not in text:
            continue
        parts = _merge(text.split(sep), sep, limit)
        if len(parts) > 1:
            return [p for part in parts for p in _recursive_split(part, limit)]

    # 沒有任何分隔符可用，只能硬切
    return _hard_split(text, limit)


def _merge(parts: list[str], sep: str, limit: int) -> list[str]:
    out: list[str] = []
    buf = ""
    for p in parts:
        candidate = f"{buf}{sep}{p}" if buf else p
        if tokens.count(candidate) > limit and buf:
            out.append(buf)
            buf = p
        else:
            buf = candidate
    if buf:
        out.append(buf)
    return out


def _hard_split(text: str, limit: int) -> list[str]:
    out, rest = [], text
    while rest:
        head = tokens.truncate(rest, limit)
        if not head:
            break
        out.append(head)
        rest = rest[len(head):]
    return out


def normalize(text: str) -> str:
    """壓縮多餘空白，但保留段落分隔。"""
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
