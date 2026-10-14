import json

import pytest

from pmstudio.application.events import GENERATION_FAILED, PACKET_EMITTED
from pmstudio.application.use_cases import ClaimEdit
from pmstudio.domain.round.step import Step
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.domain.shared.target import Target
from pmstudio.harness.errors import ModelCallFailed, PacketParseError
from tests.support.builders import PROJECT, SEAT, make_seat
from tests.support.harness import Harness

SECOND_SEAT = "seat.permissions"

#: 14 条意见 > 单席位上界 12：真跑里这种包会把整轮打死（报告 F4）
OVER_CAP_SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 全文与 E1",
        "opinions": [
            {
                "local_id": f"O{index}",
                "form": "judgment",
                "about": f"第 {index} 件事",
                "claim": "……",
                "because": "……",
                "evidence_refs": ["E1"],
            }
            for index in range(1, 15)
        ],
    },
    ensure_ascii=False,
)

#: 一个 schema 过不了的席位包：`claim`（内容字段）写成 `null`。
#: 为什么不拿"真跑里那个 `evidence_refs: null`"当坏包了：P8 把它归一成 `[]`（有明确定义的降级），
#: 再用它当"坏包"就测不到重装了。内容字段**不许凭空造**，所以它仍然是坏的。
UNPARSABLE_SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 全文与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "量级与耗时的关系",
                "claim": None,
                "because": "单次导出的常见上限是 60 秒",
                "evidence_refs": ["E1"],
            }
        ],
    },
    ensure_ascii=False,
)

SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 全文与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "导出的性能上限",
                "claim": "单次导出应在 60 秒内完成",
                "because": "量级在 1 万级，同步实现可满足",
                "evidence_refs": ["E1"],
            }
        ],
    },
    ensure_ascii=False,
)


def claims_reply() -> str:
    return json.dumps(
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
            "seats": [
                {"seat_id": SEAT, "why": "导出要评估数据量与耗时"},
            ],
        },
        ensure_ascii=False,
    )


async def started_round(harness: Harness):
    """建轮 + 落主张，停在开场卡（用真正的 StartRound，不手搓状态）。"""

    use_case = harness.start_round()
    round_id = await use_case.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await use_case.analyze(round_id=round_id)
    return round_id


async def test_submit_opening_applies_claim_edits_and_starts_the_seat_round():
    harness = Harness(replies={"claims": claims_reply()})
    harness.seed_project()
    round_id = await started_round(harness)

    await harness.submit_opening()(
        round_id=round_id,
        seats=(SEAT,),
        claim_edits=(ClaimEdit(claim_id="c1", label="goal", target=Target.body("s2")),),
    )

    round_ = harness.rounds.get(round_id)
    assert round_.claims[0].label.value == "goal"
    assert round_.claims[0].target == Target.body("s2")
    assert round_.seats == (SEAT,)
    assert round_.step is Step.SEATS_RUNNING
    assert round_.seat_round_count == 1


async def test_submit_opening_bad_unknown_claim_id_is_rejected():
    harness = Harness(replies={"claims": claims_reply()})
    harness.seed_project()
    round_id = await started_round(harness)

    with pytest.raises(ShapeError):
        await harness.submit_opening()(
            round_id=round_id,
            seats=(SEAT,),
            claim_edits=(ClaimEdit(claim_id="c9", label="need", target=Target.body("s5")),),
        )


