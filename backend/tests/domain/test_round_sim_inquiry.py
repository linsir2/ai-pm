"""轮次记住"这一轮跑过模拟调研"（M10）：artifact 三件套 + 次数 + 勾选。

三件套全是 **plain dict**（不是领域对象）：`round` 上下文不许 import `exploration`
（`test_domain_contexts_do_not_import_each_other`），而假设/盲区的校验形状住在那边。
校验在装配层发生（`sim_from_packet` 里 transient build 领域对象），落轮次时已是干净 dict；
读回时不重验（跑的那一刻验过）。切片/盲区 dict 即存即取的形状，假设 dict 与
`exploration_to_payload` 的 6 键同形。
"""

from pmstudio.infrastructure.postgres.serialization import round_from_payload, round_to_payload
from tests.support.builders import PROJECT, ROUND_ID, make_round


def _slices() -> tuple[dict, ...]:
    return (
        {
            "local_id": "S1",
            "situation": "运营月底要对账",
            "job": "按时间筛出订单",
            "trigger": "月底结账",
            "workaround": "导出全部再手工筛",
            "anchors": ["E1"],
        },
    )


def _hypotheses() -> tuple[dict, ...]:
    return (
        {
            "local_id": "H1",
            "statement": "运营宁愿等一分钟也不愿分两次导",
            "grounding": "document",
            "based_on": ["E1"],
            "falsification": "观察到运营主动分多次小批量导出",
            "evidence_needed": "跟两场月底结账看实际操作",
        },
    )


def _unknowns() -> tuple[dict, ...]:
    return (
        {
            "local_id": "U1",
            "statement": "节假日前的导出量会不会爆",
            "why_it_matters": "决定要不要做异步导出",
            "how_to_find_out": "拉去年节假日的导出日志",
        },
    )


def test_record_sim_inquiry_sets_artifact_and_counts_runs() -> None:
    round_ = make_round()
    round_.record_sim_inquiry(
        slices=_slices(), hypotheses=_hypotheses(), unknowns=_unknowns()
    )

    assert [item["local_id"] for item in round_.sim_slices] == ["S1"]
    assert [item["local_id"] for item in round_.sim_hypotheses] == ["H1"]
    assert [item["local_id"] for item in round_.sim_unknowns] == ["U1"]
    assert round_.sim_inquiry_runs == 1
    assert round_.sim_inquiry_chosen == ()

    round_.record_sim_inquiry(
        slices=_slices(), hypotheses=_hypotheses(), unknowns=_unknowns()
    )
    assert round_.sim_inquiry_runs == 2
    round_.choose_sim_inquiry(("H1",))
    round_.record_sim_inquiry(
        slices=_slices(), hypotheses=_hypotheses(), unknowns=_unknowns()
    )
    assert round_.sim_inquiry_chosen == (), "新 artifact 落下来，旧勾选必须清掉"


def test_choose_sim_inquiry_records_chosen_ids() -> None:
    round_ = make_round()
    round_.record_sim_inquiry(
        slices=_slices(), hypotheses=_hypotheses(), unknowns=_unknowns()
    )
    round_.choose_sim_inquiry(("H1",))

    assert round_.sim_inquiry_chosen == ("H1",)


def test_payload_roundtrip_keeps_sim_inquiry_and_old_rows_fall_back() -> None:
    round_ = make_round()
    round_.record_sim_inquiry(
        slices=_slices(), hypotheses=_hypotheses(), unknowns=_unknowns()
    )
    round_.choose_sim_inquiry(("H1",))

    restored = round_from_payload(
        round_to_payload(round_),
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert [item["local_id"] for item in restored.sim_hypotheses] == ["H1"]
    assert restored.sim_hypotheses[0]["falsification"].startswith("观察到运营")
    assert restored.sim_slices[0]["anchors"] == ["E1"]
    assert restored.sim_unknowns[0]["how_to_find_out"].startswith("拉去年")
    assert restored.sim_inquiry_runs == 1
    assert restored.sim_inquiry_chosen == ("H1",)

    old = round_to_payload(round_)
    for key in (
        "sim_slices",
        "sim_hypotheses",
        "sim_unknowns",
        "sim_inquiry_runs",
        "sim_inquiry_chosen",
    ):
        del old[key]  # 老行：没有这几把键
    restored_old = round_from_payload(
        old,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert restored_old.sim_hypotheses == ()
    assert restored_old.sim_inquiry_runs == 0
    assert restored_old.sim_inquiry_chosen == ()
