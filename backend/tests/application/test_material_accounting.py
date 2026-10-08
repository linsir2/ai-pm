"""F35：材料块进轮但零引用 → 可机械判定（先量不修，只留痕）。

核算口径：Round.materials 的 eid 集合 减去 (提案引用的 eid ∪ 题目引用的 eid)。
差集 = 本轮带进来但没被任何提案/题目引用的材料块出处编号。
"""

from datetime import UTC, datetime

from pmstudio.application.use_cases import _unreferenced_materials_for
from pmstudio.domain.round.draft import Origin, OriginType, Proposal, WriteOp
from pmstudio.domain.round.questionnaire import Question, QuestionType
from pmstudio.domain.round.round import MaterialRef, Round, RoundId, Step
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, QuestionId
from pmstudio.domain.shared.target import Target, Zone

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def _round_with_materials(materials):
    round_ = Round(
        round_id=RoundId("r-test"),
        project_id=ProjectId("p-test"),
        user_input="测试输入",
        created_at=NOW,
        step=Step.SEATS_RUNNING,
    )
    if materials:
        round_.add_materials(tuple(materials))
    return round_


def _proposal_with_refs(refs):
    return Proposal(
        proposal_id="p1",
        entry_id="en1",
        op=WriteOp.ADD,
        target=Target(zone=Zone.BODY, section_id="s1"),
        text="测试提案",
        origin=Origin(type=OriginType.USER, refs=tuple(EvidenceId(e) for e in refs)),
    )


def _question_with_refs(refs):
    return Question(
        question_id=QuestionId("q1"),
        target=Target(zone=Zone.BODY, section_id="s1"),
        type=QuestionType.SINGLE,
        title="测试题目",
        why="测试",
        consequence_if_skipped="跳过",
        options=(("a", "选项A"), ("b", "选项B")),
        evidence_refs=tuple(EvidenceId(e) for e in refs),
    )


class TestUnreferencedMaterialsFor:
    """_unreferenced_materials_for 的纯函数测试。"""

    def test_referenced_material_not_flagged(self):
        """材料块进轮且被提案引用 → 不标记。"""
        round_ = _round_with_materials(
            [MaterialRef(material_id="m1", chunk_id="m1#c0", eid=EvidenceId("E5"))]
        )
        proposals = [_proposal_with_refs(["E5"])]
        questions = []

        result = _unreferenced_materials_for(
            round_, proposals=proposals, questions=questions
        )

        assert result == ()

    def test_unreferenced_material_flagged(self):
        """材料块进轮但零引用 → 标记。"""
        round_ = _round_with_materials(
            [MaterialRef(material_id="m1", chunk_id="m1#c0", eid=EvidenceId("E5"))]
        )
        proposals = [_proposal_with_refs(["E1"])]
        questions = []

        result = _unreferenced_materials_for(
            round_, proposals=proposals, questions=questions
        )

        assert result == ("E5",)

    def test_partial_reference(self):
        """多个材料块部分引用 → 只标记未引用的。"""
        round_ = _round_with_materials(
            [
                MaterialRef(material_id="m1", chunk_id="m1#c0", eid=EvidenceId("E5")),
                MaterialRef(material_id="m2", chunk_id="m2#c0", eid=EvidenceId("E8")),
            ]
        )
        proposals = [_proposal_with_refs(["E5"])]
        questions = []

        result = _unreferenced_materials_for(
            round_, proposals=proposals, questions=questions
        )

        assert result == ("E8",)

    def test_no_materials_in_round(self):
        """本轮没带材料 → 不报错，返回空。"""
        round_ = _round_with_materials([])
        proposals = [_proposal_with_refs(["E1"])]
        questions = []

        result = _unreferenced_materials_for(
            round_, proposals=proposals, questions=questions
        )

        assert result == ()

    def test_question_reference_counts(self):
        """题目引用也算"交代过"。"""
        round_ = _round_with_materials(
            [MaterialRef(material_id="m1", chunk_id="m1#c0", eid=EvidenceId("E5"))]
        )
        proposals = []
        questions = [_question_with_refs(["E5"])]

        result = _unreferenced_materials_for(
            round_, proposals=proposals, questions=questions
        )

        assert result == ()
