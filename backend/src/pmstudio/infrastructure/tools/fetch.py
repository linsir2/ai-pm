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

import asyncio
import ipaddress
import random
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from pmstudio.application.ports import FetchedPage
from pmstudio.harness.errors import FetchRefused, ToolCallFailed

TOOL_ID = "tool.web_fetch"
FETCH_TIMEOUT = 15.0
MAX_FETCH_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
USER_AGENT = "PMStudio/0.1 (+local single-user workbench)"
#: 抓取重试次数（**唯一一份**）。不用 httpx 内置的 `AsyncHTTPTransport(retries=)`：
#: 那只覆盖 TCP/TLS 握手、证书错也会被重试、无 jitter、不覆盖 read timeout 与 5xx。
FETCH_ATTEMPTS = 3
#: 哪些状态码值得重试（其余 4xx 一律不试——输了就是输了）。
RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
#: 总墙钟上限（秒）。3 × 15s 只是下界：退避与 Retry-After 会把一次抓取拖长。
FETCH_TOTAL_TIMEOUT = 45.0
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
        """跟到最后一跳，返回 `(原始字节, Content-Type, 最终地址)`。

        三件事的顺序就是这道防线的全部：
        ① **解析 + 校验** → 拿到**校验过的 IP**（`_assert_public` 现在返回它，不再只断言）；
        ② **把 IP 钉进 URL**（`_pinned_target`）→ 客户端只连刚校验过的那个地址，
           校验与连接之间没有第二次解析（DNS rebinding 的修法）；
        ③ **重试复用同一个已钉的 target** → 重试不重新解析，否则每重试一次就多一个
           二次解析的窗口（OWASP：destination policy 要管到 retries 与 fallback 连接）。
        """

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
            async with asyncio.timeout(FETCH_TOTAL_TIMEOUT):
                for _ in range(MAX_REDIRECTS + 1):
                    # ① 解析并校验；多个地址取第一个（全连是过度设计）
                    addresses = _assert_public(current, resolve=self._resolve)
                    target, headers, extensions = _pinned_target(current, addresses[0])
                    # ③ 重试只在这个已钉的 target 上进行
                    attempt = await self._attempt_with_retry(
                        client, target, headers=headers, extensions=extensions
                    )
                    if attempt.location is not None:
                        current = urljoin(current, attempt.location)
                        continue           # 下一跳重新解析校验，护栏一步都不松
                    if attempt.status_code != 200:
                        raise ToolCallFailed(
                            f"这个网址返回 HTTP {attempt.status_code}",
                            tool_id=TOOL_ID,
                            status=attempt.status_code,
                        )
                    if attempt.media_type not in WANTED_MEDIA_TYPES:
                        raise FetchRefused(
                            f"这个地址返回的不是已知材料类型（{attempt.media_type or '没写类型'}）"
                            "——支持网页 / PDF / Word / Excel，先下载再上传也行"
                        )
                    return attempt.body, attempt.media_type, current
                raise ToolCallFailed(
                    f"这个网址跳转太多（超过 {MAX_REDIRECTS} 跳）", tool_id=TOOL_ID
                )
        finally:
            if owns_client:
                await client.aclose()

    async def _attempt_with_retry(
        self, client: httpx.AsyncClient, target: str, *, headers: dict, extensions: dict
    ) -> "_Attempt":
        """一个 target 最多试 `FETCH_ATTEMPTS` 次；连不上就**当次转译**，不重试。

        重试集合只有两个：`ReadTimeout`（我们的 5MB 边下边数恰好在这儿超时，是真抖动）
        与 `RemoteProtocolError`；加上状态码 `{429, 502, 503, 504}`。

        **不重试**连接类错误（`ConnectError`/`ConnectTimeout`，含证书错——httpx 把证书错
        也 map 成 `ConnectError`）：用户粘来的 URL 连不上就是连不上，不是抖动；而要把它
        分辨出来得走整条异常链（实测 `SSLCertVerificationError` → `httpcore.ConnectError`
        → `httpx.ConnectError`），为省几次无效握手赔 15 行不值得。

        任何 httpx 异常都在这里转成 `ToolCallFailed`——不许漏到 HTTP 层变成 500。
        """

        for index in range(FETCH_ATTEMPTS):
            try:
                attempt = await self._attempt(
                    client, target, headers=headers, extensions=extensions
                )
            except httpx.HTTPError as error:
                retryable = isinstance(error, (httpx.ReadTimeout, httpx.RemoteProtocolError))
                if not retryable:
                    raise ToolCallFailed(
                        f"抓这个网址失败：{type(error).__name__}", tool_id=TOOL_ID
                    ) from error
                if index + 1 == FETCH_ATTEMPTS:
                    raise ToolCallFailed(
                        f"抓这个网址失败（试了 {FETCH_ATTEMPTS} 次都没成）：{type(error).__name__}",
                        tool_id=TOOL_ID,
                    ) from error
                await _backoff(index)
                continue
            if attempt.status_code in RETRYABLE_STATUS and index + 1 < FETCH_ATTEMPTS:
                await _backoff(index, attempt.retry_after)
                continue
            return attempt
        raise AssertionError("不可能走到这里：循环里每条路都返回或抛出了")

    async def _attempt(
        self, client: httpx.AsyncClient, target: str, *, headers: dict, extensions: dict
    ) -> "_Attempt":
        """发一次请求；只有"这一跳就用它"才把 body 读完（重定向与报错都不读）。"""

        async with client.stream(
            "GET", target, follow_redirects=False, headers=headers, extensions=extensions
        ) as response:
            location = response.headers.get("location") if response.is_redirect else None
            body = None
            if location is None and response.status_code == 200:
                body = await _read_capped(response)
            return _Attempt(
                status_code=response.status_code,
                media_type=_media_type(response),
                body=body,
                location=location,
                retry_after=response.headers.get("retry-after"),
            )


