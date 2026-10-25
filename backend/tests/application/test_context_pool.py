"""证据池投影的两个纯函数（P2.1/P2.2）。

不碰 IO、不调模型：stage query 怎么构造、池里怎么选块，全部在纯函数里定死。
"""

import pytest

from pmstudio.application.context import material_block_cost
from pmstudio.application.context_pool import (
    PROJECTION_SLOTS_PER_MATERIAL,
    build_stage_query,
    select_projection,
    split_material_budget,
)
from pmstudio.application.dto import MaterialChunk, MaterialPacket, ParsedMaterial
from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.authoring.readiness import ReadinessItem, Severity
from pmstudio.domain.round.claim import Claim, Contradiction
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    Option,
    Question,
    QuestionKind,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.retrieval import ManualChunk, RetrievalHit, RetrievalStage
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import EvidenceId, FormId, QuestionId
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from tests.application.test_materials import make_material
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_claim, make_round


def question(*, qid: str = "q1", title: str = "单次导出的最大行数", blocking: bool = True):
    return Question(
        question_id=QuestionId(qid),
        target=Target.body("s5"),
        type=QuestionType.SINGLE,
        title=title,
        why="量级决定同步还是异步",
        consequence_if_skipped="按同步实现",
        options=(Option("a", "少于 1 万"), Option("b", "1 万以上")),
        kind=QuestionKind.GAP,
        blocking=blocking,
    )


def readiness_item(*, title: str = "功能缺口", detail: str = "login 还缺 3 项"):
    return ReadinessItem(
        section_id="s5",
        code="feature_field_empty",
        title=title,
        detail=detail,
        severity=Severity.BLOCKER,
    )


# —— build_stage_query ——


def test_seat_query_uses_claims_seat_scope_blocking_questions_and_gaps():
    round_ = make_round()
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=ROUND_ID,
            questions=(
                question(qid="q1", title="最大行数", blocking=True),
                question(qid="q2", title="导出格式", blocking=False),
            ),
        )
    )
    seats = (
        SeatDefinition(
            seat_id=SEAT,
            title="数据规模",
            focus="导出量级与耗时",
            sections=("s5", "s8"),
            fields=("acceptance",),
        ),
    )

    query = build_stage_query(
        RetrievalStage.SEAT, round_=round_, seats=seats, readiness=(readiness_item(),)
    )

    assert "导出量级与耗时" in query
    assert "s8" in query and "acceptance" in query
    assert round_.claims[0].text in query
    assert "最大行数" in query
    assert "导出格式" not in query, "非阻塞题不该进 SEAT query"
    assert "功能缺口" in query


def test_integrate_query_uses_gaps_deferred_contradictions_and_unreferenced_claims():
    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="点检记录不许改；点错了要能改", now=NOW
    )
    first = make_claim(eid=EvidenceId("E1"))
    second = Claim(
        claim_id="c2",
        text="点错了要能改",
        label=ClaimLabel.NEED,
        target=Target.body("s5"),
        raw_span="点错了要能改",
        eid=EvidenceId("E2"),
    )
    round_.attach_claims((first, second))
    round_.submit_opening(seats=(SEAT,))
    round_.attach_contradictions(
        (Contradiction("c1", "c2", "一个说不能改，一个说要能改"),)
    )
    round_.attest(
        SeatReview(
            seat_id=SEAT,
            basis="读了文档",
            opinions=(
                Opinion(
                    local_id="O1",
                    form=OpinionForm.JUDGMENT,
                    about="改与不改",
                    claim="应该允许改",
                    because="用户明确要求",
                    evidence_refs=(EvidenceId("E1"),),
                ),
            ),
        )
    )
    round_.defer((question(qid="q1", title="最大行数"),))

    query = build_stage_query(
        RetrievalStage.INTEGRATE, round_=round_, readiness=(readiness_item(),)
    )

    assert "功能缺口" in query
    assert "最大行数" in query
    assert "一个说不能改" in query
    assert "点错了要能改" in query, "未被席位意见引用的主张要进 query"
    assert first.text not in query, "已被席位意见引用的主张不该重复占位"


