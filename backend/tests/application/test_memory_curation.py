"""跨轮记忆的闭环（M6b）：提议 → 卡片 → 确认 → 落库。

口径：`docs/plans/2026-10-03-M6-跨轮记忆本体.md` §5 / §9（A1–A4）。
三条铁律在这里被钉住：

① **提议不是事实**：Curator 出了、用户没勾，记忆一个字都不动；
② **记忆整理挂了不打崩这一轮**：草案照出，卡片上留一行；
③ **确认是同一个事务**：文档与记忆要么一起成，要么一起回滚。
"""

import json

import pytest

from pmstudio.application.dto import round_view
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.errors import UnknownMemoryProposal
from pmstudio.domain.shared.ids import EvidenceId, FeatureSlug
from tests.support.builders import PROJECT, SEAT
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
                "feature_slug": "order-export",
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
            },
            {
                "local_id": "O2",
                "form": "question",
                "about": "导出的权限",
                "claim": "谁有权导出还没定",
                "because": "涉及客户信息",
                "evidence_refs": [],
            },
        ],
    },
    ensure_ascii=False,
)


def draft_packet() -> str:
    """一份能出草案的整合报文：所有席位意见都交代清楚（不触发归因小调用）。"""

    return json.dumps(
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
                    "evidence_refs": ["E1", "E2"],
                    "rationale": "订单含客户信息",
                }
            ],
            "dropped_opinions": [
                {"handle": f"{SEAT}:O1", "reason": "已落到上面的提案里"},
                {"handle": f"{SEAT}:O2", "reason": "本轮没有必须问用户的东西"},
            ],
        },
        ensure_ascii=False,
    )


def curator_reply(*changes: dict) -> str:
    return json.dumps({"changes": list(changes)}, ensure_ascii=False)


def decision_change(text: str = "导出保留期是 30 天") -> dict:
    return {
        "op": "add",
        "kind": "decision",
        "text": text,
        "refs": ["E1"],
        "reason": "用户这一轮定下来的口径",
    }


async def round_in_seat_pass(harness: Harness):  # type: ignore[no-untyped-def]
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    return round_id


def authorize_order_export(harness: Harness, round_id) -> None:  # type: ignore[no-untyped-def]
    """按需授权（分析者已经按"用户点名"授权过时，这里什么都不做）。"""

    round_ = harness.rounds.get(round_id)
    if FeatureSlug("order-export") in round_.authorized_new_features:
        return
    round_.authorize_new_feature(FeatureSlug("order-export"), basis=EvidenceId("E1"))
    harness.rounds.save(round_)


async def test_curator_proposals_land_on_the_draft_card_and_default_to_unchecked():
    """A1：提议进卡片；**没勾之前，记忆里一条都没有**。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_packet(),
            "memory_curation": curator_reply(decision_change()),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert [item.proposal_id for item in round_.memory_proposals] == ["mem#1"]
    view = round_view(round_)
    assert [(item.op, item.kind, item.text, item.refs) for item in view.memory_proposals] == [
        ("add", "decision", "导出保留期是 30 天", ("E1",))
    ]
    assert harness.memory.get(PROJECT).entries == ()      # 提议不是事实
    assert round_.accepted_memory_ids == ()


async def test_a_curator_failure_leaves_a_note_and_still_writes_the_draft():
    """A2：记忆整理挂了 → 草案照出，卡片上留一行（脚本里没有 memory_curation 的回复）。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": draft_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.draft is not None
    assert round_.memory_proposals == ()
    assert any("没能整理记忆" in item for item in round_.draft.gaps)
    # 记忆的事**不进** `Round.dropped`：那条通道是"席位意见的交代账"（unaccounted 的分子）
    assert all("记忆" not in item.reason for item in round_.dropped)


