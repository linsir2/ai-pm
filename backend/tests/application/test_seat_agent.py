import json

import pytest

from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.errors import PacketParseError
from pmstudio.domain.round.opinion import OpinionForm
from pmstudio.domain.shared.ids import EvidenceId
from tests.support.builders import make_seat
from tests.support.doubles import RecordingPrompts, ScriptedModelGateway

SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 全文与 E1–E4",
        "opinions": [
            {
                "local_id": "O1",
                "form": "question",
                "about": "导出的数据量级",
                "claim": "量级是同步与异步的分界，只有用户能给",
                "because": "1 万以内可同步，10 万以上必须异步，是两套实现",
                "evidence_refs": ["E4"],
            },
            {
                "local_id": "O2",
                "form": "judgment",
                "about": "导出的性能上限",
                "claim": "单次导出应在 60 秒内完成",
                "because": "量级在 1 万级，同步实现可满足",
                "evidence_refs": ["E4"],
            },
            {
                "local_id": "O3",
                "form": "judgment",
                "about": "导出记录保留时长",
                "claim": "导出记录保留 30 天",
                "because": "同类工具的常见做法，纯专业判断",
                "basis_note": "没有可引用的编号，这是经验判断",
            },
        ],
    },
    ensure_ascii=False,
)

KNOWN = frozenset({EvidenceId("E1"), EvidenceId("E4")})


def gateway_for(reply: str = SEAT_REPLY) -> ScriptedModelGateway:
    return ScriptedModelGateway(replies={}, seat_replies={"seat.data_scale": reply})


async def test_seat_reviewer_returns_opinions_with_forms_and_refs():
    prompts = RecordingPrompts()
    reviewer = SeatReviewer(model=gateway_for(), prompts=prompts)

    review = await reviewer.review(
        seat=make_seat(),
        document_state="# 文档",
        claims="- [E1] 需求 · s5 · 要做一个订单导出功能",
        opinions_so_far="（这是第一次席位轮）",
        answered="（这一轮还没有答过题）",
        known_eids=KNOWN,
    )

    assert review.seat_id == "seat.data_scale"
    assert review.basis.startswith("读了 s1–s13")
    assert [opinion.form for opinion in review.opinions] == [
        OpinionForm.QUESTION,
        OpinionForm.JUDGMENT,
        OpinionForm.JUDGMENT,
    ]
    assert review.opinions[1].evidence_refs == (EvidenceId("E4"),)
    assert review.opinions[2].basis_note is not None
    assert review.pass_index == 1  # 由 Round.attest 盖真序号


async def test_seat_reviewer_passes_seat_metadata_and_all_slots():
    gateway = gateway_for()
    prompts = RecordingPrompts()
    reviewer = SeatReviewer(model=gateway, prompts=prompts)

    await reviewer.review(
        seat=make_seat(),
        document_state="# 文档",
        claims="…",
        opinions_so_far="…",
        answered="…",
        known_eids=KNOWN,
    )

    request = gateway.requests[0]
    assert request.purpose == "seat_review"
    assert request.metadata["seat_id"] == "seat.data_scale"
    name, slots = prompts.calls[0]
    assert name == "seat_review"
    assert tuple(sorted(slots)) == tuple(sorted(SeatReviewer.PROMPT_SLOTS))
    assert slots["seat_title"] == "数据规模与性能"


async def test_seat_reviewer_bad_unknown_evidence_ref_is_rejected():
    reply = json.dumps(
        {
            "basis": "读了文档",
            "opinions": [
                {
                    "local_id": "O1",
                    "form": "judgment",
                    "about": "性能上限",
                    "claim": "60 秒内完成",
                    "because": "同类做法",
                    "evidence_refs": ["E99"],  # 账本里没有这个编号
                }
            ],
        },
        ensure_ascii=False,
    )
    reviewer = SeatReviewer(model=gateway_for(reply), prompts=RecordingPrompts())

    with pytest.raises(PacketParseError) as exc:
        await reviewer.review(
            seat=make_seat(),
            document_state="# 文档",
            claims="…",
            opinions_so_far="…",
            answered="…",
            known_eids=KNOWN,
        )
    assert "E99" in exc.value.message


async def test_seat_reviewer_empty_opinions_still_needs_basis():
    reply = json.dumps(
        {"basis": "读了全文，本领域没有需要补的", "opinions": []}, ensure_ascii=False
    )
    reviewer = SeatReviewer(model=gateway_for(reply), prompts=RecordingPrompts())

    review = await reviewer.review(
        seat=make_seat(),
        document_state="# 文档",
        claims="…",
        opinions_so_far="…",
        answered="…",
        known_eids=frozenset(),
    )
    assert review.opinions == ()
    assert "本领域没有需要补的" in review.basis
