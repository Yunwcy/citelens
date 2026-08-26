"""表格抽取的正確性測試。

這些測試存在的理由：表格解析錯誤不會拋出例外，只會讓某個數值悄悄
跑到別欄或消失。沒有斷言守著，要到 demo 當下才會發現。
"""
import re

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def test_五張表全部被偵測到(lightrag):
    assert len(lightrag.tables) == 5


def test_數值表全部通過守恆檢查(lightrag):
    """未通過的表格必須已退回整表模式，不得留下可疑的儲存格。"""
    for t in lightrag.tables:
        if t.kind == "data":
            assert t.validated, f"{t.table_id} 未通過：{t.validation_note}"
        else:
            assert not t.rows, f"{t.table_id} 已標記為整表模式卻仍留有列資料"


def test_表格二的黃金儲存格(lightrag):
    """對照論文原文逐格核對。任何一格對不上就是解析錯了。"""
    t2 = next(t for t in lightrag.tables if t.table_id == "T2")
    expected = {
        "Diversity|CS / NaiveRAG": "38.0%",
        "Diversity|CS / LightRAG": "62.0%",
        "-High / Diversity|CS / NaiveRAG": "36.8%",
        "-High / Diversity|CS / -High": "63.2%",
        "-Low / Overall|Legal / NaiveRAG": "18.8%",
        "-Origin / Comprehensiveness|Mix / -Origin": "55.6%",
    }
    for key, want in expected.items():
        assert t2.cells.get(key) == want, f"{key} 應為 {want}，實得 {t2.cells.get(key)}"


def test_表格二的形狀(lightrag):
    """四個變體 × 四個指標 = 16 列；四個資料集 × 兩個模型 = 8 欄。"""
    t2 = next(t for t in lightrag.tables if t.table_id == "T2")
    assert len(t2.rows) == 16
    assert len(t2.columns) == 8
    assert len(t2.cells) == 16 * 8      # 沒有任何鍵碰撞


def test_表格片段從不被切開(lightrag):
    """表格必須是原子單位。被切開的表格等於失去欄位對應關係。"""
    for c in lightrag.table_chunks:
        assert c.kind in ("table_full", "table_row")
    full = [c for c in lightrag.table_chunks if c.kind == "table_full"]
    assert len(full) == len(lightrag.tables)


def test_表格內容不重複入索引(lightrag):
    """表格區域的文字必須已從一般文字片段中排除。

    以只出現在表格裡的數值當標記 —— 用指標名稱（Comprehensiveness）會誤判，
    因為正文本來就會提到這些指標。
    """
    marks = ["63.2%", "72.8%", "18.8%"]
    for mark in marks:
        hits = [c.chunk_id for c in lightrag.text_chunks if mark in c.text]
        assert not hits, f"表格數值 {mark} 同時出現在文字片段 {hits}，會造成重複檢索"
