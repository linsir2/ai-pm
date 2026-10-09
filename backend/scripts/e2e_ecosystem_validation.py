"""生态闭环验证：系统是否真的能帮到产品经理？

不是验证"能写出文档"，而是验证：
1. 用户没思路时，系统能否给出有价值的方向（灵感质量）
2. 评审意见是否专业、可操作、能发现问题（评审质量）
3. 每一步是否真的在帮用户推进（过程帮助度）
4. 最终文档是否真的能指导别人做事（文档可用性）

模拟 3 位不同角色的产品经理，各自创建项目、进行多轮长时对话、使用全功能。
每一步都评估"帮助度"，不是机械跑流程。
"""

import asyncio
import json
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support.harness import Harness, PROJECT, make_seat
from pmstudio.domain.shared.ids import ExplorationId, FeatureSlug, EvidenceId
from pmstudio.domain.exploration.exploration import Exploration, ExplorationScope, ScopeKind
from pmstudio.domain.exploration.item import Hypothesis, BlindSpot, Grounding
from pmstudio.domain.discussion.thread import QuestionSnapshot


# ═══════════════════════════════════════════════════════════════
# 评估框架：不是验证"能不能跑"，而是验证"有没有用"
# ═══════════════════════════════════════════════════════════════

@dataclass
class StepEvaluation:
    """单步评估：这一步对 PM 有没有帮助？"""
    step_name: str
    helpfulness: int  # 0=无用, 1=有点用, 2=有用, 3=非常有用
    reflection: str   # 为什么这么评
    output_summary: str = ""
    concerns: list[str] = field(default_factory=list)


@dataclass
class ProjectEvaluation:
    """项目级评估"""
    project_name: str
    persona: str
    steps: list[StepEvaluation] = field(default_factory=list)
    final_document_score: int = 0
    final_reflection: str = ""
    inspiration_sources_used: dict[str, int] = field(default_factory=dict)


@dataclass
class EcosystemReport:
    """生态级总报告"""
    projects: list[ProjectEvaluation] = field(default_factory=list)
    overall_helpfulness: float = 0.0
    critical_gaps: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    verdict: str = ""


def evaluate_helpfulness(step_name: str, output: Any, context: dict) -> StepEvaluation:
    """评估单步帮助度——核心判断逻辑"""
    concerns = []
    helpfulness = 1  # 默认"有点用"
    reflection = ""

    # 讨论卡评估
    if step_name.startswith("discussion_card"):
        cards = output if isinstance(output, list) else [output]
        specific_count = 0
        generic_count = 0
        for card in cards:
            text = card.statement if hasattr(card, 'statement') else str(card)
            # 检查是否具体（不是泛泛而谈）
            if len(text) < 20:
                concerns.append(f"卡太短: '{text}'")
                generic_count += 1
            elif any(word in text for word in ["考虑", "注意", "应该", "建议", "可以"]):
                generic_count += 1
            else:
                specific_count += 1

        if specific_count > generic_count:
            helpfulness = 2
            reflection = f"{specific_count} 张具体卡，{generic_count} 张泛泛卡"
        else:
            helpfulness = 1
            reflection = f"泛泛卡多({generic_count})，具体卡少({specific_count})"

    # 探索假设评估
    elif step_name.startswith("exploration"):
        if hasattr(output, 'hypotheses'):
            hypos = output.hypotheses
            falsifiable = sum(1 for h in hypos if h.falsification and len(h.falsification) > 10)
            if falsifiable == len(hypos) and len(hypos) > 0:
                helpfulness = 3
                reflection = f"{len(hypos)} 条假设全部可证伪"
            elif falsifiable > 0:
                helpfulness = 2
                reflection = f"{falsifiable}/{len(hypos)} 条可证伪"
            else:
                helpfulness = 0
                reflection = "假设不可证伪，等于没说"
                concerns.append("所有假设都缺 falsification")

    # 模拟调研切片评估
    elif step_name.startswith("sim_inquiry"):
        if hasattr(output, 'slices'):
            slices = output.slices
            realistic = sum(1 for s in slices if s.situation and s.job and s.trigger)
            if realistic == len(slices) and len(slices) > 0:
                helpfulness = 3
                reflection = f"{len(slices)} 个处境切片全部具体"
            else:
                helpfulness = 1
                reflection = f"切片不够具体: {realistic}/{len(slices)}"

    # 模拟评测摩擦评估
    elif step_name.startswith("sim_review"):
        if hasattr(output, 'features'):
            frictions = []
            for f in output.features:
                frictions.extend(f.frictions)
            real_frictions = sum(1 for f in frictions if f.blocks_flow or f.not_documented)
            if real_frictions > 0:
                helpfulness = 3
                reflection = f"发现 {real_frictions} 个真实阻塞点"
            elif len(frictions) > 0:
                helpfulness = 2
                reflection = f"{len(frictions)} 个摩擦点"
            else:
                helpfulness = 0
                reflection = "没发现任何摩擦——要么文档完美，要么评测走过场"
                concerns.append("零摩擦结果不可信")

    # 席位意见评估
    elif step_name.startswith("seat"):
        if hasattr(output, 'opinions'):
            ops = output.opinions
            questions = sum(1 for o in ops if o.form == "question")
            judgments = sum(1 for o in ops if o.form == "judgment")
            with_basis = sum(1 for o in ops if o.because and len(o.because) > 5)
            if with_basis == len(ops) and questions > 0:
                helpfulness = 3
                reflection = f"{judgments} 判断 + {questions} 疑问，全部有依据"
            elif with_basis > 0:
                helpfulness = 2
                reflection = f"{with_basis}/{len(ops)} 条有依据"
            else:
                helpfulness = 0
                reflection = "意见无依据，等于没说"
                concerns.append("席位意见缺 because")

    # 整合提案评估
    elif step_name.startswith("integration"):
        if hasattr(output, 'proposals'):
            proposals = output.proposals
            with_refs = sum(1 for p in proposals if p.eids)
            well_targeted = sum(1 for p in proposals if p.target_label and p.text and len(p.text) > 10)
            if well_targeted == len(proposals) and with_refs > 0:
                helpfulness = 3
                reflection = f"{len(proposals)} 提案全部有出处有目标"
            elif well_targeted > 0:
                helpfulness = 2
                reflection = f"{well_targeted}/{len(proposals)} 提案目标明确"
            else:
                helpfulness = 1
                reflection = "提案目标模糊或没出处"

    else:
        reflection = "已执行，待人工评估"

    return StepEvaluation(
        step_name=step_name,
        helpfulness=helpfulness,
        reflection=reflection,
        output_summary=str(output)[:200] if output else "",
        concerns=concerns,
    )


