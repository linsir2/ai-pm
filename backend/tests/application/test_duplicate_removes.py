"""装配层收口：草案里同一条目只留一条 `remove`（P32 · M1，F28）。

真跑现场（P30 · L4 第 3 轮）里那两族 `remove` 是在**这里**汇合的：

- 整合者那条路（`assemble()` 收 `packet.proposals`，`remove` 的空文本由 `assembly.py` 补上
  **被撤下那条的正文**）；
- 机械清理那条路（`use_cases.py::_retire_answered_open_items()`，"被回答过的 s11 条目要撤下"）。

两族都对，但汇合之后**说的是同一件事**。`draft_of()` 是 `use_cases.py:1408` 那条真实路径上
新草案的**唯一构造点**，所以收口放它里面：一次受益，规则只读 `merge_duplicate_removes`。
"""

from __future__ import annotations

from pmstudio.application.assembly import draft_of
from pmstudio.domain.round.draft import Origin, OriginType, Proposal
from pmstudio.domain.shared.ids import DraftId, EntryId, EvidenceId, ProposalId, RoundId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp

ROUND_ID = RoundId("r3")


def remove(proposal_id: str, *, entry: str, text: str) -> Proposal:
    return Proposal(
        proposal_id=ProposalId(proposal_id),
        entry_id=EntryId(entry),
        op=WriteOp.REMOVE,
        target=Target.body("s11"),
        text=text,
        origin=Origin(OriginType.SYSTEM_DEFAULT, (EvidenceId("E1"),), None),
    )


def test_draft_of_keeps_one_remove_per_entry():
    """整合者的那条（文本是被撤下条目的正文）留下；机械清理那条不再进卡片。"""

    from_integrator = remove(
        "p01", entry="r2#open23", text="〔未决·阻塞〕受试者编号生成规则及可逆性说明"
    )
    from_cleanup = remove("p02", entry="r2#open23", text="本轮已回答，撤下这条未决项")

    draft = draft_of(ROUND_ID, DraftId("d1"), (from_integrator, from_cleanup), ())

    assert draft is not None
    assert [item.proposal_id for item in draft.proposals] == [ProposalId("p01")]
    assert draft.proposals[0].text == "〔未决·阻塞〕受试者编号生成规则及可逆性说明"


def test_draft_of_keeps_two_removes_on_different_entries():
    first = remove("p01", entry="r2#open23", text="〔未决〕编号规则")
    second = remove("p02", entry="r2#open24", text="〔未决〕脱敏时机")

    draft = draft_of(ROUND_ID, DraftId("d1"), (first, second), ())

    assert draft is not None
    assert [item.proposal_id for item in draft.proposals] == [
        ProposalId("p01"),
        ProposalId("p02"),
    ]


def test_draft_of_is_unchanged_when_there_is_nothing_to_merge():
    """整合者**没有**提这条的时候，机械清理那条照样进卡片（它是唯一的一条）。"""

    from_cleanup = remove("p01", entry="r2#open23", text="本轮已回答，撤下这条未决项")

    draft = draft_of(ROUND_ID, DraftId("d1"), (from_cleanup,), ("还有 2 条没处理",))

    assert draft is not None
    assert [item.proposal_id for item in draft.proposals] == [ProposalId("p01")]
    assert draft.gaps == ("还有 2 条没处理",)
