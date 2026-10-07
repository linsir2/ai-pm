"""结构性断言：不看文案，只看形状。

全部是纯函数：`RoundView` 进，错误消息或 `None` 出。真模型那条路
（`scripts/eval.py`）只负责"把一轮跑出来"，判断在这里——所以断言本身能离线测。
"""

import re

from pmstudio.application.dto import RoundView
from pmstudio.application.sim_inquiry import SIM_NUMERIC

EID = re.compile(r"^E\d+$")

#: 跑每条用例时默认要过的十二条（case 里可以覆盖）
EVAL_ASSERTIONS: list[dict] = [
    {"type": "evidence_refs_are_eids"},
    {"type": "every_proposal_has_evidence"},
    {"type": "questions_at_most", "count": 5},
    {"type": "seats_at_most", "count": 7},
    {"type": "no_duplicate_questions"},
    {"type": "dropped_items_are_explained"},
    {"type": "outside_scope_items_are_labeled"},
    {"type": "inquiry_hypotheses_are_grounded"},
    {"type": "inquiry_needs_verification"},
    {"type": "inquiry_has_no_quantities"},
    {"type": "sim_review_proposals_stay_proposed"},
    {"type": "sim_review_reports_are_well_formed"},
]

#: **没进默认集**的那一条：`unaccounted_at_most`。
#:
#: 为什么：默认集里那十二条量的是**结构**（编号形状、题数、标注对齐……），而
#: `unaccounted` 量的是**模型行为率**——四个车道 13 个轮次实测 4–26 条/轮，
#: 补了提示词之后 2–3 轮实测 8–23 条/轮，**没有可测的下降**（见
#: `.codex/runs/2026-10-01/P22验证.md`）。给这种量挂一个"默认必须过"的阈值，
#: 换来的是一盏人人学会忽略的红灯（真跑 4–17 倍超阈）。
#:
#: 要盯它就卸在**某条用例**上（case JSON 的 `extra_assertions`，与
#: `contradiction_is_labelled` 同一个用法），或者用只读脚本
#: `.codex/runs/2026-10-01/bin/measure_unaccounted.py` 复量。


def normalized_title(text: str) -> str:
    """标题归一化：空白与标点不参与判重（"这面怎么定？" 与 "这面怎么定" 是同一道题）。"""

    return "".join(character for character in text if character.isalnum())