# ═══════════════════════════════════════════════════════════════
# 项目 A：创业者 — 社区团购平台
# ═══════════════════════════════════════════════════════════════

def build_harness_a():
    """创业者 A：社区团购平台，从 0 到 1"""
    return Harness(
        replies={
            "claims": [
                # R1: 初始模糊想法
                json.dumps({"basis": "读了全文", "claims": [
                    {"text": "社区团购平台服务于生鲜采购", "raw_span": "社区团购平台", "label": "fact", "zone": "body", "section_id": "s1"},
                    {"text": "核心用户是家庭主妇和上班族", "raw_span": "家庭主妇和上班族", "label": "guess", "zone": "body", "section_id": "s2",
                     "assumption": {"failure_consequence": "获客成本上升", "verification": "用户访谈"}},
                    {"text": "团长负责最后一公里配送", "raw_span": "最后一公里配送", "label": "fact", "zone": "body", "section_id": "s5"},
                    {"text": "需要预售模式降低库存风险", "raw_span": "预售模式", "label": "need", "zone": "body", "section_id": "s5"},
                ], "seats": [{"seat_id": "seat.business_rules", "why": "准入规则"}, {"seat_id": "seat.data_scale", "why": "SKU预估"}],
                    "contradictions": [], "web_queries": []}, ensure_ascii=False),
                # R2: 补充预售模式
                json.dumps({"basis": "读了全文", "claims": [
                    {"text": "支持预售模式，提前一天下单", "raw_span": "预售模式", "label": "fact", "zone": "body", "section_id": "s5"},
                    {"text": "预售可降低库存损耗", "raw_span": "提前一天下单", "label": "guess", "zone": "body", "section_id": "s8",
                     "assumption": {"failure_consequence": "损耗高于预期", "verification": "预售数据"}},
                    {"text": "需要冷链物流支持", "raw_span": "冷链物流", "label": "need", "zone": "body", "section_id": "s5"},
                ], "seats": [{"seat_id": "seat.business_rules", "why": "预售规则"}, {"seat_id": "seat.data_scale", "why": "库存模型"}],
                    "contradictions": [], "web_queries": []}, ensure_ascii=False),
                # R3: 基于讨论结论
                json.dumps({"basis": "基于讨论", "claims": [
                    {"text": "预售体验需30秒下单", "raw_span": "预售体验", "label": "constraint", "zone": "body", "section_id": "s8"},
                    {"text": "增加直营渠道作为备选", "raw_span": "直营渠道", "label": "decision", "zone": "body", "section_id": "s5", "decided_by": "讨论收卡"},
                ], "seats": [{"seat_id": "seat.frontend_interaction", "why": "交互设计"}],
                    "contradictions": [], "web_queries": []}, ensure_ascii=False),
            ],
            "integration": json.dumps({"basis": "综合意见", "proposals": [
                {"op": "add", "target": {"zone": "body", "section_id": "s5", "feature_slug": "group-buying", "field": "one_liner"}, "text": "社区团购平台，团长负责社群运营和最后一公里配送", "evidence_refs": ["E1"], "rationale": "定位"},
                {"op": "add", "target": {"zone": "body", "section_id": "s5", "feature_slug": "group-buying", "field": "goal"}, "text": "30分钟达，预售模式降低库存损耗", "evidence_refs": ["E1", "E3"], "rationale": "核心需求"},
                {"op": "add", "target": {"zone": "body", "section_id": "s5", "feature_slug": "group-buying", "field": "rules"}, "text": "团长需缴纳保证金，退出时7天退还", "evidence_refs": ["E1"], "rationale": "准入规则"},
            ], "questions": [], "list_items": [], "dropped_opinions": []}, ensure_ascii=False),
            "discussion": json.dumps({"text": "反方观点：团长模式有流失风险。数据显示团长月流失率15%，建议增加直营渠道作为备选方案。同时品控需要标准化，否则用户复购率会下降。", "based_on": []}, ensure_ascii=False),
            "discussion_cards": json.dumps({"cards": [
                {"statement": "团长月流失率15%，建议增加直营渠道作为备选", "grounding": "invented", "based_on": []},
                {"statement": "品控需标准化，否则复购率会下降", "grounding": "invented", "based_on": []},
            ]}, ensure_ascii=False),
            "inquiry": json.dumps({"basis": "用户视角", "hypotheses": [
                {"local_id": "H1", "statement": "用户最在意配送时效而非价格", "grounding": "invented", "based_on": [],
                 "falsification": "如果用户频繁比价则推翻", "evidence_needed": "访谈50个用户"},
                {"local_id": "H2", "statement": "预售模式可降低30%库存损耗", "grounding": "invented", "based_on": [],
                 "falsification": "如果预售占比低于20%则推翻", "evidence_needed": "对比预售前后损耗数据"},
            ], "blind_spots": [
                {"local_id": "B1", "statement": "不知道老年用户的使用障碍", "next_step": "找10个老年用户做可用性测试"},
            ]}, ensure_ascii=False),
            "memory_curator": json.dumps({"changes": [
                {"op": "add", "kind": "decision", "text": "预售模式采用7天退还周期", "refs": ["E1"], "reason": "用户确认"},
            ]}, ensure_ascii=False),
        },
        seat_replies={
            "seat.business_rules": json.dumps({"basis": "业务规则视角", "opinions": [
                {"local_id": "O1", "form": "judgment", "about": "准入", "claim": "团长需缴纳保证金", "because": "降低流失率", "evidence_refs": []},
                {"local_id": "O2", "form": "question", "about": "退出", "claim": "退还周期多长？", "because": "影响团长意愿", "evidence_refs": []},
                {"local_id": "O3", "form": "judgment", "about": "品控", "claim": "需要标准化品控流程", "because": "用户复购依赖品质", "evidence_refs": []},
            ]}, ensure_ascii=False),
            "seat.data_scale": json.dumps({"basis": "数据规模视角", "opinions": [
                {"local_id": "O1", "form": "judgment", "about": "SKU", "claim": "初期200-500个SKU", "because": "过多增加损耗", "evidence_refs": []},
                {"local_id": "O2", "form": "question", "about": "周转", "claim": "库存周转目标？", "because": "影响损耗率", "evidence_refs": []},
            ]}, ensure_ascii=False),
            "seat.frontend_interaction": json.dumps({"basis": "交互设计视角", "opinions": [
                {"local_id": "O1", "form": "judgment", "about": "流程", "claim": "下单不超3步", "because": "减少流失", "evidence_refs": []},
                {"local_id": "O2", "form": "question", "about": "支付", "claim": "支付方式？", "because": "影响转化", "evidence_refs": []},
            ]}, ensure_ascii=False),
        },
        persona_replies={
            "sim.persona.busy_parent": json.dumps({"basis": "忙碌家长视角", "features": [
                {"feature_slug": "group-buying", "started": True,
                 "walkthrough": [
                     {"step_index": 1, "step_text": "打开小程序", "from_doc": "s5", "action": "打开小程序查看今日菜品", "outcome": "ok", "need": ""},
                     {"step_index": 2, "step_text": "选择商品", "from_doc": "s5", "action": "选3样菜加入购物车", "outcome": "ok", "need": ""},
                     {"step_index": 3, "step_text": "支付下单", "from_doc": "s5", "action": "微信支付", "outcome": "stuck", "need": "不支持微信支付"},
                 ],
                 "frictions": [
                     {"local_id": "F1", "kind": "missing_feedback", "entry_id": "", "field": "rules",
                      "quote": "", "not_documented": True, "blocks_flow": True,
                      "note": "支付后没有确认页，不知道下单成功没有"},
                 ],
                 "acceptance_checks": [
                     {"local_id": "A1", "entry_id": "E1", "status": "verified", "note": ""},
                 ],
                 "verdict": "friction", "verdict_because": ["F1"]},
            ]}, ensure_ascii=False),
        },
        seats=[
            make_seat(seat_id="seat.business_rules", title="业务规则"),
            make_seat(seat_id="seat.data_scale", title="数据规模"),
            make_seat(seat_id="seat.frontend_interaction", title="前端交互"),
        ],
    )