@dataclass(frozen=True, slots=True)
class _Attempt:
    """一次尝试的结果：`_read` 靠它决定"重试 / 跟跳 / 收下"的最小信息。"""

    status_code: int
    media_type: str
    body: bytes | None
    location: str | None
    retry_after: str | None


async def _backoff(index: int, retry_after: str | None = None) -> None:
    """退避：优先听服务端 `Retry-After`（封顶 5s），否则封顶指数 + full jitter。

    jitter 不是装饰：无 jitter 的同步重试会在限流场景里自己制造峰值
    （AWS 架构博客：Full Jitter 在 100 竞争客户端下工作量减半以上）。
    """

    if retry_after:
        try:
            await asyncio.sleep(min(float(retry_after), 5.0))
            return
        except ValueError:
            pass
    ceiling = min(2.0 * 2**index, 8.0)
    await asyncio.sleep(ceiling / 2 * (1 + random.random()))


def _pinned_target(url: str, ip: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """把 URL 的域名换成**已校验的 IP**，Host 头与 SNI/证书校验保留原域名。

    这是 httpx 官方文档给出的扩展点（`extensions={"sni_hostname": ...}`）：URL 里是
    字面 IP 时 `getaddrinfo` 直接返回它、不再查 DNS，"校验的 IP"与"连接的 IP"由此
    同一个。证书校验走 `sni_hostname`（域名），完全不受影响。
    """

    parts = urlsplit(url)
    host = parts.hostname or ""
    literal = f"[{ip}]" if ":" in ip else ip
    netloc = f"{literal}:{parts.port}" if parts.port else literal
    pinned = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
    host_header = f"{host}:{parts.port}" if parts.port else host
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    return pinned, {"Host": host_header}, extensions


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


def _assert_public(url: str, *, resolve: Callable[[str], tuple[str, ...]]) -> tuple[str, ...]:
    """解析域名 → 每个地址都必须是**公网**地址（内网 / 环回 / 链路本地一律拒）。

    **返回**校验过的地址，而不只是断言：调用方要让客户端只连这里面的 IP，
    否则"校验时解析出的公网 IP"与"连接时重新解析出的 IP"可以是两个——那就是
    DNS rebinding 的缝（OWASP：validate the resolved IP, then ensure the client
    connects only to validated addresses）。
    """

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
    return addresses


def _media_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";")[0].strip().lower()


def _looks_like_html(media_type: str) -> bool:
    return media_type in ("text/html", "application/xhtml+xml")


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
