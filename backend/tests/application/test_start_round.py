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
        {"basis": "读了 s1–s13 全文（空文档）与本轮输入", "claims": [claim, second]},
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
    assert harness.uow.commits == 2  # 建轮一个事务、落主张一个事务；模型调用不在事务里
    assert harness.events.names == [ROUND_UPDATED, ROUND_UPDATED]


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
