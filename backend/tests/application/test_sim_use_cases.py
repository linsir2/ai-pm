"""模拟调研用例层（M10-T4）：跑一趟 + 收下为材料。

到草案卡（AWAITING_CONFIRM/DRAFT）才能跑：与 DigRound 同一扇门（`_require_step` +
`_require_waiting`，错一步就报对应的错，不编人话）。
"""

import json

import pytest

from pmstudio.application.use_cases import (  # noqa: F401
    KeepSimInquiry,
    RunSimInquiry,
    _material_kind,
)
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId, FeatureSlug
from pmstudio.harness.errors import ModelCallFailed
from tests.support.builders import PROJECT, ROUND_ID, SEAT, document_with_feature
from tests.support.harness import Harness

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "想做个订单导出的功能",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
            }
        ],
        "seats": [{"seat_id": SEAT, "why": "导出要评估数据量与耗时"}],
    },
    ensure_ascii=False,
)

SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "导出的性能上限",
                "claim": "单次导出应在 60 秒内完成",
                "because": "同类导出的常见上限",
                "evidence_refs": ["E1"],
            }
        ],
        "dropped_opinions": [],
    },
    ensure_ascii=False,
)

DRAFT_REPLY = json.dumps(
    {
        "questions": [],
        "list_items": [],
        "proposals": [
            {
                "op": "add",
                "target": {
                    "zone": "body",
                    "section_id": "s5",
                    "feature_slug": "order-export",
                    "field": "exceptions",
                },
                "text": "无导出权限的用户不显示导出入口",
                "origin_type": "derived",
                "evidence_refs": ["E1"],
                "rationale": "订单含客户信息",
            }
        ],
        "dropped_opinions": [],
    },
    ensure_ascii=False,
)


def _sim_reply(**overrides) -> str:
    base = {
        "basis": "读了 E1 的现状",
        "slices": [
            {
                "local_id": "S1",
                "situation": "运营月底要对账",
                "job": "按时间筛出订单",
                "trigger": "月底结账",
                "workaround": "导出全部再手工筛",
                "anchors": ["E1"],
            }
        ],
        "hypotheses": [
            {
                "local_id": "H1",
                "slice_id": "S1",
                "statement": "运营宁愿等一分钟也不愿分两次导",
                "grounding": "document",
                "based_on": ["E1"],
                "falsification": "观察到运营主动分多次小批量导出",
                "evidence_needed": "跟两场月底结账看实际操作",
            }
        ],
        "unknowns": [
            {
                "local_id": "U1",
                "statement": "节假日前的导出量会不会爆",
                "why_it_matters": "决定要不要做异步导出",
                "how_to_find_out": "拉去年节假日的导出日志",
            }
        ],
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def _seed_e1(harness: Harness) -> None:
    ledger = harness.evidences.get(PROJECT)
    ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="想做个订单导出的功能",
        source=EvidenceSource(round_id=ROUND_ID, raw_span="想做个订单导出的功能"),
        now=harness.clock.now(),
    )
    harness.evidences.seed(ledger)
    # E1 必须**可见**（2026-10-10 §11.0.61）：只进账本的编号模型看不见，
    # 引它就是"凭空造 E 编号"（`seat_review.md:14` 规定只能引本轮主张的编号）。
    # **只在还没有条目时补**：调用方自己定制 one_liner（如去重测那处）时，
    # 这里覆盖会抹掉人家的意图。默认 `make_document()` 是十三章全空，
    # 所以判"有没有条目"，不是判"有没有章节"。
    from pmstudio.application import context as _context

    if not any(
        list(_context._entries_of(block))
        for section in harness.documents.get(PROJECT).view().sections
        for block in section.blocks
    ):
        harness.documents.seed(document_with_feature())


async def _round_on_draft_card(harness: Harness):  # type: ignore[no-untyped-def]
    harness.seed_project()
    _seed_e1(harness)
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    round_.authorize_new_feature(FeatureSlug("order-export"), basis=EvidenceId("E1"))
    harness.rounds.save(round_)
    await harness.integrate_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_CONFIRM
    assert round_.waiting_on is WaitingOn.DRAFT
    return round_id


