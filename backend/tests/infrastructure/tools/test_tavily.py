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
from pmstudio.infrastructure.tools.tavily import (
    MAX_QUERY_CHARS,
    TavilySearchGateway,
)


def gateway(
    handler,  # type: ignore[no-untyped-def]
    *,
    slept: list[float] | None = None,
) -> TavilySearchGateway:
    """真网关 + `MockTransport`。

    **注入即时时钟**：重试不再真等（否则重试类用例各慢 3 秒），但走过的退避时长
    照记进 `slept`——"封顶生效"这类断言才测得准。
    """

    async def sleeper(seconds: float) -> None:
        if slept is not None:
            slept.append(seconds)

    return TavilySearchGateway(
        api_key="tvly-secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        sleeper=sleeper,
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
    # 锚点标明是第几段（联网摘要没有"行号"这回事）
    assert packets[0].chunks[0].anchor == "网页摘要 · 第 1 段"
    # **不再截到 300**（2026-10-19 实测：Tavily 实给 568–2302 字，截 300 等于
    # 扔掉 50–78%）。截断是**预算层**该管的事，入库层管它就是职责混淆。
    assert all(len(chunk.text) <= 400 for chunk in packets[0].chunks)
    assert packets[0].truncated is False
    assert not any("截断" in warning for warning in packets[0].warnings)
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


# ── 有界重试（工具韧性 P1／缺口 G1：原来一次即炸）─────────────────────────────
# 重试集与 fetch 同形：超时 / 连接错 / 429 / 5xx。**400/401/403/432/433 永不重试**
# （400 是客户端错含超长、432/433 是账单，等多久都不会好）。


async def test_a_500_is_retried_then_succeeds():
    """R：Tavily 500 是瞬时错（官方文档：过几分钟重试）——一次抖动不该白给。"""

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(
            200, json={"results": [{"title": "t", "url": "https://a", "content": "一句话"}]}
        )

    packets = await gateway(handler).search(query="x", limit=1)

    assert len(packets) == 1
    assert calls["n"] == 2


async def test_a_429_honours_retry_after_and_the_wait_is_capped():
    """R：`Retry-After: 60` 实测只等 5s——fetch 的 5s 封顶现货，opening 里真等 60s 是灾难。"""

    calls = {"n": 0}
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, headers={"Retry-After": "60"}, json={"detail": "slow down"}
            )
        return httpx.Response(200, json={"results": []})

    packets = await gateway(handler, slept=slept).search(query="x", limit=1)

    assert packets == ()
    assert calls["n"] == 2
    assert slept == [5.0]  # 封顶生效，不是 60


async def test_a_400_is_never_retried():
    """R：400 是客户端错（含超长），重试多少次都一样——只烧时间。"""

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"detail": {"error": "Query is too long"}})

    with pytest.raises(ToolCallFailed) as error:
        await gateway(handler).search(query="x", limit=1)

    assert calls["n"] == 1
    assert error.value.message == "联网搜索失败：HTTP 400"


async def test_a_432_is_a_billing_limit_not_a_rate_limit():
    """R：432 = plan 用量上限（Tavily 文档）。等多久都不会好，重试只烧时间。"""

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(432, json={"detail": {"error": "usage limit"}})

    with pytest.raises(ToolCallFailed) as error:
        await gateway(handler).search(query="x", limit=1)

    assert calls["n"] == 1
    assert error.value.message == "联网搜索失败：HTTP 432"


async def test_retries_are_capped_at_three():
    """R：3 次封顶（与 fetch 的 `FETCH_ATTEMPTS` 同形）——不退化成无限重试。"""

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    with pytest.raises(ToolCallFailed) as error:
        await gateway(handler).search(query="x", limit=1)

    assert calls["n"] == 3
    assert "3 次" in error.value.message


async def test_a_timeout_is_retried_then_succeeds():
    """R：超时算瞬时错——Tavily 是固定可靠端点，网络抖动该重试。"""

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("too slow", request=request)
        return httpx.Response(200, json={"results": []})

    await gateway(handler).search(query="x", limit=1)

    assert calls["n"] == 2