def check(assertion: dict, view: RoundView) -> str | None:
    """过 = `None`；不过 = 一句能读懂的错误。未知断言类型直接炸（不许静默通过）。"""

    kind = assertion["type"]

    if kind == "claims_at_least":
        count = len(view.claims)
        return None if count >= assertion["count"] else f"主张只有 {count} 条"

    if kind == "seats_at_most":
        count = len(view.seats)
        return None if count <= assertion["count"] else f"席位 {count} 个，超了"

    if kind == "questions_at_most":
        count = len(view.questions)
        return None if count <= assertion["count"] else f"题 {count} 道，超了"

    if kind == "evidence_refs_are_eids":
        bad = sorted(
            {
                eid
                for question in view.questions
                for eid in question.eids
                if not EID.match(eid)
            }
            | {
                eid
                for proposal in (view.draft.proposals if view.draft else ())
                for eid in proposal.eids
                if not EID.match(eid)
            }
        )
        return None if not bad else f"这些不是出处编号：{'、'.join(bad)}"

    if kind == "every_proposal_has_evidence":
        bad = [
            proposal.proposal_id
            for proposal in (view.draft.proposals if view.draft else ())
            if not proposal.eids
        ]
        return None if not bad else f"这些提案没有依据：{'、'.join(bad)}"

    if kind == "no_duplicate_questions":
        # 真重复 = **同一个去处 + 同一个问法**（2026-09-28 拍板）。
        # 同一去处上的两件不同的事照问：各自有选项与跳过后果，合并会丢结构。
        # 真模型实测（`case-a`）：`s8.availability` 上的"幂等性与重试策略"与
        # "对端不可用时的降级策略"是两道题，旧的"只看去处"判据把它们判成了重复。
        seen: dict[tuple[str, str], int] = {}
        for question in view.questions:
            key = (question.target_label, normalized_title(question.title))
            seen[key] = seen.get(key, 0) + 1
        repeated = sorted(
            f"{target}｜{title}" for (target, title), count in seen.items() if count > 1
        )
        return None if not repeated else f"同一道题问了两遍：{'、'.join(repeated)}"

    if kind == "dropped_items_are_explained":
        bad = [item.handle for item in view.dropped if not item.reason.strip()]
        return None if not bad else f"这些留痕没写原因：{'、'.join(bad)}"

    if kind == "unaccounted_at_most":
        # 2026-10-01（F19）：`unaccounted` 是"整合既没说丢掉它、也没交代落到哪"的机械核对，
        # 每轮 5–26 条，而实测 48–100% 是假阳性——整合者把意见的内容写进了卡片，
        # 只是没引那条判断自己的编号 / 没写疑问的地址（提示词里从没交代过这件事）。
        # 修法是补提示词 + 让"本批没排上的题"也算产出；**不去猜**哪条其实被用了。
        #
        # 阈值 2 不给 0：这条判据的输入是模型行为，抖一下不该变成红灯；
        # 要的是"降一个数量级"，不是"允许它继续每轮十几条"。
        unaccounted = [item.handle for item in view.dropped if item.source == "unaccounted"]
        limit = assertion["count"]
        return (
            None
            if len(unaccounted) <= limit
            else f"这一轮有 {len(unaccounted)} 条意见既没被引用也没被申报丢弃："
            f"{'、'.join(unaccounted[:5])}"
        )

    if kind == "contradiction_is_labelled":
        # 2026-09-29：`conflict` 与 `gap` 在界面上是**分开显示**的（`document/status.ts` 把
        # conflict / decision 当阻塞、gap 当"还差一点信息"），所以判据本身要能看出来。
        # P11 C3：`conflict` 与 `decision` **等价**（模型把"两侧互斥"落成"二选一"是合理读法），
        # 所以两者都算标对了；真正要抓的是"把它当普通缺信息题、只出 gap"。
        # 这条只挂在"输入本来就自相矛盾"的用例上（`06-edge-contradiction`）——别的用例
        # 本来就该全是 gap，挂上去就是假红。
        #
        # 2026-10-03（P35 · M3）：取样面从"**当前批**"改成"**整轮问过的题**"。
        # 真跑现场（P30 完整 eval 的 `r-8889fcf0`）：`q4 kind=conflict`（依据 E1+E3）出现在
        # **第 1 批**，用户跳过之后第 2 批只剩两道 gap 题；判据只读 `RoundView.questions`
        # （当前批）→ 把"问过"读成"没问过"、报了一次假红。`asked_questions` 是整轮那份投影。
        # 这不是把判据改松：`test_a_round_that_only_ever_asked_gaps_still_fails` 用两批全 gap
        # 钉着"照旧红"。
        asked = view.asked_questions or view.questions
        labelled = [question for question in asked if question.kind in {"conflict", "decision"}]
        if labelled:
            return None
        kinds = sorted({question.kind for question in asked})
        return (
            f"输入里两句互斥，应该出一道要你拍板的题（kind=conflict 或 decision），"
            f"实际这一轮问过 {len(asked)} 道题、类型是 {kinds or '（没出题）'}"
        )

    if kind == "web_queries_at_least":
        # 2026-10-03（P35 · M3b）：输入明确依赖**外部客观事实**（法规 / 标准 / 第三方接口的
        # 既有做法）时，分析者至少要提名一条检索词——否就是"该查的不查"。
        # 只挂在那一调用例上：提名是模型行为率，进默认集只会变成一盏人人学会忽略的红灯
        # （与 `unaccounted_at_most` 同一处置）。
        count = len(view.web_queries)
        limit = assertion["count"]
        if count >= limit:
            return None
        return (
            f"输入要的是外部客观事实（文档里没有、用户也答不了），至少要提名 {limit} 条检索词，"
            f"实际 {count} 条"
        )

    if kind == "draft_section_has_kind":
        # 2026-10-03（P36 · M4）：草案里有没有一条**落在这一章、这个块类型**的提案。
        # 用来看"该写的写了没"（第 6 章 `s6` + `kind=steps`：跨功能/端到端流程）。
        # 只看**草案**：eval 一轮跑到草案卡为止，确认写入是另一条通道（`tests/api`、e2e）。
        section = assertion["section"]
        wanted = assertion["kind"]
        proposals = view.draft.proposals if view.draft else ()
        if any(p.target_label == section and p.kind == wanted for p in proposals):
            return None
        labels = sorted({p.target_label for p in proposals})
        return (
            f"草案里没有落在 {section} 的 {wanted} 条目"
            f"（这一轮草案落到的去处：{'、'.join(labels[:8]) or '（一条都没有）'}）"
        )

    if kind == "outside_scope_items_are_labeled":
        # 2026-09-28 拍板：跑出本轮去处**不拒绝**——`02` §3①（2026-09-22 定）写着
        # "静默拒绝会让席位的专业判断整批白干"，只在草案卡顶部与每条提案上标注。
        # 所以这条断言不再把"跑出本轮去处"当失败，改成盯**标注这条路没断**：
        # ① 卡片上带标注的提案数 == 顶部提示数（两处不许漂移）；② 标注是人话。
        #
        # 2026-09-29 修：顶部提示里**只有"跑出本轮去处"那一类**该跟卡片标注对齐。
        # 置顶告警（`dto.feature_coverage_advisory`）说的是**这一轮整体**：它不挂在任何提案上，
        # 算进总数就会假红——真跑前复现过（主张点了功能 + 草案落 s1）。
        # 除了越界提示，顶部只允许出现以 `⚠` 开头的置顶告警，别的一律当"来历不明的提示"报错。
        #
        # 它管不了、也不该管的那件事：**不得新增未授权功能子树**——那条硬约束由
        # `assembly.assemble` 的 `pending_features` → `new_feature_yes` 决策题
        # （用户点头才授权）与领域层的授权集合守着，
        # 单测在 `tests/application/test_assembly.py` 的
        # `test_assemble_creates_feature_subtree_only_when_authorized`。
        proposals = view.draft.proposals if view.draft else ()
        labeled = [proposal for proposal in proposals if proposal.warning]
        outside = [text for text in view.advisories if "不在本轮去处里" in text]
        others = [text for text in view.advisories if "不在本轮去处里" not in text]
        unknown = [text for text in others if not text.startswith("⚠")]
        if unknown:
            return f"这条标注既不是越界提示、也不是置顶告警：{unknown[0]}"
        if len(labeled) != len(outside):
            return (
                f"跑出本轮去处的条目与顶部提示的标注对不上：卡片上标了 {len(labeled)} 条、"
                f"顶部有 {len(outside)} 条（另有 {len(others)} 条置顶告警）"
            )
        return None

    if kind == "inquiry_hypotheses_are_grounded":
        # M10：模拟假设的编号必须真实存在（E 形状），invented 必须留空 based_on。
        # 没跑过模拟（sim_inquiry 是 None）无事可查，直接过——默认集加这三条
        # 不炸旧用例的前提（`test_inquiry_assertions_pass_vacuously_without_sim`）。
        inquiry = view.sim_inquiry
        if inquiry is None:
            return None
        bad = sorted(
            {
                ref
                for item in inquiry.hypotheses
                for ref in item.based_on
                if not EID.match(ref)
            }
        )
        if bad:
            return f"模拟假设引用了不存在的编号：{'、'.join(bad)}"
        naked = sorted(
            item.local_id
            for item in inquiry.hypotheses
            if item.grounding == "invented" and item.based_on
        )
        if naked:
            return f"凭空推演却带了依据编号：{'、'.join(naked)}"
        return None

    if kind == "inquiry_needs_verification":
        # M10：每条假设/盲区必须写清"怎么被推翻/去哪验证"——没写就是口号，不是假设。
        inquiry = view.sim_inquiry
        if inquiry is None:
            return None
        bad = sorted(
            item.local_id
            for item in inquiry.hypotheses
            if not item.falsification.strip() or not item.evidence_needed.strip()
        )
        bad += sorted(
            item.local_id
            for item in inquiry.unknowns
            if not item.why_it_matters.strip() or not item.how_to_find_out.strip()
        )
        return None if not bad else f"这些模拟条目没写怎么验证：{'、'.join(bad)}"

    if kind == "inquiry_has_no_quantities":
        # M10：模拟推演不许出数字（与装配层 `_SIM_NUMERIC` 同一口径，判据写两遍、
        # 各判各的层：装配层拦回包，eval 拦退化）。
        inquiry = view.sim_inquiry
        if inquiry is None:
            return None
        texts = [
            item.statement
            for item in (*inquiry.hypotheses, *inquiry.unknowns)
        ] + [item.situation for item in inquiry.slices]
        bad = sorted({text for text in texts if SIM_NUMERIC.search(text)})
        return None if not bad else f"模拟产物出现了数量断言：{bad[0][:30]}"

    if kind == "sim_review_proposals_stay_proposed":
        # M11：引用了模拟编号的提案必须是"它提议的"——防它被洗成"它替你判断的"。
        if view.draft is None:
            return None
        bad = [
            item
            for item in view.draft.proposals
            if item.simulation_based and item.origin_type != "proposed"
        ]
        if not bad:
            return None
        return f"引用了模拟推演的提案不是「它提议的」：{bad[0].proposal_id}"

    if kind == "sim_review_reports_are_well_formed":
        # M11：走查报告的结构一致性（与筛的判据同源；此处拦的是"真跑退化"）。
        review = view.sim_review
        if review is None:
            return None
        for persona in review.personas:
            for feature in persona.features:
                where = f"{persona.persona_id}·{feature.feature_slug}"
                if not feature.started and not any(
                    item.kind == "cannot_start" for item in feature.frictions
                ):
                    return f"{where}：started=false 却没有 cannot_start 摩擦"
                if feature.started and not feature.walkthrough:
                    return f"{where}：started=true 却没有走查步骤"
                if feature.verdict in ("friction", "blocked") and not feature.verdict_because:
                    return f"{where}：verdict={feature.verdict} 却没有 verdict_because"
                known = {item.local_id for item in feature.frictions}
                dangling = [ref for ref in feature.verdict_because if ref not in known]
                if dangling:
                    return f"{where}：verdict_because 指向不存在的摩擦：{dangling[0]}"
        return None

    raise ValueError(f"未知断言：{kind}")


def run_assertions(
    assertions: list[dict], view: RoundView
) -> list[tuple[dict, str | None]]:
    """逐条跑，返回 `(断言, 失败原因或 None)`——脚本据此打印。"""

    return [(assertion, check(assertion, view)) for assertion in assertions]
