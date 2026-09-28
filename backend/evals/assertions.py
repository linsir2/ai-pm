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
    {"type": "no_unauthorized_features"},
]


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
        seen: dict[str, int] = {}
        for question in view.questions:
            seen[question.target_label] = seen.get(question.target_label, 0) + 1
        repeated = sorted(target for target, count in seen.items() if count > 1)
        return None if not repeated else f"同一个去处问了两遍：{'、'.join(repeated)}"

    if kind == "dropped_items_are_explained":
        bad = [item.handle for item in view.dropped if not item.reason.strip()]
        return None if not bad else f"这些留痕没写原因：{'、'.join(bad)}"

    if kind == "no_unauthorized_features":
        # 未授权的新功能会被 `ScopeGuard` 标成 advisory（草案卡上本来就会写）；
        # eval 里把它当失败：这条结构守卫不该在真模型下失守。
        return None if not view.advisories else f"跑出本轮去处：{'；'.join(view.advisories)}"

    raise ValueError(f"未知断言：{kind}")


def run_assertions(
    assertions: list[dict], view: RoundView
) -> list[tuple[dict, str | None]]:
    """逐条跑，返回 `(断言, 失败原因或 None)`——脚本据此打印。"""

    return [(assertion, check(assertion, view)) for assertion in assertions]