async def test_run_seat_round_attests_and_numbers_judgments_only():
    harness = Harness(
        replies={"claims": claims_reply()}, seat_replies={SEAT: SEAT_REPLY}
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert len(round_.current_reviews) == 1
    ledger = harness.evidences.get(PROJECT)
    judgments = [
        evidence for evidence in ledger.evidences() if evidence.source.seat_id == SEAT
    ]
    assert len(judgments) == 1  # 判断占编号；疑问不占
    assert judgments[0].eid == EvidenceId("E2")  # E1 是那条主张，判断从 E2 起
    # 建轮 / 落主张 / 收开场卡 / 席位交回落库 / 汇合点编号（读取不开事务）
    assert harness.uow.commits == 5


async def test_run_seat_round_emits_one_packet_event_per_seat():
    harness = Harness(replies={"claims": claims_reply()}, seat_replies={SEAT: SEAT_REPLY})
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    await harness.run_seat_round()(round_id=round_id)

    packets = [event for event in harness.events.published if event.name == PACKET_EMITTED]
    assert len(packets) == 1
    assert packets[0].payload["seat_id"] == SEAT
    # 铁律④：事件不带 opinions（真源在 GET /round），只含身份 + 进度
    assert "opinions" not in packets[0].payload
    assert packets[0].payload["packet_id"].startswith("pkt-")
    assert packets[0].payload["pass_index"] == 1


async def test_run_seat_round_bad_one_seat_failure_fails_the_round_but_keeps_what_came_back():
    """失败必须是失败（整轮 failed、文档零写入），但**已交回的意见不丢**（计划 §3.11）。"""

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: SEAT_REPLY},  # 只给一个席位配脚本
        seats=(make_seat(), make_seat(SECOND_SEAT, title="权限与角色")),
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT, SECOND_SEAT))

    with pytest.raises(ModelCallFailed):
        await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.FAILED
    assert SECOND_SEAT in round_.failure.reason
    assert len(round_.seat_reviews) == 1  # A 交回了，留着
    assert round_.pending_seats == (SECOND_SEAT,)
    # 汇合点还没到 → 判断还没占编号，账本里只有那条主张
    assert harness.evidences.get(PROJECT).evidences()[-1].eid == EvidenceId("E1")


async def test_run_seat_round_only_refills_seats_that_have_not_reported():
    """断点续传：A 已交回、B 崩了 → 重进只叫 B，A 不重跑、编号也不重复（§3.11）。"""

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: SEAT_REPLY},  # 只给 A 配脚本 → B 会失败
        seats=(make_seat(), make_seat(SECOND_SEAT, title="权限与角色")),
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT, SECOND_SEAT))
    with pytest.raises(ModelCallFailed):
        await harness.run_seat_round()(round_id=round_id)

    assert harness.rounds.get(round_id).pending_seats == (SECOND_SEAT,)

    await harness.retry_round()(round_id=round_id)  # 失败 → resume，回到 seats_running
    harness.gateway._seat_replies[SECOND_SEAT] = [SEAT_REPLY]  # 补上 B 的脚本
    await harness.run_seat_round()(round_id=round_id)

    seat_requests = [
        request.metadata["seat_id"]
        for request in harness.gateway.requests
        if request.purpose == "seat_review"
    ]
    assert seat_requests == [SEAT, SECOND_SEAT, SECOND_SEAT]  # A 只被叫过一次
    judgment_eids = [
        str(evidence.eid)
        for evidence in harness.evidences.get(PROJECT).evidences()
        if evidence.source.seat_id is not None
    ]
    assert len(judgment_eids) == len(set(judgment_eids))  # 编号没被重复占用


async def test_run_seat_round_repacks_once_when_a_seat_returns_too_many_opinions():
    """第一次超限 → 这一趟先重装（带上"你给了 14 条，上限 12"）→ 救回来就不截断。"""

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: [OVER_CAP_SEAT_REPLY, SEAT_REPLY]},
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert len(round_.current_reviews) == 1
    assert len(round_.current_reviews[0].opinions) == 1  # 落下来的是第二次那份
    assert round_.dropped == ()                          # 没有截断，就没有截断留痕
    assert [request.purpose for request in harness.gateway.requests].count("seat_review") == 2
    assert harness.prompts.calls[-1][1]["validation_error"] != ""  # 重装带上了纠错指引
    assert GENERATION_FAILED not in harness.events.names


