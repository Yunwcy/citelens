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
from collections.abc import Callable
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


# 同一份文件同時只允許一次建立：摘要改到背景執行後，
# 使用者可能在建立中途就要求摘要 —— 沒有鎖的話會啟動第二次，
# 兩邊都寫 summary.json，而且白花一倍的模型呼叫。
_build_locks: dict[str, asyncio.Lock] = {}


def _lock_for(doc_id: str) -> asyncio.Lock:
    return _build_locks.setdefault(doc_id, asyncio.Lock())


async def build(
    doc_id: str,
    sections: list[Section],
    lang: str = "zh",
    on_progress: Callable[[str, int, int], None] | None = None,
) -> dict:
    """產生並快取摘要。

    章節摘要與語言無關，只做一次；最終整合依語言各自產生並快取。
    完整重做一次要八次模型呼叫、約十四秒，只重做整合則是一次、約三秒。

    on_progress 讓呼叫端把 map-reduce 的進度轉成畫面上的階段訊息 ——
    十四秒的空白畫面會被當成當機，而這正好是最值得展示的一段。
    """
    note = on_progress or (lambda *_: None)
    async with _lock_for(doc_id):
        return await _build(doc_id, sections, lang, note)


async def _build(doc_id: str, sections: list[Section], lang: str, note) -> dict:
    path = settings.storage_dir / doc_id / "summary.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        # 舊格式只有單一 summary 欄位，視為既有語言的整合結果
        if "summaries" not in data:
            data["summaries"] = {data.get("lang", "zh"): data.get("summary", "")}
        if lang in data.get("summaries", {}):
            return {**data, "summary": data["summaries"][lang], "lang": lang}
        # 章節摘要已存在，只需補做該語言的整合。
        # n_llm_calls 要反映「這次做了幾次」，不是原始建立時的次數 ——
        # 否則補一個語言只花一次呼叫，指標卻記成十幾次。
        note("merge", 0, 1)
        data["summaries"][lang] = await _reduce(data["section_summaries"], lang)
        note("merge", 1, 1)
        data["n_llm_calls"] = 1
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**data, "summary": data["summaries"][lang], "lang": lang}

    if not sections:
        raise ValueError(f"{doc_id} 沒有章節資料，無法建立摘要")

    started = time.perf_counter()
    groups = _group_by_top_level(sections)
    log.info("摘要 %s：%d 個一級章節", doc_id, len(groups))

    done = 0
    note("map", 0, len(groups))

    async def _tracked(g: tuple[str, str, int]) -> str:
        nonlocal done
        summary = await _map_one(g[0], g[1])
        done += 1
        note("map", done, len(groups))
        return summary

    results = await asyncio.gather(*(_tracked(g) for g in groups))
    section_summaries = [
        {"section": g[0], "page": g[2], "summary": summary}
        for g, summary in zip(groups, results)
    ]

    note("merge", 0, 1)
    final = await _reduce(section_summaries, lang)
    note("merge", 1, 1)

    elapsed = time.perf_counter() - started
    data = {
        "doc_id": doc_id,
        "section_summaries": section_summaries,
        "summaries": {lang: final},
        "summary": final,
        "lang": lang,
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


async def _reduce(section_summaries: list[dict], lang: str) -> str:
    joined = "\n\n".join(f"## {s['section']}\n{s['summary']}" for s in section_summaries)
    result = await client.generate(
        tokens.truncate(joined, _MAP_INPUT_LIMIT),
        system=prompts.SUMMARY_REDUCE_SYSTEM + prompts.LANGUAGE_DIRECTIVE[lang],
        max_tokens=1_600,
    )
    return result.text


def load(doc_id: str, lang: str = "zh") -> dict | None:
    """僅在該語言的整合已存在時回傳；否則交由 build 補做。"""
    path = settings.storage_dir / doc_id / "summary.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if lang not in data.get("summaries", {}):
        return None      # 尚未產生該語言的整合，交由 build 補做
    return {**data, "summary": data["summaries"][lang], "lang": lang}
