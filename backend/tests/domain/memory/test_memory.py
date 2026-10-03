"""跨轮记忆（`ProjectMemory`）：容量、出处、点名、只增版本、只读投影。

口径：`docs/plans/2026-10-03-M6-跨轮记忆本体.md` §5 / §6.1 / §9（D1–D6）。
这个聚合是**唯一真相源里新增的那一块**（教训 / 已定口径 / 明确否掉），
所以不变量都在这里一次实现，别处不许再判一遍。
"""

from datetime import datetime

import pytest

from pmstudio.domain.memory.memory import (
    MEMORY_CAPACITY,
    MemoryChange,
    MemoryEntry,
    MemoryKind,
    MemoryOp,
    ProjectMemory,
    screen_changes,
)
from pmstudio.domain.shared.errors import (
    MemoryCapacityExceeded,
    UnknownMemoryEntry,
    UntraceableMemory,
)
from pmstudio.domain.shared.ids import EvidenceId, ProjectId

NOW = datetime(2026, 10, 3, 20, 0, 0)
LATER = datetime(2026, 10, 3, 21, 0, 0)
PROJECT = ProjectId("p-memory")
KNOWN = frozenset({"E1", "E2", "E3"})


class SeqIds:
    """测试里的 id 发生器（prefix-N），与真实现 `UuidIdGenerator` 同形状。"""

    def __init__(self) -> None:
        self._seq: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        self._seq[prefix] = self._seq.get(prefix, 0) + 1
        return f"{prefix}-{self._seq[prefix]}"


def add(text: str, *, refs: tuple[str, ...] = ("E1",), kind: MemoryKind = MemoryKind.LESSON):
    return MemoryChange(op=MemoryOp.ADD, kind=kind, text=text, refs=refs, reason="测试")


def memory_with(count: int) -> ProjectMemory:
    """造一份已经装满 `count` 条的记忆（不经过 apply：只读夹具）。"""

    entries = tuple(
        MemoryEntry(
            memory_id=f"mem-{index}",
            kind=MemoryKind.DECISION,
            text=f"第 {index} 条已经定过的口径",
            refs=(EvidenceId("E1"),),
            round_id="r-1",
            created_at=NOW,
            updated_at=NOW,
        )
        for index in range(1, count + 1)
    )
    return ProjectMemory(project_id=PROJECT, entries=entries)


def test_a_memory_entry_must_carry_at_least_one_existing_e_ref():
    no_refs = add("没有出处的教训", refs=())
    unknown = add("出处是编的", refs=("E99",))
    good = add("这条是好的")

    outcome = screen_changes(
        (no_refs, unknown, good), memory=ProjectMemory.empty(PROJECT), known_refs=KNOWN
    )

    assert [item.text for item in outcome.accepted] == ["这条是好的"]
    assert [drop.reason for drop in outcome.dropped] == [
        "记忆条目必须带至少一个出处编号",
        "出处编号不在账本里：E99",
    ]


def test_apply_refuses_a_change_without_a_real_reference():
    with pytest.raises(UntraceableMemory):
        ProjectMemory.empty(PROJECT).apply(
            (add("凭空来的口径", refs=("E99",)),),
            round_id="r-1",
            known_refs=KNOWN,
            now=NOW,
            ids=SeqIds(),
        )


def test_memory_capacity_is_sixty_and_overflow_needs_a_merge_or_delete():
    full = memory_with(MEMORY_CAPACITY)
    overflow = (add("第 61 条"),)

    refused = screen_changes(overflow, memory=full, known_refs=KNOWN)
    assert refused.accepted == ()
    assert refused.dropped[0].reason.startswith(f"记忆最多 {MEMORY_CAPACITY} 条")

    with pytest.raises(MemoryCapacityExceeded):
        full.apply(overflow, round_id="r-1", known_refs=KNOWN, now=NOW, ids=SeqIds())

    freeing = (
        MemoryChange(
            op=MemoryOp.DELETE, kind=MemoryKind.DECISION, text="", target_id="mem-1"
        ),
        add("第 61 条"),
    )
    assert len(screen_changes(freeing, memory=full, known_refs=KNOWN).accepted) == 2

    after = full.apply(freeing, round_id="r-1", known_refs=KNOWN, now=NOW, ids=SeqIds())
    assert len(after.entries) == MEMORY_CAPACITY
    assert after.entries[-1].text == "第 61 条"
    # 被删掉的是**那一条内容**（不能按 id 判：夹具的 id 与 `ids.new()` 造出来的会撞名）
    assert "第 1 条已经定过的口径" not in [item.text for item in after.entries]


