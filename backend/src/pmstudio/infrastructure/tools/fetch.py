"""按网址抓网页：真 httpx + 一条 HTML→正文（与上传 `.html` 共用 `extract.html_text`）。

与联网检索（Tavily）分开：那是"搜线索"（300 字摘要），这是"**用户点名的一个页面**"——
抓正文、块级切分，跟上传的材料走同一条链（存储、编号、渲染、检索、出处）。

四条规矩：
① **只抓公开网页**：http/https、拒绝内网 / 环回 / 链路本地地址，**每一跳都重新判**。
   为什么：这套 API 没有鉴权，任何页面都能诱导本机后端去抓内网地址（CSRF 型 SSRF）；
② **有上限**：15 秒、5MB（边下边数）、最多 3 跳、Content-Type 必须是网页；
③ **原始字节照原样留下**（存进材料表）——重解析、追证都不用再抓一次；
④ **失败抛 `ToolCallFailed`**：超时 / 非 2xx / 不是网页 / 太大 / 内网——不吞、不假装抓到。
"""

import ipaddress
import socket
from collections.abc import Callable

import httpx

from pmstudio.application.dto import ParsedMaterial
from pmstudio.application.errors import ToolCallFailed, UnsupportedMaterial
from pmstudio.application.ports import FetchedPage
from pmstudio.infrastructure.tools.extract import (
    HTML_PARSER_ID,
    TRUNCATED_WARNING,
    cap_text,
    chunk_text,
    html_parser_version,
    html_text,
)
from pmstudio.infrastructure.tools.limits import MAX_MATERIAL_CHARS

TOOL_ID = "tool.web_fetch"
#: 抓来的网页在信息包里的 origin（前端文案与提示词标题都按它写："抓来的网页"）
FETCH_ORIGIN = "web_fetch"
FETCH_TIMEOUT = 15.0
MAX_FETCH_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
USER_AGENT = "PMStudio/0.1 (+local single-user workbench)"
#: 只收这几种 Content-Type（其余一律"这不是网页"）
WEB_MEDIA_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


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
        try:
            text, page_title = html_text(body)
        except UnsupportedMaterial as error:
            raise ToolCallFailed(
                f"这个网址抓下来的页面读不了（{error.message}）", tool_id=TOOL_ID
            ) from error
        text, truncated = cap_text(text)
        if not text.strip():
            raise UnsupportedMaterial(
                f"这个网址里没有可抽取的文字：{final_url}（可能是脚本渲染的页面）",
                source_ref=final_url,
            )
        chunks = chunk_text(text, parser_id=HTML_PARSER_ID)
        warnings: list[str] = []
        if final_url != target:
            warnings.append(f"这个网址跳转过（最终地址：{final_url}）")
        if truncated:
            warnings.append(TRUNCATED_WARNING.format(limit=MAX_MATERIAL_CHARS))
        return FetchedPage(
            parsed=ParsedMaterial(
                origin=FETCH_ORIGIN,
                title=page_title or final_url,
                source_ref=final_url,
                media_type=media_type,
                bytes_len=len(body),
                chunks=chunks,
                parser_id=HTML_PARSER_ID,
                parser_version=html_parser_version(),
                truncated=truncated,
                warnings=tuple(warnings),
            ),
            # 抓到的 HTML 原始字节跟着一起走（材料表要存它，重解析靠它）
            content=body,
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
                        if media_type not in WEB_MEDIA_TYPES:
                            raise ToolCallFailed(
                                f"这个地址返回的不是网页（{media_type or '没写类型'}）"
                                "——先把内容下载下来再上传",
                                tool_id=TOOL_ID,
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
        raise ToolCallFailed("先给一个网址（http / https 开头）", tool_id=TOOL_ID)
    parsed = httpx.URL(text)
    if parsed.scheme not in ("http", "https"):
        raise ToolCallFailed(
            f"只抓 http / https 的网页（这个地址的协议是「{parsed.scheme or '没写'}」）",
            tool_id=TOOL_ID,
        )
    if parsed.userinfo:
        raise ToolCallFailed("网址里不要带账号密码", tool_id=TOOL_ID)
    if not parsed.host:
        raise ToolCallFailed(f"这个地址没有主机名：{text}", tool_id=TOOL_ID)
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
            raise ToolCallFailed(
                f"这是内网地址（{host} → {parsed}）——先把它导出成文件再上传",
                tool_id=TOOL_ID,
            )


def _media_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";")[0].strip().lower()


async def _read_capped(response: httpx.Response) -> bytes:
    """边下边数：超过上限当场停（不是先收完 1GB 再说"太大了"）。"""

    body = bytearray()
    async for part in response.aiter_bytes():
        body.extend(part)
        if len(body) > MAX_FETCH_BYTES:
            raise ToolCallFailed(
                f"这个页面太大了（超过 {MAX_FETCH_BYTES // 1024 // 1024}MB）"
                "——先把它下载下来、另存成文件再上传",
                tool_id=TOOL_ID,
            )
    return bytes(body)
