"""真跑验证：6 轮完整主闭环，覆盖 6 个不同领域。
用 Harness（真实容器 + 脚本化模型），跑 6 轮完整迭代后逐项断言。"""
import json

import pytest

from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.shared.ids import FeatureSlug
from tests.support.harness import Harness

# ── 席位目录（10 个真实席位）──
ALL_SEATS = [
    SeatDefinition("seat.data_scale", "数据规模与性能",
                   "量级、性能上限与同步异步的分界", (), True,
                   ("s8", "s10"), ("acceptance", "io")),
    SeatDefinition("seat.business_rules", "业务规则",
                   "状态流转、筛选口径、边界条件、异常处理", (), True,
                   ("s5", "s6", "s9"), ("rules", "exceptions")),
    SeatDefinition("seat.frontend_interaction", "前端交互",
                   "入口、反馈、加载与失败态、多端覆盖", (), True,
                   ("s5", "s6"), ("main_flow", "user_story")),
    SeatDefinition("seat.permissions", "权限与角色",
                   "谁能看、谁能做、数据外流口径、审计留痕", (), True,
                   ("s3", "s5", "s7"), ("audience", "exceptions")),
    SeatDefinition("seat.tech_approach", "技术方案",
                   "需求与方案的区分、依赖引入成本、可运维性、备选方案", (), True,
                   ("s7", "s9"), ("dependencies", "rules")),
    SeatDefinition("seat.export_import", "导出与导入",
                   "导出范围、格式、大文件、脱敏、幂等与重复导入",
                   ("导出", "导入", "Excel", "CSV", "报表", "下载"), False,
                   ("s5", "s7"), ("io", "acceptance")),
    SeatDefinition("seat.state_machine", "状态机与流转",
                   "对象有哪些状态、谁能改、非法流转怎么拦、回滚与重试",
                   ("状态", "流转", "审核", "审批", "工作流", "驳回", "撤销"), False,
                   ("s5", "s6"), ("rules", "exceptions")),
    SeatDefinition("seat.integration", "第三方集成",
                   "对接方式、鉴权、幂等、超时与补偿、对端不可用时的降级",
                   ("对接", "接口", "API", "第三方", "ERP", "支付", "同步", "回调"), False,
                   ("s7", "s9"), ("dependencies", "io")),
    SeatDefinition("seat.compliance", "合规与数据安全",
                   "敏感信息范围、脱敏与留存、审计留痕、对外提供数据的口径",
                   ("合规", "审计", "隐私", "敏感", "脱敏", "等保", "安全", "加密"), False,
                   ("s7", "s8"), ("exceptions", "acceptance")),
    SeatDefinition("seat.notification", "通知与触达",
                   "触达渠道、频率、模板、退订与失败重试",
                   ("通知", "提醒", "短信", "邮件", "推送", "订阅", "消息", "触达"), False,
                   ("s5", "s6"), ("main_flow", "rules")),
]


def _make_claims(raw_spans_texts, slug, seat_ids, section_id="s5"):
    """构造 claims reply。raw_spans_texts: [(raw_span, text, label), ...]"""
    claims = []
    for raw_span, text, label in raw_spans_texts:
        c = {"text": text, "raw_span": raw_span, "label": label,
             "zone": "body", "section_id": section_id}
        if slug:
            c["feature_slug"] = slug
            c["field"] = "one_liner"
        claims.append(c)
    return json.dumps({
        "basis": "读了全文",
        "claims": claims,
        "seats": [{"seat_id": s, "why": f"需要{s}视角"} for s in seat_ids],
    })


def _make_seat_reply(opinions):
    """构造席位回复。opinions: [(local_id, form, about, claim, because), ...]"""
    return json.dumps({
        "basis": "专业视角",
        "opinions": [
            {"local_id": oid, "form": form, "about": about,
             "claim": claim, "because": because}
            for oid, form, about, claim, because in opinions
        ],
    })


