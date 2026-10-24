"""每步上下文准备（P2.3）：检索 → 投影 → 发号 → 一个事务落库。

关键行为：
- 新检索到的块**先入池**再投影（否则会"命中 K、带进 0"）；
- E 号只发给选中块；
- 同一 (stage, pass) 重入不重查、不重发号；
- web 材料不进材料池，只走 `render_web_results`；
- 检索失败留痕（trace.ok=False），仍从池投影。
"""

from dataclasses import replace

from pmstudio.application.dto import MaterialHit
from pmstudio.application.round_context import (
    MAX_ROUND_RETRIEVALS,
    PreparedContext,
    RoundContextPreparer,
)
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.round.retrieval import RetrievalStage, RetrievalTrace
from pmstudio.domain.round.round import MaterialRef, Round
from pmstudio.domain.shared.template import standard_template
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.materials import (
    SqliteFtsRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteEvidenceRepository,
    SqliteRoundRepository,
)
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.api.test_routes import QUESTION_REPLY
from tests.application.test_materials import make_material
from tests.application.test_seat_round import SEAT_REPLY, claims_reply, started_round
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_round
from tests.support.doubles import FixedClock, RecordingMaterialRetriever
from tests.support.harness import Harness


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


def test_the_round_retrieval_cap_stops_querying_but_still_projects():
    packet = make_material("权限：管理员才能导出")
    round_ = make_round()
    round_.pin_materials(("m1",))
    for index, stage in enumerate(
        (RetrievalStage.SEAT, RetrievalStage.SEAT, RetrievalStage.INTEGRATE), start=1
    ):
        round_.note_retrieval(
            RetrievalTrace(
                stage=stage,
                pass_index=index,
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
    assert len(round_after.retrievals) == MAX_ROUND_RETRIEVALS
    assert "权限：管理员才能导出" in prepared.text


def test_prepare_round_trips_through_real_sqlite(tmp_path):
    connection = connect(tmp_path / "context.db")
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

    prepared = RoundContextPreparer(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        materials=store,
        retriever=SqliteFtsRetriever(connection),
        uow=SqliteUnitOfWork(connection),
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
