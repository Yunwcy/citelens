"""Block C 驗收：對一份 PDF 建索引並檢索，比較三種模式的命中差異。

用法：
    python scripts/search.py <pdf> "查詢" [--mode hybrid|vector] [--strategy section|fixed]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.retrieval.index import DocumentIndex  # noqa: E402
from app.services.ingest import ingest  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("query")
    ap.add_argument("--mode", default=None, choices=["hybrid", "vector"])
    ap.add_argument("--strategy", default=None, choices=["section", "fixed"])
    ap.add_argument("--k", type=int, default=6)
    args = ap.parse_args()

    res = ingest(args.pdf, args.strategy)
    idx = DocumentIndex.build(Path(args.pdf).stem, res)

    print(f"片段 {idx.meta['n_chunks']} · 向量化 {idx.meta['index_seconds']}s "
          f"（{idx.meta['chunks_per_second']} 片段/秒）· {idx.meta['embedding_model']}")
    print(f"查詢：{args.query}\n")

    t = time.perf_counter()
    hits = idx.search(args.query, top_k=args.k, mode=args.mode)
    ms = (time.perf_counter() - t) * 1000

    for rank, h in enumerate(hits, 1):
        c = idx.chunk(h.index)
        ranks = " ".join(f"{k}#{v}" for k, v in sorted(h.ranks.items()))
        print(f"{rank}. [{c.kind:10s}] {idx.cite(h.index):<44} RRF {h.score:.4f}  {ranks}")
        print(f"   {' '.join(c.text.split())[:150]}")
    print(f"\n檢索耗時 {ms:.1f} ms")


if __name__ == "__main__":
    main()