async def test_run_seat_round_truncates_and_traces_when_the_repack_still_overflows():
    """两次都超限 → 截断保留 + 留痕，**整轮继续**（不再判死）。"""

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: [OVER_CAP_SEAT_REPLY, OVER_CAP_SEAT_REPLY]},
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert len(round_.current_reviews[0].opinions) == 12
    assert round_.pending_seats == ()
    assert round_.step is Step.SEATS_RUNNING
    assert any("超出单席位上限" in item.reason for item in round_.dropped)
    assert GENERATION_FAILED not in harness.events.names


async def test_run_seat_round_repacks_when_a_seat_packet_is_unparsable():
    """某个席位的报文 schema 过不了（内容字段 `claim` 写成 `null`）→ 整轮 `席位失败`。

    解析错和超限一样是**可修复的错**：该席位重装一次就好了（其余席位不动）。
    """

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: [UNPARSABLE_SEAT_REPLY, SEAT_REPLY]},
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert len(round_.current_reviews) == 1
    assert len(round_.current_reviews[0].opinions) == 1  # 第二次那份
    assert round_.step is Step.SEATS_RUNNING
    assert GENERATION_FAILED not in harness.events.names
    assert [request.purpose for request in harness.gateway.requests].count("seat_review") == 2
    assert harness.prompts.calls[-1][1]["validation_error"] != ""  # 重装带上了纠错指引


async def test_run_seat_round_still_fails_when_the_seat_repack_also_fails():
    """两次都过不了 → 照旧整轮失败（"失败必须是失败"，口径不变）。"""

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: [UNPARSABLE_SEAT_REPLY, UNPARSABLE_SEAT_REPLY]},
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    with pytest.raises(PacketParseError):
        await harness.run_seat_round()(round_id=round_id)

    assert harness.rounds.get(round_id).step is Step.FAILED


#: 真跑 L2 第 3 轮：`opinions[1]` 是模型自己写的占位符字符串 `"O2_placeholder"`（报告 F17）。
#: 它与 `UNPARSABLE_SEAT_REPLY` 不是一类：那个是"对象里字段写错"，这个是"元素根本不是对象"。
PLACEHOLDER_SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 全文与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "导出的性能上限",
                "claim": "单次导出应在 60 秒内完成",
                "because": "量级在 1 万级，同步实现可满足",
                "evidence_refs": ["E1"],
            },
            "O2_placeholder",
        ],
    },
    ensure_ascii=False,
)


async def test_run_seat_round_repacks_a_placeholder_opinion_once():
    """第一次回占位符 → 先重装一次（与超限同一个口径）；救回来就不留痕。"""

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: [PLACEHOLDER_SEAT_REPLY, SEAT_REPLY]},
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert len(round_.current_reviews[0].opinions) == 1  # 落下来的是第二次那份
    assert round_.dropped == ()
    assert [request.purpose for request in harness.gateway.requests].count("seat_review") == 2
    assert "不是对象" in harness.prompts.calls[-1][1]["validation_error"]
    assert GENERATION_FAILED not in harness.events.names


async def test_run_seat_round_drops_a_placeholder_and_traces_it_instead_of_failing():
    """两次都回占位符 → 丢掉那一条 + 留痕，**整轮继续**（F17 的现场不再判死）。"""

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: [PLACEHOLDER_SEAT_REPLY, PLACEHOLDER_SEAT_REPLY]},
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert len(round_.current_reviews[0].opinions) == 1  # 好的那条留着
    assert round_.pending_seats == ()
    assert round_.step is Step.SEATS_RUNNING
    assert GENERATION_FAILED not in harness.events.names

    traces = [item for item in round_.dropped if "不是对象" in item.reason]
    assert len(traces) == 1
    assert traces[0].seat_id == SEAT
    assert traces[0].handle == f"{SEAT}:opinions[2]"