async def test_an_over_long_query_is_truncated_before_sending():
    """R：Tavily 硬墙——query 超 1500 字回 400（真跑实测：2040 回 400、1501 回 200，
    原文 "Max query length is 1500 characters"）。漏斗在网关入口，截了才发。"""

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": []})

    await gateway(handler).search(query="长" * 2000, limit=1)

    assert len(seen["body"]["query"]) == MAX_QUERY_CHARS  # type: ignore[arg-type]
    # 常量必须留在实测墙以内——将来有人往上调过头，这条先红。
    assert MAX_QUERY_CHARS <= 1500


# ── 联网结果切块（批 6a Task 4）───────────────────────────────────────────────


def _one_result(content: str, *, title: str = "导出方案") -> str:
    """造一个只含一条结果的 Tavily 回包（JSON 文本）。"""

    return json.dumps({"results": [{"title": title, "url": "https://example.com/a",
                                    "content": content}]})


async def _search_content(content: str, *, title: str = "导出方案"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=json.loads(_one_result(content, title=title)))

    gateway_ = gateway(handler)
    packets = await gateway_.search(query="导出", limit=5)
    return packets


async def test_a_long_result_is_split_into_several_chunks():
    """R：超过块宽的结果**切成多块**（今天恒定 1 块，尾部对向量不可见）。

    实测（Tavily 真跑 2026-10-19）：`content` 实给 568–2302 字，而实现截到 300 字
    ——**扔掉 50–78%**。整段一块也不行：bge 的 512-token 上限会静默截断
    （实测 embed(700字) 与 embed(前510字) 余弦 1.000000）。
    """
    packets = await _search_content("结论是：先做同步导出。" * 100)

    assert len(packets) == 1
    assert len(packets[0].chunks) > 1, "长结果没有切块"


async def test_every_chunk_respects_the_web_chunk_width():
    """R：每块 ≤ `WEB_CHUNK_CHARS`（400 字，留出标题/路径的 512-token 预算）。"""
    from pmstudio.infrastructure.tools.limits import WEB_CHUNK_CHARS

    packets = await _search_content("结论是：先做同步导出。" * 100)

    for chunk in packets[0].chunks:
        assert len(chunk.text) <= WEB_CHUNK_CHARS, f"块 {len(chunk.text)} 字，超了"


async def test_a_short_result_stays_one_chunk():
    """R：短结果仍是 1 块（不要为了"统一"硬切）。"""
    packets = await _search_content("结论很短。")

    assert len(packets[0].chunks) == 1


async def test_the_anchor_tells_which_part_it_is():
    """R：锚点标明是第几段（"网页摘要"是块级的东西，多块就分不清了）。"""
    packets = await _search_content("结论是：先做同步导出。" * 100)

    anchors = [chunk.anchor for chunk in packets[0].chunks]
    assert anchors[0] == "网页摘要 · 第 1 段"
    assert anchors[1] == "网页摘要 · 第 2 段"


async def test_a_markdown_heading_in_the_content_becomes_the_heading_path():
    """R：content 里的 `## 标题` 变成 `heading_path`。

    白捡的检索信号：章节链进索引源（`index_source` = 标题 + 章节链 + 正文），
    "只在这一节出现的内容"才搜得到。今天联网材料的 `heading_path` **恒为空**。
    """
    packets = await _search_content("## 数据保护与隐私边界\n\n正文说日志要脱敏。")

    # 章节链里**带 `##` 前缀**——这是 `chunk_text` 的既有形状（上传的 .md 也一样，
    # 同一份代码）。这里跟它保持一致，不为了联网这条路单开一个形状。
    assert packets[0].chunks[0].heading_path == ("## 数据保护与隐私边界",)


async def test_the_content_is_cleaned_before_it_is_chunked():
    """R：切块前先清洗（Tavily 的 `[...]` 标记不进块）。"""
    packets = await _search_content("结论是：先做同步导出。 [...] 后半段。")

    assert "..." not in "".join(c.text for c in packets[0].chunks)