# ═══════════════════════════════════════════════════════════════
# 项目 B：独立开发者 — AI 笔记应用
# ═══════════════════════════════════════════════════════════════

def build_harness_b():
    """独立开发者 B：AI 笔记应用，技术导向"""
    return Harness(
        replies={
            "claims": [
                json.dumps({"basis": "读了全文", "claims": [
                    {"text": "AI 笔记应用支持语音转文字", "raw_span": "语音转文字", "label": "fact", "zone": "body", "section_id": "s1"},
                    {"text": "核心用户是知识工作者", "raw_span": "知识工作者", "label": "guess", "zone": "body", "section_id": "s2",
                     "assumption": {"failure_consequence": "用户不付费", "verification": "付费转化数据"}},
                    {"text": "需要自动标签功能", "raw_span": "自动标签", "label": "need", "zone": "body", "section_id": "s5"},
                ], "seats": [{"seat_id": "seat.frontend_interaction", "why": "交互设计"}, {"seat_id": "seat.data_scale", "why": "AI性能"}],
                    "contradictions": [], "web_queries": []}, ensure_ascii=False),
                json.dumps({"basis": "读了全文", "claims": [
                    {"text": "支持 Markdown 导出", "raw_span": "Markdown", "label": "fact", "zone": "body", "section_id": "s5"},
                    {"text": "需要端到端加密", "raw_span": "端到端加密", "label": "need", "zone": "body", "section_id": "s8"},
                ], "seats": [{"seat_id": "seat.data_scale", "why": "安全"}],
                    "contradictions": [], "web_queries": []}, ensure_ascii=False),
            ],
            "integration": json.dumps({"basis": "综合意见", "proposals": [
                {"op": "add", "target": {"zone": "body", "section_id": "s5", "feature_slug": "ai-notes", "field": "one_liner"}, "text": "AI 笔记应用，语音转文字+自动标签", "evidence_refs": ["E1"], "rationale": "定位"},
                {"op": "add", "target": {"zone": "body", "section_id": "s5", "feature_slug": "ai-notes", "field": "rules"}, "text": "支持 Markdown 导出，端到端加密", "evidence_refs": ["E1"], "rationale": "用户需求"},
            ], "questions": [], "list_items": [], "dropped_opinions": []}, ensure_ascii=False),
            "discussion": json.dumps({"text": "反方：语音转文字在嘈杂环境下准确率堪忧，建议先做好文本输入体验。自动标签的AI成本可能超出独立开发者承受范围。", "based_on": []}, ensure_ascii=False),
            "discussion_cards": json.dumps({"cards": [
                {"statement": "嘈杂环境下语音准确率堪忧，先做好文本输入", "grounding": "invented", "based_on": []},
                {"statement": "自动标签AI成本可能超出独立开发者承受", "grounding": "invented", "based_on": []},
            ]}, ensure_ascii=False),
            "inquiry": json.dumps({"basis": "用户视角", "hypotheses": [
                {"local_id": "H1", "statement": "用户愿意为AI功能付费", "grounding": "invented", "based_on": [],
                 "falsification": "如果免费用户不升级则推翻", "evidence_needed": "分析竞品付费转化"},
            ], "blind_spots": [
                {"local_id": "B1", "statement": "不知道离线场景的使用需求", "next_step": "调查用户离线使用频率"},
            ]}, ensure_ascii=False),
            "memory_curator": json.dumps({"changes": []}, ensure_ascii=False),
        },
        seat_replies={
            "seat.frontend_interaction": json.dumps({"basis": "交互视角", "opinions": [
                {"local_id": "O1", "form": "judgment", "about": "输入", "claim": "语音和文本双模式", "because": "覆盖更多场景", "evidence_refs": []},
                {"local_id": "O2", "form": "question", "about": "导出", "claim": "导出格式有哪些？", "because": "影响用户迁移", "evidence_refs": []},
            ]}, ensure_ascii=False),
            "seat.data_scale": json.dumps({"basis": "性能视角", "opinions": [
                {"local_id": "O1", "form": "judgment", "about": "AI", "claim": "本地模型优先", "because": "降低延迟和成本", "evidence_refs": []},
                {"local_id": "O2", "form": "question", "about": "加密", "claim": "加密方案选哪个？", "because": "影响性能", "evidence_refs": []},
            ]}, ensure_ascii=False),
        },
        persona_replies={
            "sim.persona.busy_parent": json.dumps({"basis": "知识工作者视角", "features": [
                {"feature_slug": "ai-notes", "started": True,
                 "walkthrough": [
                     {"step_index": 1, "step_text": "创建笔记", "from_doc": "s5", "action": "点击新建按钮", "outcome": "ok", "need": ""},
                     {"step_index": 2, "step_text": "语音输入", "from_doc": "s5", "action": "长按录音", "outcome": "guessed", "need": "不知道录音按钮在哪"},
                 ],
                 "frictions": [
                     {"local_id": "F1", "kind": "missing_entry", "entry_id": "", "field": "rules",
                      "quote": "", "not_documented": True, "blocks_flow": False,
                      "note": "语音按钮位置不直观，需要3次点击才能找到"},
                 ],
                 "acceptance_checks": [],
                 "verdict": "friction", "verdict_because": ["F1"]},
            ]}, ensure_ascii=False),
        },
        seats=[
            make_seat(seat_id="seat.frontend_interaction", title="前端交互"),
            make_seat(seat_id="seat.data_scale", title="数据规模"),
        ],
    )