def test_update_and_delete_must_name_an_existing_entry():
    change = MemoryChange(
        op=MemoryOp.UPDATE,
        kind=MemoryKind.LESSON,
        text="改一下",
        refs=("E1",),
        target_id="mem-404",
    )

    with pytest.raises(UnknownMemoryEntry):
        memory_with(2).apply(
            (change,), round_id="r-1", known_refs=KNOWN, now=NOW, ids=SeqIds()
        )

    outcome = screen_changes((change,), memory=memory_with(2), known_refs=KNOWN)
    assert outcome.accepted == ()
    assert outcome.dropped[0].reason == "点名的记忆条目不存在：mem-404"


def test_a_merge_rewrites_the_target_entry_and_keeps_its_identity():
    base = ProjectMemory.empty(PROJECT).apply(
        (add("席位必须给依据"),), round_id="r-1", known_refs=KNOWN, now=NOW, ids=SeqIds()
    )
    target = base.entries[0].memory_id

    merged = base.apply(
        (
            MemoryChange(
                op=MemoryOp.MERGE,
                kind=MemoryKind.LESSON,
                text="失败必须显式声明（合并了三条同类教训）",
                refs=("E2",),
                target_id=target,
            ),
        ),
        round_id="r-2",
        known_refs=KNOWN,
        now=LATER,
        ids=SeqIds(),
    )

    assert len(merged.entries) == 1
    assert merged.entries[0].memory_id == target
    assert merged.entries[0].text == "失败必须显式声明（合并了三条同类教训）"
    assert merged.entries[0].refs == (EvidenceId("E2"),)
    assert merged.entries[0].updated_at == LATER
    assert merged.entries[0].created_at == NOW


def test_only_applied_changes_move_entries():
    empty = ProjectMemory.empty(PROJECT)
    outcome = screen_changes((add("一条提议"),), memory=empty, known_refs=KNOWN)

    assert len(outcome.accepted) == 1
    assert empty.entries == ()

    applied = empty.apply(
        outcome.accepted, round_id="r-1", known_refs=KNOWN, now=NOW, ids=SeqIds()
    )
    assert [item.text for item in applied.entries] == ["一条提议"]
    assert empty.entries == ()


def test_revisions_are_append_only_and_carry_the_changes():
    first = ProjectMemory.empty(PROJECT).apply(
        (add("第一条"),), round_id="r-1", known_refs=KNOWN, now=NOW, ids=SeqIds()
    )
    second = first.apply(
        (
            MemoryChange(
                op=MemoryOp.DELETE,
                kind=MemoryKind.LESSON,
                text="",
                target_id=first.entries[0].memory_id,
            ),
        ),
        round_id="r-2",
        known_refs=KNOWN,
        now=LATER,
        ids=SeqIds(),
    )

    assert [item.revision_no for item in second.revisions] == [1, 2]
    assert [item.round_id for item in second.revisions] == ["r-1", "r-2"]
    assert second.revisions[0].changes[0].text == "第一条"
    assert second.revisions[0].created_at == NOW
    assert second.revisions[1].changes[0].op is MemoryOp.DELETE
    assert second.entries == ()


def test_an_empty_memory_has_no_entries_and_no_revisions():
    empty = ProjectMemory.empty(PROJECT)

    assert empty.entries == ()
    assert empty.revisions == ()
    assert empty.project_id == PROJECT


def test_an_entry_longer_than_two_lines_is_refused():
    change = add("第一行\n第二行\n第三行")

    outcome = screen_changes((change,), memory=ProjectMemory.empty(PROJECT), known_refs=KNOWN)

    assert outcome.accepted == ()
    assert outcome.dropped[0].reason == "记忆条目最多两行"
