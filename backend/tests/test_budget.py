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


def test_命中表格列時以整張表取代該列():
    """逐列片段的用途是讓表格被找到；找到之後，完整表格才是更好的脈絡。

    兩者同時出現時，被命中的那幾列會錨定模型的注意力 ——
    實測問「消融版本的表現」時，模型只回答了被命中的基準區塊。
    """
    row = _chunk("T1-r00", 50, kind="table_row", table_id="T1")
    full = _chunk("T1-full", 300, kind="table_full", table_id="T1")
    ctx = pack([row], all_chunks=[row, full])
    ids = [c.chunk_id for _, c, _ in ctx.blocks]
    assert ids == ["T1-full"]


def test_整表只會被帶入一次():
    rows = [_chunk(f"T1-r{i:02d}", 50, kind="table_row", table_id="T1") for i in range(4)]
    full = _chunk("T1-full", 300, kind="table_full", table_id="T1")
    ctx = pack(rows, all_chunks=rows + [full])
    ids = [c.chunk_id for _, c, _ in ctx.blocks]
    assert ids == ["T1-full"]


def test_沒有整表時逐列片段仍保留():
    """整表片段不存在（例如驗證失敗被清空）時，逐列仍是唯一的資料來源。"""
    rows = [_chunk(f"T9-r{i:02d}", 50, kind="table_row", table_id="T9") for i in range(3)]
    ctx = pack(rows, all_chunks=rows)
    assert [c.chunk_id for _, c, _ in ctx.blocks] == ["T9-r00", "T9-r01", "T9-r02"]


def test_引用編號連續且從一開始():
    ctx = pack([_chunk(f"c{i}", 100) for i in range(5)])
    assert [n for n, _, _ in ctx.blocks] == [1, 2, 3, 4, 5]


def test_比較類問題的整表只保留被點名的列(lightrag):
    """勝率表每一列是對「單一」基準方法的兩兩對比。

    整張 16 列送進脈絡時，模型會把「對 NaiveRAG 的 61.6%」和
    「對 GraphRAG 那一列的 51.6%」並列 —— 相加 113.2%，兩個數字
    各自都對，配在一起卻是錯的。不相關的列本來就不該出現。
    """
    from app.retrieval.budget import pack
    from app.services.qa import _focus_table_blocks

    full = [c for c in lightrag.chunks if c.kind == "table_full" and c.meta["table_id"] == "T1"]
    ctx = _focus_table_blocks(pack(full), ["LightRAG", "GraphRAG"])
    shown = ctx.blocks[0][2]

    rows = [ln for ln in shown.split("\n") if "】" in ln]
    assert rows, "整表列全被濾掉了"
    assert all("GraphRAG" in r for r in rows), "留下了非 GraphRAG 區塊的列"
    assert "兩兩對比" in shown, "警語不應被濾掉"
