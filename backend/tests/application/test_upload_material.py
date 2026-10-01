"""上传/列出材料：解析在事务外、落库在事务内；上限在**读 body 之前**判。"""

import pytest

from pmstudio.application.errors import MaterialTooLarge, UnknownProject, UnsupportedMaterial
from pmstudio.application.use_cases import (
    MAX_MATERIAL_BYTES,
    MAX_MATERIALS_PER_PROJECT,
    ListMaterials,
    UploadMaterial,
)
from pmstudio.domain.shared.ids import ProjectId
from tests.support.builders import PROJECT
from tests.support.harness import Harness


def usecase(harness: Harness) -> UploadMaterial:
    return UploadMaterial(
        documents=harness.documents,
        materials=harness.materials,
        parser=harness.parser,
        clock=harness.clock,
        uow=harness.uow,
    )


def lister(harness: Harness) -> ListMaterials:
    return ListMaterials(
        materials=harness.materials, parser=harness.parser, index=harness.material_index
    )


def seeded() -> Harness:
    harness = Harness(replies={})
    harness.seed_project()
    return harness


async def test_upload_parses_and_lists():
    harness = seeded()

    saved = await usecase(harness)(
        project_id=PROJECT,
        filename="需求.md",
        media_type="text/markdown",
        data="# 目标\n导出要有权限".encode(),
    )

    assert saved.parser_id == "local-text"
    assert saved.chunk_count == 1
    assert saved.chars == len("# 目标\n导出要有权限")
    assert saved.stale is False

    listed = await lister(harness)(project_id=PROJECT)
    assert [item.title for item in listed] == ["需求.md"]


async def test_the_list_says_which_materials_have_been_used():
    """列表带 `eids`：被引用过的写清是哪几个编号，没被用过的空着（P19 的冻结判据）。"""

    harness = seeded()
    used = await usecase(harness)(
        project_id=PROJECT,
        filename="用过的.md",
        media_type="text/markdown",
        data="# 目标\n导出要有权限".encode(),
    )
    unused = await usecase(harness)(
        project_id=PROJECT,
        filename="没用的.md",
        media_type="text/markdown",
        data="# 边界\n只给管理员".encode(),
    )

    # 让"用过的"那份进一轮：直接往轮次里记一条材料引用（真相源就是它）
    from pmstudio.domain.round.round import MaterialRef, Round
    from pmstudio.domain.round.step import Step, WaitingOn
    from pmstudio.domain.shared.ids import EvidenceId, RoundId

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
                material_id=used.material_id,
                chunk_id=f"{used.material_id}#c00",
                eid=EvidenceId("E4"),
            ),
        )
    )
    harness.rounds.save(round_)

    listed = {item.material_id: item for item in await lister(harness)(project_id=PROJECT)}

    assert listed[used.material_id].eids == ("E4",)
    assert listed[unused.material_id].eids == ()


async def test_oversize_is_refused_before_parsing():
    harness = seeded()

    with pytest.raises(MaterialTooLarge):
        await usecase(harness)(
            project_id=PROJECT,
            filename="大.pdf",
            media_type="application/pdf",
            data=b"x" * (MAX_MATERIAL_BYTES + 1),
        )

    assert await lister(harness)(project_id=PROJECT) == ()


async def test_unsupported_type_is_a_clear_error():
    harness = seeded()

    with pytest.raises(UnsupportedMaterial):
        await usecase(harness)(
            project_id=PROJECT,
            filename="scan.jpg",
            media_type="image/jpeg",
            data=b"\x89PNG",
        )


async def test_uploading_to_an_unknown_project_is_refused():
    harness = seeded()

    with pytest.raises(UnknownProject):
        await usecase(harness)(
            project_id=ProjectId("没这个项目"),
            filename="a.md",
            media_type="text/markdown",
            data="一条".encode(),
        )


async def test_the_per_project_cap_is_enforced():
    harness = seeded()
    upload = usecase(harness)

    for number in range(MAX_MATERIALS_PER_PROJECT):
        await upload(
            project_id=PROJECT,
            filename=f"{number}.md",
            media_type="text/markdown",
            data=f"第 {number} 份".encode(),
        )

    with pytest.raises(MaterialTooLarge):
        await upload(
            project_id=PROJECT,
            filename="再来一份.md",
            media_type="text/markdown",
            data="最后一篇".encode(),
        )


async def test_the_same_bytes_uploaded_twice_do_not_take_two_slots():
    harness = seeded()
    upload = usecase(harness)
    payload = "同一份材料".encode()

    first = await upload(
        project_id=PROJECT, filename="a.md", media_type="text/markdown", data=payload
    )
    second = await upload(
        project_id=PROJECT, filename="b.md", media_type="text/markdown", data=payload
    )

    assert second.material_id == first.material_id
    assert len(await lister(harness)(project_id=PROJECT)) == 1
