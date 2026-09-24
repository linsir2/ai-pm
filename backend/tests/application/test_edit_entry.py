"""手改文档（F17）：改 / 加 / 删各产生一版，手改内容有自己的出处，改过的块 AI 不能再动。

这条能力的设计依据在 `04-未决.md` D1（手改内容如何获得出处）与 `01-规格.md` §5
（"卡片改的与文档改的谁优先"）。
"""

import pytest

from pmstudio.application.events import DOC_CHANGED
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.provenance.evidence import EvidenceKind
from pmstudio.domain.shared.errors import InvariantViolation, ShapeError
from pmstudio.domain.shared.ids import BlockId, EntryId, EvidenceId, ProjectId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from tests.support.builders import NOW, PROJECT, document_with_feature
from tests.support.harness import Harness


@pytest.fixture()
def world():
    """一个已经有一个功能、一条「一句话描述」的项目。"""

    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()  # 先铺空骨架（它会覆盖文档）
    harness.documents.seed(document_with_feature(one_liner="把订单导出成表格"))
    return harness


def block_with_field(document, block_field: str):
    """按字段名找出那个块（手改就是改它的条目）。"""

    for block in document.blocks:
        if block.field == block_field:
            return block
    raise AssertionError(f"没有这个字段块：{block_field}")


async def test_replace_writes_a_manual_version_with_its_own_evidence(world):
    before = world.documents.get(PROJECT)
    entry = block_with_field(before, "one_liner").entries[0]

    result = await world.edit_entry()(
        project_id=ProjectId(PROJECT),
        op=WriteOp.REPLACE,
        entry_id=str(entry.entry_id),
        text="把订单按时间筛出来导出成表格",
    )

    assert result.version_no == before.current_version.version_no + 1
    assert result.written_block_ids

    after = world.documents.get(PROJECT)
    assert after.current_version.trigger.value == "manual"
    assert after.current_version.summary.startswith("手工改一条")

    manual_block = block_with_field(after, "one_liner")
    block_id, updated = manual_block.block_id, manual_block.entries[0]
    assert updated.text == "把订单按时间筛出来导出成表格"
    # 原来的出处留着，再加上"你自己改的"那一条 → 新旧都能查
    assert str(entry.refs[0]) in [str(item) for item in updated.refs]
    assert len(updated.refs) == len(entry.refs) + 1
    manual_eid = updated.refs[-1]
    ledger = world.evidences.get(PROJECT)
    assert ledger.get(manual_eid).kind is EvidenceKind.MANUAL
    assert ledger.get(manual_eid).text.startswith("手工改写")
    # 改过的块打上标记：AI 之后不许静默覆盖（D5）
    assert [item.block_id for item in after.blocks if item.manually_edited] == [block_id]


async def test_add_appends_to_a_multi_value_field_and_to_the_preference_area(world):
    before = world.documents.get(PROJECT)
    existing = world.documents.get(PROJECT)
    # 先给这个功能补一个多值字段（清单），再往里面加一条
    existing.apply(
        [
            WriteInstruction(
                instruction_id="seed-exceptions",
                entry_id=EntryId("en-exc-1"),
                op=WriteOp.ADD,
                target=Target.feature_field("order-export", "exceptions"),
                text="无权限的用户看不到导出入口",
                refs=(EvidenceId("E1"),),
            )
        ],
        now=NOW,
        summary="铺一条多值字段",
        authorized_new_features=frozenset({"order-export"}),
    )
    world.documents.seed(existing)
    block_id = block_with_field(world.documents.get(PROJECT), "exceptions").block_id

    result = await world.edit_entry()(
        project_id=ProjectId(PROJECT),
        op=WriteOp.ADD,
        block_id=str(block_id),
        text="导出失败要给一句人话原因",
    )

    after = world.documents.get(PROJECT)
    assert result.version_no == after.current_version.version_no
    assert [entry.text for entry in block_with_field(after, "exceptions").entries] == [
        "无权限的用户看不到导出入口",
        "导出失败要给一句人话原因",
    ]
    assert after.current_version.version_no == before.current_version.version_no + 2

    # 偏好区也能手加（它不是块，是一条独立的条目）
    added = await world.edit_entry()(
        project_id=ProjectId(PROJECT),
        op=WriteOp.ADD,
        block_id="preference-area",
        text="表格里的金额要千分位",
    )
    final = world.documents.get(PROJECT)
    assert final.preference_entries[-1].text == "表格里的金额要千分位"
    assert added.version_no == final.current_version.version_no


