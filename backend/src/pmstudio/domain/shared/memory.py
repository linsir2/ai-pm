"""记忆的词汇表（`MemoryKind` / `MemoryOp` / `MemoryChange`）。

为什么放在 `shared` 而不是 `memory` 上下文里：**它是两个上下文之间传的那件东西**——
轮次（草案卡上那一档）拿着它，记忆聚合按它改自己。跨上下文只传 id 与共享词汇，
不传聚合（`05-DDD架构.md` §1）；聚合本身在 `domain/memory/`。
"""

from dataclasses import dataclass
from enum import StrEnum


class MemoryKind(StrEnum):
    """三类条目（原设计 `记忆层与门禁条款.md` §二）：教训 / 已定口径 / 明确否掉。"""

    LESSON = "lesson"  # 教训：会影响以后每一轮判断的事
    DECISION = "decision"  # 已经定过的口径（用户确认过）
    REJECTED = "rejected"  # 明确否掉的东西


class MemoryOp(StrEnum):
    ADD = "add"
    UPDATE = "update"
    MERGE = "merge"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class MemoryChange:
    """Curator 交出的一条**变更指令**（提议，不是事实；只有确认才生效）。

    `refs` 是**出处编号的文本形态**（`E7`）——它们来自模型报文，还没经过领域校验；
    校验点在 `domain/memory/memory.py::screen_changes`（缺依据 / 账本里没有 / 点名不存在）。
    形状故意宽松：模型写歪的东西正是要留痕的东西，写歪就抛错等于"写歪了就不留痕"。
    """

    op: MemoryOp
    kind: MemoryKind
    text: str = ""
    refs: tuple[str, ...] = ()
    target_id: str | None = None
    reason: str = ""
