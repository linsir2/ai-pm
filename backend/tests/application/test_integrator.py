import json

import pytest

from pmstudio.application.agents import schemas
from pmstudio.application.agents.integrator import LIMITS_TEXT, Integrator
from pmstudio.harness.errors import PacketParseError
from tests.support.doubles import RecordingPrompts, ScriptedModelGateway


def test_integrator_limits_forbid_the_same_question_twice():
    """口径（2026-09-28 拍板）：**同一件事**不许问两遍；**同一去处的两件不同事各出一题**。

    来历：先写成"同一个去处只出一题"，结果 `case-a` 上 `s8.availability` 的
    "幂等性与重试策略" 与 "对端不可用时的降级策略" 被当成重复——那是两道真问题，
    合并会丢结构、丢掉哪一道都是把用户该填的题吞了。提示词与断言的口径必须一致
    （断言考的东西，提示词先说）。
    """

    assert "同一件事不要问两遍" in LIMITS_TEXT
    assert "两件不同的事" in LIMITS_TEXT


def test_limits_tell_the_truth_about_what_repetition_and_overflow_actually_cost():
    """两句提示词曾经与机制**相反**，这里钉住说真话的那一面。

    ① 题位满：`assembly.py:701-708` 把超 5 的题放进**轮内队列 deferred**（下一批先问），
       轮结束没排上的落 s11，其中阻塞题被就绪判定挡住（`readiness.py:211-227`）。
       提示词原来写"放进 `list_items`（本轮不挡交付）"——那是 **F12 那个 bug 的做法**
       （`test_assembly.py:1264` 的 docstring 记着"真跑里它们会消失"）。

    ② 重复题：`assembly.py:666-668` 命中去重后是 `continue`——**悄悄丢掉，模型收不到
       任何提示**。原来写"整轮不算通过"，那是一句系统并不执行、模型也无法验证的威胁，
       正撞 AGENTS.md「门禁条款别写成形容词，机器判不了的条款最终会退化成永远通过」。
    """

    assert "多出来的放进 list_items" not in LIMITS_TEXT
    assert "下一批先问" in LIMITS_TEXT
    assert "整轮不算通过" not in LIMITS_TEXT
    assert "悄悄丢掉" in LIMITS_TEXT


def test_limits_say_authorized_features_must_land_in_feature_fields():
    """真跑实测（报告 F1）：主张点名的功能已授权，模型却把内容降级成章节级 → s5 整章空。"""

    assert "已授权" in LIMITS_TEXT
    assert "功能字段" in LIMITS_TEXT


def test_limits_tell_the_model_to_process_open_items():
    """未决项不能原样再留一遍：能定的写内容 + 出 remove；定不了的出题（P9-B）。"""

    assert "未决" in LIMITS_TEXT
    assert "remove" in LIMITS_TEXT


def test_limits_say_an_adopted_opinion_must_be_cited():
    """真跑审计 F19：整合者把判断的内容写进提案，却只引它依据的用户编号（E4）、
    不引那条判断自己的编号（E9）→ 判据说"这条意见没人交代"，而它的内容其实已经进卡片了。

    实测：4 条车道 13 个轮次里，`unaccounted` 有 48–100% 是这种情况。
    判据考的是"引它自己的编号"，提示词就必须先说——考一件从没交代过的事，
    等于在惩罚模型的猜测（learnings L22）。
    """

    assert "那条判断自己的出处编号" in LIMITS_TEXT
    assert "变成题" in LIMITS_TEXT  # 疑问那条路：变成题就要留地址


async def test_integrator_passes_the_open_item_list():
    gateway = ScriptedModelGateway(replies={"integration": INTEGRATION_REPLY})
    prompts = RecordingPrompts()
    integrator = Integrator(model=gateway, prompts=prompts)

    await integrator.integrate(
        document_state="# 文档",
        claims="…",
        opinions="…",
        answered="…",
        skipped="…",
        entry_ids="…",
        open_items="- [r0#open01] 点检完成率的统计口径（跳过后果：报表口径不清）",
    )

    assert "r0#open01" in prompts.calls[0][1]["open_items"]


