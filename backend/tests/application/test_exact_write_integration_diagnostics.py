import json

from pmstudio.domain.round.step import Step
from tests.application.test_integrate_answers import (
    CLAIMS_REPLY,
    SEAT_REPLY,
    authorize_order_export,
    draft_packet,
    round_in_seat_pass,
)
from tests.support.builders import SEAT
from tests.support.harness import Harness


async def test_all_empty_repair_sends_multiple_actual_drop_reasons_to_second_model_call():
    bad = json.loads(draft_packet(field="not-a-field"))
    bad["proposals"].append({
        "op": "add",
        "entry_id": None,
        "target": {"zone": "body", "section_id": "s404"},
        "text": "另一条无法归位的内容",
        "evidence_refs": ["E1"],
    })
    harness = Harness(
        replies={"claims": CLAIMS_REPLY,
                 "integration": [json.dumps(bad, ensure_ascii=False), draft_packet()]},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    calls = [slots for name, slots in harness.prompts.calls if name == "integrator"]
    assert len(calls) == 2
    assert calls[0]["validation_error"] == ""
    note = calls[1]["validation_error"]
    assert "装配完什么都不剩" in note
    assert "提案 #1：去处不合法：功能字段表里没有 not-a-field" in note
    assert "提案 #2：去处不合法：未知章节：s404" in note
    assert '"field":"not-a-field"' in note
    assert '"section_id":"s404"' in note
    requests = [request for request in harness.gateway.requests if request.purpose == "integration"]
    assert len(requests) == 2
    assert any(note in message.content for message in requests[1].messages)
    assert harness.rounds.get(round_id).step is Step.AWAITING_CONFIRM
