"""C12 骨架：五个区块的形状、写者、读权限与广播映射。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import (
    ContextBlockSource,
    Credibility,
    EventType,
    ProducerIdentity,
    RegionName,
    RoundEndReason,
    RoundEntry,
    RoundPhase,
)
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import (
    REGION_BROADCAST,
    REGION_READERS,
    REGION_VALUE_TYPES,
    REGION_WRITERS,
    ConfirmedRegion,
    ContextBlock,
    ContextRegion,
    RoundRegion,
    assert_region_value,
)

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


def _round(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "round_id": "rnd_1",
        "project_id": "prj_1",
        "entry": RoundEntry.MAIN,
        "user_input": "细化一下功能清单",
        "scope": Scope(selected_fields=("功能清单",)),
        "phase": RoundPhase.ASSEMBLING,
    }
    base.update(overrides)
    return base


def test_round_region_field_set_matches_contract() -> None:
    assert set(RoundRegion.model_fields) == {
        "round_id",
        "project_id",
        "entry",
        "user_input",
        "scope",
        "phase",
        "ended_at",
        "end_reason",
    }


def test_running_round_has_no_end_fields() -> None:
    region = RoundRegion(**_round())
    assert region.ended_at is None
    assert region.end_reason is None
    with pytest.raises(ValidationError):
        RoundRegion(**_round(ended_at=NOW, end_reason=RoundEndReason.COMPLETED))


@pytest.mark.parametrize("phase", [RoundPhase.DONE, RoundPhase.FAILED])
def test_finished_round_must_say_when_and_why(phase: RoundPhase) -> None:
    with pytest.raises(ValidationError):
        RoundRegion(**_round(phase=phase))
    region = RoundRegion(**_round(phase=phase, ended_at=NOW, end_reason=RoundEndReason.COMPLETED))
    assert region.ended_at == NOW


def test_round_region_requires_ids_and_input() -> None:
    with pytest.raises(ValidationError):
        RoundRegion(**_round(round_id=""))
    with pytest.raises(ValidationError):
        RoundRegion(**_round(project_id=""))
    with pytest.raises(ValidationError):
        RoundRegion(**_round(user_input="  "))


def test_context_region_field_set_matches_contract() -> None:
    assert set(ContextRegion.model_fields) == {"blocks", "dropped", "assembled_at"}
    region = ContextRegion(assembled_at=NOW)
    assert region.blocks == ()
    assert region.dropped == ()


def test_context_block_field_set_matches_contract() -> None:
    assert set(ContextBlock.model_fields) == {"source", "ref", "content", "priority", "credibility"}


def test_context_block_must_point_back_at_something() -> None:
    with pytest.raises(ValidationError):
        ContextBlock(source=ContextBlockSource.DOCUMENT, ref="", priority=10)


def test_context_block_priority_is_a_non_negative_integer() -> None:
    """N2：M24 只按它裁，所以它必须可比；越大越先保留（排序规则见 invariants）。"""
    block = ContextBlock(source=ContextBlockSource.DOCUMENT, ref="blk_1", priority=0)
    assert block.priority == 0
    with pytest.raises(ValidationError):
        ContextBlock(source=ContextBlockSource.DOCUMENT, ref="blk_1", priority=-1)


def test_only_material_blocks_carry_credibility() -> None:
    ContextBlock(
        source=ContextBlockSource.MATERIAL,
        ref="mat_1",
        priority=5,
        credibility=Credibility.MEDIUM,
    )
    with pytest.raises(ValidationError):
        ContextBlock(source=ContextBlockSource.MATERIAL, ref="mat_1", priority=5)
    with pytest.raises(ValidationError):
        ContextBlock(
            source=ContextBlockSource.DOCUMENT,
            ref="blk_1",
            priority=5,
            credibility=Credibility.HIGH,
        )


def test_every_region_has_exactly_one_value_type() -> None:
    assert set(REGION_VALUE_TYPES) == set(RegionName)
    assert REGION_VALUE_TYPES[RegionName.ROUND] is RoundRegion
    assert REGION_VALUE_TYPES[RegionName.CONTEXT] is ContextRegion
    assert REGION_VALUE_TYPES[RegionName.CARD_GROUP] is CardGroup
    assert REGION_VALUE_TYPES[RegionName.CONFIRMED] is ConfirmedRegion


def test_only_the_orchestration_layer_may_write_any_region() -> None:
    """I19 区块单写者。"""
    assert set(REGION_WRITERS) == set(RegionName)
    for writers in REGION_WRITERS.values():
        assert writers == {ProducerIdentity.ORCHESTRATION}


def test_claims_region_is_the_only_read_restricted_region() -> None:
    assert REGION_READERS[RegionName.CLAIMS] == {ProducerIdentity.ORCHESTRATION}
    for region, readers in REGION_READERS.items():
        if region is not RegionName.CLAIMS:
            assert readers is None


def test_write_broadcasts_exactly_two_regions() -> None:
    """写入即广播：写 round / card_group 自动发事件，其余三个不发。"""
    assert REGION_BROADCAST == {
        RegionName.ROUND: EventType.ROUND_UPDATED,
        RegionName.CARD_GROUP: EventType.CARD_GROUP_UPDATED,
    }
    for silent in (RegionName.CONTEXT, RegionName.CLAIMS, RegionName.CONFIRMED):
        assert silent not in REGION_BROADCAST


def test_confirmed_region_holds_ids_only() -> None:
    assert set(ConfirmedRegion.model_fields) == {"card_group_ids", "memory_ids"}
    region = ConfirmedRegion(card_group_ids=("grp_1",), memory_ids=("mry_1",))
    assert region.card_group_ids == ("grp_1",)


def _round_region() -> RoundRegion:
    return RoundRegion(**_round())


def test_region_value_must_match_its_region() -> None:
    assert_region_value(RegionName.ROUND, _round_region())
    assert_region_value(RegionName.CONTEXT, ContextRegion(assembled_at=NOW))
    assert_region_value(RegionName.CONFIRMED, ConfirmedRegion())
    assert_region_value(RegionName.CLAIMS, ())

    with pytest.raises(ContractViolation):
        assert_region_value(RegionName.ROUND, ContextRegion(assembled_at=NOW))
    with pytest.raises(ContractViolation):
        assert_region_value(RegionName.CONTEXT, _round_region())


def test_claims_region_only_takes_an_opaque_sequence() -> None:
    """`claims` 是类型别名，`isinstance` 用不了参数化泛型——所以单独判。"""
    assert_region_value(RegionName.CLAIMS, (object(), object()))
    with pytest.raises(ContractViolation):
        assert_region_value(RegionName.CLAIMS, _round_region())
    with pytest.raises(ContractViolation):
        assert_region_value(RegionName.CLAIMS, [])
