"""枚举值域与 CONTRACTS.md 的表格逐字对应。

下面这张期望表就是契约的机器版本：漏一个值、多一个值、改一个拼写都会红。
"""

import json

import pytest

from pmstudio.contracts import enums

EXPECTED_VALUES: dict[type, set[str]] = {
    # C3 trigger
    enums.VersionTrigger: {"manual", "submit", "rollback"},
    # C7 proposals[].op 与 §5.3 writeDocument 的 payload 项
    enums.BlockOpKind: {"replace", "append"},
    # C15 target_type
    enums.CitationTargetType: {"block", "memory", "material", "idea", "user_input", "claim"},
    # C12 round.entry
    enums.RoundEntry: {"main", "discussion"},
    # C12 round.phase
    enums.RoundPhase: {
        "assembling",
        "restating",
        "working",
        "awaiting_user",
        "drafting",
        "writing",
        "done",
        "failed",
    },
    # C12 round.end_reason
    enums.RoundEndReason: {"completed", "user_stopped", "failed", "process_restart"},
    # C4 claim.kind，被 C11 主张留痕复用
    enums.ClaimKind: {"proposal", "challenge"},
    # C7 card.kind
    enums.CardKind: {"understanding", "question", "conflict", "fill"},
    # C7 card.status
    enums.CardStatus: {"pending", "answered", "skipped"},
    # C7 proposals[].state
    enums.ProposalState: {"kept", "removed"},
    # C8 card_group.state
    enums.CardGroupState: {"answering", "confirmed"},
    # C6 idea.status
    enums.IdeaStatus: {"pending", "selected", "discarded", "consumed"},
    # C5 material.source_type
    enums.MaterialSourceType: {"retrieval", "web", "discussion"},
    # C5 material.credibility
    enums.Credibility: {"high", "medium", "low"},
    # C9 memory.type
    enums.MemoryType: {"fact", "decision", "lesson", "preference"},
    # C9 memory.status
    enums.MemoryStatus: {"candidate", "active", "invalid", "retired"},
    # C10 registry.kind
    enums.RegistryKind: {
        "role",
        "prompt",
        "tool",
        "skill",
        "template",
        "model",
        "orchestration_policy",
    },
    # C10 registry.status
    enums.RegistryStatus: {"active", "retired"},
    # C10 registry.owner
    enums.RegistryOwner: {"preset", "user"},
    # C12 context.blocks[].source
    enums.ContextBlockSource: {
        "document",
        "brief",
        "memory",
        "material",
        "user_input",
        "discussion",
    },
    # C14 discussion.state
    enums.DiscussionState: {"running", "finished"},
    # C14 discussion.end_reason
    enums.DiscussionEndReason: {"all_spoke", "user_stopped", "round_limit"},
    # C13 tool.invoked 的 status
    enums.ToolInvocationStatus: {"started", "finished"},
    # C12 五个区块
    enums.RegionName: {"round", "context", "claims", "card_group", "confirmed"},
    # C13 八个事件
    enums.EventType: {
        "round.updated",
        "card_group.updated",
        "doc.changed",
        "discussion.utterance_added",
        "memory.updated",
        "registry.updated",
        "tool.invoked",
        "generation.failed",
    },
    # C13 事件的生产者（窄发布的依据）
    enums.ProducerIdentity: {
        "orchestration",
        "document_writer",
        "tools",
        "memory",
        "registry",
        "harness",
    },
    # §5.3 canUse / canEnter 的返回值（I7 权限两档）
    enums.Permission: {"allow", "deny"},
    # complete(model_ref, messages) 的入参项
    enums.PromptRole: {"system", "user", "assistant"},
}


@pytest.mark.parametrize(
    ("enum_cls", "expected"),
    list(EXPECTED_VALUES.items()),
    ids=[enum_cls.__name__ for enum_cls in EXPECTED_VALUES],
)
def test_value_set_matches_contract(enum_cls: type, expected: set[str]) -> None:
    assert {member.value for member in enum_cls} == expected


@pytest.mark.parametrize("enum_cls", list(EXPECTED_VALUES), ids=lambda cls: cls.__name__)
def test_no_duplicate_values(enum_cls: type) -> None:
    values = [member.value for member in enum_cls]
    assert len(values) == len(set(values))


@pytest.mark.parametrize("enum_cls", list(EXPECTED_VALUES), ids=lambda cls: cls.__name__)
def test_members_are_plain_strings_on_the_wire(enum_cls: type) -> None:
    """契约要能序列化成 JSON、将来能导出成 TS 类型，值必须是裸字符串。"""
    for member in enum_cls:
        assert isinstance(member, str)
        assert json.loads(json.dumps({"v": member})) == {"v": member.value}


def test_event_types_are_dotted_names() -> None:
    """事件类型一律 `<对象>.<变化>`，订阅者靠它分流。"""
    for event_type in enums.EventType:
        head, _, tail = event_type.value.partition(".")
        assert head and tail
