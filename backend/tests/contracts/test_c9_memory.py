"""C9 Memory：候选与生效两态，逐条可追。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import CitationTargetType, MemoryStatus, MemoryType
from pmstudio.contracts.models.citation import Citation
from pmstudio.contracts.models.memory import Memory


def _citation() -> Citation:
    return Citation(target_type=CitationTargetType.BLOCK, target_id="blk_1", target_version=2)


def _memory(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "memory_id": "mry_1",
        "type": MemoryType.DECISION,
        "content": "这一版先不做多人协作",
        "status": MemoryStatus.CANDIDATE,
        "citations": (_citation(),),
    }
    base.update(overrides)
    return base


def test_field_set_matches_contract() -> None:
    assert set(Memory.model_fields) == {"memory_id", "type", "content", "status", "citations"}


def test_memory_must_have_a_source() -> None:
    """无出处的记忆不许入库。"""
    with pytest.raises(ValidationError):
        Memory(**_memory(citations=()))


def test_content_and_id_are_required() -> None:
    with pytest.raises(ValidationError):
        Memory(**_memory(content="  "))
    with pytest.raises(ValidationError):
        Memory(**_memory(memory_id=""))


def test_all_types_and_statuses_are_reachable() -> None:
    for memory_type in MemoryType:
        Memory(**_memory(type=memory_type))
    for status in MemoryStatus:
        Memory(**_memory(status=status))
