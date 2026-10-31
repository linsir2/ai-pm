"""真实整合消费：不能只给模型批盖章，机械合流必须可独立确认。"""

import pytest

from pmstudio.domain.round.draft import OriginType
from pmstudio.domain.shared.ids import EntryId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from tests.application.test_exact_write_consumers import mechanical_round
from tests.domain.test_exact_write_guards import apply, instruction
from tests.support.builders import PROJECT


async def test_model_input_signature_survives_later_mechanical_document_read():
    harness, rid = await mechanical_round(target=Target.body("s1"), text="本轮用户事实")
    use_case = harness.integrate_round()
    curate = use_case._curate

    async def advance_document(**kwargs):
        result = await curate(**kwargs)
        document = harness.documents.get(PROJECT)
        apply(document, instruction("unrelated", "期间新事实", target=Target.body("s3")))
        harness.documents.save(document)
        return result

    use_case._curate = advance_document
    await use_case(round_id=rid)
    draft = harness.rounds.get(rid).draft
    model, mechanical = draft.proposals
    assert model.base_version_no == 2
    assert mechanical.base_version_no == 3
    assert mechanical.origin.type is OriginType.USER
    await harness.confirm_draft()(
        round_id=rid, draft_id=str(draft.draft_id), accepted=(str(mechanical.proposal_id),)
    )
    entry = harness.documents.get(PROJECT).locate_entry(mechanical.entry_id).entry
    assert entry.text == "本轮用户事实"


async def test_empty_single_competitors_remain_cards_and_each_is_confirmable_alone():
    target = Target.body("s5", feature_slug="order-export", field="priority")
    harness, rid = await mechanical_round(target=target, text="P1")
    import json

    from tests.support.doubles import ScriptedModelGateway

    packet = {"questions": [], "list_items": [], "proposals": [{
        "op": "add", "entry_id": None,
        "target": {"zone": "body", "section_id": "s5", "feature_slug": "order-export",
                   "field": "priority"},
        "text": "P0", "origin_type": "proposed", "evidence_refs": ["E2"],
    }]}
    use_case = harness.integrate_round()
    use_case._integrator._model = ScriptedModelGateway(replies={"integration": json.dumps(packet)})
    await use_case(round_id=rid)
    draft = harness.rounds.get(rid).draft
    assert len(draft.proposals) == 2
    assert [p.op for p in draft.proposals] == [WriteOp.ADD, WriteOp.ADD]
    assert any("未生成全选结果预览" in note for note in draft.gaps)
    mechanical = draft.proposals[1]
    await harness.confirm_draft()(
        round_id=rid, draft_id=str(draft.draft_id), accepted=(str(mechanical.proposal_id),)
    )
    entry = harness.documents.get(PROJECT).locate_entry(EntryId(mechanical.entry_id)).entry
    assert entry.text == "P1"


@pytest.mark.parametrize("missing", [False, True])
async def test_retired_open_item_keeps_real_old_text_and_never_consumes_the_answer(missing):
    from pmstudio.application.use_cases import AnswerInput
    from tests.application.test_integrate_answers import (
        CLAIMS_REPLY,
        OPEN_ITEM_TEXT,
        SEAT_REPLY,
        authorize_order_export,
        draft_packet,
        questionnaire_packet,
        round_in_seat_pass,
        seed_open_item,
    )
    from tests.support.builders import SEAT
    from tests.support.harness import Harness

    harness = Harness(replies={"claims": CLAIMS_REPLY,
                               "integration": [questionnaire_packet(), draft_packet()]},
                      seat_replies={SEAT: SEAT_REPLY})
    rid = await round_in_seat_pass(harness)
    seed_open_item(harness, entry_id="r0#open01", text=OPEN_ITEM_TEXT)
    authorize_order_export(harness, rid)
    await harness.integrate_round()(round_id=rid)
    form = harness.rounds.get(rid).questionnaire
    question = form.questions[0]
    await harness.submit_answers()(round_id=rid, form_id=str(form.form_id), answers=(
        AnswerInput(question_id=str(question.question_id), text="按设备台数算"),))
    if missing:
        document = harness.documents.get(PROJECT)
        apply(document, instruction("r0#open01", OPEN_ITEM_TEXT, op=WriteOp.REMOVE,
                                    target=Target.body("s11")))
        harness.documents.save(document)
    await harness.run_seat_round()(round_id=rid)
    await harness.integrate_round()(round_id=rid)
    draft = harness.rounds.get(rid).draft
    removes = [p for p in draft.proposals if p.op is WriteOp.REMOVE]
    if missing:
        assert removes == []
        assert any("已撤下" in note for note in draft.gaps)
    else:
        assert len(removes) == 1
        assert removes[0].text == OPEN_ITEM_TEXT
        assert removes[0].base_version_no == 2
    answer = next(p for p in draft.proposals if "按设备台数算" in p.text)
    assert answer.origin.type is OriginType.USER
    assert answer.base_version_no == (3 if missing else 2)
    await harness.confirm_draft()(round_id=rid, draft_id=str(draft.draft_id),
                                 accepted=(str(answer.proposal_id),))
    assert "按设备台数算" in harness.documents.get(PROJECT).locate_entry(answer.entry_id).entry.text


async def test_unaddressable_mechanical_claim_is_not_accounted_as_successfully_landed():
    from dataclasses import replace

    from pmstudio.domain.round.round import DropSource
    from tests.domain.test_exact_write_guards import changed_blocks

    target = Target.body("s5", feature_slug="order-export", field="one_liner")
    harness, rid = await mechanical_round(target=target, text="新的整句")
    document = harness.documents.get(PROJECT)
    document = changed_blocks(document, lambda b:
        replace(b, entries=b.entries * 2) if b.field == "one_liner" else b)
    apply(document, instruction("unrelated", "保存当前结构", target=Target.body("s3")))
    harness.documents.save(document)
    await harness.integrate_round()(round_id=rid)
    stored = harness.rounds.get(rid)
    assert not any(p.origin.type is OriginType.USER for p in stored.draft.proposals)
    assert any("单值字段有多个条目" in note for note in stored.draft.gaps)
    assert not any(item.source is DropSource.CLAIM for item in stored.dropped)


@pytest.mark.parametrize("text,overlap", [("单行原文", "1/1"), ("第一行\n第二行", "2/2")])
async def test_mechanical_exact_overlap_reports_the_actual_line_count(text, overlap):
    target = Target.body("s5", feature_slug="order-export", field="exceptions")
    harness, rid = await mechanical_round(target=target, text=text, duplicate=True)
    await harness.integrate_round()(round_id=rid)
    mechanical = next(p for p in harness.rounds.get(rid).draft.proposals
                      if p.origin.type is OriginType.USER)
    assert mechanical.overlap == overlap
