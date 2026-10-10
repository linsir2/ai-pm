"""按网址抓一份材料：真 httpx，**只管网络与护栏，不解析**。

与联网检索（Tavily）分开：那是"搜线索"（300 字摘要），这是"**用户点名的一个页面**"——
拿到字节之后交给 `MaterialParser`（用例里做），于是 HTML / PDF / Office 全都走
`extract` 那一条真实分派，跟上传的材料是同一条链（切块、发号、渲染、检索、出处）。

四条规矩：
① **只抓公开网页**：http/https、拒绝内网 / 环回 / 链路本地地址，**每一跳都重新判**。
   为什么：这套 API 没有鉴权，任何页面都能诱导本机后端去抓内网地址（CSRF 型 SSRF）；
② **有上限**：15 秒、5MB（边下边数）、最多 3 跳、Content-Type 必须是已知材料类型；
③ **原始字节照原样留下**（存进材料表）——重解析、追证都不用再抓一次；
④ **失败抛 `ToolCallFailed`**：超时 / 非 2xx / 不是材料 / 太大 / 内网——不吞、不假装抓到。
"""

import ipaddress
import socket
from collections.abc import Callable

import httpx
from bs4 import BeautifulSoup

from pmstudio.application.ports import FetchedPage
from pmstudio.harness.errors import FetchRefused, ToolCallFailed

TOOL_ID = "tool.web_fetch"
FETCH_TIMEOUT = 15.0
MAX_FETCH_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
USER_AGENT = "PMStudio/0.1 (+local single-user workbench)"
#: 只收这些 Content-Type：HTML，加上 `extract` 已有真实分派的 PDF / Word / Excel。
#: 判决按 media_type 与扩展名**两级**都接受（站点经常把 .pdf 返成 application/octet-stream，
#: 那种情况下 URL 扩展名才是唯一线索）。
WANTED_MEDIA_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
WANTED_SUFFIXES = (".html", ".htm", ".pdf", ".docx", ".xlsx")


def resolve_host(host: str) -> tuple[str, ...]:
    """域名 → IP 列表（默认实现；测试注入自己的解析，免得依赖 DNS）。"""

    return tuple(str(item[4][0]) for item in socket.getaddrinfo(host, None))


class HttpxWebFetcher:
    def __init__(
        self,
        *,
        timeout: float = FETCH_TIMEOUT,
        client: httpx.AsyncClient | None = None,
        resolve: Callable[[str], tuple[str, ...]] = resolve_host,
    ) -> None:
        self._timeout = timeout
        self._client = client
        self._resolve = resolve

    async def fetch(self, *, url: str) -> FetchedPage:
        target = _checked_url(url)
        body, media_type, final_url = await self._read(target)
        # 标题只对 HTML 有意义；PDF / Office 没有 <title>，那种情况留 None，
        # 由用例退回用最终地址当材料标题
        return FetchedPage(
            content=body,
            media_type=media_type,
            final_url=final_url,
            title=_page_title(body) if _looks_like_html(media_type) else None,
        )

    async def _read(self, url: str) -> tuple[bytes, str, str]:
        """跟到最后一跳，返回 `(原始字节, Content-Type, 最终地址)`。每一跳都判公私网。"""

        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT},
            )
        current = url
        try:
            for _ in range(MAX_REDIRECTS + 1):
                _assert_public(current, resolve=self._resolve)
                try:
                    async with client.stream("GET", current, follow_redirects=False) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise ToolCallFailed(
                                    "这个网址跳转去了一个没有地址的地方", tool_id=TOOL_ID
                                )
                            current = str(httpx.URL(current).join(location))
                            continue
                        if response.status_code != 200:
                            raise ToolCallFailed(
                                f"这个网址返回 HTTP {response.status_code}",
                                tool_id=TOOL_ID,
                                status=response.status_code,
                            )
                        media_type = _media_type(response)
                        if not _is_wanted(media_type, current):
                            raise FetchRefused(
                                f"这个地址返回的不是已知材料类型（{media_type or '没写类型'}）"
                                "——支持网页 / PDF / Word / Excel，先下载再上传也行"
                            )
                        body = await _read_capped(response)
                except httpx.HTTPError as error:
                    raise ToolCallFailed(
                        f"抓这个网址失败：{type(error).__name__}", tool_id=TOOL_ID
                    ) from error
                return body, media_type, current
        finally:
            if owns_client:
                await client.aclose()
        raise ToolCallFailed(
            f"这个网址跳转太多（超过 {MAX_REDIRECTS} 跳）", tool_id=TOOL_ID
        )


def _checked_url(url: str) -> str:
    """只收"干净的 http/https 网址"：没协议 / 带账号密码 / 没主机名的一律当场拒。"""

    text = (url or "").strip()
    if not text:
        raise FetchRefused("先给一个网址（http / https 开头）")
    parsed = httpx.URL(text)
    if parsed.scheme not in ("http", "https"):
        raise FetchRefused(
            f"只抓 http / https 的网页（这个地址的协议是「{parsed.scheme or '没写'}」）"
        )
    if parsed.userinfo:
        raise FetchRefused("网址里不要带账号密码")
    if not parsed.host:
        raise FetchRefused(f"这个地址没有主机名：{text}")
    return str(parsed)


def _assert_public(url: str, *, resolve: Callable[[str], tuple[str, ...]]) -> None:
    """解析域名 → 每个地址都必须是**公网**地址（内网 / 环回 / 链路本地一律拒）。"""

    host = httpx.URL(url).host
    try:
        addresses = resolve(host)
    except OSError as error:
        raise ToolCallFailed(f"这个网址解析不了（{host}）", tool_id=TOOL_ID) from error
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global:
            raise FetchRefused(
                f"这是内网地址（{host} → {parsed}）——先把它导出成文件再上传"
            )


def _media_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";")[0].strip().lower()


def _looks_like_html(media_type: str) -> bool:
    return media_type in ("text/html", "application/xhtml+xml")


def _is_wanted(media_type: str, url: str) -> bool:
    """Content-Type 在名单里，**或者** URL 扩展名在名单里（两级都认）。"""

    return media_type in WANTED_MEDIA_TYPES or url.lower().endswith(WANTED_SUFFIXES)


def _page_title(body: bytes) -> str | None:
    """HTML 的 `<title>`（只用来当材料标题，不参与正文）。"""

    try:
        soup = BeautifulSoup(body, "lxml")
    except Exception:  # noqa: BLE001 —— 标题拿不到不影响抓取，交给解析器去说
        return None
    node = soup.title
    return node.get_text(strip=True) if node is not None else None


async def _read_capped(response: httpx.Response) -> bytes:
    """边下边数：超过上限当场停（不是先收完 1GB 再说"太大了"）。"""

    body = bytearray()
    async for part in response.aiter_bytes():
        body.extend(part)
        if len(body) > MAX_FETCH_BYTES:
            raise FetchRefused(
                f"这个页面太大了（超过 {MAX_FETCH_BYTES // 1024 // 1024}MB）"
                "——先把它下载下来、另存成文件再上传"
            )
    return bytes(body)
