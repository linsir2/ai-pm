"""C5 Material Packet：进生成链路的外部信息。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import Credibility, MaterialSourceType
from pmstudio.contracts.models.material import CREDIBILITY_BY_SOURCE, MaterialPacket


def _material(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "material_id": "mat_1",
        "source_type": MaterialSourceType.WEB,
        "content": "某产品上线前做过压测",
        "ref": "https://example.com/post",
        "credibility": Credibility.MEDIUM,
    }
    base.update(overrides)
    return base


def test_credibility_follows_source_type() -> None:
    assert CREDIBILITY_BY_SOURCE == {
        MaterialSourceType.RETRIEVAL: Credibility.HIGH,
        MaterialSourceType.WEB: Credibility.MEDIUM,
        MaterialSourceType.DISCUSSION: Credibility.LOW,
    }
    for source_type, credibility in CREDIBILITY_BY_SOURCE.items():
        packet = MaterialPacket(**_material(source_type=source_type, credibility=credibility))
        assert packet.credibility is credibility


def test_credibility_that_contradicts_source_is_rejected() -> None:
    """可信度由来源推断，不由调用方随便填。"""
    with pytest.raises(ValidationError):
        MaterialPacket(**_material(source_type=MaterialSourceType.DISCUSSION, credibility=Credibility.HIGH))
    with pytest.raises(ValidationError):
        MaterialPacket(**_material(source_type=MaterialSourceType.RETRIEVAL, credibility=Credibility.LOW))


def test_ref_is_required() -> None:
    """I4 无来源不得进上下文。"""
    with pytest.raises(ValidationError):
        MaterialPacket(**_material(ref=""))
    with pytest.raises(ValidationError):
        MaterialPacket(**_material(ref="   "))


def test_content_is_required() -> None:
    with pytest.raises(ValidationError):
        MaterialPacket(**_material(content=""))


def test_material_id_is_required() -> None:
    with pytest.raises(ValidationError):
        MaterialPacket(**_material(material_id=""))


def test_discussion_material_maps_ref_from_idea() -> None:
    """讨论候选转材料包时，ref 是"讨论轮 id + 候选 id"，可信度固定为 low。"""
    packet = MaterialPacket(
        **_material(
            source_type=MaterialSourceType.DISCUSSION,
            credibility=Credibility.LOW,
            ref="rnd_discussion_1/ide_1",
        )
    )
    assert packet.source_type is MaterialSourceType.DISCUSSION
    assert packet.credibility is Credibility.LOW