async def test_integrator_passes_the_authorized_feature_list():
    gateway = ScriptedModelGateway(replies={"integration": INTEGRATION_REPLY})
    prompts = RecordingPrompts()
    integrator = Integrator(model=gateway, prompts=prompts)

    await integrator.integrate(
        document_state="# 文档",
        claims="…",
        opinions="…",
        answered="…",
        skipped="…",
        entry_ids="…",
        authorized_features="- order-export（被 E4 点名，已授权）：直接写它的功能字段",
    )

    assert "order-export" in prompts.calls[0][1]["authorized_features"]

INTEGRATION_REPLY = json.dumps(
    {
        "questions": [
            {
                "kind": "gap",
                "title": "单次导出的最大行数是多少？",
                "why": "量级决定同步还是异步，只有用户知道",
                "consequence_if_skipped": "按同步实现，超时风险由 PM 承担",
                "target": {"zone": "body", "section_id": "s8"},
                "type": "single",
                "options": [
                    {"option_id": "a", "text": "少于 1 万"},
                    {"option_id": "b", "text": "1 万–10 万"},
                ],
                "evidence_refs": ["E4"],
                "asked_by": ["seat.data_scale"],
            }
        ],
        "list_items": [],
        "proposals": [
            {
                "op": "add",
                "target": {
                    "zone": "body",
                    "section_id": "s5",
                    "feature_slug": "order-export",
                    "field": "acceptance",
                },
                "text": "5 万条以内 60 秒可下载",
                "origin_type": "derived",
                "evidence_refs": ["E4", "E9"],
                "rationale": "量级在 1 万级，同步实现可满足",
                "kind": "acceptance",
            }
        ],
    },
    ensure_ascii=False,
)


async def test_integrator_returns_parsed_packet_and_all_slots():
    gateway = ScriptedModelGateway(replies={"integration": INTEGRATION_REPLY})
    prompts = RecordingPrompts()
    integrator = Integrator(model=gateway, prompts=prompts)

    outcome = await integrator.integrate(
        document_state="# 文档",
        claims="- [E1] 需求 · s5 · 要做一个订单导出功能",
        opinions="### seat.data_scale（数据规模与性能）…",
        answered="（还没有答过题）",
        skipped="（没有跳过的题）",
        entry_ids="（文档现在没有任何条目）",
    )
    packet = outcome.envelope.packet

    assert packet.questions[0].kind == "gap"
    assert packet.proposals[0].target.field == "acceptance"
    request = gateway.requests[0]
    assert request.purpose == "integration"
    assert request.schema_name == "IntegrationPacket"
    name, slots = prompts.calls[0]
    assert name == "integrator"
    assert tuple(sorted(slots)) == tuple(sorted(Integrator.definition.slots))
    assert slots["validation_error"] == ""  # 第一次不给错误
    assert "最多 5" in slots["limits"]
    assert "功能子树" in slots["limits"]
    assert "60" in slots["limits"]  # 提案上限与 schema 对齐


async def test_integrator_passes_validation_error_on_repair():
    gateway = ScriptedModelGateway(replies={"integration": INTEGRATION_REPLY})
    prompts = RecordingPrompts()
    integrator = Integrator(model=gateway, prompts=prompts)

    await integrator.integrate(
        document_state="# 文档",
        claims="…",
        opinions="…",
        answered="…",
        skipped="…",
        entry_ids="…",
        validation_error="未知字段：exceptionss",
    )

    assert "未知字段：exceptionss" in prompts.calls[0][1]["validation_error"]


async def test_integrator_bad_missing_proposal_refs_is_a_packet_error():
    reply = json.dumps(
        {
            "questions": [],
            "list_items": [],
            "proposals": [
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s5"},
                    "text": "x",
                    "origin_type": "derived",
                    "evidence_refs": [],
                }
            ],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"integration": reply})
    integrator = Integrator(model=gateway, prompts=RecordingPrompts())

    with pytest.raises(PacketParseError):
        await integrator.integrate(
            document_state="# 文档",
            claims="…",
            opinions="…",
            answered="…",
            skipped="…",
            entry_ids="…",
        )


def over_cap_reply(count: int) -> str:
    return json.dumps(
        {
            "questions": [],
            "list_items": [],
            "proposals": [
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s1"},
                    "text": f"第 {index} 条",
                    "origin_type": "derived",
                    "evidence_refs": ["E4"],
                }
                for index in range(count)
            ],
            "dropped_opinions": [],
        },
        ensure_ascii=False,
    )


