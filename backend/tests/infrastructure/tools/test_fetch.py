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

    assert calls == ["https://93.184.216.34/spec"]   # 2026-10-10（§I ⑦）：连的是已校验的 IP
    assert fetched.content == PAGE         # 原始字节照原样回来（材料表存它）
    assert fetched.media_type == "text/html"
    assert fetched.final_url == "https://example.com/spec"   # 出处仍按原域名算
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
    assert calls == ["https://93.184.216.34/go"]   # 第一跳连的是校验过的那个 IP


async def test_redirect_chain_is_followed_and_the_final_url_is_reported():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/go":
            return httpx.Response(302, headers={"location": "https://cdn.example.com/final"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=PAGE)

    page_ = await fetcher(handler).fetch(url="https://example.com/go")

    assert calls == ["https://93.184.216.34/go", "https://93.184.216.34/final"]
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




# ── 重试与 SSRF（`04-未决.md` §I 的 ⑦，2026-10-10）─────────────────────────
#
# httpx 内置的 `AsyncHTTPTransport(retries=N)` 在这个场景不能用（官方文档 + httpcore
# 源码）：只覆盖 TCP/TLS 握手、**证书错也被重试**、无 jitter、不覆盖 read timeout
# 与 5xx——而 5MB 边下边数超过 15s 恰好在 body 阶段抛 ReadTimeout。
# 所以自己写 3-attempt 循环，并且**把校验过的 IP 钉进 URL**：否则"校验时解析的公网 IP"
# 与"连接时重新解析的 IP"可以是两个（DNS rebinding / TOCTOU）。


def counting_resolve(answers: list[tuple[str, ...]]):
    """按调用次序返回预设的解析结果（测试 DNS 行为，不依赖真 DNS）。"""
    calls = {"n": 0}

    def resolve(host: str) -> tuple[str, ...]:
        index = min(calls["n"], len(answers) - 1)
        calls["n"] += 1
        return answers[index]

    return resolve, calls


async def test_a_timeout_is_retried_and_the_second_attempt_succeeds():
    calls: list[str] = []
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ReadTimeout("body too slow")
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>ok</p>")

    fetched = await fetcher(handler).fetch(url="https://example.com/spec")

    assert b"ok" in fetched.content
    assert len(calls) == 2                     # 真的重试了一次，不是只试一次


async def test_a_connection_error_is_not_retried():
    """连接类错误（含证书错——httpx 把它 map 成 ConnectError）一律不重试。

    用户粘来的 URL 连不上就是连不上，不是抖动；重试它只是把同样的失败做三遍。
    """

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise httpx.ConnectError("certificate verify failed")

    with pytest.raises(ToolCallFailed):
        await fetcher(handler).fetch(url="https://example.com/spec")

    assert len(calls) == 1


async def test_a_retryable_status_is_retried_but_a_404_is_not():
    calls: list[str] = []
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(503, headers={"content-type": "text/html"}, content=b"later")
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>ok</p>")

    fetched = await fetcher(handler).fetch(url="https://example.com/spec")
    assert b"ok" in fetched.content
    assert len(calls) == 2

    not_found: list[str] = []

    def missing(request: httpx.Request) -> httpx.Response:
        not_found.append(str(request.url))
        return httpx.Response(404, headers={"content-type": "text/html"}, content=b"gone")

    with pytest.raises(ToolCallFailed):
        await fetcher(missing).fetch(url="https://example.com/x")
    assert len(not_found) == 1


async def test_the_client_connects_to_the_ip_it_just_validated():
    """钉住校验过的 IP：解析第一次给公网、第二次给内网，客户端仍连第一次那个。

    这才是 DNS rebinding 的修法——校验与连接之间不能再有第二次解析。
    """

    seen: list[str] = []
    resolve, dns = counting_resolve([("93.184.216.34",), ("127.0.0.1",)])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>ok</p>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetched = await HttpxWebFetcher(client=client, resolve=resolve).fetch(
        url="https://example.com/spec"
    )

    assert "93.184.216.34" in seen[0]        # 连的是校验过的那个 IP
    assert "example.com" not in seen[0]      # URL 里不再是域名
    assert dns["n"] == 1                     # 只解析了一次
    assert b"ok" in fetched.content


async def test_every_hop_is_revalidated_after_a_redirect():
    """重定向后重新解析+校验——已有的护栏不能因为加重试而松掉。"""

    seen: list[str] = []
    resolve, dns = counting_resolve([("93.184.216.34",), ("10.0.0.5",)])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/go":
            return httpx.Response(302, headers={"location": "https://cdn.example.com/next"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>ok</p>")

    with pytest.raises(FetchRefused) as error:
        await HttpxWebFetcher(
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), resolve=resolve
        ).fetch(url="https://example.com/go")

    assert "内网" in str(error.value)
    assert dns["n"] == 2                     # 每一跳都重新解析校验


async def test_a_pdf_served_as_octet_stream_is_still_accepted():
    """R：站点把 .pdf 返成 `application/octet-stream` **照样收**。

    `fetch.py` 的注释承诺「判决按 media_type 与扩展名**两级**都接受
    （站点经常把 .pdf 返成 application/octet-stream，那种情况下 URL 扩展名才是唯一线索）」，
    但代码里**只判 media_type** → 那些站点今天被拒。**注释与实现不符 = bug**。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=minimal_pdf("Hello export"),
        )

    page = await fetcher(handler).fetch(url="https://example.com/spec.pdf")

    assert page.content.startswith(b"%PDF")
    assert page.final_url == "https://example.com/spec.pdf"


async def test_an_unknown_type_with_an_unknown_extension_is_still_refused():
    """R：扩展名也认不出来时**照旧拒**（两级判决不是"放宽成什么都收"）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=b"\x00\x01binary",
        )

    with pytest.raises(FetchRefused) as error:
        await fetcher(handler).fetch(url="https://example.com/archive.zip")

    assert "不是已知材料类型" in str(error.value)
