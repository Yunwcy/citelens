"""檢索層的正確性保證。"""
import numpy as np

from app.retrieval.bm25 import tokenize
from app.retrieval.hybrid import rrf
from app.retrieval.vector import VectorIndex


def test_中文要斷詞而非整串當一個詞():
    """把整串中文當成單一 token 等於沒有斷詞，中文查詢會完全命不中。"""
    toks = tokenize("消融實驗顯示 LightRAG 的 dual-level retrieval 有效")
    assert "lightrag" in toks
    assert "dual-level" in toks
    assert any("消融" in t or "實驗" in t for t in toks)
    assert "消融實驗顯示" not in toks


def test_向量檢索回傳餘弦相似度():
    v = np.array([[1.0, 0.0], [0.0, 1.0], [0.7071, 0.7071]], dtype=np.float32)
    hits = VectorIndex(v).search(np.array([1.0, 0.0], dtype=np.float32), k=3)
    assert [i for i, _ in hits] == [0, 2, 1]
    assert abs(hits[0][1] - 1.0) < 1e-5


def test_rrf_只看名次不看原始分數():
    """兩個檢索器的分數量綱不同，融合必須對量綱不敏感。"""
    a = {"vector": [(1, 0.99), (2, 0.98)], "bm25": [(2, 120.0), (1, 3.0)]}
    b = {"vector": [(1, 0.11), (2, 0.10)], "bm25": [(2, 1.2), (1, 0.03)]}
    assert [h.index for h in rrf(a)] == [h.index for h in rrf(b)]


def test_rrf_記錄各檢索器的名次():
    hits = rrf({"vector": [(5, 0.9)], "bm25": [(5, 2.0), (7, 1.0)]})
    top = next(h for h in hits if h.index == 5)
    assert top.ranks == {"vector": 1, "bm25": 1}


def test_消融實驗查詢命中正確章節(lightrag_index):
    """作業指定的第三題。目標章節必須進入前三名。"""
    hits = lightrag_index.search("Performance of ablated versions of LightRAG", top_k=3)
    sections = {lightrag_index.chunk(h.index).section_title for h in hits}
    assert any("Ablation" in s for s in sections), sections


def test_比較類查詢涵蓋多個章節(lightrag_index):
    """第二題需要跨章節資訊，不能全部集中在同一節。"""
    hits = lightrag_index.search("compare LightRAG with GraphRAG", top_k=8)
    sections = {lightrag_index.chunk(h.index).section_title for h in hits}
    assert len(sections) >= 2, sections


def test_索引可存回並讀出(lightrag_index, tmp_path, monkeypatch):
    from app.config import settings
    from app.retrieval.index import DocumentIndex

    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    lightrag_index.save()
    loaded = DocumentIndex.load(lightrag_index.doc_id)
    assert len(loaded.chunks) == len(lightrag_index.chunks)
    assert loaded.vector.vectors.shape == lightrag_index.vector.vectors.shape
    assert loaded.tables.keys() == lightrag_index.tables.keys()
