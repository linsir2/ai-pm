"""按网址抓网页：真 httpx 语义（`MockTransport`）+ 六个护栏各一条。

不联网也能跑：**域名解析注入**（`resolve=`）——公网那几个域名给固定 IP，
内网那几条（`127.0.0.1` / `localhost`）走真解析（本机地址，不需要网络）。
"""

import socket

import httpx
import pytest

from pmstudio.application.errors import UnsupportedMaterial
from pmstudio.harness.errors import FetchRefused, ToolCallFailed
from pmstudio.infrastructure.tools.fetch import MAX_FETCH_BYTES, HttpxWebFetcher

PAGE = (
    "<html><head><title>导出权限</title></head><body>"
    "<script>var x=1</script><style>p{}</style><p># 导出</p><p>只有管理员能导出。</p>"
    "</body></html>"
).encode()

PUBLIC_HOSTS = {"example.com", "cdn.example.com", "other.example"}


def resolve(host: str) -> tuple[str, ...]:
    """公网域名给固定 IP（测试不依赖 DNS）；其余（localhost / 127.0.0.1）走真解析。"""

    if host in PUBLIC_HOSTS:
        return ("93.184.216.34",)
    return tuple(str(item[4][0]) for item in socket.getaddrinfo(host, None))


def fetcher(handler, **kwargs) -> HttpxWebFetcher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpxWebFetcher(client=client, resolve=resolve, **kwargs)


def page(handler_calls: list[str], **headers):
    def handler(request: httpx.Request) -> httpx.Response:
        handler_calls.append(str(request.url))
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8", **headers}, content=PAGE
        )

    return handler


async def test_fetch_returns_a_parsed_material_with_anchors():
    calls: list[str] = []

    parsed = (await fetcher(page(calls)).fetch(url="https://example.com/spec")).parsed

    assert calls == ["https://example.com/spec"]
    assert parsed.origin == "web_fetch"
    assert parsed.source_ref == "https://example.com/spec"
    assert parsed.media_type == "text/html"
    assert parsed.parser_id == "bs4-html"
    assert parsed.title == "导出权限"
    texts = [chunk.text for chunk in parsed.chunks]
    # `<title>` 那行也进正文——与上传 `.html` 走的是同一条抽取（`extract.html_text`）
    assert texts[0] == "导出权限"
    assert any("# 导出" in text for text in texts)
    assert any("只有管理员能导出。" in text for text in texts)
    assert all("var x" not in text and "p{}" not in text for text in texts)
    assert parsed.chunks[0].anchor == "第 1 行"


async def test_a_page_without_a_title_falls_back_to_the_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"<p>no title here</p>"
        )

    parsed = (await fetcher(handler).fetch(url="https://example.com/no-title")).parsed

    assert parsed.title == "https://example.com/no-title"


async def test_non_2xx_is_a_tool_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, headers={"content-type": "text/html"}, content=b"nope")

    with pytest.raises(ToolCallFailed) as error:
        await fetcher(handler).fetch(url="https://example.com/gone")

    assert "404" in str(error.value)


async def test_non_html_content_type_is_a_tool_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4"
        )

    with pytest.raises(FetchRefused) as error:
        await fetcher(handler).fetch(url="https://example.com/spec.pdf")

    assert "不是网页" in str(error.value)


async def test_timeout_is_a_tool_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    with pytest.raises(ToolCallFailed) as error:
        await fetcher(handler).fetch(url="https://example.com/slow")

    assert "ConnectTimeout" in str(error.value)


async def test_too_large_is_a_tool_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"x" * (MAX_FETCH_BYTES + 1),
        )

    with pytest.raises(FetchRefused) as error:
        await fetcher(handler).fetch(url="https://example.com/huge")

    assert "太大" in str(error.value)


async def test_private_address_is_refused_without_any_request():
    """**一次请求都不许发**——护栏不是装饰品。"""

    calls: list[str] = []

    for url in ("http://127.0.0.1:8000/x", "http://localhost/secret", "http://10.0.0.5/"):
        with pytest.raises(FetchRefused) as error:
            await fetcher(page(calls)).fetch(url=url)
        assert "内网" in str(error.value)

    assert calls == []


async def test_redirect_to_a_private_address_is_refused():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://10.0.0.5/inside"})

    with pytest.raises(FetchRefused) as error:
        await fetcher(handler).fetch(url="https://example.com/go")

    assert "内网" in str(error.value)
    assert calls == ["https://example.com/go"]


async def test_redirect_chain_is_followed_and_the_final_url_is_reported():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/go":
            return httpx.Response(302, headers={"location": "https://cdn.example.com/final"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=PAGE)

    page_ = await fetcher(handler).fetch(url="https://example.com/go")

    assert calls == ["https://example.com/go", "https://cdn.example.com/final"]
    assert page_.parsed.source_ref == "https://cdn.example.com/final"
    assert page_.content == PAGE          # 原始字节跟着一起回来
    assert any("跳转" in warning for warning in page_.parsed.warnings)


async def test_too_many_redirects_is_a_tool_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/loop"})

    with pytest.raises(ToolCallFailed) as error:
        await fetcher(handler).fetch(url="https://example.com/loop")

    assert "跳转" in str(error.value)


@pytest.mark.parametrize(
    "url", ["example.com/x", "ftp://example.com/x", "http://user:pw@example.com/x", ""]
)
async def test_a_url_that_is_not_a_plain_public_web_page_is_refused(url):
    calls: list[str] = []

    with pytest.raises(FetchRefused):
        await fetcher(page(calls)).fetch(url=url)

    assert calls == []


async def test_a_page_that_cannot_be_parsed_says_so():
    """抽不出正文的页面（比如只有一个空 body）→ 明确报错，不存空包。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")

    with pytest.raises(UnsupportedMaterial) as error:
        await fetcher(handler).fetch(url="https://example.com/empty")

    assert "没有可抽取的文字" in str(error.value)
