"""URL 匯入的安全檢查。

「讓伺服器去抓使用者給的網址」是典型的 SSRF 攻擊面。
這些斷言存在的目的，是讓防護有機制保證，而非僅寫在文件上。
"""
import pytest

from app.services.fetcher import UnsafeUrl, _assert_safe, normalize


@pytest.mark.parametrize("raw,expected", [
    ("https://arxiv.org/abs/2410.05779", "https://arxiv.org/pdf/2410.05779"),
    ("https://arxiv.org/abs/2410.05779v2", "https://arxiv.org/pdf/2410.05779v2"),
    ("https://arxiv.org/pdf/2410.05779", "https://arxiv.org/pdf/2410.05779"),
    ("2410.05779", "https://arxiv.org/pdf/2410.05779"),
    ("arxiv.org/abs/2410.05779", "https://arxiv.org/pdf/2410.05779"),
])
def test_arxiv_網址正規化(raw, expected):
    assert normalize(raw) == expected


def test_http_會被升級為_https():
    """使用者貼上 http 連結是常見情形，升級比拒絕合理，且安全性更高。"""
    assert normalize("http://example.com/a.pdf") == "https://example.com/a.pdf"


def test_非_https_協定一律拒絕():
    """這道檢查真正守的是轉址的每一跳 —— 轉址目標不會經過 normalize。"""
    for url in ("http://example.com/a.pdf", "file:///etc/passwd",
                "gopher://example.com/", "ftp://example.com/a.pdf"):
        with pytest.raises(UnsafeUrl):
            _assert_safe(url)


@pytest.mark.parametrize("url", [
    "https://169.254.169.254/latest/meta-data/",     # 雲端 metadata 端點
    "https://127.0.0.1/secret.pdf",
    "https://10.0.0.5/internal.pdf",
    "https://192.168.1.1/router.pdf",
    "https://localhost/a.pdf",
])
def test_拒絕內部位址(url):
    with pytest.raises(UnsafeUrl, match="內部位址"):
        _assert_safe(url)


def test_無法解析的網域會被擋下():
    with pytest.raises(UnsafeUrl):
        _assert_safe("https://this-domain-should-not-exist-99999.invalid/a.pdf")


def test_非_http_協定的訊息要說對原因():
    """先前是無條件補上 https，ftp://x 變成 https://ftp://x，
    最後失敗在 DNS 解析並回報「無法解析這個網域」—— 訊息與原因不符。"""
    import pytest

    from app.services.fetcher import UnsafeUrl, normalize

    for url in ("ftp://example.com/a.pdf", "file:///etc/passwd", "gopher://x/1"):
        with pytest.raises(UnsafeUrl, match="http"):
            normalize(url)