async def test_integrator_reports_overflow_instead_of_raising():
    """真跑实测：CRF 那轮 44 条提案整轮被毙；现在只截断并报告（报告 F2）。"""

    gateway = ScriptedModelGateway(replies={"integration": over_cap_reply(70)})
    integrator = Integrator(model=gateway, prompts=RecordingPrompts())

    outcome = await integrator.integrate(
        document_state="# 文档",
        claims="…",
        opinions="…",
        answered="…",
        skipped="…",
        entry_ids="…",
    )

    assert len(outcome.envelope.packet.proposals) == 60
    assert outcome.overflow == (schemas.Overflow(container="proposals", kept=60, dropped=10),)


async def test_integrator_reports_a_non_object_item_instead_of_raising():
    """P23 B：整合的容器里混进占位符 → 摘掉那一条并上报（和席位、主张同一套）。"""

    reply = json.dumps(
        {
            "questions": [],
            "list_items": [],
            "proposals": [
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s1"},
                    "text": "正常的一条",
                    "origin_type": "derived",
                    "evidence_refs": ["E4"],
                },
                "p2_placeholder",
            ],
            "dropped_opinions": [],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"integration": reply})
    integrator = Integrator(model=gateway, prompts=RecordingPrompts())

    outcome = await integrator.integrate(
        document_state="# 文档",
        claims="…",
        opinions="…",
        answered="…",
        skipped="…",
        entry_ids="…",
    )

    assert [item.text for item in outcome.envelope.packet.proposals] == ["正常的一条"]
    assert [(item.container, item.index) for item in outcome.discarded] == [("proposals", 2)]


async def test_a_packet_that_is_all_placeholders_is_an_error_not_an_empty_draft():
    """整包都是占位符 = 什么都没给：**判成报错**走重装/降级那条路。

    不判死的话它会一路走到 `draft_of(...) is None` → `ShapeError` → HTTP 409，
    用户看到的是一句莫名其妙的"整合既没有给出题目也没有给出提案"。
    """

    reply = json.dumps(
        {
            "questions": [],
            "list_items": [],
            "proposals": ["p1_placeholder"],
            "dropped_opinions": [],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"integration": reply})
    integrator = Integrator(model=gateway, prompts=RecordingPrompts())

    with pytest.raises(PacketParseError) as error:
        await integrator.integrate(
            document_state="# 文档",
            claims="…",
            opinions="…",
            answered="…",
            skipped="…",
            entry_ids="…",
        )

    assert "占位符" in error.value.message


async def test_dispositions_returns_the_declaration_packet():
    """归因补交代的小调用：同一个席位、另一种提示词、另一个 purpose（`02` §11.0.38）。"""

    reply = json.dumps(
        {"dropped_opinions": [{"handle": "seat.x:O2", "reason": "与文档已有内容重复"}]},
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"opinion_dispositions": reply})
    prompts = RecordingPrompts()
    integrator = Integrator(model=gateway, prompts=prompts)

    outcome = await integrator.dispositions(
        opinions="上述几条意见", produced="题目 #1：evidence_refs=[E1]"
    )

    assert [item.handle for item in outcome.envelope.packet.dropped_opinions] == ["seat.x:O2"]
    assert gateway.requests[-1].purpose == "opinion_dispositions"
    assert prompts.calls[-1][0] == "opinion_dispositions"


async def test_dispositions_drops_a_non_object_entry_without_raising():
    """小调用也会遇上占位符：与整包同一条口径——摘掉那一条、上报，其余照收。"""

    reply = json.dumps(
        {
            "dropped_opinions": [
                {"handle": "seat.x:O2", "reason": "重复"},
                "O3_placeholder",
            ]
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"opinion_dispositions": reply})
    integrator = Integrator(model=gateway, prompts=RecordingPrompts())

    outcome = await integrator.dispositions(opinions="…", produced="…")

    assert [item.handle for item in outcome.envelope.packet.dropped_opinions] == ["seat.x:O2"]
    assert [(item.container, item.index) for item in outcome.discarded] == [
        ("dropped_opinions", 2)
    ]
