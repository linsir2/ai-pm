"""记忆命中的只读端点（批 4 Task 2）：最近 10 轮一次没被引用的记忆 id。

只读：路由 → 用例只读三样东西（记忆 / 轮次头 / 轮次 payload），不写任何库。
"""

from pmstudio.api.app import create_app
from pmstudio.domain.memory.memory import ProjectMemory
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.questionnaire import Question, Questionnaire, QuestionType
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import (
    DraftId,
    EntryId,
    EvidenceId,
    FormId,
    ProjectId,
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.memory import MemoryChange, MemoryKind, MemoryOp
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from tests.support.api_client import async_client
from tests.support.builders import PROJECT
from tests.support.harness import Harness

OTHER = ProjectId("p-other")


def seed_memory(harness: Harness, project_id: ProjectId, refs: tuple[str, ...]) -> str:
    """直接塞一条**已确认**的记忆（照 `test_memory_api.py:120-136` 原样）。"""

    memory = ProjectMemory.empty(project_id).apply(
        (
            MemoryChange(
                op=MemoryOp.ADD,
                kind=MemoryKind.DECISION,
                text="导出保留期是 30 天",
                refs=refs,
            ),
        ),
        round_id="r0",
        known_refs=frozenset(refs),
        now=harness.clock.now(),
        ids=harness.ids,
    )
    harness.memory.seed(memory)
    return memory.entries[0].memory_id


def seed_round(
    harness: Harness,
    *,
    round_id: str,
    project_id: ProjectId,
    proposal_refs: tuple[str, ...],
    question_refs: tuple[str, ...],
) -> None:
    """直接塞一轮带草案的轮次：提案引 `proposal_refs`，题目引 `question_refs`。"""

    rid = RoundId(round_id)
    round_ = Round(
        round_id=rid,
        project_id=project_id,
        user_input="想做个订单导出的功能",
        created_at=harness.clock.now(),
        draft=Draft(
            draft_id=DraftId(f"d-{round_id}"),
            round_id=rid,
            proposals=(
                Proposal(
                    proposal_id=ProposalId(f"p-{round_id}"),
                    entry_id=EntryId("en-1"),
                    op=WriteOp.REMOVE,
                    target=Target.body("s5"),
                    text="导出记录保留 30 天",
                    origin=Origin(
                        OriginType.DERIVED,
                        tuple(EvidenceId(ref) for ref in proposal_refs),
                        "席位判断",
                    ),
                ),
            ),
        ),
        questionnaire_history=(
            Questionnaire(
                form_id=FormId("f1"),
                round_id=rid,
                questions=(
                    Question(
                        question_id=QuestionId("q1"),
                        target=Target.body("s5"),
                        type=QuestionType.TEXT,
                        title="导出保留期定多久？",
                        why="落 s5 的保留口径",
                        consequence_if_skipped="落成开放问题",
                        evidence_refs=tuple(EvidenceId(ref) for ref in question_refs),
                    ),
                ),
            ),
        ),
    )
    harness.rounds.seed(round_)


async def test_usage_lists_entries_never_cited_in_the_window():
    """R：专用端点返回"最近 10 轮一次没被引用"的记忆 id（含 JSON 形状）。"""
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    mem_id = seed_memory(harness, PROJECT, ("E1",))
    seed_round(
        harness,
        round_id="r1",
        project_id=PROJECT,
        proposal_refs=("E2",),
        question_refs=("E2",),
    )
    app = create_app(harness.api_services(model_name="m"))

    async with async_client(app) as client:
        response = await client.get(f"/api/projects/{PROJECT}/memory/usage")

    assert response.status_code == 200
    assert response.json() == {"window": 10, "unused": [mem_id]}


async def test_usage_is_empty_when_nothing_is_unused():
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    seed_memory(harness, PROJECT, ("E1",))
    seed_round(
        harness,
        round_id="r1",
        project_id=PROJECT,
        proposal_refs=("E2",),
        question_refs=("E1",),
    )
    app = create_app(harness.api_services(model_name="m"))

    async with async_client(app) as client:
        response = await client.get(f"/api/projects/{PROJECT}/memory/usage")

    assert response.status_code == 200
    assert response.json() == {"window": 10, "unused": []}


async def test_usage_is_scoped_to_the_owner():
    """隔离：别 owner 的记忆不出现在你的 usage 里。"""
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    own = seed_memory(harness, PROJECT, ("E1",))
    other = seed_memory(harness, OTHER, ("E9",))
    assert own != other
    app = create_app(harness.api_services(model_name="m"))

    async with async_client(app) as client:
        response = await client.get(f"/api/projects/{PROJECT}/memory/usage")

    assert response.status_code == 200
    assert response.json() == {"window": 10, "unused": [own]}