async def test_R8_ok_two_runs_are_within_cap() -> None:
    """R8 正例：每轮 2 次模拟在上限内，两次都落 artifact。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": _sim_reply(),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)

    await harness.run_sim_inquiry()(round_id=round_id)
    await harness.run_sim_inquiry()(round_id=round_id)

    assert harness.rounds.get(round_id).sim_inquiry_runs == 2


async def test_R8_bad_third_run_is_rejected() -> None:
    """R8 反例：第 3 次直接拒（409 口径），不烧调用、不落 artifact。"""

    from pmstudio.domain.shared.errors import TooManySimInquiries

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": _sim_reply(),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)
    await harness.run_sim_inquiry()(round_id=round_id)
    await harness.run_sim_inquiry()(round_id=round_id)

    calls_before = len(harness.gateway.requests)
    with pytest.raises(TooManySimInquiries):
        await harness.run_sim_inquiry()(round_id=round_id)
    assert len(harness.gateway.requests) == calls_before


async def test_run_records_artifact() -> None:
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": _sim_reply(),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)

    await harness.run_sim_inquiry()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert [item["local_id"] for item in round_.sim_slices] == ["S1"]
    assert [item["local_id"] for item in round_.sim_hypotheses] == ["H1"]
    assert [item["local_id"] for item in round_.sim_unknowns] == ["U1"]


async def test_run_rejects_when_not_on_draft_card() -> None:
    """门错一步报一步：step 不对报 step，waiting 不对报 waiting（与 confirm/dig 同款）。"""

    from pmstudio.domain.shared.errors import IllegalStepTransition, WaitingOnHuman

    harness = Harness(replies={"sim_inquiry": _sim_reply()})
    harness.seed_project()
    _seed_e1(harness)
    round_id = await harness.start_round().create(
        project_id=PROJECT, user_input="想做个订单导出的功能"
    )

    with pytest.raises((IllegalStepTransition, WaitingOnHuman)):
        await harness.run_sim_inquiry()(round_id=round_id)
    assert harness.rounds.get(round_id).sim_inquiry_runs == 0


async def test_model_failure_leaves_round_untouched() -> None:
    """网关失败（脚本里没给 sim_inquiry 回复）：动作失败，轮次不动，不记 runs。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": DRAFT_REPLY},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)

    with pytest.raises(ModelCallFailed):
        await harness.run_sim_inquiry()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.sim_inquiry_runs == 0
    assert round_.sim_hypotheses == ()
    assert round_.step is Step.AWAITING_CONFIRM


async def test_reinstall_then_succeeds() -> None:
    """报文不合格重装一圈：坏 slice_id → 指引 → 好包，两次调用，一份 artifact。"""

    bad = _sim_reply()
    bad_obj = json.loads(bad)
    bad_obj["hypotheses"][0]["slice_id"] = "S9"
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": [json.dumps(bad_obj, ensure_ascii=False), _sim_reply()],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)

    await harness.run_sim_inquiry()(round_id=round_id)

    sim_calls = [
        request
        for request in harness.gateway.requests
        if request.purpose == "sim_inquiry"
    ]
    assert len(sim_calls) == 2
    assert [item["local_id"] for item in harness.rounds.get(round_id).sim_hypotheses] == [
        "H1"
    ]


async def test_duplicate_hypothesis_dropped_with_trace() -> None:
    """假设撞上文档既有条目：丢条＋ASSEMBLY 留痕，干净的照单全收。"""

    dup_text = "运营宁愿等一分钟也不愿分两次导"
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": _sim_reply(),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    harness.seed_project()
    harness.documents.seed(document_with_feature(one_liner=dup_text))
    _seed_e1(harness)
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    round_.authorize_new_feature(FeatureSlug("order-export"), basis=EvidenceId("E1"))
    harness.rounds.save(round_)
    await harness.integrate_round()(round_id=round_id)

    await harness.run_sim_inquiry()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.sim_hypotheses == ()
    assert round_.sim_unknowns != ()
    dropped = [item for item in round_.dropped if item.source.value == "assembly"]
    assert any("H1" in item.handle and "重复" in item.reason for item in dropped)


async def test_demographic_hit_reinstalls_once_then_drops() -> None:
    """人口学命中：第一次重装（以儆效尤），第二次仍犯则丢条＋留痕，干净条目不受影响。"""

    bad_obj = json.loads(_sim_reply())
    bad_obj["hypotheses"][0]["statement"] = "35 岁一线城市女性运营更爱用导出"
    bad = json.dumps(bad_obj, ensure_ascii=False)
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": [bad, bad],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)

    await harness.run_sim_inquiry()(round_id=round_id)

    sim_calls = [
        request
        for request in harness.gateway.requests
        if request.purpose == "sim_inquiry"
    ]
    assert len(sim_calls) == 2
    round_ = harness.rounds.get(round_id)
    assert round_.sim_hypotheses == ()
    assert round_.sim_unknowns != ()
    assert any(item.source.value == "assembly" for item in round_.dropped)


async def test_keep_creates_simulation_material() -> None:
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": _sim_reply(),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)
    await harness.run_sim_inquiry()(round_id=round_id)

    result = await harness.keep_sim_inquiry()(round_id=round_id, chosen=("H1", "U1"))

    assert result.kept == ("H1", "U1")
    stored = harness.materials.get(result.material_id)
    assert stored is not None
    assert stored.parsed.origin == "simulation"
    assert harness.rounds.get(round_id).sim_inquiry_chosen == ("H1", "U1")


