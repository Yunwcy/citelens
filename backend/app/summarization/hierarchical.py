"""階層式摘要（map-reduce）。

為什麼摘要不能走一般檢索：top-k 取回的是「與『摘要』這個詞最相似的片段」，
產出的是 retrieval summary 而非 document summary —— 看起來像摘要，
實際上只涵蓋文件的一小部分，而且無法得知漏了什麼。

改以章節為 map 單位（而非固定長度）：學術文獻的章節本身就是作者劃定的
語意邊界，比每 N 個 token 切一刀更貼近內容結構。

結果寫入 summary.json 快取。摘要一次要數次模型呼叫，若每次提問都重跑，
使用者要等數十秒；快取後是毫秒級。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from app.config import settings
from app.llm import client, prompts
from app.models import Section
from app.observability import metrics
from app.util import tokens

log = logging.getLogger(__name__)

_MAP_INPUT_LIMIT = 6_000       # 單一章節送進模型的上限，留給系統提示與輸出
_MIN_SECTION_TOKENS = 60       # 太短的章節併入前一節，不值得單獨呼叫
_MAX_GROUP_TOKENS = 3_500      # 超過此長度的一級章節改以其子章節為單位
_SECTION_SUMMARY_TOKENS = 700  # 每段章節摘要的長度上限


async def build(doc_id: str, sections: list[Section]) -> dict:
    """產生並快取摘要。已存在則直接讀回。"""
    path = settings.storage_dir / doc_id / "summary.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    if not sections:
        raise ValueError(f"{doc_id} 沒有章節資料，無法建立摘要")

    started = time.perf_counter()
    groups = _group_by_top_level(sections)
    log.info("摘要 %s：%d 個一級章節", doc_id, len(groups))

    results = await asyncio.gather(*(_map_one(g[0], g[1]) for g in groups))
    section_summaries = [
        {"section": g[0], "page": g[2], "summary": summary}
        for g, summary in zip(groups, results)
    ]

    joined = "\n\n".join(f"## {s['section']}\n{s['summary']}" for s in section_summaries)
    final = await client.generate(
        tokens.truncate(joined, _MAP_INPUT_LIMIT),
        system=prompts.SUMMARY_REDUCE_SYSTEM,
        max_tokens=1_600,
    )

    elapsed = time.perf_counter() - started
    data = {
        "doc_id": doc_id,
        "section_summaries": section_summaries,
        "summary": final.text,
        "n_llm_calls": len(groups) + 1,
        "build_seconds": round(elapsed, 1),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    metrics.record(
        "summary_build", doc_id=doc_id, sections=len(groups),
        n_llm_calls=len(groups) + 1, build_seconds=round(elapsed, 1),
    )
    return data


def _group_by_top_level(sections: list[Section]) -> list[tuple[str, str, int]]:
    """決定摘要的 map 單位。

    預設以一級章節為單位，但過長的一級章節改以其子章節為單位 ——
    LightRAG 論文的 Evaluation 一節涵蓋實驗設定與四個研究問題，
    壓成一段 700 字的摘要時，模型會把「610,000 tokens vs 不到 100 tokens」
    這類具體數字概括成「遠低於」，最有說服力的內容因此消失。
    """
    groups: list[tuple[str, list[tuple[str, str, int]]]] = []
    for sec in sections:
        text = sec.text.strip()
        if not text:
            continue
        item = (sec.title, f"### {sec.title}\n{text}", sec.page_start)
        if sec.level <= 1 or not groups:
            groups.append((sec.title, [item]))
        else:
            groups[-1][1].append(item)

    out: list[tuple[str, str, int]] = []
    for title, items in groups:
        body = "\n\n".join(i[1] for i in items)
        if tokens.count(body) > _MAX_GROUP_TOKENS and len(items) > 1:
            # 過長：改以子章節為單位，標題保留父章節作為前綴
            for sub_title, sub_body, page in items:
                label = sub_title if sub_title == title else f"{title} · {sub_title}"
                out.append((label, sub_body, page))
            continue
        if tokens.count(body) < _MIN_SECTION_TOKENS and out:
            out[-1] = (out[-1][0], out[-1][1] + "\n\n" + body, out[-1][2])
        else:
            out.append((title, body, items[0][2]))
    return out


async def _map_one(title: str, text: str) -> str:
    body = tokens.truncate(text, _MAP_INPUT_LIMIT)
    result = await client.generate(
        f"章節標題：{title}\n\n{body}",
        system=prompts.SUMMARY_MAP_SYSTEM,
        max_tokens=_SECTION_SUMMARY_TOKENS,
    )
    return result.text


def load(doc_id: str) -> dict | None:
    path = settings.storage_dir / doc_id / "summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
