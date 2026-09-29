import json

import pytest

from pmstudio.application.errors import ModelCallFailed, PacketParseError
from pmstudio.application.events import GENERATION_FAILED, ROUND_UPDATED
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import FeatureSlug
from tests.support.builders import PROJECT
from tests.support.harness import Harness

INPUT = "想做个订单导出的功能，运营每天都要用，数据量大概几万。"


def claims_reply(*, feature_slug: str | None = None) -> str:
    claim: dict[str, object] = {
        "text": "要做一个订单导出功能",
        "raw_span": "想做个订单导出的功能",
        "label": "need",
        "zone": "body",
        "section_id": "s5",
    }
    if feature_slug is not None:
        claim["feature_slug"] = feature_slug
    second: dict[str, object] = {
        "text": "数据量大概几万",
        "raw_span": "数据量大概几万",
        "label": "guess",
        "zone": "body",
        "section_id": "s10",
        "assumption": {
            "failure_consequence": "若实际是百万级，同步导出会超时",
            "verification": "上线后看导出耗时分布",
        },
    }
    return json.dumps(
        {
            "basis": "读了 s1–s13 全文（空文档）与本轮输入",
            "claims": [claim, second],
            "seats": [
                {"seat_id": "seat.data_scale", "why": "你说到了数据量，量级决定同步还是异步"},
            ],
        },
        ensure_ascii=False,
    )