def test_stage_query_is_deterministic_and_capped():
    round_ = make_round()
    seats = (
        SeatDefinition(
            seat_id=SEAT,
            title="数据规模",
            focus="导出量级",
            sections=("s5",),
            fields=("acceptance",),
        ),
    )

    one = build_stage_query(RetrievalStage.SEAT, round_=round_, seats=seats)
    two = build_stage_query(RetrievalStage.SEAT, round_=round_, seats=seats)

    assert one == two
    assert 0 < len(one) <= 300


def test_seed_has_no_stage_query():
    with pytest.raises(ValueError):
        build_stage_query(RetrievalStage.SEED, round_=make_round())


# —— split_material_budget ——


def test_auto_reserve_is_the_remainder_after_pinned_cap():
    split = split_material_budget(usable=1000, pinned_share=0.6, auto_costs=(400,))

    assert split.usable == 1000
    assert split.auto_feasible is True
    assert split.pinned_cap == 600
    assert split.usable - split.pinned_cap == 400


def test_no_auto_candidates_does_not_reserve():
    split = split_material_budget(usable=1000, pinned_share=0.6, auto_costs=())

    assert split.auto_feasible is False
    assert split.pinned_cap == 1000


def test_auto_candidate_that_cannot_fit_does_not_reserve():
    split = split_material_budget(usable=1000, pinned_share=0.6, auto_costs=(401,))

    assert split.auto_feasible is False
    assert split.pinned_cap == 1000


def test_pinned_share_is_bounded():
    with pytest.raises(ValueError):
        split_material_budget(usable=1000, pinned_share=1.1, auto_costs=())


# —— select_projection ——


def packets_of(*items):
    return {item.material_id: item for item in items}


def pool_of(*items):
    return {
        item.material_id: frozenset(chunk.chunk_id for chunk in item.parsed.chunks)
        for item in items
    }


def hit(packet: MaterialPacket, index: int, score: float = 1.0) -> RetrievalHit:
    chunk = packet.parsed.chunks[index]
    return RetrievalHit(packet.material_id, chunk.chunk_id, score)


def spent_on(selection, packets, *, only: set[str] | None = None) -> int:
    seen: set[str] = set()
    total = 0
    for material_id, chunk in selection.chunks:
        if only is not None and material_id not in only:
            continue
        first = material_id not in seen
        seen.add(material_id)
        total += material_block_cost(
            packets[material_id], chunk, first_of_material=first
        )
    return total


def test_manual_chunks_are_selected_first():
    manual_packet = make_material("手动带进来的一段", material_id="m2")
    other = make_material("普通材料", material_id="m1")
    selection = select_projection(
        packets=packets_of(manual_packet, other),
        pool=pool_of(manual_packet, other),
        pinned=(),
        manual=(ManualChunk("m2", "m2#c00"),),
        stage_hits=(hit(other, 0),),
        query="普通",
        budget=400,
        section_overhead=0,
    )

    assert selection.chunks[0] == ("m2", manual_packet.parsed.chunks[0])


def test_pinned_materials_never_exceed_the_share_when_auto_candidates_exist():
    pinned = make_material(*[f"第 {index} 段" * 10 for index in range(10)], material_id="m1")
    auto = make_material("自动材料" * 20, material_id="m2")
    packets = packets_of(pinned, auto)
    selection = select_projection(
        packets=packets,
        pool=pool_of(pinned, auto),
        pinned=("m1",),
        manual=(),
        stage_hits=(),
        query="第 1 段",
        budget=1000,
        section_overhead=0,
        pinned_share=0.6,
    )

    pinned_spent = spent_on(selection, packets, only={"m1"})
    assert any(material_id == "m1" for material_id, _ in selection.chunks)
    assert 0 < pinned_spent <= 600
    assert any(material_id == "m2" for material_id, _ in selection.chunks)


