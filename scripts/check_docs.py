"""檢查文件裡的數字與實際產出是否一致。

本專案的原則是「一個數字只有一個出處」：脈絡預算由設定推導，
量測報表由腳本產生。README 是最容易違反這條的地方 —— 它是給人看的，
沒有任何機制會在系統改變時提醒它。

同樣的道理也套用在 Grafana 儀表板：它由 `ops/build_dashboard.py` 產生，
但產出物進了版控之後，就可能被手改、或因為腳本改了而沒重跑。

實際發生過三次漂移：
- 章節偵測補上 front-matter 之後每篇 +1，README 仍是舊值
- 併發的端到端 p50 寫 0.8 秒，重測是 3.5 秒
- 測試數量同時存在 36、50、52 三個值，實際是另一個數

因此把「對不對得上」變成會失敗的檢查，而不是靠人核對。

用法：
    python scripts/check_docs.py            # 檢查，不一致則以非零結束
    python scripts/check_docs.py --fix      # 可自動修正者直接改寫（測試數量）
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
RESULTS = ROOT / "docs" / "results"

TEST_COUNT_PATTERNS = [
    re.compile(r"(\d+)(\s*項測試)"),
    re.compile(r"(\d+)(\s*項自動化測試)"),
    re.compile(r"(\d+)(\s*項全過)"),
]
SYNC_TARGETS = [README, ROOT / "docs" / "architecture.md"]


def collected_tests() -> int:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
        cwd=ROOT / "backend", capture_output=True, text=True,
    )
    m = re.search(r"(\d+) tests? collected", out.stdout)
    if not m:
        raise SystemExit(f"無法取得測試數：\n{out.stdout[-400:]}{out.stderr[-400:]}")
    return int(m.group(1))


def sync_test_count(n: int, fix: bool) -> list[str]:
    """測試數量可以安全地自動改寫 —— 它只是一個數字，沒有上下文。"""
    stale = []
    for path in SYNC_TARGETS:
        if not path.exists():
            continue
        text = original = path.read_text(encoding="utf-8")
        for pat in TEST_COUNT_PATTERNS:
            text = pat.sub(lambda m: f"{n}{m.group(2)}", text)
        if text != original:
            found = sorted({m.group(1) for p in TEST_COUNT_PATTERNS for m in p.finditer(original)})
            stale.append(f"{path.relative_to(ROOT)}：測試數寫的是 {'、'.join(found)}，實際 {n}")
            if fix:
                path.write_text(text, encoding="utf-8")
    return stale


def _cell(text: str, row_pattern: str, group: int = 1) -> str | None:
    m = re.search(row_pattern, text)
    return m.group(group) if m else None


def check_measurements() -> list[str]:
    """量測數字只檢查、不自動改寫 —— 它們嵌在敘述裡，改錯比不改更糟。"""
    bad: list[str] = []
    rd = README.read_text(encoding="utf-8")

    def compare(label: str, in_readme: str | None, in_report: str | None) -> None:
        if in_readme is None or in_report is None:
            bad.append(f"{label}：抓不到（README={in_readme} 報表={in_report}）")
        elif in_readme != in_report:
            bad.append(f"{label}：README 寫 {in_readme}，報表是 {in_report}")

    # README 原本逐篇列出章節數，現已改為一句話帶過（逐篇數據在 retrieval.md）。
    # 沒有寫出來的數字就不會漂移，所以那項檢查跟著移除，改為核對 README
    # 仍然主張的表格保真度數字。
    retrieval = (RESULTS / "retrieval.md").read_text(encoding="utf-8")
    for label, rd_pat, rp_pat in (
        ("偵測到的表格數", r"偵測到 (\d+) 張表", r"偵測到 (\d+) 張表"),
        ("數值型表格通過率", r"數值型表格驗證通過率 (\d+/\d+)",
         r"數值型表格的驗證通過率 (\d+/\d+)"),
    ):
        compare(label, _cell(rd, rd_pat), _cell(retrieval, rp_pat))

    # 檢索準確度是這個專案的招牌數字，先前卻是手算的、沒有任何腳本產生它 ——
    # 系統改過之後它悄悄變成錯的（宣稱 95%、21/22，而測試集只有 12 題）。
    # 現在 eval.py 會算，這裡逐列比對。用順序而非標籤比對：兩份文件的
    # 列名寫法本來就不同（「＋結構感知切塊」vs「2 加結構感知」），
    # 拿標籤去配對只會配不到，然後靜靜地什麼都沒檢查。
    rd_rates = re.findall(r"^\|[^|]+\| \*\*(\d+)%\*\*", rd, re.M)
    rp_rates = re.findall(r"^\| \d [^|]+\| \*\*\d+/\d+（(\d+)%）\*\*", retrieval, re.M)
    if len(rd_rates) != 3 or len(rp_rates) != 3:
        bad.append(f"檢索命中率：抓不到三列（README {len(rd_rates)} 列、"
                   f"報表 {len(rp_rates)} 列）")
    else:
        for i, (a, b) in enumerate(zip(rd_rates, rp_rates), 1):
            compare(f"檢索命中率（第 {i} 組設定）", a, b)

    load = (RESULTS / "load.md").read_text(encoding="utf-8")
    for label in ("1 併發", "20 併發"):
        compare(
            f"檢索 p50（{label}）",
            _cell(rd, rf"\| {label} \| (\d+) ms"),
            _cell(load, rf"\| {label} \| \d+ \| \d+ \| (\d+) ms"),
        )

    runtime = (RESULTS / "runtime.md").read_text(encoding="utf-8")
    for label, pat in (("有標註引用", r"有標註引用 \| (\d+)"),
                       ("如實拒答", r"如實拒答 \| (\d+)")):
        compare(label, _cell(rd, pat), _cell(runtime, pat))

    cost_report = _cell(runtime, r"平均每次成本 \| US\$0\.(\d+)")
    cost_readme = _cell(rd, r"單次查詢平均 US\$0\.(\d+)")
    if cost_report and cost_readme and not cost_report.startswith(cost_readme.rstrip("0")):
        bad.append(f"單次查詢成本：README 寫 0.{cost_readme}，報表是 0.{cost_report}")

    return bad


def check_dashboard() -> list[str]:
    """儀表板 JSON 必須是 build_dashboard.py 的最新產物。

    產生式的檔案一旦進了版控就有兩個真相來源。這裡把「以哪個為準」
    變成會失敗的檢查，而不是靠記得重跑。
    """
    out = subprocess.run(
        [sys.executable, "ops/build_dashboard.py", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if out.returncode == 0:
        return []
    return [(out.stdout + out.stderr).strip().splitlines()[0]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="自動修正測試數量")
    args = ap.parse_args()

    n = collected_tests()
    stale = sync_test_count(n, args.fix)
    bad = check_measurements() + check_dashboard()

    if stale and args.fix:
        print(f"已將測試數量同步為 {n} 項：")
        for s in stale:
            print(f"  - {s}")
        stale = []

    if not stale and not bad:
        print(f"文件與產出一致（測試 {n} 項，量測數字逐項核對通過）")
        return 0

    print("文件與實際產出不符：")
    for s in stale + bad:
        print(f"  ✗ {s}")
    if stale:
        print("\n測試數量可用 python scripts/check_docs.py --fix 自動修正")
    if bad:
        print("\n量測數字需先重跑對應腳本，再依 docs/results/ 更新 README")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
