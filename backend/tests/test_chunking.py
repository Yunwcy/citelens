"""切塊與章節偵測的基本保證。"""
from app.config import settings


def test_檢索預算符合作業限制():
    """10K 的假設上限扣掉各項保留後，留給檢索內容的額度。"""
    assert settings.max_context == 10_000
    assert settings.retrieval_budget == 7_000


def test_片段長度不超過目標(lightrag):
    """允許表格片段超標（不可切分），文字片段則必須守住上限。"""
    for c in lightrag.text_chunks:
        assert c.n_tokens <= settings.chunk_target_tokens + 40, c.chunk_id


def test_章節偵測級聯對各種排版都有效(all_pdfs):
    """四篇不同排版的論文都要能取得章節結構，且回報所用的級別。"""
    from app.services.ingest import ingest

    for path in all_pdfs:
        res = ingest(path)
        assert len(res.sections) >= 10, f"{path.name} 只找到 {len(res.sections)} 個章節"
        assert res.profile.section_source in ("toc", "regex", "font"), path.name


def test_目標章節落在正確頁次(lightrag):
    """消融實驗章節必須涵蓋 Table 2 所在的第 8 頁。"""
    sec = next(s for s in lightrag.sections if "Ablation" in s.title)
    pages = {b.page for b in sec.blocks}
    assert 8 in pages or sec.page_start <= 8


def test_第一節之前的內容也要入索引(all_pdfs):
    """學術論文的標題、作者與摘要都落在第一節之前。

    直接跳過等於這些內容完全不進索引 —— 實測四篇論文中有三篇的摘要因此遺失，
    而摘要往往是整份文件資訊密度最高的一段。
    """
    from app.services.ingest import ingest

    for path in all_pdfs:
        res = ingest(path)
        assert res.sections[0].title in ("Abstract", "Front matter"), (
            f"{path.name} 的第一節是 {res.sections[0].title}，第一節之前的內容可能被丟棄"
        )


def test_文件資訊片段可被檢索(lightrag):
    """標題與作者原本以「一行一個人名」的形式散落，語意訊號極弱。

    整理成敘述句後才檢索得到 —— 與表格逐列線性化是同一個原理。
    """
    info = [c for c in lightrag.chunks if c.meta.get("doc_info")]
    assert len(info) == 1
    assert "Title" in info[0].text or "標題" in info[0].text
