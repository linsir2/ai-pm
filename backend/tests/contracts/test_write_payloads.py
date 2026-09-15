"""`writeDocument(trigger, payload)` 的三种入参，对应三种 trigger。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import BlockOpKind
from pmstudio.contracts.models.document import (
    BlockOp,
    ManualEditPayload,
    RollbackPayload,
    SubmitPayload,
)


def _edit() -> BlockOp:
    return BlockOp(block_id="blk_1", op=BlockOpKind.REPLACE, content="新内容", expected_version=2)


def test_submit_payload_carries_only_the_group() -> None:
    payload = SubmitPayload(group_id="grp_1")
    assert payload.group_id == "grp_1"
    with pytest.raises(ValidationError):
        SubmitPayload(group_id="")


def test_manual_payload_carries_edits_with_expected_versions() -> None:
    """一次显式保存 = 一版；每处改动都带 expected_version，对不上不静默覆盖。"""
    payload = ManualEditPayload(block_ops=(_edit(),))
    assert payload.block_ops[0].expected_version == 2
    with pytest.raises(ValidationError):
        ManualEditPayload(block_ops=())


def test_rollback_payload_carries_only_the_target_version() -> None:
    payload = RollbackPayload(target_version_id="ver_0")
    assert payload.target_version_id == "ver_0"
    with pytest.raises(ValidationError):
        RollbackPayload(target_version_id="")


def test_payloads_do_not_accept_each_others_fields() -> None:
    with pytest.raises(ValidationError):
        SubmitPayload(group_id="grp_1", target_version_id="ver_0")
    with pytest.raises(ValidationError):
        RollbackPayload(target_version_id="ver_0", block_ops=(_edit(),))
