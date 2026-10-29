"""每步上下文准备（P2.3）：检索 → 投影 → 发号 → 一个事务落库。

关键行为：
- 新检索到的块**先入池**再投影（否则会"命中 K、带进 0"）；
- E 号只发给选中块；
- 同一 (stage, pass) 重入不重查、不重发号；
- web 材料不进材料池，只走 `render_web_results`；
- 检索失败留痕（trace.ok=False），仍从池投影。
"""

from dataclasses import replace

from pmstudio.application.context import MATERIAL_BUDGET
from pmstudio.application.dto import MaterialHit
from pmstudio.application.round_context import (
    MAX_INTEGRATE_RETRIEVALS,
    PreparedContext,
    RoundContextPreparer,
)
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.round.retrieval import ManualChunk, RetrievalStage, RetrievalTrace
from pmstudio.domain.round.round import DropSource, MaterialRef, Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.template import standard_template
from pmstudio.infrastructure.postgres.materials import (
    SqliteFtsRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.postgres.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.postgres.repositories import (
    SqliteEvidenceRepository,
    SqliteRoundRepository,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.api.test_routes import QUESTION_REPLY
from tests.application.test_materials import make_material
from tests.application.test_seat_round import (
    SEAT_REPLY,
    SECOND_SEAT,
    claims_reply,
    started_round,
)
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_round, make_seat
from tests.support.doubles import FixedClock, RecordingMaterialRetriever
from tests.support.harness import Harness
from tests.support.leases import bind_round_lease
from tests.support.postgres import open_direct


def material_hit(packet, index: int = 0, score: float = 1.0) -> MaterialHit:
    chunk = packet.parsed.chunks[index]
    return MaterialHit(
        material_id=packet.material_id,
        chunk_id=chunk.chunk_id,
        score=score,
        excerpt=chunk.text[:20],
        anchor=chunk.anchor,
        source_ref=packet.parsed.source_ref,
        title=packet.parsed.title,
    )


def seeded_harness(*packets, round_=None) -> Harness:
    harness = Harness(replies={})
    harness.seed_project()
    for packet in packets:
        harness.materials.seed(packet)
    harness.rounds.seed(round_ or make_round())
    return harness


def preparer(harness: Harness, retriever) -> RoundContextPreparer:
    return RoundContextPreparer(
        rounds=harness.rounds,
        round_writer=harness.rounds,
        evidence=harness.evidences,
        evidence_writer=harness.evidences,
        materials=harness.materials,
        retriever=retriever,
        uow=harness.uow,
        clock=harness.clock,
    )


def test_prepare_retrieves_projects_and_assigns_eids():
    packet = make_material("权限：管理员才能导出", "审计：导出要留痕")
    harness = seeded_harness(packet)
    retriever = RecordingMaterialRetriever(returns=(material_hit(packet, 0),))

    prepared = preparer(harness, retriever).prepare(
        round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="权限"
    )

    round_ = harness.rounds.get(ROUND_ID)
    assert prepared.hit_count == 1
    assert prepared.selected_count >= 1
    assert "本步从证据池带了" in prepared.text
    assert "权限" in prepared.text
    assert len(round_.materials) == prepared.selected_count
    assert round_.retrievals[0].stage is RetrievalStage.SEAT
    assert round_.retrievals[0].ok is True
    assert round_.retrievals[0].selected
    assert retriever.calls


def test_prepare_is_idempotent_for_the_same_stage_and_pass():
    packet = make_material("权限：管理员才能导出")
    harness = seeded_harness(packet)
    retriever = RecordingMaterialRetriever(returns=(material_hit(packet, 0),))
    one_preparer = preparer(harness, retriever)

    first = one_preparer.prepare(
        round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="权限"
    )
    before = harness.evidences.get(PROJECT).evidences()
    calls_before = len(retriever.calls)
    second = one_preparer.prepare(
        round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="权限"
    )

    round_ = harness.rounds.get(ROUND_ID)
    assert second.text == first.text
    assert len(retriever.calls) == calls_before
    assert len(round_.retrievals) == 1
    assert harness.evidences.get(PROJECT).evidences() == before


def test_a_zero_hit_step_writes_a_trace_and_says_the_pool_is_not_empty():
    packet = make_material("x" * 5000, "y" * 5000, material_id="m1")
    round_ = make_round()
    round_.pin_materials(("m1",))
    harness = seeded_harness(packet, round_=round_)
    retriever = RecordingMaterialRetriever(returns=())

    prepared = preparer(harness, retriever).prepare(
        round_id=ROUND_ID, stage=RetrievalStage.SEAT, query=""
    )

    round_after = harness.rounds.get(ROUND_ID)
    assert prepared.hit_count == 0
    assert prepared.selected_count == 0
    assert prepared.pool_count == 2
    assert "本步没有选中材料" in prepared.text
    assert round_after.retrievals[0].hits == ()
    assert round_after.retrievals[0].ok is True


def test_web_materials_never_enter_the_material_projection():
    web = make_material("联网正文", material_id="w1")
    web = replace(web, parsed=replace(web.parsed, origin="web_search"))
    harness = seeded_harness(web)
    ledger = harness.evidences.get(PROJECT)
    eid = ledger.record(
        kind=EvidenceKind.EXTERNAL,
        text="联网正文",
        source=EvidenceSource(external_ref="https://example.com/x"),
        now=NOW,
    )
    harness.evidences.save(ledger)
    round_ = make_round()
    round_.add_materials(
        (MaterialRef(web.material_id, web.parsed.chunks[0].chunk_id, eid),)
    )
    harness.rounds.seed(round_)

    prepared = preparer(harness, RecordingMaterialRetriever(returns=())).prepare(
        round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="联网"
    )

    assert str(eid) in prepared.text
    assert "需求.md" in prepared.text
    assert "【上传的材料】" not in prepared.text
    assert prepared.pool_count == 0


def test_a_failed_retrieval_is_visible_but_still_projects_from_the_pool():
    class _Boom:
        def search(self, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("检索器坏了")

    packet = make_material("权限：管理员才能导出")
    round_ = make_round()
    round_.pin_materials(("m1",))
    harness = seeded_harness(packet, round_=round_)

    prepared = preparer(harness, _Boom()).prepare(
        round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="权限"
    )

    round_after = harness.rounds.get(ROUND_ID)
    assert "权限：管理员才能导出" in prepared.text
    assert round_after.retrievals[0].ok is False
    assert "检索器坏了" in round_after.retrievals[0].error


def test_each_stage_has_its_own_retrieval_cap():
    """额度按 stage 分账：3 条 INTEGRATE trace 才封顶下一次整合检索，SEAT 不受牵连。"""

    packet = make_material("权限：管理员才能导出")
    round_ = Round(
        round_id=ROUND_ID,
        project_id=PROJECT,
        user_input="导出",
        created_at=NOW,
        step=Step.SEATS_RUNNING,
        waiting_on=WaitingOn.NONE,
        seats=(SEAT,),
        active_seats=(SEAT,),
        integration_seq=MAX_INTEGRATE_RETRIEVALS,
    )
    round_.pin_materials(("m1",))
    for pass_index in range(1, MAX_INTEGRATE_RETRIEVALS + 1):
        round_.note_retrieval(
            RetrievalTrace(
                stage=RetrievalStage.INTEGRATE,
                pass_index=pass_index,
                query="q",
                hits=(),
                selected=(),
                ok=True,
                error=None,
                created_at=NOW,
            )
        )
    harness = seeded_harness(packet, round_=round_)
    retriever = RecordingMaterialRetriever(returns=(material_hit(packet, 0),))

    prepared = preparer(harness, retriever).prepare(
        round_id=ROUND_ID, stage=RetrievalStage.INTEGRATE, query="权限"
    )

    round_after = harness.rounds.get(ROUND_ID)
    assert retriever.calls == []
    assert len(round_after.retrievals) == MAX_INTEGRATE_RETRIEVALS
    assert "权限：管理员才能导出" in prepared.text


def test_integration_trace_pass_is_the_next_integration_number():
    packet = make_material("权限：管理员才能导出")
    round_ = make_round()
    round_.pin_materials(("m1",))
    harness = seeded_harness(packet, round_=round_)

    preparer(harness, RecordingMaterialRetriever(returns=())).prepare(
        round_id=ROUND_ID, stage=RetrievalStage.INTEGRATE, query=""
    )

    trace = harness.rounds.get(ROUND_ID).retrievals[-1]
    assert trace.pass_index == 1, "integration_seq=0 时，这次是第 1 次整合"


def test_manual_chunks_are_projected_and_get_an_eid():
    packet = make_material("手动带进来的一段")
    round_ = make_round()
    round_.pin_materials(("m1",))
    round_.note_manual_chunks((ManualChunk("m1", "m1#c00"),))
    harness = seeded_harness(packet, round_=round_)

    prepared = preparer(harness, RecordingMaterialRetriever(returns=())).prepare(
        round_id=ROUND_ID, stage=RetrievalStage.SEAT, query=""
    )

    after = harness.rounds.get(ROUND_ID)
    assert "手动带进来的一段" in prepared.text
    assert any(ref.chunk_id == "m1#c00" for ref in after.materials)


def test_prepare_round_trips_through_real_pg():
    connection = open_direct()
    ids = UuidIdGenerator()
    store = SqliteMaterialStore(connection, ids=ids)
    rounds = SqliteRoundRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT, title="投影核验", now=NOW, template=standard_template()
    )
    parsed = make_material("权限：管理员才能导出").parsed
    packet = store.put(
        project_id=PROJECT,
        parsed=parsed,
        sha256="b" * 64,
        content=b"raw",
        now=NOW,
    )
    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="导出", now=NOW
    )
    round_.pin_materials((packet.material_id,))
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
    ).prepare(round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="权限")

    again = rounds.get(ROUND_ID)
    assert prepared.text
    assert again.materials
    assert again.retrievals and again.retrievals[0].ok
    ledger = evidences.get(PROJECT)
    eids = {str(item.eid) for item in ledger.evidences()}
    assert {str(ref.eid) for ref in again.materials} <= eids
    connection.close()