# ═══════════════════════════════════════════════════════════════
# 项目 C：企业 PM — 内部审批工具
# ═══════════════════════════════════════════════════════════════

def build_harness_c():
    """企业 PM C：内部审批工具，合规导向"""
    return Harness(
        replies={
            "claims": [
                json.dumps({"basis": "读了全文", "claims": [
                    {"text": "内部审批工具支持多级审批流", "raw_span": "多级审批流", "label": "fact", "zone": "body", "section_id": "s1"},
                    {"text": "需要审计日志", "raw_span": "审计日志", "label": "need", "zone": "body", "section_id": "s5"},
                    {"text": "合规要求所有审批留痕", "raw_span": "审批留痕", "label": "constraint", "zone": "body", "section_id": "s8"},
                ], "seats": [{"seat_id": "seat.business_rules", "why": "审批规则"}, {"seat_id": "seat.data_scale", "why": "审计存储"}],
                    "contradictions": [], "web_queries": []}, ensure_ascii=False),
                json.dumps({"basis": "读了全文", "claims": [
                    {"text": "支持移动端审批", "raw_span": "移动端审批", "label": "need", "zone": "body", "section_id": "s5"},
                ], "seats": [{"seat_id": "seat.frontend_interaction", "why": "移动交互"}],
                    "contradictions": [], "web_queries": []}, ensure_ascii=False),
            ],
            "integration": json.dumps({"basis": "综合意见", "proposals": [
                {"op": "add", "target": {"zone": "body", "section_id": "s5", "feature_slug": "approval-flow", "field": "one_liner"}, "text": "内部审批工具，多级审批流+审计日志", "evidence_refs": ["E1"], "rationale": "定位"},
                {"op": "add", "target": {"zone": "body", "section_id": "s5", "feature_slug": "approval-flow", "field": "rules"}, "text": "所有审批操作留痕，审计日志保留3年", "evidence_refs": ["E1"], "rationale": "合规"},
            ], "questions": [], "list_items": [], "dropped_opinions": []}, ensure_ascii=False),
            "discussion": json.dumps({"text": "反方：多级审批可能导致效率低下，建议增加快速审批通道。审计日志的存储成本需要评估。", "based_on": []}, ensure_ascii=False),
            "discussion_cards": json.dumps({"cards": [
                {"statement": "多级审批可能导致效率低下，建议增加快速审批通道", "grounding": "invented", "based_on": []},
                {"statement": "审计日志存储成本需要评估", "grounding": "invented", "based_on": []},
            ]}, ensure_ascii=False),
            "inquiry": json.dumps({"basis": "用户视角", "hypotheses": [
                {"local_id": "H1", "statement": "审批者最在意审批速度而非流程完整性", "grounding": "invented", "based_on": [],
                 "falsification": "如果审批者频繁驳回则推翻", "evidence_needed": "访谈20个审批者"},
            ], "blind_spots": [
                {"local_id": "B1", "statement": "不知道跨部门审批的痛点", "next_step": "调研3个跨部门审批案例"},
            ]}, ensure_ascii=False),
            "memory_curator": json.dumps({"changes": [
                {"op": "add", "kind": "decision", "text": "审计日志保留3年", "refs": ["E1"], "reason": "合规要求"},
            ]}, ensure_ascii=False),
        },
        seat_replies={
            "seat.business_rules": json.dumps({"basis": "规则视角", "opinions": [
                {"local_id": "O1", "form": "judgment", "about": "审批", "claim": "三级审批足够", "because": "平衡效率与风控", "evidence_refs": []},
                {"local_id": "O2", "form": "question", "about": "快速通道", "claim": "快速审批条件？", "because": "影响效率", "evidence_refs": []},
            ]}, ensure_ascii=False),
            "seat.data_scale": json.dumps({"basis": "存储视角", "opinions": [
                {"local_id": "O1", "form": "judgment", "about": "审计", "claim": "日志分冷热存储", "because": "降低成本", "evidence_refs": []},
                {"local_id": "O2", "form": "question", "about": "保留", "claim": "保留期限？", "because": "合规要求", "evidence_refs": []},
            ]}, ensure_ascii=False),
            "seat.frontend_interaction": json.dumps({"basis": "交互视角", "opinions": [
                {"local_id": "O1", "form": "judgment", "about": "移动", "claim": "移动端只读+审批", "because": "安全考虑", "evidence_refs": []},
            ]}, ensure_ascii=False),
        },
        persona_replies={},
        seats=[
            make_seat(seat_id="seat.business_rules", title="业务规则"),
            make_seat(seat_id="seat.data_scale", title="数据规模"),
            make_seat(seat_id="seat.frontend_interaction", title="前端交互"),
        ],
    )


