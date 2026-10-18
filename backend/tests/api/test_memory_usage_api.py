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


def seed_memories(harness: Harness, project_id: ProjectId, items) -> list[str]:
    """一次塞多条记忆：同项目多次 seed 会覆写，所以一批 apply 后一次 seed。

    `items` = [(refs, text), ...]；text 必须各不相同（同类+同文算重复，`apply` 会拒）。
    """

    memory = ProjectMemory.empty(project_id)
    known = frozenset(ref for refs, _text in items for ref in refs)
    for refs, text in items:
        memory = memory.apply(
            (
                MemoryChange(
                    op=MemoryOp.ADD,
                    kind=MemoryKind.DECISION,
                    text=text,
                    refs=refs,
                ),
            ),
            round_id="r0",
            known_refs=known,
            now=harness.clock.now(),
            ids=harness.ids,
        )
    harness.memory.seed(memory)
    return [entry.memory_id for entry in memory.entries]


def seed_window_rounds(
    harness: Harness, project_id: ProjectId, cited: dict[str, tuple[str, ...]]
) -> None:
    """11 轮 r-00…r-10：同一时刻，顺序全靠 id 字典序（与生产 ORDER BY 同口径）。

    `cited[round_id]` = 该轮提案引用的编号；没点名的轮引无关 E-X（提案 refs 不许空）。
    """

    for index in range(11):
        rid = f"r-{index:02d}"
        seed_round(
            harness,
            round_id=rid,
            project_id=project_id,
            proposal_refs=cited.get(rid, ("E-X",)),
            question_refs=(),
        )


def seed_round_two_batches(
    harness: Harness,
    *,
    round_id: str,
    project_id: ProjectId,
    old_refs: tuple[str, ...],
    new_refs: tuple[str, ...],
    proposal_refs: tuple[str, ...],
) -> None:
    """一轮两批问卷：旧批 f1 引 old_refs，新批 f2 引 new_refs（history 按追加顺序）。"""

    rid = RoundId(round_id)

    def _form(form_id: str, question_id: str, refs: tuple[str, ...]) -> Questionnaire:
        return Questionnaire(
            form_id=FormId(form_id),
            round_id=rid,
            questions=(
                Question(
                    question_id=QuestionId(question_id),
                    target=Target.body("s5"),
                    type=QuestionType.TEXT,
                    title="导出保留期定多久？",
                    why="落 s5 的保留口径",
                    consequence_if_skipped="落成开放问题",
                    evidence_refs=tuple(EvidenceId(ref) for ref in refs),
                ),
            ),
        )

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
            _form("f1", "q1", old_refs),
            _form("f2", "q2", new_refs),
        ),
    )
    harness.rounds.seed(round_)


async def test_usage_window_excludes_the_11th_oldest_round():
    """R：窗口只看最近 10 轮——第 11 老的 r-00 里交代过的也算未命中。

    守的是 `limit=MEMORY_USAGE_WINDOW` 那一行：改成 5（或取头不取尾），
    r-05 的 E-MID 会掉出窗口 → `unused` 多出它 → 红。
    """
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    mem_old, _mem_mid = seed_memories(
        harness,
        PROJECT,
        [(("E-OLD",), "11 轮前定的保留期"), (("E-MID",), "6 轮前定的保留口径")],
    )
    seed_window_rounds(harness, PROJECT, {"r-00": ("E-OLD",), "r-05": ("E-MID",)})
    app = create_app(harness.api_services(model_name="m"))

    async with async_client(app) as client:
        response = await client.get(f"/api/projects/{PROJECT}/memory/usage")

    assert response.status_code == 200
    assert response.json() == {"window": 10, "unused": [mem_old]}


async def test_usage_sees_citations_in_older_batches():
    """R：引用集扫的是整条 questionnaire_history——旧批题目交代过的算命中。

    守的是扁平化那两行：只读当批（`round_.questionnaire`）的话，
    旧批的 E-Q 看不见 → 这条会被冤成未命中 → 红。
    """
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    seed_memory(harness, PROJECT, ("E-Q",))
    seed_round_two_batches(
        harness,
        round_id="r1",
        project_id=PROJECT,
        old_refs=("E-Q",),
        new_refs=(),
        proposal_refs=("E-X",),
    )
    app = create_app(harness.api_services(model_name="m"))

    async with async_client(app) as client:
        response = await client.get(f"/api/projects/{PROJECT}/memory/usage")

    assert response.status_code == 200
    assert response.json() == {"window": 10, "unused": []}


async def test_usage_counts_proposal_only_citations():
    """R：只被提案引用的也算命中（题目一个没引它）。

    守的是 `cited_eids(proposals, questions)` 这一侧：提案被无视的话，
    E-P 在问卷里找不到 → 冤成未命中 → 红。
    """
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    seed_memory(harness, PROJECT, ("E-P",))
    seed_round(
        harness,
        round_id="r1",
        project_id=PROJECT,
        proposal_refs=("E-P",),
        question_refs=(),
    )
    app = create_app(harness.api_services(model_name="m"))

    async with async_client(app) as client:
        response = await client.get(f"/api/projects/{PROJECT}/memory/usage")

    assert response.status_code == 200
    assert response.json() == {"window": 10, "unused": []}


async def test_usage_window_tail_covers_newest_round():
    """R：窗口是尾部 10 轮——最新 r-10 里交代过的算命中，最老 r-00 的不算。

    守的是 `order` 升序+取尾：取头（最老 10 轮）的话 r-10 的 E-NEW 看不见 → 红；
    窗口放大到全覆盖的话 r-00 的 E-OLD 会被命中 → 红。与窗口测试互补：
    那边用 r-05 验窗内，这边用边界两端验窗沿。
    """
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    mem_old, _mem_new = seed_memories(
        harness,
        PROJECT,
        [(("E-OLD",), "11 轮前定的保留期"), (("E-NEW",), "最新轮重申的保留期")],
    )
    seed_window_rounds(harness, PROJECT, {"r-00": ("E-OLD",), "r-10": ("E-NEW",)})
    app = create_app(harness.api_services(model_name="m"))

    async with async_client(app) as client:
        response = await client.get(f"/api/projects/{PROJECT}/memory/usage")

    assert response.status_code == 200
    assert response.json() == {"window": 10, "unused": [mem_old]}