#: 引用了账本里没有的编号（真跑 2026-10-14：E23 是新一号空间外推出来的幻觉）
BAD_REF_SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 全文与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "导出的性能上限",
                "claim": "单次导出应在 60 秒内完成",
                "because": "量级在 1 万级，同步实现可满足",
                "evidence_refs": ["E99"],  # 账本里没有这个编号
            }
        ],
    },
    ensure_ascii=False,
)


async def test_run_seat_round_salvages_a_hallucinated_ref_instead_of_failing():
    """引用了不存在的编号：重装一次仍犯 → 最后一次摘掉该编号 + 留痕，整轮不判死。

    2026-10-14 修复：以前这类错一次就把整轮（所有席位）打死，真跑连着两轮全失败。
    """

    harness = Harness(
        replies={"claims": claims_reply()}, seat_replies={SEAT: BAD_REF_SEAT_REPLY}
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    await harness.run_seat_round()(round_id=round_id)  # 不再抛

    round_ = harness.rounds.get(round_id)
    assert len(round_.current_reviews) == 1
    opinion = round_.current_reviews[0].opinions[0]
    assert opinion.evidence_refs == ()  # E99 摘掉
    assert "E99" in (opinion.basis_note or "")
    assert any("E99" in item.reason for item in round_.dropped)  # 留痕说清了


async def test_seat_known_counts_visible_eids_but_not_ledger_only_ones():
    """known 补齐的两半都不破：提示词里看得见的编号算数（哪怕不在原来四处），
    账本里有但提示词里没出现的仍不算（§11.0.61）。"""

    from pmstudio.application.use_cases import _seat_known_eids

    harness = Harness(replies={"claims": claims_reply()}, seat_replies={SEAT: SEAT_REPLY})
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    ledger = harness.evidences.get(PROJECT)  # E1=主张、E2=席位判断

    known = _seat_known_eids(frozenset(), "本席位上一趟：出处编号 E2", ledger=ledger)

    assert EvidenceId("E2") in known  # 文本里看得见 → 算
    assert EvidenceId("E1") not in known  # 账本里有、文本里没出现 → 不算


async def test_run_seat_round_skips_a_twice_bad_seat_and_continues_with_the_rest():
    """④：一个席位两次都交不出可用报文 → 记名跳过它，其余席位照常汇合（不判死整轮）。"""

    from pmstudio.domain.round.round import DropSource

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={
            SEAT: SEAT_REPLY,
            SECOND_SEAT: [UNPARSABLE_SEAT_REPLY, UNPARSABLE_SEAT_REPLY],
        },
        seats=(make_seat(), make_seat(SECOND_SEAT, title="权限与角色")),
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT, SECOND_SEAT))

    await harness.run_seat_round()(round_id=round_id)  # 不抛

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.SEATS_RUNNING
    assert round_.skipped_seats == (SECOND_SEAT,)
    assert round_.pending_seats == ()
    assert len(round_.current_reviews) == 1
    assert GENERATION_FAILED not in harness.events.names
    skipped = [item for item in round_.dropped if item.source is DropSource.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].seat_id == SECOND_SEAT
    # 汇合点照常：交回的那个席位判断占了 E2
    assert harness.evidences.get(PROJECT).evidences()[-1].eid == EvidenceId("E2")


async def test_run_seat_round_fails_when_all_seats_are_twice_bad():
    """全军覆没（本趟零交回）→ 照旧整轮失败：零疑问才判死（`02` §7 口径）。"""

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={
            SEAT: [UNPARSABLE_SEAT_REPLY, UNPARSABLE_SEAT_REPLY],
            SECOND_SEAT: [UNPARSABLE_SEAT_REPLY, UNPARSABLE_SEAT_REPLY],
        },
        seats=(make_seat(), make_seat(SECOND_SEAT, title="权限与角色")),
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT, SECOND_SEAT))

    with pytest.raises(PacketParseError):
        await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.FAILED
    assert "所有席位" in round_.failure.reason
