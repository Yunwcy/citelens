"""把文件裡的測試數量對齊實際收集到的數量。

起因：README 寫 36、系統說明文件同時寫著 52 與 50，實際是 54 ——
四個數字沒有一個對。而這個專案自己主張「數字只有一個出處」，
手寫的測試數正好是打臉自己的地方。

不能用「數 def test_ 的個數」代替：parametrize 會展開成多項，
實測 46 個函式收集到 54 項。唯一可信的來源是 pytest 自己的收集結果。

用法：
    python scripts/sync_test_count.py            # 改寫文件
    python scripts/sync_test_count.py --check    # 只檢查，不一致則以非零結束（供 CI）
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 文件裡可能出現的寫法。數字一律換成實際值。
PATTERNS = [
    re.compile(r"(\d+)(\s*項測試)"),
    re.compile(r"(\d+)(\s*項自動化測試)"),
    re.compile(r"(\d+)(\s*項全過)"),
    re.compile(r"(\d+)(\s*tests? passed)"),
]

TARGETS = [
    ROOT / "README.md",
    ROOT / "docs" / "architecture.md",
    ROOT / "_working" / "系統說明文件.md",
]


def collected() -> int:
    """實際收集到的測試項數。"""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
        cwd=ROOT / "backend", capture_output=True, text=True,
    )
    m = re.search(r"(\d+) tests? collected", out.stdout)
    if not m:
        raise SystemExit(f"無法從 pytest 取得測試數：\n{out.stdout[-500:]}{out.stderr[-500:]}")
    return int(m.group(1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只檢查，不改寫")
    args = ap.parse_args()

    n = collected()
    stale: list[str] = []

    for path in TARGETS:
        if not path.exists():
            continue
        text = original = path.read_text(encoding="utf-8")
        for pat in PATTERNS:
            text = pat.sub(lambda m: f"{n}{m.group(2)}", text)
        if text != original:
            found = {m.group(1) for pat in PATTERNS for m in pat.finditer(original)}
            stale.append(f"{path.relative_to(ROOT)}：寫的是 {'、'.join(sorted(found))}")
            if not args.check:
                path.write_text(text, encoding="utf-8")

    if args.check and stale:
        print(f"文件記載的測試數與實際的 {n} 項不符：")
        for s in stale:
            print(f"  - {s}")
        print("\n執行 python scripts/sync_test_count.py 修正")
        return 1

    print(f"實際收集到 {n} 項測試" + ("　文件已同步" if stale else "　文件本來就一致"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
