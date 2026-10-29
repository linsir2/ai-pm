"""有界研究工具（P3b-1）：`web_search` / `fetch_url` 的边界。

这一层是**唯一碰网络的地方**，也是"照实说"最容易被破坏的地方：
来源上限、重复 URL、正文截断、抓取失败——每一条都要有可断言的出口，
不许静默截断、不许把失败伪装成结果。

替身只用仓库里已有的两个（`RecordingWebSearch` / `RecordingWebFetch`）与**真解析器**：
另造一套 Fake* 只会多一份"和真实现不一样"的行为。
"""

import asyncio

import pytest

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.ports import FetchedPage
from pmstudio.domain.exploration.research import (
    MAX_RESEARCH_SOURCES,
    RESEARCH_SOURCE_CARD_CHARS,
)
from pmstudio.harness.errors import ToolCallFailed
from pmstudio.harness.tools.spec import ToolCall
from pmstudio.infrastructure.tools import limits
from pmstudio.infrastructure.tools.extract import LocalMaterialParser
from pmstudio.infrastructure.tools.research_tools import ResearchToolbox
from tests.support.doubles import RecordingWebFetch, RecordingWebSearch


def draft(url: str, *, text: str | None = None, title: str = "标题") -> ParsedMaterial:
    """一条"搜索结果"：`source_ref` 就是网址（真实 `TavilySearchGateway` 的盖法）。"""

    body = text or f"{title} 的正文：这类产品默认 60 秒上限。"
    return ParsedMaterial(
        origin="upload",
        title=title,
        source_ref=url,
        media_type="text/html",
        bytes_len=len(body.encode()),
        chunks=(
            MaterialChunk(
                chunk_id=f"{url}#c00",
                ordinal=0,
                text=body,
                heading_path=(),
                anchor="正文",
            ),
        ),
        parser_id="fake",
        parser_version="1",
    )


def page(url: str, *, text: str = "内网线走背板槽位。", title: str = "挖到的页面"):
    return FetchedPage(
        content=f"<html><body><p>{text}</p></body></html>".encode(),
        media_type="text/html",
        final_url=url,
        title=title,
    )


def toolbox(*, search=None, fetcher=None, parser=None) -> ResearchToolbox:
    return ResearchToolbox(
        search=search or RecordingWebSearch(returns=(draft("https://example.com/a"),)),
        fetcher=fetcher or RecordingWebFetch(returns=(page("https://example.com/a"),)),
        parser=parser or LocalMaterialParser(),
    )


def call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(call_id=f"c-{name}", name=name, arguments=arguments)


async def dispatch(box: ResearchToolbox, name: str, **arguments: object) -> str:
    spec = next(item for item in box.specs() if item.name == name)
    return await box.invoke(spec, call(name, **arguments))


# —— 工具声明 ——


def test_specs_are_the_two_tools_with_documented_arguments() -> None:
    specs = {spec.name: spec for spec in toolbox().specs()}

    assert set(specs) == {"web_search", "fetch_url"}
    assert specs["web_search"].parameters["required"] == ["query"]
    assert "query" in specs["web_search"].parameters["properties"]
    assert specs["fetch_url"].parameters["required"] == ["url"]
    assert specs["web_search"].description.strip()
    assert specs["fetch_url"].description.strip()


# —— 失败语义：只说失败，不装成功 ——


async def test_unknown_tool_and_bad_arguments_fail_loudly() -> None:
    box = toolbox()
    with pytest.raises(ToolCallFailed):
        await box.invoke(
            next(iter(box.specs())), ToolCall(call_id="c", name="不存在的工具")
        )

    with pytest.raises(ToolCallFailed):
        await dispatch(box, "web_search")  # 缺 query
    with pytest.raises(ToolCallFailed):
        await dispatch(box, "web_search", query="   ")
    with pytest.raises(ToolCallFailed):
        await dispatch(box, "web_search", query=123)
    with pytest.raises(ToolCallFailed):
        await dispatch(box, "fetch_url", url="不是网址")


