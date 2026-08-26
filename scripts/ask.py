"""Block D 驗收：在終端機直接對文件提問。

用法：
    python scripts/ask.py <pdf> "問題" [--k 8] [--debug]
    python scripts/ask.py <pdf> --demo          # 依序問作業指定的三個問題
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.retrieval.index import DocumentIndex  # noqa: E402
from app.services import qa  # noqa: E402
from app.services.ingest import ingest  # noqa: E402

DEMO = [
    ("summary this document", "qa"),
    ("compare lightRAG with GraphRAG", "comparison"),
    ("Performance of ablated versions of LightRAG", "qa"),
]


async def run(idx: DocumentIndex, question: str, route: str, k: int, debug: bool) -> None:
    print(f"\n{'=' * 78}\n問：{question}\n{'-' * 78}")
    res = await qa.answer(idx, question, top_k=k, route=route)
    print(res.text)

    if res.sources:
        print("\n引用來源")
        for s in res.sources:
            loc = f"p.{s.page}" + (f" · {s.section}" if s.section else "")
            print(f"  [{s.n}] {loc}   {s.kind}   分數 {s.score:.4f}")

    d = res.debug
    print(f"\n脈絡 {d['context_tokens']}/{d['context_budget']} tokens · "
          f"片段 {d['packed']}/{d['retrieved']}"
          + (f" · 捨棄 {len(d['dropped'])}" if d["dropped"] else "")
          + (f" · 裁切 {len(d['truncated'])}" if d["truncated"] else ""))
    print(f"用量 {d['prompt_tokens']} in / {d['completion_tokens']} out · "
          f"US${d['cost_usd']:.6f} · 檢索 {d['retrieval_ms']}ms · 生成 {d['llm_ms']}ms")
    if debug:
        print(f"除錯 {d}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("question", nargs="?")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    res = ingest(args.pdf)
    idx = DocumentIndex.build(Path(args.pdf).stem, res)
    print(f"索引完成：{idx.meta['n_chunks']} 片段 · {idx.meta['index_seconds']}s · "
          f"{idx.meta['embedding_model']}")

    if args.demo:
        for q, route in DEMO:
            await run(idx, q, route, args.k, args.debug)
    else:
        await run(idx, args.question, "qa", args.k, args.debug)


if __name__ == "__main__":
    asyncio.run(main())
