"""執行指標記錄。

每一次索引與查詢都寫一筆 JSON Lines 到 storage/metrics.jsonl。
選 JSONL 而非 CSV 的理由：欄位會隨功能增加而變動，JSONL 對缺欄位寬容，
且可直接以 jq 或 pandas 讀取；CSV 一旦加欄位，舊資料就對不上。

這個檔案是成本、延遲、準確度三份報表的唯一資料來源。
沒有它，交付時只能重跑一次現場量測，拿不到 p95 這種需要累積樣本的數字。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)
_lock = threading.Lock()


def _path() -> Path:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings.storage_dir / "metrics.jsonl"


def record(event: str, **fields: Any) -> None:
    """附加一筆事件。寫入失敗不得影響主流程 —— 監控不該讓服務掛掉。"""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        **fields,
    }
    try:
        line = json.dumps(row, ensure_ascii=False)
        with _lock, _path().open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:                                    # noqa: BLE001
        log.warning("指標寫入失敗", exc_info=True)


def read_all() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
