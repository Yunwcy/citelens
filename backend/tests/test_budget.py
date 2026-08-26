"""脈絡預算的硬性保證。

作業明確要求說明「文件 token 超過模型脈絡上限時如何處理」，
這些測試就是那個說明的可執行版本。
"""
from app.config import settings
from app.models import Chunk
from app.retrieval.budget import pack


def _chunk(cid: str, n_tokens: int, kind: str = "text", table_id: str | None = None) -> Chunk:
    # 以英文單字構造可預期的 token 數
    return Chunk(
        chunk_id=cid, text=" ".join(["token"] * n_tokens), page=1,
        section_id="s0", section_title="測試", kind=kind, n_tokens=n_tokens,
        meta={"table_id": table_id} if table_id else {},
    )


def test_組裝後不超過預算():
    chunks = [_chunk(f"c{i}", 2000) for i in range(10)]
    ctx = pack(chunks)
    assert ctx.used_tokens <= settings.retrieval_budget
    assert ctx.dropped, "超出預算的片段必須被記錄，而不是靜默丟棄"


def test_捨棄與裁切都要留下紀錄():
    """答案不完整時，必須能從除錯資訊看出是預算不足造成的。"""
    ctx = pack([_chunk("a", 6800), _chunk("b", 900), _chunk("c", 500)])
    assert ctx.truncated or ctx.dropped


def test_表格永不裁切():
    """少了幾列的表格會讓模型讀出錯誤的欄列對應，比沒有這張表更危險。"""
    ctx = pack([_chunk("t", 6900, kind="table_full", table_id="T1"),
                _chunk("u", 900, kind="table_row", table_id="T1")])
    assert "u" in ctx.dropped
    assert "u" not in ctx.truncated


def test_命中表格列時整張表一併帶入():
    """否則模型只看得到被命中的那幾列，卻據此回答整張表的問題。"""
    row = _chunk("T1-r00", 50, kind="table_row", table_id="T1")
    full = _chunk("T1-full", 300, kind="table_full", table_id="T1")
    ctx = pack([row], all_chunks=[row, full])
    ids = [c.chunk_id for _, c, _ in ctx.blocks]
    assert ids == ["T1-r00", "T1-full"]


def test_整表只會被帶入一次():
    rows = [_chunk(f"T1-r{i:02d}", 50, kind="table_row", table_id="T1") for i in range(4)]
    full = _chunk("T1-full", 300, kind="table_full", table_id="T1")
    ctx = pack(rows, all_chunks=rows + [full])
    ids = [c.chunk_id for _, c, _ in ctx.blocks]
    assert ids.count("T1-full") == 1


def test_引用編號連續且從一開始():
    ctx = pack([_chunk(f"c{i}", 100) for i in range(5)])
    assert [n for n, _, _ in ctx.blocks] == [1, 2, 3, 4, 5]