async def test_an_untraceable_memory_change_never_reaches_the_card():
    """机械筛：出处是编的 → 丢 + 留痕，不进卡片让用户勾。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_packet(),
            "memory_curation": curator_reply(
                decision_change() | {"refs": ["E99"]},
                decision_change("这条是好的"),
            ),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert [item.change.text for item in round_.memory_proposals] == ["这条是好的"]
    assert any(
        "记忆 #1 没进卡片：出处编号不在账本里：E99" in item
        for item in round_.draft.gaps
    )


# —— M6d：整批缺出处 = 形状错误 → 给一次带错因的补问（与席位/整合者同一条规矩） ——


def change_without_refs(text: str = "点检记录提交后不可修改") -> dict:
    """真跑第一趟的现场：内容全对、`refs` 整批省略。"""

    item = decision_change(text)
    item.pop("refs")
    return item


async def test_a_curator_reply_without_refs_gets_the_round_refs_filled_in():
    """M6e：模型连着两趟都不写 `refs` → 系统按**本轮账本**盖上（模型只决定记哪条）。

    真跑现场（L1 第 1 轮，`qwen-plus`）：提示词里有 E 编号、补问也带了错因，
    两次回包都把 `refs` 整栏省掉——那就别再赌它写，出处由装配层盖。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_packet(),
            "memory_curation": [
                curator_reply(change_without_refs()),
                curator_reply(change_without_refs("第二次还是没出处")),
            ],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    # 回填发生在**第一次**回包上（它已经合法了），所以没有补问、也没有丢
    assert harness.gateway.purposes.count("memory_curation") == 1
    assert [item.change.text for item in round_.memory_proposals] == ["点检记录提交后不可修改"]
    # 出处是**本轮账本里的真编号**（E1 用户输入、E2 席位判断），不是编的
    assert round_.memory_proposals[0].change.refs == ("E1", "E2")
    assert not any("没进卡片" in item for item in round_.draft.gaps)


async def test_a_curator_reply_with_bad_refs_is_retried_once_with_the_reason():
    """回填只管"没写"；**写歪了**（编号不在账本里）仍然要补问一次，且带上错因。

    一轮里 Curator 调用数因此要么 1、要么 2，不会更多。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_packet(),
            "memory_curation": [
                curator_reply(decision_change() | {"refs": ["E99"]}),
                curator_reply(decision_change()),
            ],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    assert harness.gateway.purposes.count("memory_curation") == 2
    assert "出处编号不在账本里" in harness.prompts.last("memory_curator")["validation_error"]
    round_ = harness.rounds.get(round_id)
    assert [item.change.text for item in round_.memory_proposals] == ["导出保留期是 30 天"]


async def test_a_clean_curator_reply_is_not_retried():
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_packet(),
            "memory_curation": curator_reply(decision_change()),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    assert harness.gateway.purposes.count("memory_curation") == 1      # 正常一轮仍是一次


async def test_a_second_bad_curator_reply_still_does_not_break_the_round():
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_packet(),
            "memory_curation": [
                curator_reply(decision_change() | {"refs": ["E99"]}),
                curator_reply(decision_change("第二次还是编的") | {"refs": ["E98"]}),
            ],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    assert harness.gateway.purposes.count("memory_curation") == 2      # 不会无限补问
    round_ = harness.rounds.get(round_id)
    assert round_.draft is not None                                    # 草案照出
    assert round_.memory_proposals == ()
    assert any("记忆 #1 没进卡片" in item for item in round_.draft.gaps)


async def test_memory_is_curated_only_when_the_round_reaches_a_draft():
    """出问卷的那几批**不跑**记忆整理：一轮一次，别按批重复花钱。"""

    question = {
        "kind": "gap",
        "title": "单次导出的最大行数是多少？",
        "why": "量级决定同步还是异步",
        "consequence_if_skipped": "按同步实现，超时风险由 PM 承担",
        "target": {"zone": "body", "section_id": "s8"},
        "type": "single",
        "options": [
            {"option_id": "a", "text": "少于 1 万"},
            {"option_id": "b", "text": "1 万–10 万"},
        ],
        "evidence_refs": ["E1"],
        "from_opinions": [f"{SEAT}:O2"],
    }
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": json.dumps(
                {
                    "questions": [question],
                    "list_items": [],
                    "proposals": [],
                    "dropped_opinions": [
                        {"handle": f"{SEAT}:O1", "reason": "本轮没有必须写的内容"}
                    ],
                },
                ensure_ascii=False,
            ),
            "memory_curation": curator_reply(decision_change()),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    assert harness.gateway.purposes.count("memory_curation") == 0
    assert harness.rounds.get(round_id).waiting_on is WaitingOn.QUESTIONNAIRE


async def test_confirmed_memory_is_written_in_the_same_transaction():
    """A3：勾了就随确认一起落库，且 `Round` 记下勾了哪几条。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_packet(),
            "memory_curation": curator_reply(decision_change()),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    await harness.integrate_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    accepted = [str(item.proposal_id) for item in round_.draft.proposals]

    result = await harness.confirm_draft()(
        round_id=round_id, accepted=accepted, accepted_memory=["mem#1"]
    )

    memory = harness.memory.get(PROJECT)
    assert [(item.kind.value, item.text, item.refs) for item in memory.entries] == [
        ("decision", "导出保留期是 30 天", ("E1",))
    ]
    assert [item.round_id for item in memory.revisions] == [str(round_id)]
    assert harness.rounds.get(round_id).accepted_memory_ids == ("mem#1",)
    assert harness.rounds.get(round_id).step is Step.DONE
    assert result.version.version_no == 2


