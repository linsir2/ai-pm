"""C10 Registry Entry：只存定义；工具和 skill 必须能被 AI 挑。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import RegistryKind, RegistryOwner, RegistryStatus, RoundEntry
from pmstudio.contracts.models.registry import RegistryEntry


def _entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "reg_1",
        "kind": RegistryKind.TOOL,
        "name": "联网搜索",
        "content": {"endpoint": "search_web"},
        "description": "按关键词搜网上的资料",
        "tags": ("web_search",),
        "when_to_use": "需要外部事实、文档里没有的时候",
        "owner": RegistryOwner.PRESET,
    }
    base.update(overrides)
    return base


def test_field_set_matches_contract() -> None:
    assert set(RegistryEntry.model_fields) == {
        "id",
        "kind",
        "name",
        "content",
        "description",
        "tags",
        "when_to_use",
        "owner",
        "status",
        "allowed_tools",
        "allowed_entries",
    }


def test_new_entry_is_active() -> None:
    assert RegistryEntry(**_entry()).status is RegistryStatus.ACTIVE


@pytest.mark.parametrize("kind", [RegistryKind.TOOL, RegistryKind.SKILL])
@pytest.mark.parametrize("missing", ["description", "tags", "when_to_use"])
def test_tools_and_skills_must_be_describable(kind: RegistryKind, missing: str) -> None:
    """I13：缺这三样，AI 就没法自己挑工具。"""
    value = () if missing == "tags" else None
    with pytest.raises(ValidationError):
        RegistryEntry(**_entry(kind=kind, **{missing: value}))


@pytest.mark.parametrize(
    "kind",
    [RegistryKind.ROLE, RegistryKind.PROMPT, RegistryKind.TEMPLATE, RegistryKind.MODEL],
)
def test_other_kinds_do_not_need_the_three_fields(kind: RegistryKind) -> None:
    entry = RegistryEntry(**_entry(kind=kind, description=None, tags=(), when_to_use=None))
    assert entry.description is None


def test_role_may_declare_two_allowlists() -> None:
    """I7 权限两档：能用哪些工具 + 能在哪些入口出现。"""
    entry = RegistryEntry(
        **_entry(
            kind=RegistryKind.ROLE,
            description=None,
            tags=(),
            when_to_use=None,
            allowed_tools=("reg_tool_1",),
            allowed_entries=(RoundEntry.DISCUSSION,),
        )
    )
    assert entry.allowed_tools == ("reg_tool_1",)
    assert entry.allowed_entries == (RoundEntry.DISCUSSION,)


def test_role_allowlists_may_be_absent() -> None:
    entry = RegistryEntry(**_entry(kind=RegistryKind.ROLE, description=None, tags=(), when_to_use=None))
    assert entry.allowed_tools is None
    assert entry.allowed_entries is None


@pytest.mark.parametrize("kind", [RegistryKind.TOOL, RegistryKind.PROMPT, RegistryKind.TEMPLATE])
def test_allowlists_belong_to_roles_only(kind: RegistryKind) -> None:
    with pytest.raises(ValidationError):
        RegistryEntry(**_entry(kind=kind, allowed_tools=("reg_tool_1",)))
    with pytest.raises(ValidationError):
        RegistryEntry(**_entry(kind=kind, allowed_entries=(RoundEntry.MAIN,)))


def test_tags_must_be_real_capability_words() -> None:
    with pytest.raises(ValidationError):
        RegistryEntry(**_entry(tags=("",)))
    with pytest.raises(ValidationError):
        RegistryEntry(**_entry(tags=("web_search", "web_search")))


def test_name_and_id_are_required() -> None:
    with pytest.raises(ValidationError):
        RegistryEntry(**_entry(id=""))
    with pytest.raises(ValidationError):
        RegistryEntry(**_entry(name="  "))
