"""P（生产·真跑）：真 SQLite + 真 FTS 检索器 + 真用例，走完 SEED→SEAT→INTEGRATE。

模型是脚本化的（仓库 e2e 的既有口径：检索/投影/发号/落库全是真的，只有模型回包是预置的）。
本文件钉的是**上下文数据流**，不是答案质量：

① 每步只发选中块的 E 号；
② 同一 (stage, pass) 重入不重查、不重发号；
③ SEED/SEAT/INTEGRATE 三条 trace 都在，材料与账本一致；
④ 席位与整合的提示词里真的出现了投影文本。
"""

import json

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.round_context import RoundContextPreparer
from pmstudio.application.use_cases import (
    AnswerInput,
    IntegrateRound,
    RunSeatRound,
    StartRound,
    SubmitAnswers,
    SubmitOpening,
)
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.round.retrieval import RetrievalStage
from pmstudio.domain.round.round import Round
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.shared.template import standard_template
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.materials import (
    SqliteFtsRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteDiscussionRepository,
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteExplorationRepository,
    SqliteMemoryRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT
from tests.support.doubles import FixedClock, RecordingPublisher
from tests.support.harness import Harness

INPUT = "导出权限怎么定"

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了文档与材料",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "导出权限怎么定",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
            }
        ],
        "seats": [{"seat_id": SEAT, "why": "导出要评估数据量"}],
    },
    ensure_ascii=False,
)

SEAT_REPLY = json.dumps(
    {
        "basis": "读了文档与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "导出权限",
                "claim": "导出按角色控制",
                "because": "订单含客户信息",
                "evidence_refs": ["E1"],
            }
        ],
    },
    ensure_ascii=False,
)

INTEGRATION_REPLY = json.dumps(
    {
        "basis": "读了主张与席位意见",
        "questions": [
            {
                "kind": "gap",
                "title": "单次导出的最大行数",
                "why": "量级决定同步还是异步",
                "consequence_if_skipped": "按同步实现",
                "target": {"zone": "body", "section_id": "s8"},
                "type": "single",
                "options": [
                    {"option_id": "a", "text": "少于 1 万"},
                    {"option_id": "b", "text": "1 万以上"},
                ],
                "evidence_refs": ["E1"],
                "asked_by": [SEAT],
            }
        ],
        "list_items": [],
        "proposals": [],
        "dropped_opinions": [],
    },
    ensure_ascii=False,
)

INTEGRATION_DRAFT_REPLY = json.dumps(
    {
        "basis": "读了主张与第二轮席位意见",
        "questions": [],
        "list_items": [],
        "proposals": [
            {
                "op": "add",
                "target": {"zone": "body", "section_id": "s1"},
                "text": "订单导出用于把订单批量导出成表格。",
                "origin_type": "derived",
                "evidence_refs": ["E1"],
                "rationale": "来自用户说的导出需求",
            }
        ],
        "dropped_opinions": [],
    },
    ensure_ascii=False,
)

MEMORY_REPLY = json.dumps({"changes": []}, ensure_ascii=False)


def put(store: SqliteMaterialStore, text: str, title: str):
    parsed = ParsedMaterial(
        origin="upload",
        title=title,
        source_ref=f"attachment:{title}",
        media_type="text/markdown",
        bytes_len=len(text.encode("utf-8")),
        chunks=(
            MaterialChunk(
                chunk_id="c00",
                ordinal=0,
                text=text,
                heading_path=(),
                anchor="第 1 行",
            ),
        ),
        parser_id="local-text",
        parser_version="v1",
    )
    content = text.encode("utf-8")
    import hashlib

    return store.put(
        project_id=PROJECT,
        parsed=parsed,
        sha256=hashlib.sha256(content).hexdigest(),
        content=content,
        now=NOW,
    )


