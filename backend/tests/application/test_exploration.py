"""探索纯函数：接地检查 / 条目定位 / 产物校验 / 卡片渲染 / 材料合成。"""

import pytest

from pmstudio.application.deliverable import iter_document_entries
from pmstudio.application.errors import UnknownEntryId
from pmstudio.domain.exploration.exploration import ExplorationScope, ScopeKind
from pmstudio.domain.exploration.item import (
    BlindSpot,
    Candidate,
    CandidateState,
    Grounding,
    Hypothesis,
)
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId
from tests.support.builders import document_with_feature, make_document

KNOWN = frozenset({EvidenceId("E1"), EvidenceId("E2")})


def hypo(**over: object) -> Hypothesis:
    fields: dict[str, object] = {
        "local_id": "H1",
        "statement": "运营做日报时按客户名找单",
        "grounding": Grounding.INVENTED,
        "based_on": (),
        "falsification": "访谈后若没人这么找即被推翻",
        "evidence_needed": "两个真实运营的访谈记录",
    }
    fields.update(over)
    return Hypothesis(**fields)  # type: ignore[arg-type]


def blind() -> BlindSpot:
    return BlindSpot(local_id="B1", statement="客户合并后的归属未知", next_step="去问数据组")


def test_document_has_content_distinguishes_empty_and_filled() -> None:
    from pmstudio.application.exploration import document_has_content

    assert document_has_content(make_document().view()) is False
    assert document_has_content(document_with_feature().view()) is True


def test_entry_focus_returns_the_entry_text_and_rejects_unknown() -> None:
    from pmstudio.application.exploration import entry_focus

    view = document_with_feature().view()
    first = next(iter_document_entries(view))
    assert entry_focus(view, str(first.entry_id)) == first.text

    with pytest.raises(UnknownEntryId):
        entry_focus(view, "entry-nope")


def test_scope_line_renders_range_and_focus() -> None:
    from pmstudio.application.exploration import scope_line

    whole = ExplorationScope(kind=ScopeKind.DOCUMENT)
    assert scope_line(whole) == "调研范围：整份文档"

    entry = ExplorationScope(kind=ScopeKind.ENTRY, ref="e1", text="重点看异常路径")
    line = scope_line(entry, focus="把订单导出成表格")
    assert "某一条内容" in line
    assert "把订单导出成表格" in line
    assert "重点看异常路径" in line


def test_validate_inquiry_rejects_unknown_based_on() -> None:
    from pmstudio.application.exploration import validate_inquiry

    item = hypo(grounding=Grounding.DOCUMENT, based_on=(EvidenceId("E99"),))
    with pytest.raises(ShapeError):
        validate_inquiry((item,), (), known_eids=KNOWN)


def test_validate_inquiry_rejects_numeric_claims() -> None:
    from pmstudio.application.exploration import validate_inquiry

    with pytest.raises(ShapeError):
        validate_inquiry((hypo(statement="约 62% 的用户会按客户名找单"),), (), known_eids=KNOWN)
    with pytest.raises(ShapeError):
        validate_inquiry((hypo(statement="调查显示多数人会这么干"),), (), known_eids=KNOWN)


def test_validate_inquiry_passes_clean_produce() -> None:
    from pmstudio.application.exploration import validate_inquiry

    grounded = hypo(grounding=Grounding.DOCUMENT, based_on=(EvidenceId("E1"),))
    validate_inquiry((grounded,), (blind(),), known_eids=KNOWN)


def test_candidate_card_renders_both_shapes() -> None:
    from pmstudio.application.exploration import candidate_card

    hypothesis = Candidate(
        candidate_id="H1",
        kind="hypothesis",
        statement="运营按客户名找单",
        state=CandidateState.NEW,
        grounding=Grounding.INVENTED,
        falsification="访谈没人这么找",
        evidence_needed="两个运营的访谈",
    )
    text = candidate_card(hypothesis)
    assert "假设：" in text
    assert "怎么被推翻" in text
    assert "需要什么真实证据" in text
    assert "凭空推演" in text

    spot = Candidate(
        candidate_id="B1",
        kind="blind_spot",
        statement="客户合并后的归属未知",
        state=CandidateState.NEW,
        next_step="去问数据组",
    )
    assert "下一步" in candidate_card(spot)


def test_candidate_material_shape_and_stable_key() -> None:
    from pmstudio.application.exploration import (
        ORIGIN_EXPLORATION,
        candidate_material,
        material_sha,
    )

    hypothesis = Candidate(
        candidate_id="H1",
        kind="hypothesis",
        statement="运营按客户名找单",
        state=CandidateState.NEW,
        grounding=Grounding.INVENTED,
        falsification="访谈没人这么找",
        evidence_needed="两个运营的访谈",
    )
    parsed, content = candidate_material(hypothesis, exploration_id="x-1")

    assert parsed.origin == ORIGIN_EXPLORATION
    assert parsed.source_ref == "exploration:x-1#H1"
    assert parsed.parser_id == "exploration"
    assert len(parsed.chunks) == 1
    assert "运营按客户名找单" in parsed.chunks[0].text
    assert content == parsed.chunks[0].text.encode("utf-8")
    assert parsed.title.startswith("调研假设")

    key = material_sha(parsed.source_ref)
    assert key == material_sha("exploration:x-1#H1")
    assert key != material_sha("exploration:x-1#B1")