async def test_unconfirmed_memory_proposals_change_nothing():
    """A4：没勾 → 记忆一个字都不动（文档照写）。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_packet(),
            "memory_curation": curator_reply(decision_change()),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    await harness.integrate_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    accepted = [str(item.proposal_id) for item in round_.draft.proposals]

    await harness.confirm_draft()(round_id=round_id, accepted=accepted)

    assert harness.memory.get(PROJECT).entries == ()
    assert harness.memory.saves == 0
    assert harness.rounds.get(round_id).accepted_memory_ids == ()


async def test_a_rejected_memory_proposal_id_rolls_the_whole_confirm_back():
    """同一个事务：记忆写不进去，文档也不许写（宁可一条都不写）。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_packet(),
            "memory_curation": curator_reply(decision_change()),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    await harness.integrate_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    accepted = [str(item.proposal_id) for item in round_.draft.proposals]

    with pytest.raises(UnknownMemoryProposal):
        await harness.confirm_draft()(
            round_id=round_id, accepted=accepted, accepted_memory=["mem#404"]
        )

    assert harness.documents.get(PROJECT).current_version.version_no == 1   # 文档没写
    assert harness.memory.get(PROJECT).entries == ()
    assert harness.rounds.get(round_id).waiting_on is WaitingOn.DRAFT       # 还能重来


async def test_a_memory_change_that_names_a_missing_entry_is_refused_at_confirm():
    """领域层的严格版：确认时任何一条不合法 → 整批抛错（提议侧早就筛过了）。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_packet(),
            "memory_curation": curator_reply(
                decision_change() | {"op": "delete", "text": "", "target_id": "mem-404"}
            ),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    # 提议侧就筛掉了（记忆里压根没有 mem-404），所以卡片上没有这一条
    round_ = harness.rounds.get(round_id)
    assert round_.memory_proposals == ()
    assert any("点名的记忆条目不存在" in item for item in round_.draft.gaps)


# —— M6c：注入。已确认的记忆要真的出现在席位与整合者的提示词里 ——


async def test_confirmed_memory_is_injected_into_the_seat_and_the_integrator():
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_packet(),
            "memory_curation": curator_reply(decision_change()),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    await harness.integrate_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    accepted = [str(item.proposal_id) for item in round_.draft.proposals]
    await harness.confirm_draft()(
        round_id=round_id, accepted=accepted, accepted_memory=["mem#1"]
    )

    # 第 2 轮：同一份记忆要出现在**席位**与**整合者**两处提示词里
    second = await harness.start_round().create(
        project_id=PROJECT, user_input="想做个订单导出的功能"
    )
    await harness.start_round().analyze(round_id=second)
    await harness.submit_opening()(round_id=second, seats=(SEAT,))
    await harness.run_seat_round()(round_id=second)
    await harness.integrate_round()(round_id=second)

    seat_memory = harness.prompts.last("seat_review")["project_memory"]
    integrator_memory = harness.prompts.last("integrator")["project_memory"]
    assert "导出保留期是 30 天" in seat_memory
    assert "导出保留期是 30 天" in integrator_memory
    assert "以正文为准" in seat_memory and "以正文为准" in integrator_memory


async def test_a_project_without_memory_states_it_plainly_in_both_prompts():
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": draft_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    assert (
        harness.prompts.last("seat_review")["project_memory"]
        == "（这个项目还没有确认过的记忆）"
    )
    assert (
        harness.prompts.last("integrator")["project_memory"]
        == "（这个项目还没有确认过的记忆）"
    )