async def test_the_whole_context_chain_is_real_and_idempotent(tmp_path):
    connection = connect(tmp_path / "context_e2e.db")
    ids = UuidIdGenerator()
    store = SqliteMaterialStore(connection, ids=ids)
    documents = SqliteDocumentRepository(connection)
    rounds = SqliteRoundRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    memory = SqliteMemoryRepository(connection)
    index = SqliteWorkspaceIndex(connection)
    uow = SqliteUnitOfWork(connection)
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT, title="检索投影 e2e", now=NOW, template=standard_template()
    )

    pinned = put(store, "导出权限按角色控制，管理员才能导出全量。", "权限设计.md")
    for number in range(5):
        put(store, f"无关内容 {number}：颜色与字体。", f"杂项{number}.md")

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [INTEGRATION_REPLY, INTEGRATION_DRAFT_REPLY],
            "memory_curation": MEMORY_REPLY,
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    clock = FixedClock()
    events = RecordingPublisher()

    start = StartRound(
        documents=documents,
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        uow=uow,
        analyzer=harness.analyzer,
        assigner=EvidenceAssigner(),
        checker=ReadinessChecker(),
        clock=clock,
        ids=ids,
        events=events,
        materials=store,
        explorations=SqliteExplorationRepository(connection),
        discussions=SqliteDiscussionRepository(connection),
        retriever=SqliteFtsRetriever(connection),
    )
    round_id = await start.create(
        project_id=PROJECT, user_input=INPUT, material_ids=[pinned.material_id]
    )
    await start.analyze(round_id=round_id)

    opening = SubmitOpening(
        rounds=rounds,
        round_writer=rounds,
        catalog=harness.seats,
        tools=harness.tools,
        available_tool_keys=harness.available_tool_keys,
        web_search=None,
        evidence=evidences,
        evidence_writer=evidences,
        materials=store,
        index=index,
        uow=uow,
        clock=clock,
        events=events,
    )
    await opening(round_id=round_id, seats=(SEAT,), claim_edits=())

    preparer = RoundContextPreparer(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        materials=store,
        retriever=SqliteFtsRetriever(connection),
        uow=uow,
        clock=clock,
    )
    seat_round = RunSeatRound(
        rounds=rounds,
        round_writer=rounds,
        documents=documents,
        evidence=evidences,
        evidence_writer=evidences,
        uow=uow,
        reviewer=harness.reviewer,
        catalog=harness.seats,
        assigner=EvidenceAssigner(),
        checker=ReadinessChecker(),
        clock=clock,
        events=events,
        materials=store,
        memory=memory,
        preparer=preparer,
    )
    before = rounds.get(round_id)
    before_refs = {(ref.material_id, ref.chunk_id) for ref in before.materials}
    before_eids = {str(ref.eid) for ref in before.materials}

    await seat_round(round_id=round_id)

    after_seat = rounds.get(round_id)
    assert [trace.stage for trace in after_seat.retrievals] == [
        RetrievalStage.SEED,
        RetrievalStage.SEAT,
    ]
    seat_trace = after_seat.retrievals[-1]
    assert seat_trace.ok is True
    assert seat_trace.selected
    selected = {(hit.material_id, hit.chunk_id) for hit in seat_trace.selected}
    new_refs = [
        ref
        for ref in after_seat.materials
        if (ref.material_id, ref.chunk_id) not in before_refs
    ]
    # **只给选中块发号**：新增 refs 恰好等于 selected 里此前没有的那些；未选中的 hit 不新增。
    assert {(ref.material_id, ref.chunk_id) for ref in new_refs} == selected - before_refs
    assert {str(ref.eid) for ref in new_refs}.isdisjoint(before_eids)
    unselected = {(hit.material_id, hit.chunk_id) for hit in seat_trace.hits} - selected
    assert unselected.isdisjoint(
        {(ref.material_id, ref.chunk_id) for ref in new_refs}
    )
    ledger = evidences.get(PROJECT)
    known = {str(item.eid) for item in ledger.evidences()}
    assert {str(ref.eid) for ref in after_seat.materials} <= known

    # 同一 (stage, pass) 重入：文本全等、trace 不改、E 号不新增。
    before_traces = len(after_seat.retrievals)
    before_eid_count = len(known)
    first_text = preparer.prepare(
        round_id=round_id, stage=RetrievalStage.SEAT, query=INPUT
    )
    second_text = preparer.prepare(
        round_id=round_id, stage=RetrievalStage.SEAT, query=INPUT
    )
    assert first_text.text == second_text.text
    assert len(rounds.get(round_id).retrievals) == before_traces
    assert len(evidences.get(PROJECT).evidences()) == before_eid_count

    # 席位提示词里真的出现了投影文本（材料标题）。
    seat_request = next(
        request
        for request in harness.gateway.requests
        if request.purpose == "seat_review"
    )
    assert any("权限设计.md" in message.content for message in seat_request.messages)

    integrate = IntegrateRound(
        rounds=rounds,
        round_writer=rounds,
        documents=documents,
        evidence=evidences,
        evidence_writer=evidences,
        uow=uow,
        integrator=harness.integrator,
        catalog=harness.seats,
        index=index,
        checker=ReadinessChecker(),
        clock=clock,
        ids=ids,
        events=events,
        materials=store,
        curator=harness.curator,
        memory=memory,
        preparer=preparer,
    )
    await integrate(round_id=round_id)

    first_integrate = rounds.get(round_id)
    assert [trace.stage for trace in first_integrate.retrievals] == [
        RetrievalStage.SEED,
        RetrievalStage.SEAT,
        RetrievalStage.INTEGRATE,
    ]
    assert first_integrate.retrievals[-1].pass_index == 1
    integration_request = next(
        request
        for request in harness.gateway.requests
        if request.purpose == "integration"
    )
    assert any(
        "权限设计.md" in message.content for message in integration_request.messages
    )

    # 答一批 → 第二趟席位（pass=2）→ 第二次整合（pass=2，草案）。
    questionnaire = first_integrate.questionnaire
    assert questionnaire is not None
    answers = SubmitAnswers(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        catalog=harness.seats,
        uow=uow,
        clock=clock,
        events=events,
    )
    await answers(
        round_id=round_id,
        form_id=str(questionnaire.form_id),
        answers=(
            AnswerInput(
                question_id=str(questionnaire.questions[0].question_id),
                selected=("a",),
            ),
        ),
    )
    await seat_round(round_id=round_id)
    second_seat = rounds.get(round_id)
    assert [trace.stage for trace in second_seat.retrievals] == [
        RetrievalStage.SEED,
        RetrievalStage.SEAT,
        RetrievalStage.INTEGRATE,
        RetrievalStage.SEAT,
    ]
    assert second_seat.retrievals[-1].pass_index == 2

    await integrate(round_id=round_id)
    final = rounds.get(round_id)
    assert [trace.stage for trace in final.retrievals] == [
        RetrievalStage.SEED,
        RetrievalStage.SEAT,
        RetrievalStage.INTEGRATE,
        RetrievalStage.SEAT,
        RetrievalStage.INTEGRATE,
    ]
    assert [trace.pass_index for trace in final.retrievals] == [0, 1, 1, 2, 2]
    assert final.draft is not None, "第二次整合是草案回复"
    connection.close()


def test_a_handpicked_block_over_the_share_is_not_put_in_the_prompt(tmp_path):
    connection = connect(tmp_path / "context_budget_e2e.db")
    ids = UuidIdGenerator()
    store = SqliteMaterialStore(connection, ids=ids)
    rounds = SqliteRoundRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT, title="预算投影 e2e", now=NOW, template=standard_template()
    )
    handpicked = put(store, "手勾" * 1500, "手勾长文.md")
    put(store, "自动材料" * 175, "自动短材料.md")
    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="自动材料", now=NOW
    )
    round_.pin_materials((handpicked.material_id,))
    rounds.save(round_)

    prepared = RoundContextPreparer(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        materials=store,
        retriever=SqliteFtsRetriever(connection),
        uow=SqliteUnitOfWork(connection),
        clock=FixedClock(),
    ).prepare(round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="自动材料")

    assert "自动材料" * 175 in prepared.text
    assert "手勾" * 1500 not in prepared.text
    assert len(prepared.text) <= 4000
    connection.close()
