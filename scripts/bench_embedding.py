"""比較向量化後端對檢索準確度與速度的影響。

輸出直接作為報告數據：模型選擇不靠臆測，靠這張表。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.retrieval.index import DocumentIndex  # noqa: E402
from app.services.ingest import ingest  # noqa: E402

PDF = Path(__file__).resolve().parents[1] / "_working/testdocs/2410.05779.pdf"

# 查詢 -> 期望命中的章節關鍵字
CASES = [
    ("summary this document", None),
    ("compare lightRAG with GraphRAG", "Comparison"),
    ("Performance of ablated versions of LightRAG", "Ablation"),
    ("消融實驗的結果如何？", "Ablation"),
    ("LightRAG 和 GraphRAG 有什麼差別", "Comparison"),
]


def rank_of(idx: DocumentIndex, query: str, keyword: str, k: int = 10) -> int | None:
    for rank, h in enumerate(idx.search(query, top_k=k), 1):
        if keyword.lower() in idx.chunk(h.index).section_title.lower():
            return rank
    return None


def main() -> None:
    res = ingest(PDF)
    print(f"文件 {PDF.name} · {len(res.chunks)} 個片段\n")

    for backend in sys.argv[1:] or ["onnx", "onnx-large"]:
        t = time.perf_counter()
        idx = DocumentIndex.build(f"bench-{backend}", res, backend=backend)
        build_s = time.perf_counter() - t

        lat = []
        for q, _ in CASES:
            s = time.perf_counter()
            idx.search(q, top_k=10)
            lat.append((time.perf_counter() - s) * 1000)

        print(f"=== {backend} · {idx.meta['embedding_model']} · {idx.meta['embedding_dim']} 維 ===")
        print(f"建索引 {build_s:.1f}s（{len(res.chunks)/build_s:.1f} 片段/秒）· "
              f"查詢中位數 {sorted(lat)[len(lat)//2]:.0f} ms")
        for q, kw in CASES:
            if kw is None:
                continue
            r = rank_of(idx, q, kw)
            mark = "✓" if r and r <= 3 else ("△" if r else "✗")
            print(f"  {mark} {kw:<12} rank {r if r else '未進前 10'}   {q}")
        print()


if __name__ == "__main__":
    main()
