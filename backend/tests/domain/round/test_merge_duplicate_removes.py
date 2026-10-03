"""同一 `entry_id` 的多条 `remove` 是同一条指令（P32 · M1，F28）。

真跑现场（P30 的 L4 第 3 轮）：草案里整合者那条路（`p14`–`p21`）与机械清理那条路
（`p34`–`p39`）对**同一批 s11 条目**各提了一条 `remove`，两条都被用户接受；
确认写入时第一条把条目摘掉、第二条找不到条目 → `UnknownEntry`（422）→ **整批一条都没写**。

这条规则（同 entry 的 `remove` 只算一条）的**唯一出处**是
`domain/round/draft.py::merge_duplicate_removes`；装配层与写入端都读它，不各写一份。
"""

from __future__ import annotations

from pmstudio.domain.round.draft import (
    Origin,
    OriginType,
    Proposal,
    merge_duplicate_removes,
)
from pmstudio.domain.shared.ids import EntryId, EvidenceId, ProposalId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp


def proposal(
    proposal_id: str,
    *,
    entry: str,
    op: WriteOp = WriteOp.REMOVE,
    text: str = "撤下这条未决项",
    origin: OriginType = OriginType.SYSTEM_DEFAULT,
) -> Proposal:
    return Proposal(
        proposal_id=ProposalId(proposal_id),
        entry_id=EntryId(entry),
        op=op,
        target=Target.body("s11"),
        text=text,
        origin=Origin(origin, (EvidenceId("E1"),), None),
    )


def test_two_removes_on_the_same_entry_merge_into_the_first():
    first = proposal("p1", entry="r1#open23", text="〔未决〕受试者编号规则")
    second = proposal("p2", entry="r1#open23", text="本轮已回答，撤下这条未决项")

    kept, merged = merge_duplicate_removes((first, second))

    assert [item.proposal_id for item in kept] == [ProposalId("p1")]
    assert merged == (ProposalId("p2"),)
    # 留下的那条是**序列里最先**出现的（它的文本更全，卡片上更好看）
    assert kept[0].text == "〔未决〕受试者编号规则"


def test_two_replaces_on_the_same_entry_are_left_alone():
    """`replace` 两条是**顺序语义**（后一条覆盖前一条），合并会把用户勾的第二条改法吃掉。"""

    first = proposal("p1", entry="r1#28", op=WriteOp.REPLACE, text="改成 25 年")
    second = proposal("p2", entry="r1#28", op=WriteOp.REPLACE, text="改成 30 年")

    kept, merged = merge_duplicate_removes((first, second))

    assert [item.proposal_id for item in kept] == [ProposalId("p1"), ProposalId("p2")]
    assert merged == ()


def test_removes_on_different_entries_are_left_alone():
    first = proposal("p1", entry="r1#open23")
    second = proposal("p2", entry="r1#open24")

    kept, merged = merge_duplicate_removes((first, second))

    assert [item.proposal_id for item in kept] == [ProposalId("p1"), ProposalId("p2")]
    assert merged == ()


def test_single_and_empty_inputs_pass_through():
    only = proposal("p1", entry="r1#open23")
    assert merge_duplicate_removes((only,)) == ((only,), ())
    assert merge_duplicate_removes(()) == ((), ())


def test_remove_and_replace_on_the_same_entry_merge_only_the_remove():
    replace = proposal("p1", entry="r1#28", op=WriteOp.REPLACE, text="改成 25 年")
    remove_first = proposal("p2", entry="r1#28")
    remove_duplicate = proposal("p3", entry="r1#28")

    kept, merged = merge_duplicate_removes((replace, remove_first, remove_duplicate))

    assert [item.proposal_id for item in kept] == [ProposalId("p1"), ProposalId("p2")]
    assert merged == (ProposalId("p3"),)


def test_three_removes_on_the_same_entry_merge_all_but_the_first():
    items = tuple(proposal(f"p{index}", entry="r1#open23") for index in (1, 2, 3))

    kept, merged = merge_duplicate_removes(items)

    assert [item.proposal_id for item in kept] == [ProposalId("p1")]
    assert merged == (ProposalId("p2"), ProposalId("p3"))