def _make_integration(slug, proposals):
    """构造整合回复。proposals: [(field, text, evidence_refs), ...]"""
    return json.dumps({
        "basis": "综合意见",
        "proposals": [
            {"op": "add",
             "target": {"zone": "body", "section_id": "s5",
                         "feature_slug": slug, "field": field},
             "text": text, "evidence_refs": evidence_refs,
             "rationale": "综合判断"}
            for field, text, evidence_refs in proposals
        ],
        "questions": [],
        "list_items": [],
        "dropped_opinions": [],
    })


@pytest.fixture
def harness():
    """构造带 10 个席位的 Harness，每轮用不同的脚本化回复。"""
    # ── R1: 社区团购 ──
    r1_claims = _make_claims(
        [("社区团购平台", "社区团购平台", "fact"),
         ("团长负责收单和配送", "团长负责收单", "fact"),
         ("用户每天拼单下单", "用户每天拼单下单", "need")],
        "group-buying", ["seat.data_scale", "seat.business_rules"])
    r1_seat_ds = _make_seat_reply([
        ("O1", "judgment", "SKU数量", "初期200-500个SKU", "过多增加损耗"),
        ("O2", "question", "日订单量", "目标日订单量是多少？", "影响配送调度"),
    ])
    r1_seat_br = _make_seat_reply([
        ("O1", "judgment", "拼单截止时间", "拼单需设定截止时间", "无限期等待导致履约混乱"),
        ("O2", "judgment", "退款规则", "拼单失败后自动退款", "用户体验底线"),
    ])
    r1_integration = _make_integration("group-buying", [
        ("one_liner", "社区团购平台", ["E1"]),
        ("audience", "团长", ["E3"]),
        ("main_flow", "1. 用户拼单下单；2. 团长收单；3. 团长配送", ["E4"]),
        ("acceptance", "拼单成功后24小时内完成配送", ["E3"]),
    ])

    # ── R2: 导出Excel ──
    r2_claims = _make_claims(
        [("订单列表要支持导出Excel报表", "订单列表导出Excel", "need"),
         ("按时间筛选导出范围", "按时间筛选导出", "need")],
        "order-export", ["seat.export_import", "seat.data_scale"])
    r2_seat_ei = _make_seat_reply([
        ("O1", "judgment", "导出格式", "支持xlsx格式", "Excel兼容性好"),
        ("O2", "judgment", "大文件处理", "超过1万行采用异步导出", "同步导出会超时"),
    ])
    r2_seat_ds = _make_seat_reply([
        ("O1", "judgment", "导出上限", "单次导出最多5万行", "防止内存溢出"),
        ("O2", "question", "并发导出", "同时几个用户导出可以？", "影响系统负载"),
    ])
    r2_integration = _make_integration("order-export", [
        ("one_liner", "订单列表导出Excel报表", ["E6"]),
        ("rules", "支持按时间范围筛选导出", ["E7"]),
        ("acceptance", "导出文件在5分钟内生成", ["E6"]),
    ])

    # ── R3: 审批流转 ──
    r3_claims = _make_claims(
        [("订单要走审批流程", "订单走审批流程", "need"),
         ("主管审批通过才能发货", "主管审批通过才能发货", "need"),
         ("驳回要能修改重提", "驳回修改重提", "need")],
        "order-approval", ["seat.state_machine", "seat.business_rules"])
    r3_seat_sm = _make_seat_reply([
        (
            "O1",
            "judgment",
            "审批状态",
            "订单需经历提交、审批中、已通过、已驳回四个状态",
            "覆盖完整审批生命周期",
        ),
        ("O2", "judgment", "非法流转", "已发货订单不可回退到审批中", "防止状态混乱"),
    ])
    r3_seat_br = _make_seat_reply([
        ("O1", "judgment", "审批人", "主管为默认审批人", "组织层级清晰"),
        ("O2", "judgment", "重提次数", "驳回后最多重提3次", "避免无限循环"),
    ])
    r3_integration = _make_integration("order-approval", [
        ("one_liner", "订单审批流程", ["E9"]),
        ("rules", "主管审批通过后方可发货", ["E10"]),
        ("exceptions", "驳回后可修改重提，最多3次", ["E11"]),
        ("main_flow", "1. 提交订单；2. 主管审批；3. 通过发货/驳回重提", ["E9", "E10", "E11"]),
    ])

    # ── R4: 第三方对接 ──
    r4_claims = _make_claims(
        [("需要对接第三方ERP系统", "对接第三方ERP系统", "need"),
         ("通过API同步库存和订单数据", "API同步库存和订单", "need")],
        "erp-sync", ["seat.integration", "seat.tech_approach"])
    r4_seat_int = _make_seat_reply([
        ("O1", "judgment", "对接方式", "采用REST API双向同步", "标准化、易维护"),
        ("O2", "judgment", "超时处理", "API超时30秒后重试，最多3次", "网络不稳定"),
    ])
    r4_seat_ta = _make_seat_reply([
        ("O1", "judgment", "鉴权方式", "使用OAuth2.0鉴权", "行业标准"),
        ("O2", "question", "对端降级", "ERP不可用时如何降级？", "影响业务连续性"),
    ])
    r4_integration = _make_integration("erp-sync", [
        ("one_liner", "对接第三方ERP系统", ["E13"]),
        ("io", "通过API同步库存和订单数据", ["E14"]),
        ("dependencies", "依赖第三方ERP提供的API文档与测试环境", ["E13"]),
    ])

    # ── R5: 数据安全 ──
    r5_claims = json.dumps({
        "basis": "读了全文",
        "claims": [
            {"text": "个人信息脱敏存储", "raw_span": "用户个人信息需要脱敏存储",
             "label": "need", "zone": "body", "section_id": "s8", "field": "security"},
            {"text": "敏感操作审计日志", "raw_span": "敏感操作要留审计日志",
             "label": "need", "zone": "body", "section_id": "s5",
             "feature_slug": "audit-log", "field": "one_liner"},
            {"text": "符合等保要求", "raw_span": "符合等保要求",
             "label": "constraint", "zone": "body", "section_id": "s7"},
        ],
        "seats": [
            {"seat_id": "seat.compliance", "why": "脱敏与等保是合规核心"},
            {"seat_id": "seat.permissions", "why": "审计日志涉及权限分离"},
        ],
    })
    r5_seat_com = _make_seat_reply([
        ("O1", "judgment", "脱敏范围", "手机号、身份证号、银行卡号必须脱敏", "个人信息保护法要求"),
        ("O2", "judgment", "审计留存", "审计日志至少留存6个月", "等保2.0要求"),
    ])
    r5_seat_per = _make_seat_reply([
        ("O1", "judgment", "三权分立", "系统管理员、安全管理员、审计员三权分立", "等保要求"),
        ("O2", "judgment", "数据外流", "敏感数据导出需审批", "防止数据泄露"),
    ])
    r5_integration = json.dumps({
        "basis": "综合意见",
        "proposals": [
            {"op": "add",
             "target": {"zone": "body", "section_id": "s8", "field": "security"},
             "text": "手机号、身份证号、银行卡号脱敏存储",
             "evidence_refs": ["E16"], "rationale": "合规要求"},
            {"op": "add",
             "target": {"zone": "body", "section_id": "s5",
                         "feature_slug": "audit-log", "field": "one_liner"},
             "text": "敏感操作审计日志",
             "evidence_refs": ["E17"], "rationale": "审计需求"},
            {"op": "add",
             "target": {"zone": "body", "section_id": "s5",
                         "feature_slug": "audit-log", "field": "main_flow"},
             "text": "1. 敏感操作触发；2. 记录操作人、时间、内容；3. 写入审计日志",
             "evidence_refs": ["E17"], "rationale": "审计流程"},
            {"op": "add",
             "target": {"zone": "body", "section_id": "s5",
                         "feature_slug": "audit-log", "field": "acceptance"},
             "text": "审计日志不可篡改，留存6个月以上",
             "evidence_refs": ["E18"], "rationale": "等保要求"},
        ],
        "questions": [],
        "list_items": [],
        "dropped_opinions": [],
    })

    # ── R6: 消息推送 ──
    r6_claims = _make_claims(
        [("订单状态变更时要推送消息通知用户", "订单状态变更推送通知", "need"),
         ("支持短信和站内信两种渠道", "支持短信和站内信", "need")],
        "notification", ["seat.notification", "seat.frontend_interaction"])
    r6_seat_not = _make_seat_reply([
        (
            "O1",
            "judgment",
            "触达渠道",
            "短信用于重要状态变更，站内信用于普通通知",
            "成本与用户体验平衡",
        ),
        ("O2", "judgment", "退订机制", "用户可退订非关键通知", "避免打扰用户"),
    ])
    r6_seat_fi = _make_seat_reply([
        ("O1", "judgment", "通知展示", "站内信在顶部红点提示", "用户注意力引导"),
        ("O2", "question", "批量已读", "通知是否支持一键已读？", "影响用户体验"),
    ])
    r6_integration = _make_integration("notification", [
        ("one_liner", "订单状态变更消息推送", ["E20"]),
        ("rules", "支持短信和站内信两种渠道", ["E21"]),
        ("main_flow", "1. 状态变更触发；2. 按规则选渠道；3. 发送并记录", ["E20", "E21"]),
        ("acceptance", "通知发送成功率不低于99.5%", ["E20"]),
    ])

    # ── 汇总 ──
    all_claims = [r1_claims, r2_claims, r3_claims, r4_claims, r5_claims, r6_claims]
    all_integration = [r1_integration, r2_integration, r3_integration,
                       r4_integration, r5_integration, r6_integration]

    seat_replies = {
        "seat.data_scale": [r1_seat_ds, r2_seat_ds],
        "seat.business_rules": [r1_seat_br, r3_seat_br],
        "seat.export_import": [r2_seat_ei],
        "seat.state_machine": [r3_seat_sm],
        "seat.integration": [r4_seat_int],
        "seat.tech_approach": [r4_seat_ta],
        "seat.compliance": [r5_seat_com],
        "seat.permissions": [r5_seat_per],
        "seat.notification": [r6_seat_not],
        "seat.frontend_interaction": [r6_seat_fi],
    }

    memory_empty = json.dumps({"changes": []})

    return Harness(
        replies={
            "claims": all_claims,
            "integration": all_integration,
            "memory_curator": [memory_empty] * 6,
            "discussion": json.dumps({"text": "讨论回复", "based_on": []}),
            "discussion_cards": json.dumps({"cards": [
                {"statement": "讨论结论", "grounding": "invented", "based_on": []},
            ]}),
        },
        seat_replies=seat_replies,
        seats=ALL_SEATS,
    )