async def test_keep_same_selection_is_idempotent() -> None:
    """同选择重复收下：材料不翻倍（source_ref 稳定 → sha 去重）。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": _sim_reply(),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)
    await harness.run_sim_inquiry()(round_id=round_id)

    first = await harness.keep_sim_inquiry()(round_id=round_id, chosen=("H1",))
    second = await harness.keep_sim_inquiry()(round_id=round_id, chosen=("H1",))

    assert first.material_id == second.material_id
    assert harness.materials.count_for_project(PROJECT) == 1


async def test_keep_unknown_id_is_rejected() -> None:
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": _sim_reply(),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)
    await harness.run_sim_inquiry()(round_id=round_id)

    with pytest.raises(ShapeError):
        await harness.keep_sim_inquiry()(round_id=round_id, chosen=("H9",))


async def test_keep_empty_is_rejected() -> None:
    """没勾不能收：空选择直接拒（不合成空材料占材料位）。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": _sim_reply(),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)
    await harness.run_sim_inquiry()(round_id=round_id)

    with pytest.raises(ShapeError):
        await harness.keep_sim_inquiry()(round_id=round_id, chosen=())


def test_material_kind_maps_simulation_to_simulated() -> None:
    """发号单点：origin=simulation → SIMULATED（与 exploration/discussion 同路）。"""

    from tests.support.builders import make_material_packet

    packet = make_material_packet(origin="simulation")
    assert _material_kind(packet) is EvidenceKind.SIMULATED


def _bad_anchor_reply() -> str:
    """切片的 anchors 写章节号（真跑 2026-10-07 的形状：空文档时模型抄 s8）。"""

    obj = json.loads(_sim_reply())
    obj["slices"][0]["anchors"] = ["s8"]
    return json.dumps(obj, ensure_ascii=False)


def _two_slice_reply(*, first_anchors: list[str]) -> str:
    obj = json.loads(_sim_reply())
    obj["slices"] = [
        {**obj["slices"][0], "local_id": "S1", "anchors": first_anchors},
        {
            "local_id": "S2",
            "situation": "财务季度审计要对账",
            "job": "核对导出与账面",
            "trigger": "季度审计",
            "workaround": "人工比对两份表",
            "anchors": ["E1"],
        },
    ]
    obj["hypotheses"].append(
        {
            "local_id": "H2",
            "slice_id": "S2",
            "statement": "审计场景需要按会计期导出",
            "grounding": "document",
            "based_on": ["E1"],
            "falsification": "访谈财务后发现沿用自然月",
            "evidence_needed": "一次财务审计的对账记录",
        }
    )
    return json.dumps(obj, ensure_ascii=False)


async def test_bad_anchor_repairs_once_then_succeeds() -> None:
    """锚点写坏（s8）：第一次带指引重跑，第二次写对 → 正常落 artifact，无丢条。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": [_bad_anchor_reply(), _sim_reply()],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)

    await harness.run_sim_inquiry()(round_id=round_id)

    sim_calls = [
        request
        for request in harness.gateway.requests
        if request.purpose == "sim_inquiry"
    ]
    assert len(sim_calls) == 2
    round_ = harness.rounds.get(round_id)
    assert [item["local_id"] for item in round_.sim_hypotheses] == ["H1"]
    assert not [item for item in round_.dropped if item.source.value == "assembly"]


async def test_bad_anchor_twice_drops_slice_and_keeps_the_rest() -> None:
    """两次都写坏：坏的切片与挂在它下面的假设丢条+留痕，好的照留（不整包判死）。"""

    bad = _two_slice_reply(first_anchors=["s8"])
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": [bad, bad],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)

    await harness.run_sim_inquiry()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert [item["local_id"] for item in round_.sim_slices] == ["S2"]
    assert [item["local_id"] for item in round_.sim_hypotheses] == ["H2"]
    dropped = [item for item in round_.dropped if item.source.value == "assembly"]
    handles = [item.handle for item in dropped]
    assert "模拟切片 S1" in handles
    assert any("S1" in item.reason for item in dropped), [i.reason for i in dropped]
    assert round_.sim_inquiry_runs == 1


async def test_all_anchors_bad_twice_fails_with_clear_reason() -> None:
    """全坏：筛完没剩 → 动作失败（502 口径），runs 不记、artifact 不落。"""

    obj = json.loads(_bad_anchor_reply())
    obj["unknowns"] = []  # 只剩坏切片+它的假设：筛完真的一无所有
    bad = json.dumps(obj, ensure_ascii=False)
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": [bad, bad],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)

    from pmstudio.harness.errors import PacketParseError

    with pytest.raises(PacketParseError, match="筛完没剩"):
        await harness.run_sim_inquiry()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.sim_inquiry_runs == 0
    assert round_.sim_hypotheses == ()
