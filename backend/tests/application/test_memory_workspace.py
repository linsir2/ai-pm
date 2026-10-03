"""记忆进工作台投影（M6a）：`WorkspaceView.memory` 是**只读**的那一份。

口径：`docs/plans/2026-10-03-M6-跨轮记忆本体.md` §6.2（11）/ §8。老项目没有记忆 = 空元组，
不是 `None`，也不许让工作台打不开。
"""

from datetime import UTC, datetime

from pmstudio.domain.memory.memory import ProjectMemory
from pmstudio.domain.shared.memory import MemoryChange, MemoryKind, MemoryOp
from tests.support.builders import PROJECT
from tests.support.doubles import SeqIds
from tests.support.harness import Harness

NOW = datetime(2026, 10, 3, 20, 0, 0, tzinfo=UTC)


def memory_with_one_entry() -> ProjectMemory:
    return ProjectMemory.empty(PROJECT).apply(
        (
            MemoryChange(
                op=MemoryOp.ADD,
                kind=MemoryKind.DECISION,
                text="导出保留期是 30 天",
                refs=("E1",),
            ),
        ),
        round_id="r1",
        known_refs=frozenset({"E1"}),
        now=NOW,
        ids=SeqIds(),
    )


async def test_workspace_carries_the_confirmed_memory():
    harness = Harness()
    harness.seed_project()
    harness.memory.seed(memory_with_one_entry())

    view = await harness.get_workspace()(project_id=PROJECT)

    assert [item.kind for item in view.memory] == ["decision"]
    assert [item.text for item in view.memory] == ["导出保留期是 30 天"]
    assert view.memory[0].refs == ("E1",)
    assert view.memory[0].round_id == "r1"


async def test_a_project_without_memory_reports_an_empty_tuple():
    harness = Harness()
    harness.seed_project()

    view = await harness.get_workspace()(project_id=PROJECT)

    assert view.memory == ()