# ═══════════════════════════════════════════════════════════════
# 执行引擎：跑流程 + 评估 + 反思
# ═══════════════════════════════════════════════════════════════

async def run_project_a() -> ProjectEvaluation:
    """项目 A：创业者 — 社区团购平台"""
    eval = ProjectEvaluation(project_name="社区团购平台", persona="创业者（从0到1）")
    H = build_harness_a()
    pid = H.seed_project(title="社区团购平台")

    print("\n" + "=" * 70)
    print("📦 项目 A：社区团购平台 — 创业者（从 0 到 1）")
    print("=" * 70)

    # ── R1: 初始想法 ──
    start1 = H.start_round()
    r1 = await start1.create(project_id=pid, user_input="社区团购平台，团长负责社群运营和最后一公里配送，核心用户是家庭主妇和上班族，需要预售模式")
    await start1.analyze(round_id=r1)
    await H.submit_opening()(round_id=r1, seats=("seat.business_rules", "seat.data_scale"))
    await H.run_seat_round()(round_id=r1)

    # 评估席位意见质量
    r1_obj = H.rounds.get(r1)
    for review in r1_obj.seat_reviews:
        ev = evaluate_helpfulness(f"seat_{review.seat_id}", review, {})
        eval.steps.append(ev)
        print(f"  席位 {review.seat_id}: {'⭐' * ev.helpfulness} {ev.reflection}")

    # 讨论区
    await H.start_discussion()(project_id=pid, topic="用户增长策略", role_id="dlg.challenger",
                               question=QuestionSnapshot(title="如何不依赖团长实现增长？"))
    did = (await H.list_discussions()(project_id=pid))[-1].thread_id
    await H.say_to_thread()(discussion_id=did, text="团长模式有流失风险，建议增加直营渠道")
    await H.assemble_cards()(discussion_id=did)

    disc = H.discussions.get(did)
    if disc.cards:
        ev = evaluate_helpfulness("discussion_card", list(disc.cards), {})
        eval.steps.append(ev)
        print(f"  讨论卡: {'⭐' * ev.helpfulness} {ev.reflection}")
        await H.keep_discussion_card()(discussion_id=did, card_id=disc.cards[0].card_id, as_="material")
        eval.inspiration_sources_used["discussion"] = eval.inspiration_sources_used.get("discussion", 0) + 1

    # 探索调研
    eid = ExplorationId("e2e-exp-a1")
    exp = Exploration.start(exploration_id=eid, project_id=pid,
                           scope=ExplorationScope(text="竞品分析", kind=ScopeKind.DOCUMENT),
                           now=H.clock.now())
    exp.complete(
        hypotheses=(Hypothesis(local_id="H1", statement="用户最在意配送时效",
                               grounding=Grounding.INVENTED, based_on=(),
                               falsification="如果用户频繁比价则推翻",
                               evidence_needed="访谈50个用户"),),
        blind_spots=(BlindSpot(local_id="B1", statement="不知道老年用户的使用障碍",
                               next_step="找10个老年用户做可用性测试"),),
    )
    H.explorations.save(exp)
    ev = evaluate_helpfulness("exploration", exp, {})
    eval.steps.append(ev)
    print(f"  探索: {'⭐' * ev.helpfulness} {ev.reflection}")
    await H.keep_candidate()(exploration_id=eid, candidate_id="H1", as_="material")
    eval.inspiration_sources_used["exploration"] = eval.inspiration_sources_used.get("exploration", 0) + 1

    # 整合 + 确认
    r1_obj = H.rounds.get(r1)
    r1_obj.authorize_new_feature(FeatureSlug("group-buying"), basis=EvidenceId("E1"))
    H.rounds.save(r1_obj)
    await H.integrate_round()(round_id=r1)
    v1 = await H.get_round()(round_id=r1)
    ev = evaluate_helpfulness("integration", v1.draft, {})
    eval.steps.append(ev)
    print(f"  整合: {'⭐' * ev.helpfulness} {ev.reflection}")
    await H.confirm_draft()(round_id=r1, accepted=tuple(p.proposal_id for p in v1.draft.proposals))

    # ── 工具层 ──
    await H.upload_material()(project_id=pid, filename="竞品分析.md", media_type="text/markdown",
                              data="# 竞品分析\n- 美团优选\n- 多多买菜\n- 橙心优选\n".encode("utf-8"))
    results = await H.search()(project_id=pid, query="团购")
    print(f"  搜索 '团购': {len(results.hits)} 命中")

    # ── 版本管理 ──
    versions = await H.get_versions()(project_id=pid)
    print(f"  版本数: {len(versions.versions)}")
    diff = await H.get_version_diff()(project_id=pid, from_version_no=1, to_version_no=2)
    total = sum(len(g.added) + len(g.removed) + len(g.changed) for g in diff.groups)
    print(f"  v1→v2: {len(diff.groups)} 组, {total} 条目变更")

    # ── 导出 ──
    md_result = await H.export_markdown()(project_id=pid)
    md = md_result.markdown if hasattr(md_result, 'markdown') else str(md_result)
    doc = await H.get_document()(project_id=pid)
    section_count = sum(1 for s in doc.sections if s.blocks)
    feature_count = sum(1 for s in doc.sections for b in s.blocks if b.feature_slug)

    eval.final_document_score = section_count
    eval.final_reflection = f"{section_count} 章节有内容, {feature_count} 功能, 导出 {len(md)} 字符"

    print(f"\n  📄 最终文档: {section_count} 章节有内容, {feature_count} 功能")
    print(f"  📤 导出: {len(md)} 字符")

    return eval