async def test_search_failure_becomes_a_tool_failure() -> None:
    box = toolbox(search=RecordingWebSearch(failure=RuntimeError("tavily 502")))
    with pytest.raises(ToolCallFailed) as caught:
        await dispatch(box, "web_search", query="导出上限")
    assert "tavily 502" in caught.value.message


async def test_fetch_failure_becomes_a_tool_failure() -> None:
    box = toolbox(fetcher=RecordingWebFetch(failure=RuntimeError("连不上")))
    with pytest.raises(ToolCallFailed):
        await dispatch(box, "fetch_url", url="https://example.com/a")


async def test_unparsable_page_becomes_a_tool_failure() -> None:
    """读不出正文 = 这个网址用不了；不许给模型一个空的"正文"。"""

    box = toolbox(
        fetcher=RecordingWebFetch(
            returns=(
                FetchedPage(
                    content=b"\x00\x01\x02",
                    media_type="application/octet-stream",
                    final_url="https://example.com/a",
                    title=None,
                ),
            )
        )
    )
    with pytest.raises(ToolCallFailed) as caught:
        await dispatch(box, "fetch_url", url="https://example.com/a")
    assert "读不出正文" in caught.value.message


async def test_cancel_is_never_swallowed() -> None:
    box = toolbox(search=RecordingWebSearch(failure=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await dispatch(box, "web_search", query="导出上限")


# —— 来源：去重与上限 ——


async def test_search_collects_distinct_urls_and_dedupes_repeats() -> None:
    results = (
        draft("https://example.com/a", title="A"),
        draft("https://example.com/a", title="A 再一次"),
        draft("https://example.com/b", title="B"),
    )
    box = toolbox(search=RecordingWebSearch(returns=results))
    await dispatch(box, "web_search", query="导出上限")

    assert [hit.url for hit in box.sources] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert [hit.title for hit in box.sources] == ["A", "B"]


async def test_source_cap_is_reported_not_silently_truncated() -> None:
    results = tuple(
        draft(f"https://example.com/{index}", title=f"T{index}") for index in range(6)
    )
    box = toolbox(search=RecordingWebSearch(returns=results))

    text = await dispatch(box, "web_search", query="导出上限", max_results=8)

    assert len(box.sources) == MAX_RESEARCH_SOURCES
    assert box.overflowed == 1
    assert "另有" in text and "未收" in text


async def test_fetch_after_cap_fails_and_says_why() -> None:
    results = tuple(
        draft(f"https://example.com/{index}", title=f"T{index}") for index in range(5)
    )
    box = toolbox(search=RecordingWebSearch(returns=results))
    await dispatch(box, "web_search", query="导出上限")

    with pytest.raises(ToolCallFailed) as caught:
        await dispatch(box, "fetch_url", url="https://example.com/extra")
    assert str(MAX_RESEARCH_SOURCES) in caught.value.message


async def test_fetching_the_same_url_twice_does_not_duplicate() -> None:
    box = toolbox()
    await dispatch(box, "fetch_url", url="https://example.com/a")
    again = await dispatch(box, "fetch_url", url="https://example.com/a")

    assert len(box.sources) == 1
    assert "已经收过" in again


# —— 出处三件套与文本预算 ——


async def test_fetched_page_gets_research_origin_and_final_url() -> None:
    box = toolbox()
    await dispatch(box, "fetch_url", url="https://example.com/a")

    parsed = box.sources[0].parsed
    assert parsed.origin == "web_research"
    assert parsed.source_ref == "https://example.com/a"
    assert parsed.title == "挖到的页面"


async def test_tool_text_is_capped_and_the_truncation_is_announced() -> None:
    box = toolbox(
        fetcher=RecordingWebFetch(returns=(page("https://example.com/a", text="字" * 20_000),))
    )
    text = await dispatch(box, "fetch_url", url="https://example.com/a")

    assert len(text) <= limits.RESEARCH_TOOL_TEXT_CHARS + 160
    assert "已截断" in text


async def test_source_excerpt_is_capped_at_the_card_width() -> None:
    long_body = "字" * (RESEARCH_SOURCE_CARD_CHARS * 2)
    box = toolbox(
        search=RecordingWebSearch(
            returns=(draft("https://example.com/a", text=long_body, title="长文"),)
        )
    )
    await dispatch(box, "web_search", query="长文")

    hit = box.sources[0]
    card = f"{hit.title}\n{hit.url}\n{hit.excerpt}"
    assert len(card) <= RESEARCH_SOURCE_CARD_CHARS
    assert hit.excerpt.endswith("…")


async def test_a_card_with_no_room_for_a_summary_is_dropped_and_reported() -> None:
    """标题 + 网址就把 800 字吃光时，不给一张空卡：丢掉这条并照实说。"""

    huge_title = "标" * (RESEARCH_SOURCE_CARD_CHARS - 10)
    box = toolbox(
        search=RecordingWebSearch(
            returns=(draft("https://example.com/a", title=huge_title),)
        )
    )
    text = await dispatch(box, "web_search", query="超长标题")

    assert box.sources == ()
    assert "装不进来源卡" in text


async def test_attempts_are_counted_including_failures() -> None:
    box = toolbox(search=RecordingWebSearch(failure=RuntimeError("502")))
    with pytest.raises(ToolCallFailed):
        await dispatch(box, "web_search", query="导出上限")

    assert box.attempts == 1


# —— 材料字节：入库要用，不能在工具里丢 ——


async def test_each_source_keeps_parsed_material_and_bytes() -> None:
    box = toolbox()
    await dispatch(box, "fetch_url", url="https://example.com/a")

    hit = box.sources[0]
    assert hit.parsed.chunks[0].text
    assert hit.content.endswith(b"</html>")


async def test_a_redirected_page_is_recorded_under_its_final_url() -> None:
    """`http`→`https` 这类跳转：收下的是**最终地址**（去重也按它）。"""

    final = "https://example.com/final"
    box = toolbox(
        fetcher=RecordingWebFetch(
            returns=(page("https://example.com/start"),),
        )
    )
    # 替身按 returns 原样返回，所以这里改它的 final_url 模拟跳转
    redirected = page(final)
    box = toolbox(fetcher=RecordingWebFetch(returns=(redirected,)))

    await dispatch(box, "fetch_url", url="https://example.com/start")

    hit = box.sources[0]
    assert hit.url == final
    assert hit.parsed.source_ref == final
    # 请求地址与最终地址都记过：换着写法再抓同一个页面会被挡住
    assert "这条网址已经收过" in await dispatch(
        box, "fetch_url", url=final
    )


async def test_max_results_zero_is_clamped_not_silently_defaulted() -> None:
    """`max_results: 0` 不在 schema 的合法范围里：夹到 1，不当成"没给"（那是 5）。"""

    search = RecordingWebSearch(returns=(draft("https://example.com/a"),))
    box = toolbox(search=search)

    await dispatch(box, "web_search", query="导出上限", max_results=0)

    assert search.calls[0][1] == 1


async def test_card_text_strips_inline_media_and_navigation_lines() -> None:
    """真跑抓到的两张脏卡（2026-10-29）：卡面 800 字不该是 base64 图片或导航行。"""

    body = (
        "![logo](data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMSIgaGVpZ2h0PSIxIj48L3N2Zz4=)\n"
        '* [](/ "网易数帆") * [首页](https://study.sf.163.com/)\n'
        "报告导出有限制：单次最多 100 万行，超过要分批。"
    )
    box = toolbox(
        search=RecordingWebSearch(
            returns=(draft("https://example.com/a", text=body, title="导出上限"),)
        )
    )
    await dispatch(box, "web_search", query="导出上限")

    excerpt = box.sources[0].excerpt
    assert "base64" not in excerpt
    assert "首页" not in excerpt
    assert "单次最多 100 万行" in excerpt
