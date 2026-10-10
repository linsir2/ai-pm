"""按网址抓材料（`P27` C-2c-4 + `02 §11.0.53`）：入库 / 同址再抓=刷新 / 解析交给真解析器。

口径：
1. 原始字节（抓到的 HTML）**留下来**——重解析、追证都不用再抓一次；
2. 同一个网址再抓 = **刷新**：没被引用过就地换（同一个 material_id），
   发过编号的**另出一份**（旧的冻结留着，编号不改指）；
3. 抓取失败是"这一趟没成"，不是"存了个空的"——**库里一行都不许留**；
4. **解析不在 fetcher 里做**：用例拿到字节后调 `MaterialParser`，于是抓 PDF / Word /
   Excel 与上传它们走同一条真实分派（切块、上限、"抽不出就说抽不出"全在同一条路上）。
   替身因此只给"字节 + 媒体类型 + 最终地址 + 标题"，**不给解析结果**。
"""

import pytest

from pmstudio.application.errors import MaterialTooLarge, UnsupportedMaterial
from pmstudio.application.ports import FetchedPage
from pmstudio.application.use_cases import MAX_MATERIALS_PER_PROJECT
from pmstudio.domain.round.round import MaterialRef, Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import EvidenceId, RoundId
from pmstudio.harness.errors import ToolCallFailed
from tests.infrastructure.tools.test_extract import minimal_pdf
from tests.support.builders import PROJECT
from tests.support.doubles import RecordingWebFetch
from tests.support.harness import Harness

URL = "https://example.com/spec"
HTML = (
    "<html><head><title>导出权限</title></head><body>"
    "<p>只有管理员能导出。</p></body></html>"
).encode()


def page(
    *,
    url: str = URL,
    title: str | None = "导出权限",
    content: bytes = HTML,
    media_type: str = "text/html",
) -> FetchedPage:
    """一次抓取的产出（与真 `HttpxWebFetcher` 同一个形状：字节 + 元信息，**不含解析结果**）。"""

    return FetchedPage(
        content=content,
        media_type=media_type,
        final_url=url,
        title=title,
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
    second_page = page(content=HTML + "<p>二次确认那句</p>".encode())
    harness = harness_with(first_page, second_page)

    first = await harness.fetch_material()(project_id=PROJECT, url=URL)
    second = await harness.fetch_material()(project_id=PROJECT, url=URL)

    assert second.material_id == first.material_id
    assert len(harness.materials.list_for_project(PROJECT, current=frozenset())) == 1
    stored = harness.materials.get(first.material_id)
    assert stored is not None
    assert "二次确认" in stored.parsed.chunks[0].text
    assert harness.materials.raw_bytes(first.material_id) == HTML + "<p>二次确认那句</p>".encode()


async def test_the_same_url_twice_is_idempotent_when_nothing_changed():
    harness = harness_with(page(), page())

    first = await harness.fetch_material()(project_id=PROJECT, url=URL)
    second = await harness.fetch_material()(project_id=PROJECT, url=URL)

    assert second.material_id == first.material_id
    assert len(harness.materials.list_for_project(PROJECT, current=frozenset())) == 1


async def test_the_same_url_after_being_referenced_creates_a_second_row():
    """发过编号就冻结：内容变了只能**另出一份**，旧的那些块照旧指着 `E4`。"""

    harness = harness_with(
        page(), page(content=HTML + "<p>改过的正文。</p>".encode())
    )
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
            parsed=harness.parser.parse(
                filename=f"{index}.txt", media_type="text/plain", data=b"seeded"
            ),
            sha256=f"{index:064d}",
            content=b"seeded",
            now=harness.clock.now(),
        )

    with pytest.raises(MaterialTooLarge):
        await harness.fetch_material()(project_id=PROJECT, url=URL)


# ── 解析交给 MaterialParser 之后长什么样（`02 §11.0.53`）──────────────────
#
# 这几条是从 `tests/infrastructure/tools/test_fetch.py` 搬过来的：fetcher 不再解析，
# 于是"抓到的正文变成哪些块、锚点是什么"的断言只在这里成立——这里才走得到真解析器。


async def test_an_html_page_is_parsed_into_chunks_with_anchors():
    harness = harness_with(page())

    summary = await harness.fetch_material()(project_id=PROJECT, url=URL)
    stored = harness.materials.get(summary.material_id)

    # 这一页没有标题行，所以标题与正文同处一块；`<title>` 进正文第一行这条
    # 在 `test_extract.py` 里另有专测（那儿有标题行可切）
    assert stored.parsed.chunks[0].text.startswith("导出权限\n")
    assert "只有管理员能导出。" in stored.parsed.chunks[0].text
    assert stored.parsed.chunks[0].anchor == "第 1–2 行"   # 标题 + 正文两行


async def test_a_pdf_url_is_parsed_by_the_pdf_path():
    """PDF 网址：走 `extract._from_pdf`——按页切块、锚点写第几页、身份是 pdfminer。

    这正是把解析交回 `MaterialParser` 换来的：上传 `.pdf` 与抓来的 `.pdf`
    从此是同一条路、同一批上限、同一句"抽不出就说抽不出"。
    """

    harness = harness_with(
        page(
            url="https://example.com/spec.pdf",
            title=None,
            media_type="application/pdf",
            content=minimal_pdf("Hello export"),
        )
    )

    await harness.fetch_material()(project_id=PROJECT, url="https://example.com/spec.pdf")

    stored = harness.materials.get(
        harness.materials.list_for_project(PROJECT, current=frozenset())[0].material_id
    )
    assert stored.parsed.parser_id == "pdfminer"
    assert "Hello export" in stored.parsed.chunks[0].text
    assert stored.parsed.chunks[0].anchor.startswith("第 1 页")


async def test_a_pdf_url_without_extractable_text_is_refused():
    harness = harness_with(
        page(
            url="https://example.com/scan.pdf",
            title=None,
            media_type="application/pdf",
            content=minimal_pdf("   "),
        )
    )

    with pytest.raises(UnsupportedMaterial) as error:
        await harness.fetch_material()(project_id=PROJECT, url="https://example.com/scan.pdf")

    assert "没有可抽取的文字" in str(error.value)
    assert harness.materials.count_for_project(PROJECT) == 0


async def test_a_page_with_nothing_but_chrome_is_refused():
    """整页只有导航 → 抽不出正文 → 明确拒绝，库里一行不留（报错还带网址）。"""

    harness = harness_with(
        page(
            content=("<html><head><title>空</title></head><body>"
                     "<nav>首页 产品 文档</nav></body></html>").encode()
        )
    )

    with pytest.raises(UnsupportedMaterial) as error:
        await harness.fetch_material()(project_id=PROJECT, url=URL)

    assert "读不出正文" in str(error.value)
    assert URL in str(error.value)          # 说的是哪个链接，不是"哪份文件"
    assert harness.materials.count_for_project(PROJECT) == 0