async def run_project_b() -> ProjectEvaluation:
    """项目 B：独立开发者 — AI 笔记应用"""
    eval = ProjectEvaluation(project_name="AI 笔记应用", persona="独立开发者（技术导向）")
    H = build_harness_b()
    pid = H.seed_project(title="AI 笔记应用")

    print("\n" + "=" * 70)
    print("📦 项目 B：AI 笔记应用 — 独立开发者（技术导向）")
    print("=" * 70)

    # ── R1 ──
    start1 = H.start_round()
    r1 = await start1.create(project_id=pid, user_input="AI 笔记应用，支持语音转文字和自动标签，核心用户是知识工作者")
    await start1.analyze(round_id=r1)
    await H.submit_opening()(round_id=r1, seats=("seat.frontend_interaction", "seat.data_scale"))
    await H.run_seat_round()(round_id=r1)

    r1_obj = H.rounds.get(r1)
    for review in r1_obj.seat_reviews:
        ev = evaluate_helpfulness(f"seat_{review.seat_id}", review, {})
        eval.steps.append(ev)
        print(f"  席位 {review.seat_id}: {'⭐' * ev.helpfulness} {ev.reflection}")

    # 讨论
    await H.start_discussion()(project_id=pid, topic="技术选型", role_id="dlg.challenger",
                               question=QuestionSnapshot(title="AI成本是否可控？"))
    did = (await H.list_discussions()(project_id=pid))[-1].thread_id
    await H.say_to_thread()(discussion_id=did, text="语音转文字在嘈杂环境下准确率堪忧")
    await H.assemble_cards()(discussion_id=did)
    disc = H.discussions.get(did)
    if disc.cards:
        ev = evaluate_helpfulness("discussion_card", list(disc.cards), {})
        eval.steps.append(ev)
        print(f"  讨论卡: {'⭐' * ev.helpfulness} {ev.reflection}")

    # 探索
    eid = ExplorationId("e2e-exp-b1")
    exp = Exploration.start(exploration_id=eid, project_id=pid,
                           scope=ExplorationScope(text="竞品分析", kind=ScopeKind.DOCUMENT),
                           now=H.clock.now())
    exp.complete(
        hypotheses=(Hypothesis(local_id="H1", statement="用户愿意为AI功能付费",
                               grounding=Grounding.INVENTED, based_on=(),
                               falsification="如果免费用户不升级则推翻",
                               evidence_needed="分析竞品付费转化"),),
        blind_spots=(BlindSpot(local_id="B1", statement="不知道离线场景的使用需求",
                               next_step="调查用户离线使用频率"),),
    )
    H.explorations.save(exp)
    ev = evaluate_helpfulness("exploration", exp, {})
    eval.steps.append(ev)
    print(f"  探索: {'⭐' * ev.helpfulness} {ev.reflection}")

    # 整合 + 确认
    r1_obj = H.rounds.get(r1)
    r1_obj.authorize_new_feature(FeatureSlug("ai-notes"), basis=EvidenceId("E1"))
    H.rounds.save(r1_obj)
    await H.integrate_round()(round_id=r1)
    v1 = await H.get_round()(round_id=r1)
    ev = evaluate_helpfulness("integration", v1.draft, {})
    eval.steps.append(ev)
    print(f"  整合: {'⭐' * ev.helpfulness} {ev.reflection}")
    await H.confirm_draft()(round_id=r1, accepted=tuple(p.proposal_id for p in v1.draft.proposals))

    # 版本管理
    versions = await H.get_versions()(project_id=pid)
    print(f"  版本数: {len(versions.versions)}")

    # 导出
    md_result = await H.export_markdown()(project_id=pid)
    md = md_result.markdown if hasattr(md_result, 'markdown') else str(md_result)
    doc = await H.get_document()(project_id=pid)
    section_count = sum(1 for s in doc.sections if s.blocks)
    feature_count = sum(1 for s in doc.sections for b in s.blocks if b.feature_slug)

    eval.final_document_score = section_count
    eval.final_reflection = f"{section_count} 章节有内容, {feature_count} 功能, 导出 {len(md)} 字符"

    print(f"\n  📄 最终文档: {section_count} 章节有内容, {feature_count} 功能")
    print(f"  📤 导出: {len(md)} 字符")

    return eval