async def _run_full_round(harness, project_id, user_input, seats_for_round):
    """跑一轮完整闭环：输入 → 复述 → 席位 → 整合 → 确认。"""
    start = harness.start_round()
    round_id = await start.create(project_id=project_id, user_input=user_input)
    await start.analyze(round_id=round_id)

    round_obj = harness.rounds.get(round_id)
    for claim in round_obj.claims:
        slug = claim.target.feature_slug
        if slug and str(slug) not in [str(f) for f in round_obj.authorized_new_features]:
            round_obj.authorize_new_feature(FeatureSlug(str(slug)), basis=claim.eid)
    harness.rounds.save(round_obj)

    await harness.submit_opening()(round_id=round_id, seats=seats_for_round)
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)

    round_view = await harness.get_round()(round_id=round_id)
    if round_view.draft:
        await harness.confirm_draft()(
            round_id=round_id,
            accepted=tuple(p.proposal_id for p in round_view.draft.proposals),
        )
    return round_id


# 每轮实际调用的席位（与 claims 中的提名一致）
ROUND_SEATS = [
    ("seat.data_scale", "seat.business_rules"),
    ("seat.export_import", "seat.data_scale"),
    ("seat.state_machine", "seat.business_rules"),
    ("seat.integration", "seat.tech_approach"),
    ("seat.compliance", "seat.permissions"),
    ("seat.notification", "seat.frontend_interaction"),
]

