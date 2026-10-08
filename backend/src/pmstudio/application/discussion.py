"""讨论的纯函数（M9-T2）：引用校验、历史渲染、卡片文本、材料合成。

它们不碰存储、不碰模型：用例层把读到的线程与模型产物交给这些函数。
校验失败抛 `ShapeError`（重装那一次拿它当 `validation_error`）。
"""

from collections.abc import Sequence

from pmstudio.application.dto import ParsedMaterial
from pmstudio.application.material_synthesis import synthesize_text_material
from pmstudio.domain.discussion.card import Card
from pmstudio.domain.discussion.thread import Turn
from pmstudio.domain.exploration.item import BlindSpot, Hypothesis
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId

#: 材料库里的来源标签（`discussion` 与 `exploration` 同等待遇：原值显示，不加新标签）
ORIGIN_DISCUSSION = "discussion"
PARSER_ID_DISCUSSION = "discussion"
PARSER_VERSION_DISCUSSION = "1"

#: say 一次最多带几条历史（与线程上限 12 解耦：带多了提示词只变长不变好）
MAX_SAY_HISTORY = 6


def render_findings(
    items: Sequence[Hypothesis | BlindSpot], *, missing: int = 0
) -> str:
    """开题调研底稿的提示词段（M13）：一条一行；空 → 空串（提示词那节自带"如果下面不是空的"）。

    **未经验证**——渲染进提示词只是给席位当讨论对象，不是依据（硬规矩第 6 条管引用）。
    找不到的引用不静默：附一行实话。
    """

    lines: list[str] = []
    for item in items:
        if isinstance(item, Hypothesis):
            lines.append(
                f"- 假设 {item.local_id}：{item.statement}"
                f"（证伪它：{item.falsification}；需要证据：{item.evidence_needed}）"
            )
        else:
            lines.append(
                f"- 盲区 {item.local_id}：{item.statement}（下一步：{item.next_step}）"
            )
    if missing > 0:
        lines.append(f"（有 {missing} 条底稿已经找不到了）")
    return "\n".join(lines)


def validate_ledger_refs(
    refs: Sequence[EvidenceId], *, known_eids: frozenset[EvidenceId]
) -> None:
    """say 的 refs 已是领域 `EvidenceId`（discusser 内已 resolve），这里只做"在账里"成员检查。

    未知编号 → `ShapeError`（重装时当 `validation_error`，与 `validate_inquiry:77-82` 同位）。
    """

    unknown = [str(ref) for ref in refs if ref not in known_eids]
    if unknown:
        raise ShapeError(
            "回答引用了账本里没有的编号：" + "、".join(unknown),
        )


def render_thread_history(turns: tuple[Turn, ...]) -> str:
    """取后 6 条，渲染成带序号的行；`based_on` 非空追一句依据。"""

    tail = turns[-MAX_SAY_HISTORY:] if len(turns) > MAX_SAY_HISTORY else turns
    lines = []
    for turn in tail:
        who = "用户" if turn.role == "user" else "助手"
        line = f"[t{turn.index} {who}] {turn.text}"
        if turn.based_on:
            line += "（依据 " + "、".join(str(ref) for ref in turn.based_on) + "）"
        lines.append(line)
    return "\n".join(lines)


def card_text(card: Card) -> str:
    """一张卡渲染成给人 / 给材料看的文本（`candidate_card:91-104` 同构，减证伪行）。"""

    basis = "、".join(str(ref) for ref in card.based_on) or "凭空推演（无据）"
    return f"灵感：{card.statement}\n依据：{basis}"


def discussion_material(card: Card, *, thread_id: str) -> tuple[ParsedMaterial, bytes]:
    """把一张"收下为材料"的灵感卡合成一份材料草稿（不经过文件解析器）。"""

    text = card_text(card)
    return synthesize_text_material(
        origin=ORIGIN_DISCUSSION,
        title=f"讨论追问：{_clip(card.statement)}",
        source_ref=f"discussion:{thread_id}#{card.card_id}",
        text=text,
        anchor="讨论追问",
        parser_id=PARSER_ID_DISCUSSION,
        parser_version=PARSER_VERSION_DISCUSSION,
    )


def _clip(text: str, limit: int = 24) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