async def test_remove_deletes_one_entry_and_keeps_the_tombstone_worth_of_a_version(world):
    before = world.documents.get(PROJECT)
    entry = block_with_field(before, "one_liner").entries[0]

    await world.edit_entry()(
        project_id=ProjectId(PROJECT),
        op=WriteOp.REMOVE,
        entry_id=str(entry.entry_id),
    )

    after = world.documents.get(PROJECT)
    assert after.current_version.version_no == before.current_version.version_no + 1
    # 旧版本里那条还在（版本只增不改），当前版本里没了
    assert after.version(before.current_version.version_no).snapshot.blocks
    assert all(
        stored.entry(entry.entry_id) is None
        for block in after.blocks
        for stored in block.entries
    ) or all(block.entry(entry.entry_id) is None for block in after.blocks)
    ledger = world.evidences.get(PROJECT)
    assert any(
        item.kind is EvidenceKind.MANUAL and item.text.startswith("手工删除")
        for item in ledger.evidences()
    )


async def test_add_to_an_empty_chapter_opens_the_first_block(world):
    """空章节也能手写第一条：`block_id` 给章节 id，就在这一章新开一段。

    这不是边角情况——新项目 13 章全是空的，用户想自己动手时，入口就在这儿。
    """

    before = world.documents.get(PROJECT)

    result = await world.edit_entry()(
        project_id=ProjectId(PROJECT),
        op=WriteOp.ADD,
        block_id="s2",
        text="现在的问题是需求散在聊天记录里，改一次要重讲一遍",
    )

    after = world.documents.get(PROJECT)
    assert result.version_no == before.current_version.version_no + 1
    created = [block for block in after.blocks if block.parent_id == BlockId("s2")]
    assert len(created) == 1
    assert created[0].entries[0].text.startswith("现在的问题是需求散在聊天记录里")
    # 手写的那一块留痕：AI 之后不许静默改写它
    assert created[0].manually_edited is True


async def test_manual_edit_publishes_doc_changed(world):
    before = world.documents.get(PROJECT)
    entry = block_with_field(before, "one_liner").entries[0]

    await world.edit_entry()(
        project_id=ProjectId(PROJECT),
        op=WriteOp.REPLACE,
        entry_id=str(entry.entry_id),
        text="改一下",
    )

    events = [item for item in world.events.published if item.name == DOC_CHANGED]
    assert len(events) == 1
    assert events[0].payload["version_no"] == before.current_version.version_no + 1


async def test_unknown_entry_and_missing_target_are_shape_errors(world):
    with pytest.raises(ShapeError):
        await world.edit_entry()(
            project_id=ProjectId(PROJECT), op=WriteOp.REPLACE, entry_id="en-nope", text="x"
        )
    with pytest.raises(ShapeError):
        await world.edit_entry()(project_id=ProjectId(PROJECT), op=WriteOp.ADD, text="x")

    # 失败的那两次没有留下版本
    assert world.documents.get(PROJECT).current_version.version_no == 2  # 播种时的 v2


async def test_ai_cannot_overwrite_a_hand_edited_block(world):
    """手改过的块，AI 的改写会被 D5 拦下来（这条是"谁优先"的机械保证）。"""

    before = world.documents.get(PROJECT)
    entry = block_with_field(before, "one_liner").entries[0]
    await world.edit_entry()(
        project_id=ProjectId(PROJECT),
        op=WriteOp.REPLACE,
        entry_id=str(entry.entry_id),
        text="我亲手写的",
    )

    with pytest.raises(InvariantViolation) as caught:
        document = world.documents.get(PROJECT)
        document.apply(
            [
                WriteInstruction(
                    instruction_id="ai-tries",
                    entry_id=entry.entry_id,
                    op=WriteOp.REPLACE,
                    target=Target.feature_field("order-export", "one_liner"),
                    text="AI 想覆盖你写的那句",
                    refs=(EvidenceId("E1"),),
                )
            ],
            now=NOW,
            summary="AI 试图覆盖",
        )
    assert caught.value.invariant == "D5"


async def test_you_can_keep_editing_your_own_text(world):
    """D5 拦的是"静默覆盖"，不是"人不许再改"：同一块可以改了再改、改了再删。"""

    plan = world.documents.get(PROJECT)
    entry = block_with_field(plan, "one_liner").entries[0]
    first = await world.edit_entry()(
        project_id=ProjectId(PROJECT),
        op=WriteOp.REPLACE,
        entry_id=str(entry.entry_id),
        text="第一稿",
    )
    second = await world.edit_entry()(
        project_id=ProjectId(PROJECT),
        op=WriteOp.REPLACE,
        entry_id=str(entry.entry_id),
        text="第二稿",
    )
    assert second.version_no == first.version_no + 1
    assert block_with_field(world.documents.get(PROJECT), "one_liner").entries[0].text == "第二稿"

    removed = await world.edit_entry()(
        project_id=ProjectId(PROJECT),
        op=WriteOp.REMOVE,
        entry_id=str(entry.entry_id),
    )
    assert removed.version_no == second.version_no + 1
    assert block_with_field(world.documents.get(PROJECT), "one_liner").entries == ()
