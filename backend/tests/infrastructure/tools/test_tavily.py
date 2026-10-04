"""Tavily 客户端：真 HTTP（测试里用 `httpx.MockTransport` 跑**真代码路径**）。

三条规矩（P17 §1·8/10、§13）：
① key 只进请求体，**绝不进结果**（信息包里搜不到它）；
② 每条结果 → 一个**信息包**（`origin="web_search"`，`source_ref` 是 URL）；
③ 失败（401 / 500 / 超时 / 非 JSON）抛 `ToolCallFailed`，**不吞**。
"""

import json
from dataclasses import asdict

import httpx
import pytest

from pmstudio.harness.errors import ToolCallFailed
from pmstudio.infrastructure.tools.tavily import TavilySearchGateway


def gateway(handler) -> TavilySearchGateway:  # type: ignore[no-untyped-def]
    return TavilySearchGateway(
        api_key="tvly-secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_search_returns_one_packet_per_result_and_never_leaks_the_key():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "导出方案",
                        "url": "https://example.com/a",
                        "content": "结论是：先做同步导出。" * 40,
                    },
                    {
                        "title": "权限口径",
                        "url": "https://example.com/b",
                        "content": "只有管理员能导出",
                    },
                ]
            },
        )

    packets = await gateway(handler).search(query="订单导出 方案", limit=5)

    assert [packet.source_ref for packet in packets] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert all(packet.origin == "web_search" for packet in packets)
    assert all(packet.parser_id == "tavily" for packet in packets)
    assert packets[0].chunks[0].anchor == "网页摘要"
    # 单条 content 实测 ≈1.1k 字符 → 截到 300，并且**写明截断**
    assert len(packets[0].chunks[0].text) <= 300
    assert packets[0].truncated is True
    assert any("截断" in warning for warning in packets[0].warnings)
    assert packets[1].truncated is False

    assert seen["body"]["api_key"] == "tvly-secret"          # 真发出去了
    assert seen["body"]["query"] == "订单导出 方案"
    assert seen["body"]["max_results"] == 5
    blob = json.dumps([asdict(packet) for packet in packets], default=str)
    assert "tvly-secret" not in blob                          # 结果里一个字节都没有


async def test_the_query_is_recorded_in_warnings_but_not_the_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [
            {"title": "t", "url": "https://a", "content": "一句话"},
        ]})

    packets = await gateway(handler).search(query="法规怎么要求", limit=1)

    assert any("法规怎么要求" in warning for warning in packets[0].warnings)
    assert all("tvly-secret" not in warning for warning in packets[0].warnings)


async def test_results_without_a_url_are_kept_but_flagged():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"title": "没有链接", "content": "内容"}]})

    packets = await gateway(handler).search(query="x", limit=1)

    assert packets[0].source_ref == "（没有链接）"
    assert any("没有链接" in warning for warning in packets[0].warnings)


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(401, json={"detail": "unauthorized"}),
        lambda request: httpx.Response(500, text="boom"),
        lambda request: httpx.Response(200, text="不是 JSON"),
        lambda request: httpx.Response(200, json={"没有 results": []}),
    ],
)
async def test_failures_are_tool_failures_not_silent_empties(handler):
    with pytest.raises(ToolCallFailed) as error:
        await gateway(handler).search(query="x", limit=1)

    assert error.value.code == "tool_call_failed"
    assert error.value.tool_id == "tool.web_search"


async def test_a_timeout_is_a_tool_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    with pytest.raises(ToolCallFailed):
        await gateway(handler).search(query="x", limit=1)


async def test_no_key_no_call():
    """没配密钥时**根本不该发起调用**（构造时就拒）。"""

    with pytest.raises(ToolCallFailed):
        TavilySearchGateway(api_key="")
