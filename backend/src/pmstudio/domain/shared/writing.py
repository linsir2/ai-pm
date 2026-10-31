"""block 类型与写入操作。两个上下文（草案 / 文档）共用这套词。"""

from enum import StrEnum


def normalize(text: str) -> str:
    """同逻辑去处的整段精确判重尺子，保留既有空白与大小写归一。"""

    return " ".join(text.split()).casefold()


class BlockKind(StrEnum):
    SECTION = "section"
    FEATURE = "feature"
    FIELD = "field"
    TEXT = "text"
    BULLET_LIST = "bullet_list"
    TABLE = "table"
    STEPS = "steps"
    ACCEPTANCE = "acceptance"
    ENTITY = "entity"
    INTERFACE = "interface"
    NFR = "nfr"
    DECISION = "decision"
    ASSUMPTION = "assumption"
    CONSTRAINT = "constraint"
    DEPENDENCY = "dependency"
    RISK = "risk"
    QUESTION = "question"
    METRIC = "metric"


class WriteOp(StrEnum):
    ADD = "add"  # 字段块不存在就建；单值字段替换内容，多值字段追加
    REPLACE = "replace"  # 按 entry_id 改已有条目（条目 id 不变，旧值留在上一版快照）
    REMOVE = "remove"  # 按 entry_id 删条目；entry_id 是块 id 时整块留墓碑
