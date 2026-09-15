"""C3 Document Version：三种 trigger 的字段组合。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import VersionTrigger
from pmstudio.contracts.models.document import Block, DocumentSnapshot, DocumentVersion


def _snapshot() -> DocumentSnapshot:
    return DocumentSnapshot(blocks=(Block(block_id="blk_1", schema_label="目标"),))


def _version(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "version_id": "ver_1",
        "doc_id": "doc_1",
        "seq": 1,
        "snapshot": _snapshot(),
        "trigger": VersionTrigger.MANUAL,
    }
    base.update(overrides)
    return base


def test_manual_version_carries_neither_group_nor_target() -> None:
    version = DocumentVersion(**_version())
    assert version.group_id is None
    assert version.target_version_id is None


def test_submit_version_requires_group_id() -> None:
    version = DocumentVersion(**_version(trigger=VersionTrigger.SUBMIT, group_id="grp_1"))
    assert version.group_id == "grp_1"
    with pytest.raises(ValidationError):
        DocumentVersion(**_version(trigger=VersionTrigger.SUBMIT))


def test_rollback_version_requires_target_version_id() -> None:
    version = DocumentVersion(**_version(trigger=VersionTrigger.ROLLBACK, target_version_id="ver_0"))
    assert version.target_version_id == "ver_0"
    with pytest.raises(ValidationError):
        DocumentVersion(**_version(trigger=VersionTrigger.ROLLBACK))


def test_manual_version_rejects_group_id() -> None:
    with pytest.raises(ValidationError):
        DocumentVersion(**_version(group_id="grp_1"))


def test_manual_version_rejects_target_version_id() -> None:
    with pytest.raises(ValidationError):
        DocumentVersion(**_version(target_version_id="ver_0"))


def test_submit_version_rejects_target_version_id() -> None:
    with pytest.raises(ValidationError):
        DocumentVersion(
            **_version(trigger=VersionTrigger.SUBMIT, group_id="grp_1", target_version_id="ver_0")
        )


def test_seq_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        DocumentVersion(**_version(seq=0))


def test_version_json_round_trip_keeps_enum() -> None:
    version = DocumentVersion(**_version(trigger=VersionTrigger.SUBMIT, group_id="grp_1"))
    restored = DocumentVersion.model_validate_json(version.model_dump_json())
    assert restored == version
    assert restored.trigger is VersionTrigger.SUBMIT
