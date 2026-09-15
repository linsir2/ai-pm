"""契约用到的全部枚举。值域直接对应 CONTRACTS.md 的字段说明。

一个值都不许在这里"顺手加"：每个成员都要能在 CONTRACTS.md 里指出出处。
`tests/contracts/test_enums.py` 拿一张期望表逐字比对，加一个值就会红。
"""

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


class RoundEndReason(StrEnum):
    """C12 `round.end_reason`：主闭环这一轮为什么结束。"""

    COMPLETED = "completed"
    USER_STOPPED = "user_stopped"
    FAILED = "failed"
    PROCESS_RESTART = "process_restart"


class ClaimKind(StrEnum):
    """C4 主张的 `kind`。

    C4 是编排层的内部通用语，不进 `contracts/`；但 C11 的主张留痕要用同一套值域，
    所以这里只放值域，不放 C4 的形状。
    """

    PROPOSAL = "proposal"
    CHALLENGE = "challenge"


class CardKind(StrEnum):
    """C7 `kind`：这张卡要用户干什么。"""

    UNDERSTANDING = "understanding"
    QUESTION = "question"
    CONFLICT = "conflict"
    FILL = "fill"


class CardStatus(StrEnum):
    """C7 `status`：这张卡回应到哪一步了。"""

    PENDING = "pending"
    ANSWERED = "answered"
    SKIPPED = "skipped"


class ProposalState(StrEnum):
    """C7 `proposals[].state`：这条提案用户要不要（删除 ≠ 跳过）。"""

    KEPT = "kept"
    REMOVED = "removed"


class CardGroupState(StrEnum):
    """C8 `state`：这组卡片的裁决状态。"""

    ANSWERING = "answering"
    CONFIRMED = "confirmed"


class IdeaStatus(StrEnum):
    """C6 `status`：讨论候选从待选到被消费的一生。"""

    PENDING = "pending"
    SELECTED = "selected"
    DISCARDED = "discarded"
    CONSUMED = "consumed"


class MaterialSourceType(StrEnum):
    """C5 `source_type`：材料从哪来。"""

    RETRIEVAL = "retrieval"
    WEB = "web"
    DISCUSSION = "discussion"


class Credibility(StrEnum):
    """C5 `credibility`：可信度，由来源推断，不由调用方填。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MemoryType(StrEnum):
    """C9 `type`：这条记忆是哪一类。"""

    FACT = "fact"
    DECISION = "decision"
    LESSON = "lesson"
    PREFERENCE = "preference"


class MemoryStatus(StrEnum):
    """C9 `status`：候选 / 生效 / 失效 / 退役。"""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    INVALID = "invalid"
    RETIRED = "retired"


class RegistryKind(StrEnum):
    """C10 `kind`：注册中心管的所有**定义**。"""

    ROLE = "role"
    PROMPT = "prompt"
    TOOL = "tool"
    SKILL = "skill"
    TEMPLATE = "template"
    MODEL = "model"
    ORCHESTRATION_POLICY = "orchestration_policy"


class RegistryStatus(StrEnum):
    """C10 `status`：只有 `active` 会被调用。"""

    ACTIVE = "active"
    RETIRED = "retired"


class RegistryOwner(StrEnum):
    """C10 `owner`：预设还是用户创建——决定工具的挑选范围。"""

    PRESET = "preset"
    USER = "user"


class ContextBlockSource(StrEnum):
    """C12 `context.blocks[].source`：这个上下文块来自哪。"""

    DOCUMENT = "document"
    BRIEF = "brief"
    MEMORY = "memory"
    MATERIAL = "material"
    USER_INPUT = "user_input"
    DISCUSSION = "discussion"


class DiscussionState(StrEnum):
    """C14 `state`：讨论轮跑完没有。"""

    RUNNING = "running"
    FINISHED = "finished"


class DiscussionEndReason(StrEnum):
    """C14 `end_reason`：讨论轮为什么结束。"""

    ALL_SPOKE = "all_spoke"
    USER_STOPPED = "user_stopped"
    ROUND_LIMIT = "round_limit"


class ToolInvocationStatus(StrEnum):
    """C13 `tool.invoked` 的 payload：一次工具调用的两端。"""

    STARTED = "started"
    FINISHED = "finished"


class RegionName(StrEnum):
    """C12 黑板的五个区块。

    `claims` 只是区块名：它**不装形状**（内容类型是编排层自有，见 directory.md §4）。
    """

    ROUND = "round"
    CONTEXT = "context"
    CLAIMS = "claims"
    CARD_GROUP = "card_group"
    CONFIRMED = "confirmed"


class EventType(StrEnum):
    """C13 的八个事件。一个状态对象只有一个事件类型。"""

    ROUND_UPDATED = "round.updated"
    CARD_GROUP_UPDATED = "card_group.updated"
    DOC_CHANGED = "doc.changed"
    DISCUSSION_UTTERANCE_ADDED = "discussion.utterance_added"
    MEMORY_UPDATED = "memory.updated"
    REGISTRY_UPDATED = "registry.updated"
    TOOL_INVOKED = "tool.invoked"
    GENERATION_FAILED = "generation.failed"


class ProducerIdentity(StrEnum):
    """谁有权发事件（C13 的生产者列）。

    事件只能由**状态的生产者**发，发布时带身份，总线和流水据此执行白名单。
    """

    ORCHESTRATION = "orchestration"
    DOCUMENT_WRITER = "document_writer"
    TOOLS = "tools"
    MEMORY = "memory"
    REGISTRY = "registry"
    HARNESS = "harness"


class Permission(StrEnum):
    """L6 权限判定的返回值（CONTRACTS §5.3 的 `canUse` / `canEnter`）。

    I7：只有两档，没有审批态。
    """

    ALLOW = "allow"
    DENY = "deny"


class PromptRole(StrEnum):
    """提示词消息的角色（`complete(model_ref, messages)` 的入参项）。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