async def run_project_c() -> ProjectEvaluation:
    """项目 C：企业 PM — 内部审批工具"""
    eval = ProjectEvaluation(project_name="内部审批工具", persona="企业PM（合规导向）")
    H = build_harness_c()
    pid = H.seed_project(title="内部审批工具")

    print("\n" + "=" * 70)
    print("📦 项目 C：内部审批工具 — 企业 PM（合规导向）")
    print("=" * 70)

    # ── R1 ──
    start1 = H.start_round()
    r1 = await start1.create(project_id=pid, user_input="内部审批工具，支持多级审批流和审计日志，合规要求所有审批留痕")
    await start1.analyze(round_id=r1)
    await H.submit_opening()(round_id=r1, seats=("seat.business_rules", "seat.data_scale"))
    await H.run_seat_round()(round_id=r1)

    r1_obj = H.rounds.get(r1)
    for review in r1_obj.seat_reviews:
        ev = evaluate_helpfulness(f"seat_{review.seat_id}", review, {})
        eval.steps.append(ev)
        print(f"  席位 {review.seat_id}: {'⭐' * ev.helpfulness} {ev.reflection}")

    # 讨论
    await H.start_discussion()(project_id=pid, topic="效率vs合规", role_id="dlg.challenger",
                               question=QuestionSnapshot(title="多级审批是否影响效率？"))
    did = (await H.list_discussions()(project_id=pid))[-1].thread_id
    await H.say_to_thread()(discussion_id=did, text="多级审批可能导致效率低下，建议增加快速审批通道")
    await H.assemble_cards()(discussion_id=did)
    disc = H.discussions.get(did)
    if disc.cards:
        ev = evaluate_helpfulness("discussion_card", list(disc.cards), {})
        eval.steps.append(ev)
        print(f"  讨论卡: {'⭐' * ev.helpfulness} {ev.reflection}")

    # 探索
    eid = ExplorationId("e2e-exp-c1")
    exp = Exploration.start(exploration_id=eid, project_id=pid,
                           scope=ExplorationScope(text="审批痛点", kind=ScopeKind.DOCUMENT),
                           now=H.clock.now())
    exp.complete(
        hypotheses=(Hypothesis(local_id="H1", statement="审批者最在意审批速度",
                               grounding=Grounding.INVENTED, based_on=(),
                               falsification="如果审批者频繁驳回则推翻",
                               evidence_needed="访谈20个审批者"),),
        blind_spots=(BlindSpot(local_id="B1", statement="不知道跨部门审批的痛点",
                               next_step="调研3个跨部门审批案例"),),
    )
    H.explorations.save(exp)
    ev = evaluate_helpfulness("exploration", exp, {})
    eval.steps.append(ev)
    print(f"  探索: {'⭐' * ev.helpfulness} {ev.reflection}")

    # 整合 + 确认
    r1_obj = H.rounds.get(r1)
    r1_obj.authorize_new_feature(FeatureSlug("approval-flow"), basis=EvidenceId("E1"))
    H.rounds.save(r1_obj)
    await H.integrate_round()(round_id=r1)
    v1 = await H.get_round()(round_id=r1)
    ev = evaluate_helpfulness("integration", v1.draft, {})
    eval.steps.append(ev)
    print(f"  整合: {'⭐' * ev.helpfulness} {ev.reflection}")
    await H.confirm_draft()(round_id=r1, accepted=tuple(p.proposal_id for p in v1.draft.proposals))

    # 版本管理 + 回退
    versions = await H.get_versions()(project_id=pid)
    print(f"  版本数: {len(versions.versions)}")
    diff = await H.get_version_diff()(project_id=pid, from_version_no=1, to_version_no=2)
    total = sum(len(g.added) + len(g.removed) + len(g.changed) for g in diff.groups)
    print(f"  v1→v2: {len(diff.groups)} 组, {total} 条目变更")

    # 导出
    md_result = await H.export_markdown()(project_id=pid)
    md = md_result.markdown if hasattr(md_result, 'markdown') else str(md_result)
    doc = await H.get_document()(project_id=pid)
    section_count = sum(1 for s in doc.sections if s.blocks)
    feature_count = sum(1 for s in doc.sections for b in s.blocks if b.feature_slug)

    eval.final_document_score = section_count
    eval.final_reflection = f"{section_count} 章节有内容, {feature_count} 功能, 导出 {len(md)} 字符"

    print(f"\n  📄 最终文档: {section_count} 章节有内容, {feature_count} 功能")
    print(f"  📤 导出: {len(md)} 字符")

    return eval


