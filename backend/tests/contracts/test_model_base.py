"""契约模型的通用三条：多字段拒、改字段拒、序列化往返相等。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pmstudio.contracts.models.base import ContractModel
from pmstudio.contracts.models.document import Block
from pmstudio.contracts.models.project import Project, ProjectConfig

VALID_INSTANCES: list[ContractModel] = [
    Block(block_id="blk_1", parent_id=None, schema_label="目标", content="", version=1),
    Project(
        project_id="prj_1",
        name="PM Studio",
        template_id="reg_tpl_initial",
        config=ProjectConfig(output_reserve_tokens=4096, system_overhead_tokens=512),
        created_at=datetime(2026, 9, 15, tzinfo=UTC),
    ),
]


def _ids() -> list[str]:
    return [type(instance).__name__ for instance in VALID_INSTANCES]


@pytest.mark.parametrize("instance", VALID_INSTANCES, ids=_ids())
def test_extra_field_is_rejected(instance: ContractModel) -> None:
    """字段名写错要当场炸，而不是静默丢掉。"""
    payload = {**instance.model_dump(), "unexpected_field": 1}
    with pytest.raises(ValidationError):
        type(instance).model_validate(payload)


@pytest.mark.parametrize("instance", VALID_INSTANCES, ids=_ids())
def test_assignment_is_rejected(instance: ContractModel) -> None:
    """I2 确认即冻结：契约对象是不可变的。"""
    field = next(iter(type(instance).model_fields))
    with pytest.raises(ValidationError):
        setattr(instance, field, None)


@pytest.mark.parametrize("instance", VALID_INSTANCES, ids=_ids())
def test_json_round_trip(instance: ContractModel) -> None:
    dumped = instance.model_dump_json()
    assert type(instance).model_validate_json(dumped) == instance
