"""切塊：結構優先，遞迴為輔。

策略由 config.CHUNK_STRATEGY 控制：
- section：先依章節切，章節內超過上限才遞迴切分（正式版）
- fixed  ：忽略章節，純固定長度（evaluation 的對照組）

兩者使用相同的目標 token 數，確保對照實驗只有「切法」一個變因。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models import Chunk, Section
from app.util import tokens

# 遞迴切分的分隔符，由粗到細
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ". ", "; ", "，", " "]


def chunk_sections(sections: list[Section], strategy: str | None = None) -> list[Chunk]:
    strategy = strategy or settings.chunk_strategy
    if strategy == "fixed":
        return _fixed(sections)
    return _section_aware(sections)


def _section_aware(sections: list[Section]) -> list[Chunk]:
    out: list[Chunk] = []
    for sec in sections:
        for page, piece in _split_blocks(sec.blocks):
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


def _fixed(sections: list[Section]) -> list[Chunk]:
    """對照組：把全文攤平成一條，純以長度切，不看任何結構。"""
    flat = [(p, t) for sec in sections for p, t in sec.blocks]
    return [
        Chunk(
            chunk_id=f"f{i:04d}",
            text=piece,
            page=page,
            section_id="",
            section_title="",
            n_tokens=tokens.count(piece),
        )
        for i, (page, piece) in enumerate(_split_blocks(flat))
    ]


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
