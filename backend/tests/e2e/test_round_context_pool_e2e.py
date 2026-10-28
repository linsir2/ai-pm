"""P（生产·真跑）：真 PostgreSQL + 真 FTS 检索器 + 真用例，走完 SEED→SEAT→INTEGRATE。

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
from pmstudio.infrastructure.postgres.materials import (
    SqliteFtsRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.postgres.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.postgres.repositories import (
    SqliteDiscussionRepository,
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteExplorationRepository,
    SqliteMemoryRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_seat
from tests.support.doubles import FixedClock, RecordingPublisher
from tests.support.harness import Harness
from tests.support.leases import bind_round_lease
from tests.support.postgres import open_direct

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
    connection = open_direct()
    ids = UuidIdGenerator()
    store = SqliteMaterialStore(connection, ids=ids)
    documents = SqliteDocumentRepository(connection)
    rounds = SqliteRoundRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    memory = SqliteMemoryRepository(connection)
    index = SqliteWorkspaceIndex(connection)
    uow = PgUnitOfWork(connection)
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT, title="检索投影 e2e", now=NOW, template=standard_template()
    )

    pinned = put(store, "导出权限按角色控制，管理员才能导出全量。", "权限设计.md")
    for number in range(5):
        put(store, f"无关内容 {number}：颜色与字体。", f"杂项{number}.md")
    # **真的未选中反例**：5000+ 字的块会被本步检索命中，但预算装不下 → 不发号、
    # 不进提示词。末尾的标记词只用来断言它一个字都没漏进上下文。
    huge_text = "导出权限怎么定：" + "这部分是无关的补充说明。" * 420 + "紫貂标记"
    huge = put(store, huge_text, "超长权限说明.md")

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
    bind_round_lease(connection, round_id)
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
    huge_key = (huge.material_id, huge.parsed.chunks[0].chunk_id)
    assert huge_key in {(hit.material_id, hit.chunk_id) for hit in seat_trace.hits}, (
        "5000 字块该被本步检索命中（否则反例没有鉴别力）"
    )
    assert unselected == {huge_key}, f"未选中的该只有这个超预算块（实际：{unselected}）"
    assert huge_key not in {
        (ref.material_id, ref.chunk_id) for ref in after_seat.materials
    }, "装不进预算的块不许发号"
    ledger = evidences.get(PROJECT)
    known = {str(item.eid) for item in ledger.evidences()}
    assert all(item.text != huge_text for item in ledger.evidences()), (
        "未选中的块不该进账本（没有 E 号）"
    )
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
    seat_prompt = "\n".join(message.content for message in seat_request.messages)
    assert "权限设计.md" in seat_prompt
    assert "紫貂标记" not in seat_prompt, "超预算块不该出现在提示词里"

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


async def test_a_seat_retrieval_issues_a_new_eid_that_reaches_the_prompt(tmp_path):
    """P2.5：SEED 没带的材料在 SEAT 命中后，**本步新发的 E 号**必须进席位提示词。

    与上一条的分工：这条只盯"新号可见"——材料在 SEED 确实不在 `Round.materials` 里
    （先断言），SEAT 检索命中并选中后才有号；账本里有它，席位拿到的
    `external_material` 里同时出现编号与正文。**不用 `_RecordingPreparer`**：
    真 `RoundContextPreparer` + 真 `SqliteFtsRetriever` + 真账本 + 真 `RunSeatRound`。
    """

    connection = open_direct()
    ids = UuidIdGenerator()
    store = SqliteMaterialStore(connection, ids=ids)
    documents = SqliteDocumentRepository(connection)
    rounds = SqliteRoundRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    memory = SqliteMemoryRepository(connection)
    index = SqliteWorkspaceIndex(connection)
    uow = PgUnitOfWork(connection)
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT, title="新号可见 e2e", now=NOW, template=standard_template()
    )

    pinned = put(store, "导出权限按角色控制，管理员才能导出全量。", "权限设计.md")
    # 用户原话里没有"吞吐上限"；只有席位职责（fields）会把它带进 SEAT 的检索词。
    target_text = "吞吐上限按每分钟一万单压测，超过就排队。"
    target = put(store, target_text, "压测目标.md")

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [INTEGRATION_REPLY, INTEGRATION_DRAFT_REPLY],
            "memory_curation": MEMORY_REPLY,
        },
        seat_replies={SEAT: SEAT_REPLY},
        seats=(make_seat(fields=("吞吐上限",)),),
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
    bind_round_lease(connection, round_id)
    await start.analyze(round_id=round_id)

    before = rounds.get(round_id)
    target_key = (target.material_id, target.parsed.chunks[0].chunk_id)
    assert target.material_id not in {ref.material_id for ref in before.materials}, (
        "SEED 不该带这份材料（否则测不到本步新发号）"
    )
    before_eids = {str(ref.eid) for ref in before.materials}
    assert all(
        item.text != target_text for item in evidences.get(PROJECT).evidences()
    ), "SEED 没带它，账本里就不该有它的号"

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
        preparer=RoundContextPreparer(
            rounds=rounds,
            round_writer=rounds,
            evidence=evidences,
            evidence_writer=evidences,
            materials=store,
            retriever=SqliteFtsRetriever(connection),
            uow=uow,
            clock=clock,
        ),
    )
    await seat_round(round_id=round_id)

    after_seat = rounds.get(round_id)
    assert [trace.stage for trace in after_seat.retrievals] == [
        RetrievalStage.SEED,
        RetrievalStage.SEAT,
    ]
    seat_trace = after_seat.retrievals[-1]
    assert seat_trace.ok is True
    assert target_key in {(hit.material_id, hit.chunk_id) for hit in seat_trace.hits}
    assert target_key in {
        (hit.material_id, hit.chunk_id) for hit in seat_trace.selected
    }
    ref = next(
        ref
        for ref in after_seat.materials
        if (ref.material_id, ref.chunk_id) == target_key
    )
    assert str(ref.eid) not in before_eids, "本步选中的块该拿一个**新**号"
    ledger = evidences.get(PROJECT)
    assert any(
        str(item.eid) == str(ref.eid) and item.text == target_text
        for item in ledger.evidences()
    ), "账本里必须有这条新号（编号与正文对得上）"

    seat_request = next(
        request
        for request in harness.gateway.requests
        if request.purpose == "seat_review"
    )
    seat_prompt = "\n".join(message.content for message in seat_request.messages)
    assert f"[{ref.eid}]" in seat_prompt, "本步新发的 E 号没进席位提示词"
    assert target_text in seat_prompt, "新号对应的正文没进席位提示词"
    connection.close()


def test_a_handpicked_block_over_the_share_is_not_put_in_the_prompt(tmp_path):
    connection = open_direct()
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
    bind_round_lease(connection, ROUND_ID)

    prepared = RoundContextPreparer(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        materials=store,
        retriever=SqliteFtsRetriever(connection),
        uow=PgUnitOfWork(connection),
        clock=FixedClock(),
    ).prepare(round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="自动材料")

    assert "自动材料" * 175 in prepared.text
    assert "手勾" * 1500 not in prepared.text
    assert len(prepared.text) <= 4000
    connection.close()