ROUND_INPUTS = [
    "做一个社区团购平台，团长负责收单和配送，用户每天拼单下单",
    "订单列表要支持导出Excel报表，按时间筛选导出范围",
    "订单要走审批流程，主管审批通过才能发货，驳回要能修改重提",
    "需要对接第三方ERP系统，通过API同步库存和订单数据",
    "用户个人信息需要脱敏存储，敏感操作要留审计日志，符合等保要求",
    "订单状态变更时要推送消息通知用户，支持短信和站内信两种渠道",
]


@pytest.mark.asyncio
async def test_6_rounds_full_pipeline(harness):
    """6 轮完整主闭环：每轮不同领域，逐轮断言 + 最终汇总断言。"""
    pid = harness.seed_project(title="长期项目")

    results = []

    for i in range(6):
        user_input = ROUND_INPUTS[i]
        print(f"\n{'='*60}")
        print(f"  第 {i+1} 轮: {user_input[:30]}...")
        print(f"{'='*60}")

        round_id = await _run_full_round(harness, pid, user_input, ROUND_SEATS[i])

        # ── 逐轮断言 ──
        round_obj = harness.rounds.get(round_id)
        assert len(round_obj.claims) > 0, f"R{i+1}: 主张应被提取"
        assert len(round_obj.nominated_seats) > 0, f"R{i+1}: 席位应被提名"
        assert round_obj.seat_reviews, f"R{i+1}: 席位轮应完成"
        assert round_obj.draft is not None, f"R{i+1}: 草案应生成"

        # 确认后文档应多一版
        versions = await harness.get_versions()(project_id=pid)
        assert len(versions.versions) == i + 2, \
            f"R{i+1}: 确认后应有 {i+2} 版（初始1 + {i+1}次确认），实际 {len(versions.versions)}"

        # L2 观察
        recent_obs = harness.observations.recent(str(pid))
        print(f"  主张数: {len(round_obj.claims)}")
        print(f"  提名席位: {[s.seat_id for s in round_obj.nominated_seats]}")
        print(f"  席位意见数: {sum(len(r.opinions) for r in round_obj.seat_reviews)}")
        print(f"  草案提案数: {len(round_obj.draft.proposals) if round_obj.draft else 0}")
        print(f"  L2 观察数(累计): {len(recent_obs)}")

        results.append({
            "round": i + 1,
            "claims": len(round_obj.claims),
            "seats": [s.seat_id for s in round_obj.nominated_seats],
            "opinions": sum(len(r.opinions) for r in round_obj.seat_reviews),
            "proposals": len(round_obj.draft.proposals) if round_obj.draft else 0,
            "observations": len(recent_obs),
        })

    # ── 最终汇总断言 ──
    print(f"\n{'='*60}")
    print("  最终汇总验证")
    print(f"{'='*60}")

    # 文档应有 7 版（初始1 + 6次确认）
    versions = await harness.get_versions()(project_id=pid)
    assert len(versions.versions) == 7, \
        f"最终应有 7 版，实际 {len(versions.versions)}"
    print(f"  文档版本数: {len(versions.versions)} ✓")

    # L2 观察应有累计（去重后）
    all_obs = harness.observations.recent(str(pid), limit=50)
    print(f"  L2 观察总数(去重): {len(all_obs)}")
    assert len(all_obs) > 0, "6 轮后应有 L2 观察"

    # L4 摘要：每轮确认后生成
    all_digests = harness.digests.recent(str(pid), limit=10)
    print(f"  L4 摘要数: {len(all_digests)}")
    assert len(all_digests) == 6, f"应有 6 份摘要，实际 {len(all_digests)}"

    # 轮次总数
    all_rounds = harness.rounds.all_rounds()
    round_count = sum(1 for r in all_rounds if str(r.project_id) == str(pid))
    assert round_count == 6, f"应有 6 轮，实际 {round_count}"
    print(f"  轮次数: {round_count} ✓")

    # 逐轮详情
    print("\n  逐轮详情:")
    for r in results:
        print(f"    R{r['round']}: claims={r['claims']}, "
              f"seats={r['seats']}, "
              f"opinions={r['opinions']}, "
              f"proposals={r['proposals']}, "
              f"obs={r['observations']}")

    print("\n  ✓ 6 轮完整主闭环验证通过")
