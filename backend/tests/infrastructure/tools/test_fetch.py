"""按网址抓网页：真 httpx 语义（`MockTransport`）+ 六个护栏各一条。

不联网也能跑：**域名解析注入**（`resolve=`）——公网那几个域名给固定 IP，
内网那几条（`127.0.0.1` / `localhost`）走真解析（本机地址，不需要网络）。
"""

import socket

import httpx
import pytest

from pmstudio.harness.errors import FetchRefused, ToolCallFailed
from pmstudio.infrastructure.tools.fetch import MAX_FETCH_BYTES, HttpxWebFetcher
from tests.infrastructure.tools.test_extract import minimal_pdf

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


async def test_the_fetcher_returns_raw_bytes_the_final_url_and_the_title():
    """fetcher 只回答"这一页的字节、地址、标题"——**解析不归它管**（`02 §11.0.53`）。

    旧版它自己调 `html_text`：同一份 HTML 于是有两个解析调用点，而 `extract` 里现成的
    PDF / Office 分派抓取侧一种都用不上。切成"fetcher 管网络、parser 管文字"之后，
    抓到的正文长什么样在 `test_fetch_material.py` 里断言（那儿才走得到真解析器）。
    """

    calls: list[str] = []

    fetched = await fetcher(page(calls)).fetch(url="https://example.com/spec")

    assert calls == ["https://example.com/spec"]
    assert fetched.content == PAGE         # 原始字节照原样回来（材料表存它）
    assert fetched.media_type == "text/html"
    assert fetched.final_url == "https://example.com/spec"
    assert fetched.title == "导出权限"


async def test_a_page_without_a_title_has_no_title():
    """没有 `<title>` 就老实说没有——退回用网址当标题是**用例**的决定（它才知道怎么取舍）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"<p>no title here</p>"
        )

    page = await fetcher(handler).fetch(url="https://example.com/no-title")

    assert page.title is None


async def test_non_2xx_is_a_tool_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, headers={"content-type": "text/html"}, content=b"nope")

    with pytest.raises(ToolCallFailed) as error:
        await fetcher(handler).fetch(url="https://example.com/gone")

    assert "404" in str(error.value)


async def test_non_html_content_type_is_a_tool_failure():
    """不是网页的类型（压缩包 / 图片…）照旧拒——"先下载再上传"。

    原断言用的是 `application/pdf`，它钉住的正是本轮要拆掉的那道限制：
    `extract` 里本来就有完整的 PDF 解析路径，抓取侧却把自己关在 HTML 一种类型上。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/zip"}, content=b"PK\x03\x04xxx"
        )

    with pytest.raises(FetchRefused) as error:
        await fetcher(handler).fetch(url="https://example.com/spec.zip")

    assert "不是已知材料类型" in str(error.value)


async def test_a_pdf_url_is_fetched_and_parsed_by_the_pdf_path():
    """PDF 网址：抓回来交给**真解析器**（`MaterialParser` 的分派），于是按页切块、
    锚点写第几页、parser 身份是 pdfminer——上传 `.pdf` 走的就是这一条。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=minimal_pdf("Hello export"),
        )

    page = await fetcher(handler).fetch(url="https://example.com/spec.pdf")

    assert page.content.startswith(b"%PDF")
    assert page.media_type == "application/pdf"
    assert page.final_url == "https://example.com/spec.pdf"
    assert page.title is None               # PDF 没有 <title>；"用网址当标题"是用例的决定


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
    assert page_.final_url == "https://cdn.example.com/final"
    assert page_.content == PAGE          # 原始字节跟着一起回来


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


