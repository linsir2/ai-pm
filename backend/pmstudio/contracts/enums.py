"""契约用到的全部枚举。值域直接对应 CONTRACTS.md 的字段说明。"""

from enum import StrEnum


class VersionTrigger(StrEnum):
    """C3 `trigger`：这一版是怎么产生的。"""

    MANUAL = "manual"
    SUBMIT = "submit"
    ROLLBACK = "rollback"


class BlockOpKind(StrEnum):
    """写文档时对某个块的两种操作（CONTRACTS §5.3 `writeDocument` 的 payload）。"""

    REPLACE = "replace"
    APPEND = "append"


class CitationTargetType(StrEnum):
    """C15 `target_type`：引用的是什么。"""

    BLOCK = "block"
    MEMORY = "memory"
    MATERIAL = "material"
    IDEA = "idea"
    USER_INPUT = "user_input"
    CLAIM = "claim"


class RoundEntry(StrEnum):
    """C12 `round.entry`：这一轮从哪个入口来。"""

    MAIN = "main"
    DISCUSSION = "discussion"


class RoundPhase(StrEnum):
    """C12 `round.phase`：这一轮走到哪一步。"""

    ASSEMBLING = "assembling"
    RESTATING = "restating"
    WORKING = "working"
    AWAITING_USER = "awaiting_user"
    DRAFTING = "drafting"
    WRITING = "writing"
    DONE = "done"
    FAILED = "failed"
