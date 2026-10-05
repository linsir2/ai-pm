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
    EXTERNAL = "external"  # 外部材料（上传 / 联网检索 / 抓来的网页）
    SIMULATED = "simulated"  # 探索产物（模型推演，不是用户数据；**不是**可追溯终点）
    MANUAL = "manual"  # 用户自己在文档里改的（手改也要有自己的出处，见 04-未决 D1）


# 可追溯链的终点：人能直接为这条内容负责的两种（三种）来源。
# manual 也算终点——那是用户自己敲进去的字，和"他说的""他答的"是同一件事。
TERMINAL_KINDS: frozenset[EvidenceKind] = frozenset(
    {EvidenceKind.USER_INPUT, EvidenceKind.CARD_ANSWER, EvidenceKind.MANUAL}
)