async def test_the_gateway_drops_duplicate_results():
    """R：**这次搜索返回的结果里没有重复的**（同一件事被几家站转载 → 只留一条）。

    为什么在网关这一层做：重复会白占 `WEB_BUDGET` 与提示词——而"这次搜索返回了
    哪些结果"本来就是网关的事。跨查询的重复由 URL 级去重兜着（按 `web:{url}`）。
    """
    body = "个人信息保护法要求处理个人信息应当取得个人同意，并明确处理目的与范围。"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [
            {"title": "甲", "url": "https://a", "content": body},
            {"title": "乙", "url": "https://b", "content": body + "本文转载自网络。"},
            {"title": "丙", "url": "https://c", "content": "另一部法规，讲的是别的事。"},
        ]})

    packets = await gateway(handler).search(query="个人信息保护法", limit=5)

    assert len(packets) == 2, f"重复的应被去掉（留 2 条），实得 {len(packets)}"
    assert packets[0].source_ref == "https://a", "留的应该是第一条（Tavily 的相关序）"


async def test_the_whole_page_is_stored_not_only_the_query_relevant_part():
    """R：入库的是**整页**（`raw_content`），不是 Tavily 按 query 挑的那几段。

    实测（2026-10-20 真跑）：Tavily 的 `content` 是 "chunks_per_source 默认 3、
    每段约 500 字、**按 query 相关性挑**"——页面主体永远不在库里。冻结语料上
    目标词只有 19/45 存在于现状语料，混合检索命中仅 29%；换整页后 45/45、71%。

    这里断言两件事：① 请求确实要了整页；② 整页的后半段真的进了库。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["include_raw_content"] == "markdown", "没要求整页正文"
        return httpx.Response(200, json={"results": [{
            "title": "整页",
            "url": "https://example.com/a",
            "content": "这是按 query 挑出来的那一段。",
            "raw_content": "\n".join(
                f"## 第 {index} 节\n第 {index} 节的正文内容。" for index in range(40)
            ),
        }]})

    packets = await gateway(handler).search(query="查询", limit=5)

    text = "\n".join(chunk.text for chunk in packets[0].chunks)
    assert "第 39 节" in text, "整页的后半段没进库"
    assert "这是按 query 挑出来的那一段" not in text


async def test_the_raw_content_falls_back_to_content_when_it_is_null():
    """R：`raw_content` 为 null 时回退 `content`——**不能空包**。

    实测 Tavily 会返回 null（LangChain 为此专门打过补丁：langchain#30021）。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{
            "title": "回退",
            "url": "https://example.com/a",
            "content": "只有片段也有用。",
            "raw_content": None,
        }]})

    packets = await gateway(handler).search(query="查询", limit=5)

    assert len(packets) == 1
    assert "只有片段也有用。" in packets[0].chunks[0].text


async def test_a_huge_page_is_capped_and_says_so():
    """R：联网材料**第一次有字数上限**（此前 `cap_text` 只在上传路径调用）。

    raw_content 是个整页，个别站点能到几十万字——没有上限就是几万块。
    """
    from pmstudio.infrastructure.tools.limits import MAX_MATERIAL_CHARS

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{
            "title": "超大",
            "url": "https://example.com/a",
            "content": "短",
            "raw_content": "字" * (MAX_MATERIAL_CHARS + 5000),
        }]})

    packets = await gateway(handler).search(query="查询", limit=5)

    assert packets[0].truncated is True
    assert any("太长" in warning for warning in packets[0].warnings)
    total = sum(len(chunk.text) for chunk in packets[0].chunks)
    assert total <= MAX_MATERIAL_CHARS


async def test_the_parser_identity_moved_so_old_web_materials_are_flagged():
    """R：换了入库口径 → 解析器身份必须跟着动，老材料才会被标 stale。"""
    from pmstudio.infrastructure.tools.tavily import PARSER_VERSION

    assert PARSER_VERSION == "tavily-rest-v2-raw"
