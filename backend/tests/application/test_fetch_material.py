"""按网址抓材料（`P27` C-2c-4）：入库 / 同址再抓=刷新 / 抓不到就什么都不留。

口径（`P27` 方案 §2·3、§2·4）：
1. 原始字节（抓到的 HTML）**留下来**——重解析、追证都不用再抓一次；
2. 同一个网址再抓 = **刷新**：没被引用过就地换（同一个 material_id），
   发过编号的**另出一份**（旧的冻结留着，编号不改指）；
3. 抓取失败是"这一趟没成"，不是"存了个空的"——**库里一行都不许留**。
"""

import pytest

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.errors import MaterialTooLarge, ToolCallFailed
from pmstudio.application.ports import FetchedPage
from pmstudio.application.use_cases import MAX_MATERIALS_PER_PROJECT
from pmstudio.domain.round.round import MaterialRef, Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import EvidenceId, RoundId
from pmstudio.infrastructure.tools.extract import html_parser_version
from tests.support.builders import PROJECT
from tests.support.doubles import RecordingWebFetch
from tests.support.harness import Harness

URL = "https://example.com/spec"
HTML = (
    "<html><head><title>导出权限</title></head><body>"
    "<p>只有管理员能导出。</p></body></html>"
).encode()


def page(
    *, url: str = URL, text: str = "只有管理员能导出。", content: bytes = HTML
) -> FetchedPage:
    """一次抓取的产出（与真 `HttpxWebFetcher` 同一个形状）。"""

    return FetchedPage(
        parsed=ParsedMaterial(
            origin="web_fetch",
            title="导出权限",
            source_ref=url,
            media_type="text/html",
            bytes_len=len(content),
            chunks=(
                MaterialChunk(
                    chunk_id="bs4-html#c00",
                    ordinal=0,
                    text=text,
                    heading_path=(),
                    anchor="第 1 行",
                ),
            ),
            parser_id="bs4-html",
            parser_version=html_parser_version(),
        ),
        content=content,
    )


def harness_with(*pages: FetchedPage, seeded: bool = True) -> Harness:
    harness = Harness(replies={}, fetcher=RecordingWebFetch(returns=pages))
    if seeded:
        harness.seed_project()
    return harness


def referenced(harness: Harness, material_id: str, *, eid: str = "E4") -> None:
    """让这份材料"发过编号"（冻结判据就是它）。"""

    round_ = Round(
        round_id=RoundId("r1"),
        project_id=PROJECT,
        user_input="按这份写",
        created_at=harness.clock.now(),
        step=Step.DONE,
        waiting_on=WaitingOn.NONE,
    )
    round_.add_materials(
        (
            MaterialRef(
                material_id=material_id,
                chunk_id=f"{material_id}#c00",
                eid=EvidenceId(eid),
            ),
        )
    )
    harness.rounds.save(round_)


async def test_fetch_stores_the_material_and_returns_its_summary():
    harness = harness_with(page())

    summary = await harness.fetch_material()(project_id=PROJECT, url=URL)

    assert harness.fetcher.calls == [URL]
    assert summary.origin == "web_fetch"
    assert summary.title == "导出权限"
    assert summary.source_ref == URL
    assert summary.chunk_count == 1
    assert summary.eids == ()            # 还没发过编号 → 可以删、可以就地重解析
    assert summary.stale is False        # 解析器身份就是当前的
    # 原始字节真留下了（重解析靠它，用户不用重抓）
    assert harness.materials.raw_bytes(summary.material_id) == HTML


async def test_the_same_url_twice_refreshes_in_place_when_unreferenced():
    """网页变了：同一行被更新到最新——id 不变（界面"本轮选了哪几份"不受影响）。"""

    first_page = page()
    second_page = page(text="只有管理员能导出，而且要二次确认。", content=HTML + b"<!-- v2 -->")
    harness = harness_with(first_page, second_page)

    first = await harness.fetch_material()(project_id=PROJECT, url=URL)
    second = await harness.fetch_material()(project_id=PROJECT, url=URL)

    assert second.material_id == first.material_id
    assert len(harness.materials.list_for_project(PROJECT, current=frozenset())) == 1
    stored = harness.materials.get(first.material_id)
    assert stored is not None
    assert "二次确认" in stored.parsed.chunks[0].text
    assert harness.materials.raw_bytes(first.material_id) == HTML + b"<!-- v2 -->"


async def test_the_same_url_twice_is_idempotent_when_nothing_changed():
    harness = harness_with(page(), page())

    first = await harness.fetch_material()(project_id=PROJECT, url=URL)
    second = await harness.fetch_material()(project_id=PROJECT, url=URL)

    assert second.material_id == first.material_id
    assert len(harness.materials.list_for_project(PROJECT, current=frozenset())) == 1


async def test_the_same_url_after_being_referenced_creates_a_second_row():
    """发过编号就冻结：内容变了只能**另出一份**，旧的那些块照旧指着 `E4`。"""

    harness = harness_with(page(), page(text="改过的正文。"))
    first = await harness.fetch_material()(project_id=PROJECT, url=URL)
    referenced(harness, first.material_id)

    second = await harness.fetch_material()(project_id=PROJECT, url=URL)

    assert second.material_id != first.material_id
    assert second.eids == ()
    frozen = harness.materials.get(first.material_id)
    assert frozen is not None and "只有管理员能导出。" in frozen.parsed.chunks[0].text
    assert harness.material_index.material_references(PROJECT)[first.material_id] == ("E4",)


async def test_a_failed_fetch_leaves_nothing_behind():
    harness = Harness(
        replies={},
        fetcher=RecordingWebFetch(
            failure=ToolCallFailed("抓这个网址失败：ConnectTimeout", tool_id="tool.web_fetch")
        ),
    )
    harness.seed_project()

    with pytest.raises(ToolCallFailed):
        await harness.fetch_material()(project_id=PROJECT, url=URL)

    assert harness.materials.count_for_project(PROJECT) == 0


async def test_an_unknown_project_fails_without_storing_anything():
    harness = harness_with(page(), seeded=False)

    with pytest.raises(Exception) as error:
        await harness.fetch_material()(project_id=PROJECT, url=URL)

    assert getattr(error.value, "code", "") == "unknown_project"
    assert harness.materials.count_for_project(PROJECT) == 0


async def test_the_material_limit_is_enforced():
    harness = harness_with(page())
    for index in range(MAX_MATERIALS_PER_PROJECT):
        harness.materials.put(
            project_id=PROJECT,
            parsed=page(url=f"{URL}/{index}").parsed,
            sha256=f"{index:064d}",
            content=b"seeded",
            now=harness.clock.now(),
        )

    with pytest.raises(MaterialTooLarge):
        await harness.fetch_material()(project_id=PROJECT, url=URL)
