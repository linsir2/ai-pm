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
    return ListMaterials(materials=harness.materials, parser=harness.parser)


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
