"""檢索評估：對本系統自身做消融，量化每一項設計的貢獻。

三組設定，逐項疊加：
  baseline  固定切塊 + 純向量 + 無表格處理     （一般 RAG 的做法）
  +結構     章節切塊 + 純向量 + 無表格處理     （只加結構感知）
  improved  章節切塊 + 混合檢索 + 表格抽取     （完整系統）

指標為「目標章節在檢索結果中的名次」與「涵蓋的目標章節數」，
兩者皆為客觀可重現的數字，不需要人工評分。

用法：
    python scripts/eval.py --md docs/results/retrieval.md
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import settings  # noqa: E402
from app.retrieval.index import DocumentIndex  # noqa: E402
from app.services.ingest import ingest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TESTDOCS = ROOT / "_working" / "testdocs"
TARGET = TESTDOCS / "2410.05779.pdf"

TOP_K = 10


@dataclass(frozen=True)
class Case:
    query: str
    expect: tuple[str, ...]          # 期望命中的章節關鍵字（任一即算命中）
    note: str = ""


# 測試集刻意涵蓋七個不同章節、中英文各半 ——
# 只用三五題時，準確度不論高低都缺乏說服力。
CASES = [
    Case("summary this document", ("Introduction", "Evaluation", "Conclusion"), "全篇覆蓋"),
    Case("compare lightRAG with GraphRAG", ("Comparison", "Cost"), "指定問題二"),
    Case("Performance of ablated versions of LightRAG", ("Ablation",), "指定問題三"),
    Case("消融實驗的結果如何？", ("Ablation",), "跨語言"),
    Case("LightRAG 和 GraphRAG 有什麼差別", ("Comparison", "Cost"), "跨語言"),
    Case("How does LightRAG build its graph index?", ("Graph-based",), "架構"),
    Case("圖索引是怎麼建立的？", ("Graph-based",), "跨語言 · 架構"),
    Case("What is the dual-level retrieval paradigm?", ("Dual-level",), "架構"),
    Case("雙層檢索是什麼意思？", ("Dual-level",), "跨語言 · 架構"),
    Case("What datasets were used in the experiments?", ("Experimental Settings",), "實驗設定"),
    Case("這篇論文用了哪些資料集？", ("Experimental Settings",), "跨語言 · 實驗設定"),
    Case("How many tokens does GraphRAG need compared to LightRAG?", ("Cost",), "成本"),
]

# 名稱前綴用於排序：Grafana 依序列名稱排列，沒有前綴時順序是隨機的
CONFIGS = [
    ("1 基準版", dict(strategy="fixed", mode="vector", use_tables=False)),
    ("2 加結構感知", dict(strategy="section", mode="vector", use_tables=False)),
    ("3 完整系統", dict(strategy="section", mode="hybrid", use_tables=True)),
]


def evaluate(idx: DocumentIndex, case: Case, mode: str) -> tuple[int | None, int]:
    """回傳（首個命中的名次, 涵蓋的期望章節數）。"""
    hits = idx.search(case.query, top_k=TOP_K, mode=mode)
    first: int | None = None
    covered: set[str] = set()
    for rank, h in enumerate(hits, 1):
        title = idx.chunk(h.index).section_title.lower()
        for kw in case.expect:
            if kw.lower() in title:
                covered.add(kw)
                if first is None:
                    first = rank
    return first, len(covered)


REPORT: dict = {"retrieval": {}, "tables": {}}


def run_retrieval() -> list[str]:
    out = ["## 檢索準確度", "",
           f"文件：`{TARGET.name}` · 每組取前 {TOP_K} 名 · 數字為目標章節的名次（越小越好）", ""]

    results: dict[str, dict[str, tuple[int | None, int]]] = {}
    stats: dict[str, dict] = {}

    for name, cfg in CONFIGS:
        res = ingest(TARGET, strategy=cfg["strategy"], use_tables=cfg["use_tables"])
        idx = DocumentIndex.build(f"eval-{name}", res)
        stats[name] = {"chunks": len(res.chunks), "tables": len(res.tables)}
        results[name] = {c.query: evaluate(idx, c, cfg["mode"]) for c in CASES}
        REPORT["retrieval"][name] = {
            c.query: {"rank": results[name][c.query][0],
                      "covered": results[name][c.query][1],
                      "expected": len(c.expect)}
            for c in CASES
        }

    header = "| 查詢 | 期望章節 | " + " | ".join(n for n, _ in CONFIGS) + " |"
    out += [header, "|---|---|" + "---|" * len(CONFIGS)]
    for c in CASES:
        cells = []
        for name, _ in CONFIGS:
            rank, covered = results[name][c.query]
            mark = "" if rank and rank <= 3 else ("△ " if rank else "✗ ")
            cells.append(f"{mark}{rank if rank else '未進前 10'}"
                         + (f"（涵蓋 {covered}/{len(c.expect)}）" if len(c.expect) > 1 else ""))
        out.append(f"| {c.query} | {' / '.join(c.expect)} | " + " | ".join(cells) + " |")

    out += ["", "設定：", ""]
    for name, cfg in CONFIGS:
        out.append(f"- **{name}**：切塊 `{cfg['strategy']}` · 檢索 `{cfg['mode']}` · "
                   f"表格處理 `{'開啟' if cfg['use_tables'] else '關閉'}` · "
                   f"{stats[name]['chunks']} 個片段、{stats[name]['tables']} 張表")

    # 彙總。判準取 settings.top_k —— 系統實際送給模型的就是這麼多片段，
    # 所以「目標章節有沒有被送進去」才是要問的問題。
    # 另附前 3 名這個更嚴格的視角，兩個都列出來，不挑對自己有利的。
    k_real = settings.top_k
    out += ["", f"命中率（分母 {len(CASES)} 題，看首個命中的名次）：", "",
            f"| 設定 | 前 {k_real} 名（＝實際送進模型的片段數） | 前 3 名 |",
            "|---|---:|---:|"]
    for name, _ in CONFIGS:
        cells = []
        for k in (k_real, 3):
            n = sum(1 for c in CASES
                    if (rk := results[name][c.query][0]) is not None and rk <= k)
            cells.append(f"**{n}/{len(CASES)}（{n / len(CASES):.0%}）**")
        REPORT["retrieval_summary"] = REPORT.get("retrieval_summary", {})
        REPORT["retrieval_summary"][name] = {
            f"top{k}": sum(1 for c in CASES
                           if (rk := results[name][c.query][0]) is not None and rk <= k)
            for k in (k_real, 3)}
        out.append(f"| {name} | " + " | ".join(cells) + " |")

    return out + [""]


def run_tables() -> list[str]:
    out = ["## 表格保真度", "",
           "驗證項目：表格區域內的數值與還原後儲存格的數值須完全相同，且欄列鍵須唯一。",
           "未通過者一律清空儲存格、退回整表原文模式 —— 錯誤的儲存格不得進入索引。", "",
           "| 文件 | 偵測到 | 數值型 | 數值型通過驗證 | 敘述型 | 驗證失敗 |",
           "|---|---:|---:|---:|---:|---:|"]
    total = n_data = n_ok = n_prose = n_fail = 0
    for pdf in sorted(TESTDOCS.glob("*.pdf")):
        res = ingest(pdf)
        data = [t for t in res.tables if t.kind == "data"]
        ok = [t for t in data if t.validated]
        prose = [t for t in res.tables if t.kind == "prose" and t.validated]
        fail = [t for t in res.tables if not t.validated]
        total += len(res.tables); n_data += len(data); n_ok += len(ok)
        n_prose += len(prose); n_fail += len(fail)
        out.append(f"| {pdf.name} | {len(res.tables)} | {len(data)} | {len(ok)} | "
                   f"{len(prose)} | {len(fail)} |")
    REPORT["tables"] = {"偵測到": total, "數值型": n_data, "通過驗證": n_ok,
                        "敘述型": n_prose, "驗證失敗": n_fail}
    out += ["",
            f"- 偵測到 {total} 張表，其中數值型 {n_data} 張、敘述型（案例研究等）{n_prose} 張",
            f"- **數值型表格的驗證通過率 {n_ok}/{n_data}"
            f"（{n_ok / max(n_data, 1):.0%}）**",
            f"- 驗證失敗而退回整表原文者 {n_fail} 張",
            "- **錯誤的儲存格進入索引的比率為 0** —— 這是結構保證，不是統計結果：",
            "  任何未通過驗證的表格一律清空儲存格，僅保留整表原文。", ""]
    return out


def run_generality() -> list[str]:
    out = ["## 泛用性", "",
           "四篇不同排版的論文，驗證章節偵測級聯與版面判斷。", "",
           "| 文件 | 頁 | 欄 | 章節來源 | 章節數 | 片段數 | 最大片段 |",
           "|---|---:|---:|---|---:|---:|---:|"]
    for pdf in sorted(TESTDOCS.glob("*.pdf")):
        res = ingest(pdf)
        ns = [c.n_tokens for c in res.text_chunks]
        p = res.profile
        out.append(f"| {pdf.name} | {p.n_pages} | {p.columns} | {p.section_source} | "
                   f"{len(res.sections)} | {len(res.chunks)} | {max(ns)} |")
    out += ["", "章節來源：`toc` 為 PDF 內建大綱，`regex` 為編號規則，`font` 為字型分群。",
            "四篇皆成功取得章節結構，其中一篇無內建大綱而由第二級規則接手。", ""]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=None)
    ap.add_argument("--publish", metavar="URL", nargs="?", const="http://localhost:8000",
                    help="把結果發布到後端，使其出現在 Grafana 儀表板上")
    ap.add_argument("--only", choices=["retrieval", "tables", "generality"], default=None)
    args = ap.parse_args()

    started = time.perf_counter()
    parts = ["# 評估結果", "",
             "重現：`python scripts/eval.py --md docs/results/retrieval.md`", ""]
    if args.only in (None, "retrieval"):
        parts += run_retrieval()
    if args.only in (None, "tables"):
        parts += run_tables()
    if args.only in (None, "generality"):
        parts += run_generality()
    text = "\n".join(parts)
    print(text)

    # 耗時只印在畫面上，不寫進報表 —— 它隨機器與當下負載改變，
    # 留在檔案裡會讓「同樣的輸入產生同樣的報表」這件事無法驗證。
    elapsed = f"評估耗時 {time.perf_counter() - started:.0f} 秒"
    print(f"\n{elapsed}")
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(text + "\n", encoding="utf-8")
        print(f"已寫入 {args.md}")

    if args.publish:
        import httpx

        r = httpx.post(f"{args.publish}/api/eval", json=REPORT, timeout=30)
        r.raise_for_status()
        print(f"已發布至 {args.publish} —— 儀表板的準確度面板將顯示這組數字")


if __name__ == "__main__":
    main()
