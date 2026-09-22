"""出处类型的**词汇表**（不是实体）。

放在 shared 而不是 provenance：round 上下文要回答"这条判断算推导还是提议"，
services 要在两个上下文之间翻译，三方共用同一套词。（与 ClaimLabel / BlockKind 同级。）
"""

from enum import StrEnum


class EvidenceKind(StrEnum):
    USER_INPUT = "user_input"  # 用户在哪一轮说的（带原话片段）
    CARD_ANSWER = "card_answer"  # 用户答的哪道题
    AGENT_DERIVED = "agent_derived"  # 席位判断，有依据
    AGENT_PROPOSED = "agent_proposed"  # 席位判断，无依据
    SYSTEM_DEFAULT = "system_default"  # 系统按结构规则补的（如跳过的题落开放问题）
    EXTERNAL = "external"  # 外部材料（v1 留位，暂无生产者）


TERMINAL_KINDS: frozenset[EvidenceKind] = frozenset(
    {EvidenceKind.USER_INPUT, EvidenceKind.CARD_ANSWER}
)
