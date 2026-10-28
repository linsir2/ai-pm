"""留痕条目带 `packet_id`：从"猜这行属于哪一趟"变成"按 id 对"。

`02-编排与数据.md §4.4` 的信封现在有两个消费者（事件 + 留痕行），
这一条守住留痕那一侧：**留痕行必须能指回它来自哪一包**。

`packet_id` 是**可选**的：留痕有四个来源（integrator / assembly / unaccounted / tool），
其中 tool 来源本来就没有 agent 包（是联网检索失败，不是模型回包），
assembly 的归因对不上号也挂不到席位包下。所以 `None` 的含义是
"**不来自模型回包**"，不是"没填"——这个区别必须写进字段注释，否则半年后没人分得清。
"""

from datetime import UTC, datetime

import pytest

from pmstudio.domain.round.round import DroppedItem
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.infrastructure.postgres.serialization import _dropped_from_payload

NOW = datetime(2026, 10, 4, tzinfo=UTC)


def a_drop(**overrides: object) -> DroppedItem:
    base: dict[str, object] = {
        "handle": "seat.data_scale:O2",
        "reason": "超出单席位上限 12 条，第 13 条起未进卡片",
        "source": "assembly",
        "seat_id": "seat.data_scale",
        "pass_index": 1,
    }
    base.update(overrides)
    return DroppedItem(**base)  # type: ignore[arg-type]


# —— 可选字段 ——


def test_packet_id_is_optional_and_defaults_to_none():
    """必填会逼 21 个构造点全改，还得给 tool 来源编一个假 id。"""

    assert a_drop().packet_id is None


def test_packet_id_is_kept_when_given():
    assert a_drop(packet_id="pkt-r1-seat_review-seat.data_scale-p1a1").packet_id == (
        "pkt-r1-seat_review-seat.data_scale-p1a1"
    )


def test_the_existing_validations_are_untouched():
    """加字段不许动原有三条校验——那是"这条留痕有意义"的必要条件。"""

    with pytest.raises(ShapeError):
        a_drop(handle="   ")
    with pytest.raises(ShapeError):
        a_drop(reason="")
    with pytest.raises(ShapeError):
        a_drop(pass_index=0)


# —— 序列化：老行兼容 ——


def test_payload_without_packet_id_reads_back_as_none():
    """老行兼容：payload 里没有这个键 → 读回 `None`，不是 KeyError。

    留痕是后加的表，老库里没有 `packet_id` 这个键——**不许静默丢历史**。
    """

    payload = {
        "dropped": [
            {
                "handle": "seat.x:O1",
                "reason": "归因对不上号",
                "source": "assembly",
                "seat_id": None,
                "pass_index": 1,
            }
        ]
    }
    (item,) = _dropped_from_payload(payload)
    assert item.packet_id is None


def test_payload_with_packet_id_reads_back_with_it():
    payload = {
        "dropped": [
            {
                "handle": "seat.x:O1",
                "reason": "超出上限",
                "source": "assembly",
                "seat_id": "seat.x",
                "pass_index": 2,
                "packet_id": "pkt-r1-seat_review-seat.x-p2a1",
            }
        ]
    }
    (item,) = _dropped_from_payload(payload)
    assert item.packet_id == "pkt-r1-seat_review-seat.x-p2a1"


def test_the_legacy_questionnaire_path_still_works():
    """老行读问卷上那串裸字符串的迁移路径不许被新字段打破。

    老行里 `questionnaire.dropped` 是一串**裸 handle 字符串**（没有 reason / source），
    迁移时补成 assembly 条目。新字段在这里必须是 `None`（老行没有包的概念）。
    """

    payload = {
        "questionnaire": {"dropped": ["seat.x:O1", "seat.x:O2"]},
        "seat_round_count": 1,
    }
    items = _dropped_from_payload(payload)
    assert len(items) == 2
    assert all(item.packet_id is None for item in items)
    assert all(item.source == "assembly" for item in items)