async def test_start_round_records_claims_with_eids_in_two_transactions():
    harness = Harness(replies={"claims": claims_reply()})
    harness.seed_project()
    use_case = harness.start_round()

    round_id = await use_case.create(project_id=PROJECT, user_input=INPUT)
    await use_case.analyze(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.CLAIMS_READY
    assert round_.waiting_on is WaitingOn.OPENING
    assert [str(claim.eid) for claim in round_.claims] == ["E1", "E2"]
    assert round_.claims[1].label.value == "guess"
    assert len(harness.evidences.get(PROJECT)) == 2
    # 席位名单也跟着分析结果落下来：开场卡上默认勾的就是它
    assert [(item.seat_id, item.why) for item in round_.nominated_seats] == [
        ("seat.data_scale", "你说到了数据量，量级决定同步还是异步")
    ]
    assert harness.uow.commits == 2  # 建轮一个事务、落主张一个事务；模型调用不在事务里
    assert harness.events.names == [ROUND_UPDATED, ROUND_UPDATED]


async def test_analyzer_prompt_carries_the_readiness_gaps():
    """就绪度要真进提示词——否则它只是个界面上的仪表盘，不改变 AI 行为。"""

    harness = Harness(replies={"claims": claims_reply()})
    harness.seed_project()
    use_case = harness.start_round()

    round_id = await use_case.create(project_id=PROJECT, user_input=INPUT)
    await use_case.analyze(round_id=round_id)

    document_state = harness.prompts.calls[-1][1]["document_state"]
    assert "本章还差什么" in document_state
    assert "补缺口不等于填满" in document_state
    assert "〔阻塞〕" in document_state


async def test_start_round_authorizes_feature_the_user_named():
    harness = Harness(replies={"claims": claims_reply(feature_slug="order-export")})
    harness.seed_project()
    use_case = harness.start_round()

    round_id = await use_case.create(project_id=PROJECT, user_input=INPUT)
    await use_case.analyze(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.authorized_new_features == frozenset({FeatureSlug("order-export")})


async def test_start_round_bad_model_failure_marks_round_failed_and_writes_nothing():
    harness = Harness(replies={})  # 脚本里没有 claims 的回复
    harness.seed_project()
    use_case = harness.start_round()

    round_id = await use_case.create(project_id=PROJECT, user_input=INPUT)
    with pytest.raises(ModelCallFailed):
        await use_case.analyze(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.FAILED
    assert "脚本里没有" in round_.failure.reason
    assert len(harness.evidences.get(PROJECT)) == 0  # 账本零写入
    assert harness.events.names[-1] == GENERATION_FAILED


async def test_start_round_bad_unparsable_claims_mark_the_round_failed():
    harness = Harness(replies={"claims": '{"claims": []}'})
    harness.seed_project()
    use_case = harness.start_round()

    round_id = await use_case.create(project_id=PROJECT, user_input=INPUT)
    with pytest.raises(PacketParseError):
        await use_case.analyze(round_id=round_id)

    assert harness.rounds.get(round_id).step is Step.FAILED


def claims_reply_with_an_invented_span() -> str:
    """复述里有一句用户没说过的话——重装要能救回来（真模型抖动就是这么来的）。"""

    return json.dumps(
        {
            "basis": "读了文档",
            "claims": [
                {
                    "text": "要做一个订单导出功能",
                    "raw_span": "帮我搞个导出吧",  # 用户没这么说过
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": "seat.data_scale", "why": "你说到了数据量"}],
        },
        ensure_ascii=False,
    )


async def test_start_round_repairs_once_when_the_claims_packet_is_unusable():
    harness = Harness(
        replies={"claims": [claims_reply_with_an_invented_span(), claims_reply()]}
    )
    harness.seed_project()
    use_case = harness.start_round()

    round_id = await use_case.create(project_id=PROJECT, user_input=INPUT)
    await use_case.analyze(round_id=round_id)

    calls = [call for call in harness.prompts.calls if call[0] == "analyzer"]
    assert len(calls) == 2  # 第一次 + 重装一次
    assert calls[0][1]["validation_error"] == ""  # 第一次没有"上一次"
    note = calls[1][1]["validation_error"]
    assert "第 1 条主张（c1）" in note
    assert "帮我搞个导出吧" not in note  # 不复制写错的那串
    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.CLAIMS_READY
    assert len(round_.claims) == 2  # 落下来的是第二次那份


async def test_start_round_fails_after_the_one_repair():
    harness = Harness(replies={"claims": claims_reply_with_an_invented_span()})
    harness.seed_project()
    use_case = harness.start_round()

    round_id = await use_case.create(project_id=PROJECT, user_input=INPUT)
    with pytest.raises(PacketParseError) as exc:
        await use_case.analyze(round_id=round_id)

    assert "两次都过不了校验" in exc.value.message
    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.FAILED
    assert "两次都过不了校验" in round_.failure.reason
    assert harness.events.names[-1] == GENERATION_FAILED
    assert len(harness.evidences.get(PROJECT)) == 0  # 账本零写入


async def test_start_round_does_not_repair_a_gateway_failure():
    """网关类失败不重装：网关自己已经重试 3 次，报文重装解决不了超时与 5xx。"""

    harness = Harness(replies={})  # 脚本里没有 claims 的回复 → ModelCallFailed
    harness.seed_project()
    use_case = harness.start_round()

    round_id = await use_case.create(project_id=PROJECT, user_input=INPUT)
    with pytest.raises(ModelCallFailed):
        await use_case.analyze(round_id=round_id)

    assert len(harness.gateway.requests) == 1  # 只调了一次
    assert harness.rounds.get(round_id).step is Step.FAILED


def claims_reply_with_seats(*seat_ids: str) -> str:
    claim = {
        "text": "要做一个订单导出功能",
        "raw_span": "想做个订单导出的功能",
        "label": "need",
        "zone": "body",
        "section_id": "s5",
    }
    return json.dumps(
        {
            "basis": "读了空文档与本轮输入",
            "claims": [claim],
            "seats": [
                {"seat_id": seat_id, "why": "读完这段输入，这个专业面要表态"}
                for seat_id in seat_ids
            ],
        },
        ensure_ascii=False,
    )


async def test_round_survives_a_seat_id_that_is_not_in_the_catalog():
    """真跑实测：目录里没有 `seat.observability`，模型却提名它 → 轮次创建不了（报告 F13）。"""

    harness = Harness(
        replies={"claims": claims_reply_with_seats("seat.observability", "seat.data_scale")}
    )
    harness.seed_project()
    use_case = harness.start_round()

    round_id = await use_case.create(project_id=PROJECT, user_input=INPUT)
    await use_case.analyze(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert [item.seat_id for item in round_.nominated_seats] == ["seat.data_scale"]
    assert any("seat.observability" in item.handle for item in round_.dropped)
    assert round_.step is Step.CLAIMS_READY
    # 只调了一次：不再为了一个不存在的席位把整轮重装两遍
    assert len([call for call in harness.prompts.calls if call[0] == "analyzer"]) == 1


async def test_retrying_after_a_claims_failure_carries_the_last_error():
    """E3 剩余（claims 那一路）：重试不是"盲调"——上次为什么没通过，要递进第一次尝试。"""

    harness = Harness(replies={"claims": ["{不是 JSON", "{不是 JSON", claims_reply()]})
    harness.seed_project()
    use_case = harness.start_round()
    round_id = await use_case.create(project_id=PROJECT, user_input=INPUT)

    with pytest.raises(PacketParseError):
        await use_case.analyze(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.FAILED
    assert "两次都过不了校验" in round_.failure.reason
    assert "上一次的报文没通过校验" in round_.retry_hint

    await harness.retry_round()(round_id=round_id)  # 失败 → resume 回 started
    await use_case.analyze(round_id=round_id)  # 第三次脚本是好的 → 切出主张

    analyzer_calls = [slots for name, slots in harness.prompts.calls if name == "analyzer"]
    assert "上一次的报文没通过校验" in analyzer_calls[-1]["validation_error"]
    assert harness.rounds.get(round_id).retry_hint is None  # 用过就清
    assert harness.rounds.get(round_id).step is Step.CLAIMS_READY


class NotJsonOnceGateway:
    """第一次 claims 直接报"模型没按约定返回 JSON"（**带 hint**），之后交给内层脚本网关。

    模仿真网关：非 JSON 是 `ModelCallFailed`，但它属于"模型自己能修"，所以带 hint。
    **只失败一次**：网关类失败是立刻判死整轮的（网关自己已经重试 3 次），不走重装那一圈。
    """

    def __init__(self, inner) -> None:  # type: ignore[no-untyped-def]
        self._inner = inner
        self._left = 1

    async def complete(self, request):  # type: ignore[no-untyped-def]
        if request.purpose == "claims" and self._left > 0:
            self._left -= 1
            raise ModelCallFailed(
                "模型没有按约定返回 JSON（结构化输出契约被破坏）",
                purpose="claims",
                hint="上一次的回复不是合法 JSON。请只输出那个 JSON 对象。",
            )
        return await self._inner.complete(request)


async def test_a_not_json_failure_also_leaves_a_hint_for_the_retry():
    """真跑抓到的口径漏洞：非 JSON 由**网关**判失败，但它仍然是"模型可修"，该留提示。

    （P13 真跑实验：`max_tokens=12` 把 claims 的输出截断成非 JSON → 整轮失败，
    这时按老的 C1"网关类不递"就白丢了一次可用的提示。）
    """

    from tests.support.doubles import ScriptedModelGateway

    harness = Harness(
        gateway=NotJsonOnceGateway(ScriptedModelGateway(replies={"claims": claims_reply()})),
    )
    harness.seed_project()
    use_case = harness.start_round()
    round_id = await use_case.create(project_id=PROJECT, user_input=INPUT)

    with pytest.raises(ModelCallFailed):
        await use_case.analyze(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.FAILED
    assert round_.retry_hint is not None
    assert "不是合法 JSON" in round_.retry_hint

    await harness.retry_round()(round_id=round_id)
    await use_case.analyze(round_id=round_id)

    analyzer_calls = [slots for name, slots in harness.prompts.calls if name == "analyzer"]
    assert "不是合法 JSON" in analyzer_calls[-1]["validation_error"]
    assert harness.rounds.get(round_id).step is Step.CLAIMS_READY
