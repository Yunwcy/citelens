"""Block B 驗收：對一份 PDF 印出文件特徵、章節樹與切塊統計。

用法：
    python scripts/inspect_doc.py <pdf 路徑> [--tree] [--strategy section|fixed]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.chunking import section_chunker, section_detector  # noqa: E402
from app.parser import profiler  # noqa: E402
from app.parser.pdf_parser import ParsedPdf  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--tree", action="store_true", help="印出完整章節樹")
    ap.add_argument("--strategy", default=None, choices=["section", "fixed"])
    args = ap.parse_args()

    with ParsedPdf(args.pdf) as pdf:
        prof = profiler.profile(pdf)
        sections, source = section_detector.detect(pdf, prof)
        prof.section_source = source
        chunks = section_chunker.chunk_sections(sections, args.strategy)

        print(f"檔案          {Path(args.pdf).name}")
        print(f"特徵          {prof.summary()}")
        print(f"章節來源      {source}（級聯第 "
              f"{ {'toc': 1, 'regex': 2, 'font': 3, 'none': 4}[source] } 級）")
        print(f"表格策略      {prof.table_strategy}")
        print(f"章節數        {len(sections)}")

        if chunks:
            ns = [c.n_tokens for c in chunks]
            over = sum(1 for n in ns if n > 512)
            print(f"片段數        {len(chunks)}")
            print(f"片段 token    平均 {sum(ns)//len(ns)} · 最大 {max(ns)} · 超過 512 的有 {over} 個")

        if args.tree:
            print("\n章節樹")
            for s in sections:
                pages = {p for p, _ in s.blocks}
                span = f"p{min(pages)}-{max(pages)}" if pages else "無內文"
                indent = "  " * (s.level - 1)
                print(f"  {indent}L{s.level} {span:>9}  {s.title[:60]}")


if __name__ == "__main__":
    main()