def test_a_single_pinned_block_over_the_cap_does_not_get_floor_rescued():
    """单块手勾超过 60% cap 时，地板不能绕过 cap 把它装回来（D1）。"""

    pinned = make_material("超" * 3000, material_id="m1")
    auto = make_material("自动材料", material_id="m2")
    packets = packets_of(pinned, auto)

    selection = select_projection(
        packets=packets,
        pool=pool_of(pinned, auto),
        pinned=("m1",),
        manual=(),
        stage_hits=(),
        query="自动",
        budget=4000,
        section_overhead=204,
        pinned_share=0.6,
    )

    assert all(material_id != "m1" for material_id, _ in selection.chunks)
    assert any(material_id == "m2" for material_id, _ in selection.chunks)
    assert spent_on(selection, packets, only={"m1"}) == 0


def test_no_auto_candidates_lets_pinned_use_full_budget():
    pinned = make_material("字" * 800, material_id="m1")
    selection = select_projection(
        packets=packets_of(pinned),
        pool=pool_of(pinned),
        pinned=("m1",),
        manual=(),
        stage_hits=(),
        query="字",
        budget=1000,
        section_overhead=0,
        pinned_share=0.6,
    )

    assert [chunk.chunk_id for _, chunk in selection.chunks] == ["m1#c00"]
    assert spent_on(selection, packets_of(pinned)) > 600


def test_manual_over_the_cap_is_skipped_when_auto_candidates_exist():
    manual = make_material("手动" * 300, material_id="m1")
    auto = make_material("自动", material_id="m2")
    packets = packets_of(manual, auto)
    selection = select_projection(
        packets=packets,
        pool=pool_of(manual, auto),
        pinned=("m1",),
        manual=(ManualChunk("m1", manual.parsed.chunks[0].chunk_id),),
        stage_hits=(),
        query="",
        budget=1000,
        section_overhead=0,
        pinned_share=0.6,
    )

    assert selection.skipped_manual == ("m1#c00",)
    assert all(material_id != "m1" for material_id, _ in selection.chunks)


def test_a_second_pinned_material_is_not_forced_over_the_cap():
    first = make_material(*[f"甲 {index} " + "字" * 100 for index in range(10)], material_id="m1")
    second = make_material("乙 " + "字" * 100, material_id="m2")
    auto = make_material("自动", material_id="m3")
    packets = packets_of(first, second, auto)
    selection = select_projection(
        packets=packets,
        pool=pool_of(first, second, auto),
        pinned=("m1", "m2"),
        manual=(),
        stage_hits=(),
        query="甲 乙",
        budget=1000,
        section_overhead=0,
        pinned_share=0.6,
    )

    pinned_spent = spent_on(selection, packets, only={"m1", "m2"})
    assert any(material_id == "m1" for material_id, _ in selection.chunks)
    assert 0 < pinned_spent <= 600
    assert any(material_id == "m3" for material_id, _ in selection.chunks)


def test_stage_hits_come_before_the_rest_of_the_pool():
    packet = make_material("甲", "乙", "丙", material_id="m1")
    selection = select_projection(
        packets=packets_of(packet),
        pool=pool_of(packet),
        pinned=(),
        manual=(),
        stage_hits=(hit(packet, 2),),
        query="完全不匹配",
        # 一份材料的抬头行也要占成本 → 60 字装不下第一块；这里只验顺序，给够一块的钱。
        budget=200,
        section_overhead=0,
    )

    assert selection.chunks[0][1].chunk_id == "m1#c02"


def test_an_auto_material_gets_at_most_two_slots():
    packet = make_material("甲", "乙", "丙", material_id="m1")
    selection = select_projection(
        packets=packets_of(packet),
        pool=pool_of(packet),
        pinned=(),
        manual=(),
        stage_hits=(),
        query="甲 乙 丙",
        budget=2000,
        section_overhead=0,
    )

    assert len(selection.chunks) == PROJECTION_SLOTS_PER_MATERIAL


def test_a_chunk_is_selected_at_most_once():
    packet = make_material("甲", material_id="m1")
    selection = select_projection(
        packets=packets_of(packet),
        pool=pool_of(packet),
        pinned=(),
        manual=(ManualChunk("m1", "m1#c00"),),
        stage_hits=(hit(packet, 0),),
        query="甲",
        budget=400,
        section_overhead=0,
    )

    assert [chunk.chunk_id for _, chunk in selection.chunks] == ["m1#c00"]


