"""C1 Scope：写范围 + 版本快照。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.models.scope import Scope


def test_empty_scope_is_allowed() -> None:
    """什么都没选也要能开轮——由 M28 推导落位（PRD §5.2 第 3 条）。"""
    scope = Scope(selected_fields=(), base_versions={})
    assert scope.selected_fields == ()
    assert scope.base_versions == {}


def test_scope_holds_selected_fields_and_snapshot() -> None:
    scope = Scope(selected_fields=("功能清单", "验收条件"), base_versions={"blk_1": 1, "blk_2": 3})
    assert scope.selected_fields == ("功能清单", "验收条件")
    assert scope.base_versions == {"blk_1": 1, "blk_2": 3}


def test_duplicate_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Scope(selected_fields=("功能清单", "功能清单"), base_versions={})


def test_blank_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Scope(selected_fields=("功能清单", "  "), base_versions={})


def test_snapshot_version_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Scope(selected_fields=(), base_versions={"blk_1": 0})


def test_selected_fields_are_immutable() -> None:
    scope = Scope(selected_fields=("功能清单",), base_versions={})
    with pytest.raises(ValidationError):
        scope.selected_fields = ("验收条件",)
