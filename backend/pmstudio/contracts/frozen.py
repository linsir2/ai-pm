"""R0.6 契约冻结 —— 17 个契约的"已冻结"标记、形状锁与变更记录。

三样东西，各只有一个家：

| 什么 | 在哪 | 回答什么 |
|---|---|---|
| 冻结标记 | `FROZEN_CONTRACTS` | 哪个契约冻了、住在哪、装哪些模型、在哪条变更记录里冻的 |
| 形状锁 | `CONTRACT_SHAPES` / `INTERFACE_SHAPES` | 冻的是**什么样的**形状：字段、类型、约束、默认值 |
| 变更记录 | `CHANGE_RECORDS` | 谁在什么时候、因为什么动了契约（文档面是 `CONTRACTS.md` §0.2） |

**改契约的顺序**（`tests/contracts/test_freeze.py` 钉住，顺序反了就红）：

1. 先写变更记录：往 `CHANGE_RECORDS` 追加一条（日期 / `CONTRACTS.md` 版本 / 为什么动 / 动了哪些契约）
2. 再改契约模型，把受影响的形状行同步进 `CONTRACT_SHAPES`
3. 把那个契约的 `frozen_in` 指向新记录

形状锁与实现对不上时测试会红，并打印差在哪一行——**没写变更记录，契约就改不动**。

**锁的是形状，不是行为。** 字段名 / 类型文本 / 约束 / 默认值在锁里；模型的 `model_validator`
与 `invariants.py` 的跨对象规则不在——那些归 `CONTRACTS.md` §6 的不变量表与它们自己的用例。

形状行的格式：`模型.字段: 类型 [约束] = 默认值`。必填字段没有 `= 默认值`；枚举默认写成
`Enum.MEMBER`，工厂默认写成 `()` / `{}`。类型文本是**给人看的**：去掉模块前缀、
`datetime.datetime` 写成 `datetime`——它只用来比对与评审，不参与运行时。

**不占 C 编号的形状**（`INTERFACE_SHAPES`）：§5.3 里各层接口的入参 / 返回形状，它们挂在某个
方法上。判据是一条全覆盖：`contracts/` 里每个模型，要么挂在某个 C 编号下，要么挂在这里，
**没有第三个去处**；`undeclared_models()` 两向对齐（锁里不许有幽灵，现实里不许有漏网）。
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Final

from pydantic import BaseModel
from pydantic.fields import FieldInfo

import pmstudio.contracts.models as models_package
from pmstudio.contracts.models.card import Card, CardAnswer, CardOption, Proposal
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.contracts.models.citation import Citation
from pmstudio.contracts.models.conversation import Message, Summary
from pmstudio.contracts.models.discussion import DiscussionRound, Utterance
from pmstudio.contracts.models.document import (
    Block,
    BlockOp,
    Document,
    DocumentSnapshot,
    DocumentVersion,
    ManualEditPayload,
    RollbackPayload,
    SubmitPayload,
)
from pmstudio.contracts.models.idea import IdeaCandidate
from pmstudio.contracts.models.material import MaterialPacket
from pmstudio.contracts.models.memory import Memory
from pmstudio.contracts.models.project import Project, ProjectConfig
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.models.registry import (
    ModelBody,
    RegistryEntry,
    TemplateBody,
    TemplateField,
)
from pmstudio.contracts.models.results import (
    AssembleResult,
    CreateProjectResult,
    TrimResult,
    WriteDocumentResult,
)
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.models.trace import ClaimDigest, Trace
from pmstudio.contracts.skeleton import board, events
from pmstudio.contracts.skeleton.board import (
    ConfirmedRegion,
    ContextBlock,
    ContextRegion,
    RoundRegion,
)
from pmstudio.contracts.skeleton.events import (
    CardGroupUpdatedPayload,
    DiscussionUtteranceAddedPayload,
    DocChangedPayload,
    Event,
    EventLogEntry,
    GenerationFailedPayload,
    MemoryUpdatedPayload,
    RegistryUpdatedPayload,
    RoundUpdatedPayload,
    ToolInvokedPayload,
)


@dataclass(frozen=True, slots=True)
class ContractChange:
    """一条契约变更记录。**改契约之前**先在这里追加一条。"""

    record_id: str
    date: str
    doc_version: str
    summary: str
    contracts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FrozenContract:
    """一个契约的冻结标记。"""

    contract_id: str
    title: str
    home: str
    models: tuple[str, ...]
    frozen_in: str
    frozen: bool = True
    note: str = ""


CHANGE_RECORDS: Final[tuple[ContractChange, ...]] = (
    ContractChange(
        record_id="CR-001",
        date="2026-09-15",
        doc_version="CONTRACTS.md v0.17",
        summary="首版冻结（R0.6）：C1–C17 的形状即 R0.2–R0.5 落地出来的那一版，"
        "含 CONTRACTS v0.9–v0.16 的全部修订；此后每改一处契约，先追加一条记录",
        contracts=(
            "C1",
            "C2",
            "C3",
            "C4",
            "C5",
            "C6",
            "C7",
            "C8",
            "C9",
            "C10",
            "C11",
            "C12",
            "C13",
            "C14",
            "C15",
            "C16",
            "C17",
        ),
    ),
    ContractChange(
        record_id="CR-002",
        date="2026-09-17",
        doc_version="CONTRACTS.md v0.19",
        summary="C7 `CardAnswer` 加 `proposal_states`：填充卡提案级的「要 / 不要」原来没有传输通道，"
        "M20 要取 `state = kept` 的提案，却没有任何接口能写 `Proposal.state`。"
        "加字段是兼容的；「必须列全」那条规则是跨对象的，写在 `invariants.check_proposal_states`",
        contracts=("C7",),
    ),
    ContractChange(
        record_id="CR-003",
        date="2026-09-17",
        doc_version="CONTRACTS.md v0.19",
        summary="C13 `doc.changed` / `memory.updated` 加 `round_id`：流水按 `round_id` 建索引，"
        "而这两个 payload 没有该字段 → 索引为 NULL → 复盘查不到「这一轮改过哪些文档、哪些记忆失效」。"
            "可空，因为用户手改不在一轮里（§5.2）",
        contracts=("C13",),
    ),
    ContractChange(
        record_id="CR-004",
        date="2026-09-17",
        doc_version="CONTRACTS.md v0.20",
        summary="C10 补模板本体形状 `TemplateField` / `TemplateBody`：`RegistryEntry.content` 对"
        "`kind = template` 原来只是一个不透明的 `JsonValue`，而 R1.1 的建项目要按它实例化 9 个字段块。"
        "只放 `label` + `required`：`required` 的消费者是 M28；常驻上下文（M13，R4）与字段间依赖提示"
        "（M2 / M28，R1.2+）等真被读时再加",
        contracts=("C10",),
    ),
    ContractChange(
        record_id="CR-005",
        date="2026-09-17",
        doc_version="CONTRACTS.md v0.21",
        summary="C10 补模型本体形状 `ModelBody`：M24 的预算是「模型窗口 − 输出预留 − 系统开销」，"
        "后两项在 C16 的项目配置里，窗口只能在模型上——而 `RegistryEntry.content` 对 `kind = model` "
        "原来只是不透明的 `JsonValue`。只放 `context_window_tokens`；R1.3 接模型时再加 endpoint / "
        "模型名 / 密钥来源（加字段兼容）。密钥本身永远不进条目",
        contracts=("C10",),
    ),
    ContractChange(
        record_id="CR-006",
        date="2026-09-17",
        doc_version="CONTRACTS.md v0.22",
        summary="C10 `ModelBody` 加 `model` / `api_key_env` / `timeout_seconds`："
        "M22 需要模型名调 litellm、需要环境变量名取密钥（密钥本身不进条目）、"
        "需要超时值供 M26 判定重试。三个字段都有明确消费者，没有「将来可能需要」的占位字段",
        contracts=("C10",),
    ),
    ContractChange(
        record_id="CR-007",
        date="2026-09-18",
        doc_version="CONTRACTS.md v0.22",
        summary="C7 `CardAnswer` 加 `verdict` 字段（`confirm` / `correct`）："
        "确认/纠正必须有显式标记，不允许隐式推断。"
        "verdict = correct 时 answer 必填；verdict = confirm 时 answer 可空",
        contracts=("C7",),
    ),
)

FROZEN_CONTRACTS: Final[tuple[FrozenContract, ...]] = (
    FrozenContract("C1", "Scope（范围）", "models/scope.py", ("Scope",), "CR-001"),
    FrozenContract(
        "C2",
        "Block / Document（文档）",
        "models/document.py",
        ("Document", "Block", "DocumentSnapshot"),
        "CR-001",
    ),
    FrozenContract(
        "C3",
        "Document Version（文档版本）",
        "models/document.py",
        ("DocumentVersion", "SubmitPayload", "ManualEditPayload", "RollbackPayload", "BlockOp"),
        "CR-001",
        note="含 §5.3 `writeDocument` 的三种入参形状（`WritePayload` 的三个成员）",
    ),
    FrozenContract(
        "C4",
        "Agent Packet（信息包）",
        "L2 层内（orchestration/，R1 落地）",
        (),
        "CR-001",
        frozen=False,
        note="层内契约：形状随 R1 的编排层一起定，不进 contracts/（directory.md §3.1）。"
        "这里只登记「存在」，不假装它有形状——`claims` 区块同样只声明存在",
    ),
    FrozenContract("C5", "Material Packet（材料包）", "models/material.py", ("MaterialPacket",), "CR-001"),
    FrozenContract("C6", "Idea Candidate（思路候选）", "models/idea.py", ("IdeaCandidate",), "CR-001"),
    FrozenContract(
        "C7",
        "Card（卡片）",
        "models/card.py",
        ("Card", "CardOption", "Proposal", "CardAnswer"),
        "CR-007",
        note="CR-002 加 proposal_states；CR-007 加 verdict（确认/纠正显式标记）",
    ),
    FrozenContract("C8", "Card Group（卡片组）", "models/card_group.py", ("CardGroup",), "CR-001"),
    FrozenContract("C9", "Memory（记忆）", "models/memory.py", ("Memory",), "CR-001"),
    FrozenContract(
        "C10",
        "Registry Entry（注册条目）",
        "models/registry.py",
        ("RegistryEntry", "TemplateField", "TemplateBody", "ModelBody"),
        "CR-006",
        note=(
            "CR-004 补了模板本体；CR-005 补了模型本体；"
            "CR-006 给 ModelBody 加了 model/api_key_env/timeout_seconds"
        ),
    ),
    FrozenContract("C11", "Trace（观测）", "models/trace.py", ("Trace", "ClaimDigest"), "CR-001"),
    FrozenContract(
        "C12",
        "Blackboard（黑板）",
        "skeleton/board.py",
        ("RoundRegion", "ContextRegion", "ContextBlock", "ConfirmedRegion"),
        "CR-001",
        note="`claims` 区块不装形状，所以这里没有它的模型",
    ),
    FrozenContract(
        "C13",
        "Event（事件）",
        "skeleton/events.py",
        (
            "Event",
            "EventLogEntry",
            "RoundUpdatedPayload",
            "CardGroupUpdatedPayload",
            "DocChangedPayload",
            "DiscussionUtteranceAddedPayload",
            "MemoryUpdatedPayload",
            "RegistryUpdatedPayload",
            "ToolInvokedPayload",
            "GenerationFailedPayload",
        ),
        "CR-003",
        note="八个事件八个 payload，一个不多一个不少；"
        "CR-003 给 `doc.changed` / `memory.updated` 加了 `round_id`",
    ),
    FrozenContract(
        "C14",
        "Discussion Round（讨论轮）",
        "models/discussion.py",
        ("DiscussionRound", "Utterance"),
        "CR-001",
    ),
    FrozenContract("C15", "Citation（引用）", "models/citation.py", ("Citation",), "CR-001"),
    FrozenContract("C16", "Project（项目）", "models/project.py", ("Project", "ProjectConfig"), "CR-001"),
    FrozenContract("C17", "Conversation（会话）", "models/conversation.py", ("Message", "Summary"), "CR-001"),
)

INTERFACE_SHAPE_REASONS: Final[dict[str, str]] = {
    "PromptMessage": "§5.3 `complete(model_ref, messages)` 的消息项——L2 / L4 / L6 之间的无类型接缝",
    "AssembleResult": "§5.3 L4 `assembleContext` 的返回：上下文块 + 版本快照",
    "TrimResult": "§5.3 L6 `trim` 的返回：留下的与裁掉的",
    "WriteDocumentResult": "§5.3 L5 `writeDocument` 的返回：写过的块 + 产生的那一版",
    "CreateProjectResult": "§5.3 L5 建项目的返回：新项目 + 按模板实例化出来的文档",
}

CONTRACT_SHAPES: Final[dict[str, tuple[str, ...]]] = {
    "C1": (
        "Scope.selected_fields: tuple[str, ...] = ()",
        "Scope.base_versions: dict[str, int] = {}",
    ),
    "C2": (
        "Document.doc_id: str",
        "Document.project_id: str",
        "Document.template_id: str",
        "Block.block_id: str",
        "Block.parent_id: str | None = None",
        "Block.schema_label: str",
        "Block.content: str = ''",
        "Block.version: int [Ge(ge=1)] = 1",
        "Block.source_card_id: str | None = None",
        "DocumentSnapshot.blocks: tuple[Block, ...]",
    ),
    "C3": (
        "DocumentVersion.version_id: str",
        "DocumentVersion.doc_id: str",
        "DocumentVersion.seq: int [Ge(ge=1)]",
        "DocumentVersion.snapshot: DocumentSnapshot",
        "DocumentVersion.trigger: VersionTrigger",
        "DocumentVersion.group_id: str | None = None",
        "DocumentVersion.target_version_id: str | None = None",
        "SubmitPayload.group_id: str",
        "ManualEditPayload.block_ops: tuple[BlockOp, ...]",
        "RollbackPayload.target_version_id: str",
        "BlockOp.block_id: str",
        "BlockOp.op: BlockOpKind",
        "BlockOp.content: str",
        "BlockOp.expected_version: int [Ge(ge=1)]",
    ),
    "C5": (
        "MaterialPacket.material_id: str",
        "MaterialPacket.source_type: MaterialSourceType",
        "MaterialPacket.content: str",
        "MaterialPacket.ref: str",
        "MaterialPacket.credibility: Credibility",
    ),
    "C6": (
        "IdeaCandidate.idea_id: str",
        "IdeaCandidate.discussion_round_id: str",
        "IdeaCandidate.claim: str",
        "IdeaCandidate.from_packet_ids: tuple[str, ...]",
        "IdeaCandidate.suggested_label: str | None = None",
        "IdeaCandidate.status: IdeaStatus = IdeaStatus.PENDING",
    ),
    "C7": (
        "Card.card_id: str",
        "Card.kind: CardKind",
        "Card.prompt: str",
        "Card.options: tuple[CardOption, ...] = ()",
        "Card.proposals: tuple[Proposal, ...] = ()",
        "Card.answer: str | None = None",
        "Card.status: CardStatus = CardStatus.PENDING",
        "CardOption.option_id: str",
        "CardOption.text: str",
        "CardOption.citations: tuple[Citation, ...] = ()",
        "Proposal.proposal_id: str",
        "Proposal.target_label: str",
        "Proposal.op: BlockOpKind",
        "Proposal.content: str",
        "Proposal.state: ProposalState = ProposalState.KEPT",
        "Proposal.citations: tuple[Citation, ...] = ()",
        "CardAnswer.card_id: str",
        "CardAnswer.verdict: str",
        "CardAnswer.status: CardStatus",
        "CardAnswer.answer: str | None = None",
        "CardAnswer.proposal_states: dict[str, ProposalState] = {}",
    ),
    "C8": (
        "CardGroup.group_id: str",
        "CardGroup.cards: tuple[Card, ...]",
        "CardGroup.round_id: str",
        "CardGroup.state: CardGroupState = CardGroupState.ANSWERING",
        "CardGroup.result_version_id: str | None = None",
    ),
    "C9": (
        "Memory.memory_id: str",
        "Memory.type: MemoryType",
        "Memory.content: str",
        "Memory.status: MemoryStatus",
        "Memory.citations: tuple[Citation, ...]",
    ),
    "C10": (
        "RegistryEntry.id: str",
        "RegistryEntry.kind: RegistryKind",
        "RegistryEntry.name: str",
        "RegistryEntry.content: JsonValue",
        "RegistryEntry.owner: RegistryOwner",
        "RegistryEntry.description: str | None = None",
        "RegistryEntry.tags: tuple[str, ...] = ()",
        "RegistryEntry.when_to_use: str | None = None",
        "RegistryEntry.status: RegistryStatus = RegistryStatus.ACTIVE",
        "RegistryEntry.allowed_tools: tuple[str, ...] | None = None",
        "RegistryEntry.allowed_entries: tuple[RoundEntry, ...] | None = None",
        "TemplateField.label: str",
        "TemplateField.required: bool",
        "TemplateBody.fields: tuple[TemplateField, ...]",
        "ModelBody.model: str",
        "ModelBody.api_key_env: str",
        "ModelBody.context_window_tokens: int [Gt(gt=0)]",
        "ModelBody.timeout_seconds: int [Gt(gt=0)] = 60",
    ),
    "C11": (
        "Trace.trace_id: str",
        "Trace.round_id: str",
        "Trace.context_used: ContextRegion",
        "Trace.tools_called: tuple[str, ...] = ()",
        "Trace.final_version_id: str | None = None",
        "Trace.claims: tuple[ClaimDigest, ...] = ()",
        "ClaimDigest.packet_id: str",
        "ClaimDigest.claim_id: str",
        "ClaimDigest.role_id: str",
        "ClaimDigest.statement: str",
        "ClaimDigest.kind: ClaimKind",
        "ClaimDigest.citations: tuple[Citation, ...] = ()",
    ),
    "C12": (
        "RoundRegion.round_id: str",
        "RoundRegion.project_id: str",
        "RoundRegion.entry: RoundEntry",
        "RoundRegion.user_input: str",
        "RoundRegion.scope: Scope",
        "RoundRegion.phase: RoundPhase",
        "RoundRegion.ended_at: datetime | None = None",
        "RoundRegion.end_reason: RoundEndReason | None = None",
        "ContextRegion.blocks: tuple[ContextBlock, ...] = ()",
        "ContextRegion.dropped: tuple[ContextBlock, ...] = ()",
        "ContextRegion.assembled_at: datetime",
        "ContextBlock.source: ContextBlockSource",
        "ContextBlock.ref: str",
        "ContextBlock.content: str = ''",
        "ContextBlock.priority: int [Ge(ge=0)]",
        "ContextBlock.credibility: Credibility | None = None",
        "ConfirmedRegion.card_group_ids: tuple[str, ...] = ()",
        "ConfirmedRegion.memory_ids: tuple[str, ...] = ()",
    ),
    "C13": (
        "Event.type: EventType",
        "Event.payload: RoundUpdatedPayload | CardGroupUpdatedPayload | DocChangedPayload |"
        " DiscussionUtteranceAddedPayload | MemoryUpdatedPayload | RegistryUpdatedPayload |"
        " ToolInvokedPayload | GenerationFailedPayload",
        "Event.at: datetime",
        "EventLogEntry.event_id: str",
        "EventLogEntry.at: datetime",
        "EventLogEntry.type: EventType",
        "EventLogEntry.producer: ProducerIdentity",
        "EventLogEntry.payload: RoundUpdatedPayload | CardGroupUpdatedPayload | DocChangedPayload |"
        " DiscussionUtteranceAddedPayload | MemoryUpdatedPayload | RegistryUpdatedPayload |"
        " ToolInvokedPayload | GenerationFailedPayload",
        "EventLogEntry.round_id: str | None = None",
        "EventLogEntry.project_id: str | None = None",
        "RoundUpdatedPayload.round_id: str",
        "RoundUpdatedPayload.project_id: str",
        "RoundUpdatedPayload.entry: RoundEntry",
        "RoundUpdatedPayload.phase: RoundPhase",
        "RoundUpdatedPayload.from_phase: RoundPhase | None = None",
        "RoundUpdatedPayload.end_reason: RoundEndReason | None = None",
        "CardGroupUpdatedPayload.group_id: str",
        "CardGroupUpdatedPayload.round_id: str",
        "CardGroupUpdatedPayload.state: CardGroupState",
        "CardGroupUpdatedPayload.from_state: CardGroupState | None = None",
        "CardGroupUpdatedPayload.card_count: int [Ge(ge=0)]",
        "CardGroupUpdatedPayload.has_fill_card: bool",
        "DocChangedPayload.doc_id: str",
        "DocChangedPayload.version_id: str",
        "DocChangedPayload.seq: int [Ge(ge=1)]",
        "DocChangedPayload.trigger: VersionTrigger",
        "DocChangedPayload.block_ids: tuple[str, ...] = ()",
        "DocChangedPayload.round_id: str | None = None",
        "DiscussionUtteranceAddedPayload.round_id: str",
        "DiscussionUtteranceAddedPayload.role_id: str",
        "DiscussionUtteranceAddedPayload.packet_id: str",
        "DiscussionUtteranceAddedPayload.index: int [Ge(ge=0)]",
        "MemoryUpdatedPayload.memory_id: str",
        "MemoryUpdatedPayload.project_id: str",
        "MemoryUpdatedPayload.type: MemoryType",
        "MemoryUpdatedPayload.status: MemoryStatus",
        "MemoryUpdatedPayload.from_status: MemoryStatus | None = None",
        "MemoryUpdatedPayload.round_id: str | None = None",
        "RegistryUpdatedPayload.id: str",
        "RegistryUpdatedPayload.kind: RegistryKind",
        "RegistryUpdatedPayload.status: RegistryStatus",
        "RegistryUpdatedPayload.from_status: RegistryStatus | None = None",
        "ToolInvokedPayload.round_id: str",
        "ToolInvokedPayload.tool_id: str",
        "ToolInvokedPayload.status: ToolInvocationStatus",
        "ToolInvokedPayload.role_id: str | None = None",
        "GenerationFailedPayload.round_id: str",
        "GenerationFailedPayload.step: RoundPhase",
        "GenerationFailedPayload.reason: str",
        "GenerationFailedPayload.retryable: bool",
    ),
    "C14": (
        "DiscussionRound.round_id: str",
        "DiscussionRound.participants: tuple[str, ...]",
        "DiscussionRound.utterances: tuple[Utterance, ...] = ()",
        "DiscussionRound.state: DiscussionState = DiscussionState.RUNNING",
        "DiscussionRound.end_reason: DiscussionEndReason | None = None",
        "DiscussionRound.idea_ids: tuple[str, ...] = ()",
        "Utterance.role_id: str",
        "Utterance.text: str",
        "Utterance.packet_id: str",
    ),
    "C15": (
        "Citation.target_type: CitationTargetType",
        "Citation.target_id: str",
        "Citation.target_version: int | None = None",
        "Citation.quote: str | None = None",
    ),
    "C16": (
        "Project.project_id: str",
        "Project.name: str",
        "Project.template_id: str",
        "Project.config: ProjectConfig",
        "Project.created_at: datetime",
        "ProjectConfig.output_reserve_tokens: int [Ge(ge=0)]",
        "ProjectConfig.system_overhead_tokens: int [Ge(ge=0)]",
    ),
    "C17": (
        "Message.message_id: str",
        "Message.project_id: str",
        "Message.round_id: str",
        "Message.author: str",
        "Message.text: str",
        "Message.packet_id: str | None = None",
        "Summary.summary_id: str",
        "Summary.project_id: str",
        "Summary.covers_round_ids: tuple[str, ...]",
        "Summary.text: str",
    ),
}

INTERFACE_SHAPES: Final[dict[str, tuple[str, ...]]] = {
    "PromptMessage": (
        "PromptMessage.role: PromptRole",
        "PromptMessage.text: str",
    ),
    "AssembleResult": (
        "AssembleResult.blocks: tuple[ContextBlock, ...] = ()",
        "AssembleResult.base_versions: dict[str, int] = {}",
    ),
    "TrimResult": (
        "TrimResult.blocks: tuple[ContextBlock, ...] = ()",
        "TrimResult.dropped: tuple[ContextBlock, ...] = ()",
    ),
    "WriteDocumentResult": (
        "WriteDocumentResult.blocks: tuple[Block, ...] = ()",
        "WriteDocumentResult.version: DocumentVersion",
    ),
    "CreateProjectResult": (
        "CreateProjectResult.project_id: str",
        "CreateProjectResult.doc_id: str",
    ),
}

CONTRACTS_BY_ID: Final[dict[str, FrozenContract]] = {
    contract.contract_id: contract for contract in FROZEN_CONTRACTS
}

CONTRACT_MODEL_TYPES: Final[dict[str, type[BaseModel]]] = {
    model.__name__: model
    for model in (
        Scope,
        Document,
        Block,
        DocumentSnapshot,
        DocumentVersion,
        SubmitPayload,
        ManualEditPayload,
        RollbackPayload,
        BlockOp,
        MaterialPacket,
        IdeaCandidate,
        Card,
        CardOption,
        Proposal,
        CardAnswer,
        CardGroup,
        Memory,
        RegistryEntry,
        TemplateField,
        TemplateBody,
        ModelBody,
        Trace,
        ClaimDigest,
        RoundRegion,
        ContextRegion,
        ContextBlock,
        ConfirmedRegion,
        Event,
        EventLogEntry,
        RoundUpdatedPayload,
        CardGroupUpdatedPayload,
        DocChangedPayload,
        DiscussionUtteranceAddedPayload,
        MemoryUpdatedPayload,
        RegistryUpdatedPayload,
        ToolInvokedPayload,
        GenerationFailedPayload,
        DiscussionRound,
        Utterance,
        Citation,
        Project,
        ProjectConfig,
        Message,
        Summary,
        PromptMessage,
        AssembleResult,
        TrimResult,
        WriteDocumentResult,
        CreateProjectResult,
    )
}

_MODULE_PREFIXES: Final = (
    "pmstudio.contracts.models.",
    "pmstudio.contracts.skeleton.",
    "pmstudio.contracts.enums.",
    "pmstudio.contracts.",
)

_SUBMODULE_PREFIX: Final = re.compile(r"\b[a-z_]+\.[A-Z]")

_FACTORY_TEXT: Final = {"tuple": "()", "dict": "{}", "list": "[]"}


def _type_text(annotation: object) -> str:
    """给人看的类型文本：去模块前缀，`datetime.datetime` 写成 `datetime`。"""
    text = str(annotation)
    for wrapper in ("<class '", "<enum '"):
        text = text.replace(wrapper, "")
    text = text.replace("'>", "")
    for prefix in _MODULE_PREFIXES:
        text = text.replace(prefix, "")
    text = _SUBMODULE_PREFIX.sub(lambda match: match.group(0)[-1], text)
    return text.replace("datetime.datetime", "datetime")


def _default_text(field: FieldInfo) -> str:
    """必填就不写；有默认值就写出来——默认值是契约的一部分（谁不写就得到什么）。"""
    if field.is_required():
        return ""
    if field.default_factory is not None:
        name = getattr(field.default_factory, "__name__", "?")
        return f" = {_FACTORY_TEXT.get(name, f'{name}()')}"
    default = field.default
    if isinstance(default, Enum):
        return f" = {type(default).__name__}.{default.name}"
    return f" = {default!r}"


def _model_shape_lines(model: type[BaseModel]) -> tuple[str, ...]:
    return tuple(
        f"{model.__name__}.{name}: {_type_text(field.annotation)}"
        f"{''.join(f' [{constraint}]' for constraint in field.metadata)}"
        f"{_default_text(field)}"
        for name, field in model.model_fields.items()
    )


def shape_of(model_names: tuple[str, ...]) -> tuple[str, ...]:
    """这几个模型现在（代码里）长什么样。锁里存的就是它的输出。"""
    return tuple(line for name in model_names for line in _model_shape_lines(CONTRACT_MODEL_TYPES[name]))


def live_shape(contract_id: str) -> tuple[str, ...]:
    """某个契约现在（代码里）的形状。"""
    return shape_of(CONTRACTS_BY_ID[contract_id].models)


def live_interface_shape(name: str) -> tuple[str, ...]:
    """某个接口形状现在（代码里）的形状。"""
    return shape_of((name,))


def drift_detail(locked: tuple[str, ...], live: tuple[str, ...]) -> tuple[str, ...]:
    """锁与现实差在哪几行。测试报错直接把它打出来，不让人自己去比对。"""
    lines: list[str] = []
    for line in locked:
        if line not in live:
            lines.append(f"  锁里有、代码里没有：{line}")
    for line in live:
        if line not in locked:
            lines.append(f"  代码里有、锁里没有：{line}")
    return tuple(lines)


def shape_drift() -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """锁对不上现实的：`契约号 → (锁里的形状, 代码里的形状)`。空字典 = 契约没被悄悄改过。"""
    drift: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for contract_id, locked in CONTRACT_SHAPES.items():
        live = live_shape(contract_id)
        if live != locked:
            drift[contract_id] = (locked, live)
    for name, locked in INTERFACE_SHAPES.items():
        live = live_interface_shape(name)
        if live != locked:
            drift[name] = (locked, live)
    return drift


def declared_models() -> frozenset[str]:
    """锁里登记的模型名：17 个契约名下的 + 接口形状里的。"""
    from_contracts = {name for contract in FROZEN_CONTRACTS for name in contract.models}
    return frozenset(from_contracts | set(INTERFACE_SHAPES))


def discover_contract_models() -> frozenset[str]:
    """`contracts/` 里**实际存在**的契约模型名（`ContractModel` 基类不算）。"""
    found: set[str] = set()
    for module_info in pkgutil.iter_modules(models_package.__path__):
        module = importlib.import_module(f"{models_package.__name__}.{module_info.name}")
        found |= _contract_models_in(vars(module).values())
    for skeleton_module in (board, events):
        found |= _contract_models_in(vars(skeleton_module).values())
    return frozenset(found)


def _contract_models_in(values: Iterable[object]) -> set[str]:
    return {
        value.__name__
        for value in values
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value.__module__.startswith("pmstudio.contracts")
        and value.__name__ != "ContractModel"
    }


def undeclared_models() -> tuple[frozenset[str], frozenset[str]]:
    """两向对齐的结果：`(现实里没有的幽灵, 锁外的漏网模型)`。两者都空才算对齐。"""
    declared = declared_models()
    found = discover_contract_models()
    return frozenset(declared - found), frozenset(found - declared)


def main() -> int:
    """`python -m pmstudio.contracts.frozen`：打印冻结表，再对一次现实。"""
    print("R0.6 契约冻结（17 个契约）")
    print(f"{'契约':<5}{'状态':<6}{'模型':<4}{'冻结于':<8}标题 / 出处")
    for contract in FROZEN_CONTRACTS:
        state = "已冻结" if contract.frozen else "未冻形状"
        print(
            f"{contract.contract_id:<5}{state:<6}{len(contract.models):<4}"
            f"{contract.frozen_in:<8}{contract.title}  ← {contract.home}"
        )
        if contract.note:
            print(f"     注：{contract.note}")

    drift = shape_drift()
    if not drift:
        print("\n形状锁与代码一致。")
    else:
        print("\n形状锁与代码不一致——改契约要先写变更记录：")
        for key, (locked, live) in drift.items():
            print(f"  {key}")
            for line in drift_detail(locked, live):
                print(line)

    ghosts, strays = undeclared_models()
    if not ghosts and not strays:
        print(f"覆盖对齐：{len(discover_contract_models())} 个模型全在锁里，不多不少。")
    else:
        print(f"\n覆盖没对齐：现实里没有的幽灵 {sorted(ghosts)}；锁外的漏网 {sorted(strays)}")

    return 1 if drift or ghosts or strays else 0


if __name__ == "__main__":
    raise SystemExit(main())