def test_the_budget_is_a_hard_cap():
    packet = make_material(*[f"第 {index} 段" * 20 for index in range(10)], material_id="m1")
    selection = select_projection(
        packets=packets_of(packet),
        pool=pool_of(packet),
        pinned=(),
        manual=(),
        stage_hits=(),
        query="第 1 段",
        budget=300,
        section_overhead=40,
    )

    assert selection.used <= 260


def test_projection_is_deterministic():
    packet = make_material("甲", "乙", "丙", material_id="m1")
    kwargs = dict(
        packets=packets_of(packet),
        pool=pool_of(packet),
        pinned=(),
        manual=(),
        stage_hits=(hit(packet, 1),),
        query="甲 乙",
        budget=400,
        section_overhead=0,
    )

    assert select_projection(**kwargs) == select_projection(**kwargs)


def test_pdf_parent_cost_is_counted_for_the_selected_child():
    """PDF 同页的兄弟块会一起渲染 → 成本必须按父块算（否则预算失真）。"""

    packet = MaterialPacket(
        material_id="pdf",
        project_id=str(PROJECT),
        parsed=ParsedMaterial(
            origin="upload",
            title="诊断报告.pdf",
            source_ref="attachment:诊断报告.pdf",
            media_type="application/pdf",
            bytes_len=10,
            chunks=(
                MaterialChunk(
                    chunk_id="pdf#c00",
                    ordinal=0,
                    text="第一段内容",
                    heading_path=(),
                    anchor="第 1 页",
                ),
                MaterialChunk(
                    chunk_id="pdf#c01",
                    ordinal=1,
                    text="第二段内容",
                    heading_path=(),
                    anchor="第 1 页",
                ),
            ),
            parser_id="pdfminer",
            parser_version="v1",
        ),
        created_at=NOW,
    )
    selection = select_projection(
        packets={"pdf": packet},
        pool={"pdf": frozenset({"pdf#c00", "pdf#c01"})},
        pinned=(),
        manual=(),
        stage_hits=(),
        query="第一段",
        budget=400,
        section_overhead=0,
    )

    assert selection.chunks
    assert selection.used >= len("第一段内容") + len("第二段内容")


def test_every_pinned_material_gets_a_floor_without_auto_candidates():
    """没有自动候选时手勾 cap = 整个可用预算：两份手勾都能至少露一块。"""

    first = make_material(
        *[f"甲 {index} " + "字" * 100 for index in range(10)], material_id="m1"
    )
    second = make_material("乙 " + "字" * 100, material_id="m2")
    packets = packets_of(first, second)

    selection = select_projection(
        packets=packets,
        pool=pool_of(first, second),
        pinned=("m1", "m2"),
        manual=(),
        stage_hits=(),
        query="甲 乙",
        budget=1000,
        section_overhead=0,
        pinned_share=0.6,
    )

    materials = {material_id for material_id, _ in selection.chunks}
    assert {"m1", "m2"} <= materials
    assert selection.used <= 1000


def test_a_manual_chunk_can_use_full_budget_without_auto_candidates():
    """没有自动候选时手勾可用满预算 → manual 块能进来，也不该被报成 skipped。"""

    packet = make_material("字" * 250)
    chunk = packet.parsed.chunks[0]
    cost = material_block_cost(packet, chunk, first_of_material=True)
    budget = cost

    selection = select_projection(
        packets={"m1": packet},
        pool={"m1": frozenset({chunk.chunk_id})},
        pinned=("m1",),
        manual=(ManualChunk("m1", chunk.chunk_id),),
        stage_hits=(),
        query="",
        budget=budget,
        section_overhead=0,
        pinned_share=0.6,
    )

    assert [chunk.chunk_id for _, chunk in selection.chunks] == [chunk.chunk_id]
    assert selection.skipped_manual == ()