class _RecordingPreparer:
    """只验接线：返回一个显眼标记，看它有没有真的进提示词。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[RetrievalStage, str]] = []

    def prepare(self, *, round_id, stage, query):  # type: ignore[no-untyped-def]
        self.calls.append((stage, query))
        return PreparedContext(
            text=self.text,
            hit_count=1,
            selected_count=1,
            pool_count=1,
            skipped_manual=(),
        )


async def test_seat_round_uses_the_prepared_text():
    harness = Harness(
        replies={"claims": claims_reply()}, seat_replies={SEAT: SEAT_REPLY}
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    recorder = _RecordingPreparer("【本步投影】")

    await harness.run_seat_round(preparer=recorder)(round_id=round_id)

    assert recorder.calls and recorder.calls[0][0] is RetrievalStage.SEAT
    request = next(
        one for one in harness.gateway.requests if one.purpose == "seat_review"
    )
    assert any("【本步投影】" in message.content for message in request.messages)


async def test_every_pending_seat_sees_the_same_external_material_text():
    """多席位共用**同一份**投影文本：不是每个席位各渲染一遍（那会漂）。"""

    packet = make_material("权限：管理员才能导出")
    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: SEAT_REPLY, SECOND_SEAT: SEAT_REPLY},
        seats=(make_seat(), make_seat(SECOND_SEAT, title="权限与角色")),
    )
    harness.seed_project()
    harness.materials.seed(packet)
    round_id = await started_round(harness)
    round_ = harness.rounds.get(round_id)
    round_.pin_materials((packet.material_id,))
    harness.rounds.seed(round_)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT, SECOND_SEAT))

    await harness.run_seat_round(
        preparer=preparer(
            harness,
            RecordingMaterialRetriever(returns=(material_hit(packet, 0),)),
        )
    )(round_id=round_id)

    seat_calls = [
        slots for name, slots in harness.prompts.calls if name == "seat_review"
    ]
    assert len(seat_calls) == 2, seat_calls
    texts = [slots["external_material"] for slots in seat_calls]
    assert texts[0] == texts[1], "两个席位必须看到逐字相同的外部材料文本"
    assert "权限：管理员才能导出" in texts[0], "投影里该有材料正文"


async def test_integrate_round_uses_the_prepared_text():
    harness = Harness(
        replies={"claims": claims_reply(), "integration": QUESTION_REPLY},
        seat_replies={SEAT: SEAT_REPLY},
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    recorder = _RecordingPreparer("【整合投影】")

    await harness.integrate_round(preparer=recorder)(round_id=round_id)

    assert recorder.calls and recorder.calls[0][0] is RetrievalStage.INTEGRATE
    request = next(
        one for one in harness.gateway.requests if one.purpose == "integration"
    )
    assert any("【整合投影】" in message.content for message in request.messages)


def test_the_projected_text_respects_the_material_budget():
    packet = make_material(
        *[f"第 {index} 段" * 50 for index in range(100)], material_id="m1"
    )
    round_ = make_round()
    round_.pin_materials(("m1",))
    harness = seeded_harness(packet, round_=round_)

    prepared = preparer(
        harness, RecordingMaterialRetriever(returns=(material_hit(packet, 0),))
    ).prepare(round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="第 1 段")

    assert len(prepared.text) <= MATERIAL_BUDGET
    assert "没带进来的块在本轮材料池里" in prepared.text


def test_without_a_retriever_no_trace_is_written():
    """没接线 ≠ 搜过但零命中：不能写 `ok=True, hits=()`。"""

    packet = make_material("权限：管理员才能导出")
    harness = seeded_harness(packet)

    preparer(harness, None).prepare(
        round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="权限"
    )

    assert harness.rounds.get(ROUND_ID).retrievals == ()


def test_retrying_at_the_retrieval_cap_does_not_duplicate_the_manual_drop():
    """上限时不写 trace；同一 pass 重入不能把同一条 dropped 再加一遍。"""

    packet = make_material("超" * 5000)
    round_ = make_round()
    round_.note_manual_chunks((ManualChunk("m1", packet.parsed.chunks[0].chunk_id),))
    round_.pin_materials(("m1",))
    # 当前 pass 是 seat_round_count=1；用 2/3/4 凑满上限，避免命中"已有 trace"分支。
    for index in range(3):
        round_.note_retrieval(
            RetrievalTrace(
                stage=RetrievalStage.SEAT,
                pass_index=index + 2,
                query="权限",
                hits=(),
                selected=(),
                ok=True,
                error=None,
                created_at=NOW,
            )
        )
    harness = seeded_harness(packet, round_=round_)
    one_preparer = preparer(harness, RecordingMaterialRetriever(returns=()))

    one_preparer.prepare(round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="权限")
    one_preparer.prepare(round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="权限")

    drops = [
        item
        for item in harness.rounds.get(ROUND_ID).dropped
        if item.source is DropSource.TOOL
    ]
    assert len(drops) == 1, drops


def test_web_selection_filters_the_numbered_web_chunks_rendered():
    """新 payload 只渲染本步选中的 web 块；没选中的旧编号留在轮次里当历史。"""

    first = make_material("第一份联网正文", material_id="w1")
    first = replace(first, parsed=replace(first.parsed, origin="web_search"))
    second = make_material("第二份联网正文", material_id="w2")
    second = replace(second, parsed=replace(second.parsed, origin="web_search"))
    harness = seeded_harness(first, second)
    ledger = harness.evidences.get(PROJECT)
    first_eid = ledger.record(
        kind=EvidenceKind.EXTERNAL,
        text="第一份联网正文",
        source=EvidenceSource(external_ref="https://example.com/first"),
        now=NOW,
    )
    second_eid = ledger.record(
        kind=EvidenceKind.EXTERNAL,
        text="第二份联网正文",
        source=EvidenceSource(external_ref="https://example.com/second"),
        now=NOW,
    )
    harness.evidences.save(ledger)
    round_ = make_round()
    round_.add_materials(
        (
            MaterialRef(first.material_id, first.parsed.chunks[0].chunk_id, first_eid),
            MaterialRef(second.material_id, second.parsed.chunks[0].chunk_id, second_eid),
        )
    )
    round_.note_web_selection(((first.material_id, first.parsed.chunks[0].chunk_id),))
    harness.rounds.seed(round_)

    prepared = preparer(harness, RecordingMaterialRetriever(returns=())).prepare(
        round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="联网"
    )

    assert str(first_eid) in prepared.text
    assert str(second_eid) not in prepared.text
    assert "第二份联网正文" not in prepared.text


def test_old_payload_without_web_selection_still_renders_every_web_ref():
    first = make_material("第一份联网正文", material_id="w1")
    first = replace(first, parsed=replace(first.parsed, origin="web_search"))
    second = make_material("第二份联网正文", material_id="w2")
    second = replace(second, parsed=replace(second.parsed, origin="web_search"))
    harness = seeded_harness(first, second)
    ledger = harness.evidences.get(PROJECT)
    refs = []
    for packet, url in ((first, "https://example.com/first"), (second, "https://example.com/second")):
        eid = ledger.record(
            kind=EvidenceKind.EXTERNAL,
            text=packet.parsed.chunks[0].text,
            source=EvidenceSource(external_ref=url),
            now=NOW,
        )
        refs.append(MaterialRef(packet.material_id, packet.parsed.chunks[0].chunk_id, eid))
    harness.evidences.save(ledger)
    round_ = make_round()
    round_.add_materials(refs)
    harness.rounds.seed(round_)
    assert harness.rounds.get(ROUND_ID).web_selection is None

    prepared = preparer(harness, RecordingMaterialRetriever(returns=())).prepare(
        round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="联网"
    )

    assert "第一份联网正文" in prepared.text
    assert "第二份联网正文" in prepared.text


def test_external_material_text_filters_web_selection_and_falls_back_to_none():
    """M2：无 preparer 的旁路也要守 `web_selection`；None 时回退全部旧 refs。"""

    from pmstudio.application.use_cases import external_material_text

    first = make_material("第一份联网正文", material_id="w1")
    first = replace(first, parsed=replace(first.parsed, origin="web_search"))
    second = make_material("第二份联网正文", material_id="w2")
    second = replace(second, parsed=replace(second.parsed, origin="web_search"))
    harness = seeded_harness(first, second)
    ledger = harness.evidences.get(PROJECT)
    first_eid = ledger.record(
        kind=EvidenceKind.EXTERNAL,
        text="第一份联网正文",
        source=EvidenceSource(external_ref="https://example.com/first"),
        now=NOW,
    )
    second_eid = ledger.record(
        kind=EvidenceKind.EXTERNAL,
        text="第二份联网正文",
        source=EvidenceSource(external_ref="https://example.com/second"),
        now=NOW,
    )
    harness.evidences.save(ledger)
    round_ = make_round()
    round_.add_materials(
        (
            MaterialRef(first.material_id, first.parsed.chunks[0].chunk_id, first_eid),
            MaterialRef(second.material_id, second.parsed.chunks[0].chunk_id, second_eid),
        )
    )
    round_.note_web_selection(((first.material_id, first.parsed.chunks[0].chunk_id),))

    selected_text = external_material_text(round_, store=harness.materials)
    assert str(first_eid) in selected_text
    assert "第一份联网正文" in selected_text
    assert str(second_eid) not in selected_text
    assert "第二份联网正文" not in selected_text

    round_.clear_web_selection()
    fallback_text = external_material_text(round_, store=harness.materials)
    assert str(first_eid) in fallback_text
    assert str(second_eid) in fallback_text
    assert "第一份联网正文" in fallback_text
    assert "第二份联网正文" in fallback_text


def test_dirty_multi_eid_web_result_uses_the_same_first_chunk_in_both_renderers():
    """M1：旧脏数据一块一个号时，preparer 与 external_material_text 取同一块。"""

    from pmstudio.application.use_cases import external_material_text

    web = make_material("第一段正文", "第二段正文", material_id="w1")
    web = replace(web, parsed=replace(web.parsed, origin="web_search"))
    harness = seeded_harness(web)
    ledger = harness.evidences.get(PROJECT)
    first_eid = ledger.record(
        kind=EvidenceKind.EXTERNAL,
        text="第一段正文",
        source=EvidenceSource(external_ref="https://example.com/dirty"),
        now=NOW,
    )
    second_eid = ledger.record(
        kind=EvidenceKind.EXTERNAL,
        text="第二段正文",
        source=EvidenceSource(external_ref="https://example.com/dirty"),
        now=NOW,
    )
    harness.evidences.save(ledger)
    round_ = make_round()
    round_.add_materials(
        (
            MaterialRef(web.material_id, web.parsed.chunks[0].chunk_id, first_eid),
            MaterialRef(web.material_id, web.parsed.chunks[1].chunk_id, second_eid),
        )
    )
    round_.note_web_selection(
        (
            (web.material_id, web.parsed.chunks[0].chunk_id),
            (web.material_id, web.parsed.chunks[1].chunk_id),
        )
    )
    harness.rounds.seed(round_)

    prepared = preparer(harness, RecordingMaterialRetriever(returns=())).prepare(
        round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="联网"
    )
    external = external_material_text(harness.rounds.get(ROUND_ID), store=harness.materials)

    assert str(first_eid) in prepared.text
    assert str(first_eid) in external
    assert str(second_eid) not in prepared.text
    assert str(second_eid) not in external


def test_prepare_renders_attached_research_cards_with_their_eids():
    """挂到这一轮的来源卡必须出现在**每步投影**的文本里（P3b-1）。

    这是"零白名单"的主路径：`known_eids = visible ∩ 账本`，只要 `[E..]` 渲染出来，
    来源 E 就自动成为模型可引用的编号。回退路径（`external_material_text`）在
    `test_attach_research.py` 里另有一条。
    """

    from pmstudio.application.document_window import visible_eids
    from pmstudio.domain.round.research import RoundResearchSource
    from pmstudio.domain.shared.ids import EvidenceId

    harness = seeded_harness(make_material("权限：管理员才能导出"))
    round_ = harness.rounds.get(ROUND_ID)
    round_.attach_research_sources(
        [
            RoundResearchSource(
                eid=EvidenceId("E7"),
                title="白皮书",
                url="https://example.com/a",
                excerpt="同类产品默认 60 秒上限。",
            )
        ]
    )
    harness.rounds.seed(round_)
    harness.evidences.get(PROJECT).record(
        kind=EvidenceKind.EXTERNAL,
        text="白皮书\nhttps://example.com/a\n同类产品默认 60 秒上限。",
        source=EvidenceSource(external_ref="https://example.com/a"),
        now=NOW,
        eid=EvidenceId("E7"),
    )

    prepared = preparer(harness, None).prepare(
        round_id=ROUND_ID, stage=RetrievalStage.SEAT, query="权限"
    )

    assert "[E7]" in prepared.text
    assert "同类产品默认 60 秒上限" in prepared.text
    assert EvidenceId("E7") in visible_eids(prepared.text)
