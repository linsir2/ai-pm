"""重解析材料（`P19` C-2c-2）：**没被引用就就地重读；被引用就另出一份**。

为什么要有第二条路：被引用过的材料里那些块挂着 `E7`、`E12`——就地改形状会让编号改指，
删又不许删，而"重新上传同一份文件"会被去重键挡回旧那份。**另出一份**是唯一不破坏证据链的出路。
"""

import pytest

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.errors import UnknownMaterial, UnsupportedMaterial
from pmstudio.domain.round.round import MaterialRef, Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import EvidenceId, RoundId
from tests.support.builders import PROJECT
from tests.support.harness import Harness

RAW = "# 目标\n导出要有权限\n# 边界\n只给管理员\n"


def old_parse(
    *, title: str = "报告.md", origin: str = "upload", media_type: str = "text/markdown"
) -> ParsedMaterial:
    """模拟"库里那份是旧解析器读的"：一个块、锚点是旧的、版本号是旧的。"""

    return ParsedMaterial(
        origin=origin,
        title=title,
        source_ref=f"attachment:{title}",
        media_type=media_type,
        bytes_len=len(RAW.encode()),
        chunks=(
            MaterialChunk(
                chunk_id="local-text#c00",
                ordinal=0,
                text=RAW,
                heading_path=(),
                anchor="旧锚点",
            ),
        ),
        parser_id="local-text",
        parser_version="v0-旧解析器",
    )


def seeded(*, origin: str = "upload", sha: str = "a" * 64):  # type: ignore[no-untyped-def]
    harness = Harness(replies={})
    harness.seed_project()
    return harness.materials.put(
        project_id=PROJECT,
        parsed=old_parse(origin=origin),
        sha256=sha,
        content=RAW.encode(),
        now=harness.clock.now(),
    ), harness


def used(harness: Harness, material_id: str, *, eid: str = "E4") -> None:
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


async def test_an_untouched_material_is_reparsed_in_place():
    packet, harness = seeded()

    listed = await harness.reparse_material()(
        project_id=PROJECT, material_id=packet.material_id
    )

    again = harness.materials.get(packet.material_id)
    assert again is not None
    assert again.parsed.parser_version != "v0-旧解析器"          # 换成当前解析器读的了
    assert len(again.parsed.chunks) > 1                          # 块按新读法重建
    assert again.parsed.chunks[0].anchor != "旧锚点"
    assert harness.materials.raw_bytes(packet.material_id) == RAW.encode()   # 原始字节一字没动
    assert [item.material_id for item in listed] == [packet.material_id]     # 还是那一行
    assert listed[0].stale is False


async def test_a_frozen_material_gets_a_second_copy_instead():
    packet, harness = seeded()
    used(harness, packet.material_id, eid="E4")

    listed = await harness.reparse_material()(
        project_id=PROJECT, material_id=packet.material_id
    )

    assert len(listed) == 2
    frozen = next(item for item in listed if item.material_id == packet.material_id)
    fresh = next(item for item in listed if item.material_id != packet.material_id)
    assert frozen.eids == ("E4",)                     # 旧的冻结：编号还在它身上
    assert frozen.parser_version == "v0-旧解析器"     # 旧的形状一个字节都没改
    assert fresh.eids == ()                           # 新的那份还没被用过
    assert fresh.parser_version != "v0-旧解析器"
    assert harness.materials.get(packet.material_id).parsed.chunks[0].anchor == "旧锚点"


async def test_reparsing_twice_does_not_make_a_third_copy():
    packet, harness = seeded()
    used(harness, packet.material_id)

    first = await harness.reparse_material()(
        project_id=PROJECT, material_id=packet.material_id
    )
    second = await harness.reparse_material()(
        project_id=PROJECT, material_id=packet.material_id
    )

    assert len(first) == len(second) == 2      # 变体键幂等：不会生出第三份


async def test_a_web_result_is_not_reparsable():
    """联网搜回来的是"当时的网页快照"，不是文件：要新的就重新搜。"""

    packet, harness = seeded(origin="web_search", sha="b" * 64)

    with pytest.raises(UnsupportedMaterial) as error:
        await harness.reparse_material()(
            project_id=PROJECT, material_id=packet.material_id
        )

    assert "联网" in error.value.message
    assert harness.materials.get(packet.material_id).parsed.parser_version == "v0-旧解析器"


async def test_a_fetched_page_is_reparsable():
    """抓来的网页（`P27`）**可以重解析**：留下的原始字节就是那次的 HTML——

    与联网搜回来的"快照"（上一条）刻意不同：那份的字节是摘要拼的，重读没有意义；
    这份的字节是真 HTML，换了抽取器（docling / 更强的正文抽取）能重读出新结果。
    """

    harness = Harness(replies={})
    harness.seed_project()
    html = (
        b"<html><head><title>Example</title></head>"
        b"<body><p>\xe5\x85\x88\xe7\xbb\x99\xe4\xbd\xa0\xe7\x9c\x8b</p></body></html>"
    )
    packet = harness.materials.put(
        project_id=PROJECT,
        parsed=old_parse(title="Example Domain", origin="web_fetch", media_type="text/html"),
        sha256="d" * 64,
        content=html,
        now=harness.clock.now(),
    )

    listed = await harness.reparse_material()(
        project_id=PROJECT, material_id=packet.material_id
    )

    reparsed = next(item for item in listed if item.material_id == packet.material_id)
    assert reparsed.parser_id == "bs4-html"          # 真按 HTML 那一支重读
    assert reparsed.parser_version != "v0-旧解析器"


async def test_an_unreadable_material_reports_it_instead_of_writing_garbage():
    harness = Harness(replies={})
    harness.seed_project()
    broken = harness.materials.put(
        project_id=PROJECT,
        parsed=old_parse(title="半截.pdf", media_type="application/pdf"),
        sha256="c" * 64,
        content=b"%PDF-1.4\n% broken",          # 抽不出字
        now=harness.clock.now(),
    )

    with pytest.raises(UnsupportedMaterial):
        await harness.reparse_material()(project_id=PROJECT, material_id=broken.material_id)

    kept = harness.materials.get(broken.material_id)
    assert kept.parsed.parser_version == "v0-旧解析器"     # 库里原样不动


async def test_reparsing_something_that_is_not_there_is_a_404():
    _, harness = seeded()

    with pytest.raises(UnknownMaterial):
        await harness.reparse_material()(project_id=PROJECT, material_id="m-不存在")
