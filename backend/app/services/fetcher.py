"""由網址取得 PDF，含 SSRF 防護。

「讓伺服器去抓使用者給的網址」是典型的 SSRF 攻擊面：攻擊者可以讓服務
去存取內網資源，或雲端環境的 metadata 端點（169.254.169.254）。
本模組的五道檢查缺一不可，其中第三道最容易被遺漏 ——
攻擊者可以用一個公開網址轉址到內網。
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.config import settings

MAX_REDIRECTS = 3
_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)


class UnsafeUrl(ValueError):
    """網址未通過安全檢查。訊息會直接顯示給使用者，因此要具體。"""


@dataclass(slots=True)
class Fetched:
    data: bytes
    filename: str
    title: str | None
    url: str


# --- 檢查 -------------------------------------------------------------------

def _assert_safe(url: str) -> str:
    """第一與第二道：協定限定 https，並在 DNS 解析後檢查目標位址。"""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeUrl("只接受 https 連結")
    if not parsed.hostname:
        raise UnsafeUrl("網址格式不正確")

    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrl("無法解析這個網域") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise UnsafeUrl("這個連結指向內部位址，已拒絕")
    return url


def normalize(url: str) -> str:
    """arXiv 的各種寫法都轉成 PDF 連結。使用者不該需要知道差別。"""
    url = url.strip()
    if _ARXIV_ID.fullmatch(url):
        return f"https://arxiv.org/pdf/{url}"
    if "arxiv.org" in url:
        m = _ARXIV_ID.search(url)
        if m:
            return f"https://arxiv.org/pdf/{m.group(0)}"
    if url.startswith("http://"):
        # 升級而非拒絕：貼上 http 連結是常見情形，且升級後安全性更高。
        # _assert_safe 仍會拒絕非 https —— 那道檢查守的是轉址的每一跳。
        url = "https://" + url[7:]
    if url.startswith("https://"):
        return url
    # 到這裡若仍有協定，就是 http/https 以外的東西。
    # 先前是無條件補上 https，於是 ftp://x 變成 https://ftp://x，
    # 最後失敗在 DNS 解析並回報「無法解析這個網域」—— 訊息與原因不符。
    if "://" in url:
        raise UnsafeUrl("只接受 http 或 https 連結")
    return "https://" + url


# --- 取得 -------------------------------------------------------------------

async def fetch(raw_url: str) -> Fetched:
    url = _assert_safe(normalize(raw_url))
    limit = settings.max_upload_mb * 1024 * 1024

    async with httpx.AsyncClient(follow_redirects=False,
                                 timeout=settings.fetch_timeout_s) as client:
        for _ in range(MAX_REDIRECTS + 1):
            async with client.stream("GET", url) as res:
                if res.is_redirect:
                    location = res.headers.get("location", "")
                    if not location:
                        raise UnsafeUrl("轉址缺少目標位址")
                    # 第三道：不自動跟隨，每一跳都重新檢查
                    url = _assert_safe(normalize(httpx.URL(url).join(location).__str__()))
                    continue

                if res.status_code != 200:
                    raise UnsafeUrl(f"無法取得檔案（HTTP {res.status_code}）")

                # 第四道：內容型別與檔頭雙重驗證
                ctype = res.headers.get("content-type", "").split(";")[0].strip()
                if ctype and ctype not in ("application/pdf", "application/octet-stream"):
                    raise UnsafeUrl("這個連結不是 PDF")

                # 第五道：邊下載邊計數，超過上限立即中止
                chunks: list[bytes] = []
                total = 0
                async for chunk in res.aiter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise UnsafeUrl(f"檔案超過 {settings.max_upload_mb}MB")
                    chunks.append(chunk)

            data = b"".join(chunks)
            if not data.startswith(b"%PDF"):
                raise UnsafeUrl("這個連結不是 PDF")

            return Fetched(data=data, filename=_filename(url),
                           title=await _arxiv_title(url), url=url)

    raise UnsafeUrl("轉址次數過多")


def _filename(url: str) -> str:
    name = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or "document"
    return name if name.lower().endswith(".pdf") else f"{name}.pdf"


async def _arxiv_title(url: str) -> str | None:
    """取回論文標題，讓文件卡顯示標題而非編號。取不到就算了。"""
    m = _ARXIV_ID.search(url)
    if not m or "arxiv.org" not in url:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            res = await client.get(
                "https://export.arxiv.org/api/query",
                params={"id_list": m.group(1), "max_results": 1},
            )
        entry = res.text.split("<entry>", 1)[-1]
        hit = _TITLE.search(entry)
        return " ".join(hit.group(1).split()) if hit else None
    except Exception:                                     # noqa: BLE001
        return None