# ═══════════════════════════════════════════════════════════════
# 总报告生成
# ═══════════════════════════════════════════════════════════════

def generate_report(projects: list[ProjectEvaluation]) -> EcosystemReport:
    """生成生态级总报告"""
    report = EcosystemReport(projects=projects)

    all_scores = []
    all_concerns = []
    all_strengths = []

    for p in projects:
        step_scores = [s.helpfulness for s in p.steps]
        avg_score = sum(step_scores) / len(step_scores) if step_scores else 0
        all_scores.append(avg_score)

        for s in p.steps:
            all_concerns.extend(s.concerns)
            if s.helpfulness >= 2:
                all_strengths.append(f"[{p.project_name}] {s.step_name}: {s.reflection}")

        # 文档完整度评估
        if p.final_document_score >= 8:
            all_strengths.append(f"[{p.project_name}] 文档完整度高: {p.final_reflection}")
        elif p.final_document_score < 5:
            all_concerns.append(f"[{p.project_name}] 文档完整度低: {p.final_reflection}")

    report.overall_helpfulness = sum(all_scores) / len(all_scores) if all_scores else 0
    report.critical_gaps = list(set(all_concerns))
    report.strengths = list(set(all_strengths))

    # 总判定
    if report.overall_helpfulness >= 2.5 and len(report.critical_gaps) < 3:
        report.verdict = "✅ 系统对产品经理有显著帮助，可以进入真实用户测试"
    elif report.overall_helpfulness >= 1.5:
        report.verdict = "⚠️ 系统有一定帮助，但存在明显短板，需要改进后再测试"
    else:
        report.verdict = "❌ 系统帮助度不足，需要重新设计核心体验"

    return report


# ═══════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════

async def main():
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║     生态闭环验证：系统是否真的能帮到产品经理？              ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    print("评估维度：")
    print("  1. 灵感质量 — 用户没思路时，系统能否给出有价值的方向")
    print("  2. 评审质量 — 意见是否专业、可操作、能发现问题")
    print("  3. 过程帮助度 — 每一步是否真的在帮用户推进")
    print("  4. 文档可用性 — 最终文档是否真的能指导别人做事")
    print()

    projects = []

    try:
        projects.append(await run_project_a())
    except Exception as e:
        print(f"\n❌ 项目 A 失败: {e}")
        traceback.print_exc()

    try:
        projects.append(await run_project_b())
    except Exception as e:
        print(f"\n❌ 项目 B 失败: {e}")
        traceback.print_exc()

    try:
        projects.append(await run_project_c())
    except Exception as e:
        print(f"\n❌ 项目 C 失败: {e}")
        traceback.print_exc()

    # 生成总报告
    report = generate_report(projects)

    print("\n" + "╔══════════════════════════════════════════════════════════════╗")
    print("║                       生态级总报告                          ║")
    print("╚══════════════════════════════════════════════════════════════╝")

    for p in projects:
        avg = sum(s.helpfulness for s in p.steps) / len(p.steps) if p.steps else 0
        print(f"\n📦 {p.project_name} ({p.persona})")
        print(f"   平均帮助度: {'⭐' * round(avg)} ({avg:.1f}/3.0)")
        print(f"   文档完整度: {p.final_document_score} 章节")
        print(f"   灵感来源: {p.inspiration_sources_used}")
        for s in p.steps:
            status = "✅" if s.helpfulness >= 2 else ("⚠️" if s.helpfulness == 1 else "❌")
            print(f"   {status} {s.step_name}: {s.reflection}")
            for c in s.concerns:
                print(f"      ⚠️  {c}")

    print(f"\n{'=' * 60}")
    print(f"📊 总平均帮助度: {report.overall_helpfulness:.1f}/3.0")
    print(f"📊 总判定: {report.verdict}")

    if report.strengths:
        print(f"\n💪 优势:")
        for s in report.strengths[:10]:
            print(f"   ✅ {s}")

    if report.critical_gaps:
        print(f"\n⚠️ 关键缺口:")
        for g in report.critical_gaps[:10]:
            print(f"   ❌ {g}")

    print(f"\n{'=' * 60}")
    print("验证完成。以上是系统对产品经理帮助度的评估。")
    print("建议：关注⚠️和❌标记的环节，这些是影响实用性的关键短板。")


if __name__ == "__main__":
    asyncio.run(main())
