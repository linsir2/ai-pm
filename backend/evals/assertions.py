"""结构性断言：不看文案，只看形状。

全部是纯函数：`RoundView` 进，错误消息或 `None` 出。真模型那条路
（`scripts/eval.py`）只负责"把一轮跑出来"，判断在这里——所以断言本身能离线测。
"""

import re

from pmstudio.application.dto import RoundView

EID = re.compile(r"^E\d+$")

#: 跑每条用例时默认要过的七条（case 里可以覆盖）
EVAL_ASSERTIONS: list[dict] = [
    {"type": "evidence_refs_are_eids"},
    {"type": "every_proposal_has_evidence"},
    {"type": "questions_at_most", "count": 5},
    {"type": "seats_at_most", "count": 7},
    {"type": "no_duplicate_questions"},
    {"type": "dropped_items_are_explained"},
    {"type": "outside_scope_items_are_labeled"},
]


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

    raise ValueError(f"未知断言：{kind}")


def run_assertions(
    assertions: list[dict], view: RoundView
) -> list[tuple[dict, str | None]]:
    """逐条跑，返回 `(断言, 失败原因或 None)`——脚本据此打印。"""

    return [(assertion, check(assertion, view)) for assertion in assertions]
