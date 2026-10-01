"""删除材料（`P19` C-2c-2）：**编号发出即冻结**。

三条口径：
1. 没被任何一轮引用过 → 可以删（块与原始字节一起走，不可恢复）；
2. 被引用过 → `MaterialFrozen`（HTTP 409），消息里列出是哪几个编号——删了编号就指向空气；
3. 不存在的 id / 别的项目的 id → `UnknownMaterial`（404），不是"形状错"。
"""

import pytest

from pmstudio.application.errors import MaterialFrozen, UnknownMaterial
from pmstudio.domain.round.round import MaterialRef, Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, RoundId
from tests.support.builders import PROJECT
from tests.support.harness import Harness

OTHER = ProjectId("p2")


async def upload(harness: Harness, *, filename: str, text: str):  # type: ignore[no-untyped-def]
    return await harness.upload_material()(
        project_id=PROJECT,
        filename=filename,
        media_type="text/markdown",
        data=text.encode(),
    )


def used(harness: Harness, material_id: str, *, eid: str = "E4") -> None:
    """让这份材料"进过某一轮"——真相源就是轮次里的材料引用。"""

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


async def test_a_material_that_was_never_used_can_be_deleted():
    harness = Harness(replies={})
    harness.seed_project()
    saved = await upload(harness, filename="没用的.md", text="# 边界\n只给管理员")

    listed = await harness.delete_material()(project_id=PROJECT, material_id=saved.material_id)

    assert listed == ()                                   # 返回刷新后的列表（空了）
    assert harness.materials.get(saved.material_id) is None
    assert harness.materials.raw_bytes(saved.material_id) is None
    assert harness.materials.chunk_count(saved.material_id) == 0


async def test_the_list_comes_back_without_the_deleted_one():
    harness = Harness(replies={})
    harness.seed_project()
    keep = await upload(harness, filename="留着.md", text="# 目标\n导出")
    drop = await upload(harness, filename="删掉.md", text="# 边界\n只给管理员")

    listed = await harness.delete_material()(project_id=PROJECT, material_id=drop.material_id)

    assert [item.material_id for item in listed] == [keep.material_id]


async def test_a_material_with_numbers_is_frozen():
    harness = Harness(replies={})
    harness.seed_project()
    saved = await upload(harness, filename="用过的.md", text="# 目标\n导出")
    used(harness, saved.material_id, eid="E4")

    with pytest.raises(MaterialFrozen) as error:
        await harness.delete_material()(project_id=PROJECT, material_id=saved.material_id)

    assert "E4" in error.value.message
    assert error.value.code == "material_frozen"
    assert harness.materials.get(saved.material_id) is not None       # 原样还在


async def test_deleting_something_that_is_not_there_is_a_404():
    harness = Harness(replies={})
    harness.seed_project()

    with pytest.raises(UnknownMaterial):
        await harness.delete_material()(project_id=PROJECT, material_id="m-不存在")


async def test_a_material_of_another_project_is_not_deletable_from_here():
    """串项目删除 = 删别人的东西：按"资源不在"处理（404），不是"状态不允许"。"""

    harness = Harness(replies={})
    harness.seed_project()
    # 别家的那份直接用解析器读一份、存进别的项目（不走上传用例：那要求项目先存在）
    draft = harness.parser.parse(
        filename="别家的.md", media_type="text/markdown", data="# 别家".encode()
    )
    foreign = harness.materials.put(
        project_id=OTHER,
        parsed=draft,
        sha256="f" * 64,
        content="# 别家".encode(),
        now=harness.clock.now(),
    )

    with pytest.raises(UnknownMaterial):
        await harness.delete_material()(project_id=PROJECT, material_id=foreign.material_id)

    assert harness.materials.get(foreign.material_id) is not None
