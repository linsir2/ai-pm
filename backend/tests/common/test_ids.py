"""标识生成：前缀词表与 directory.md §3.3 逐字对应。"""

import pytest

from pmstudio.common.ids import ALL_PREFIXES, EVENT, TimestampIdGenerator

EXPECTED_PREFIXES = {
    "prj",
    "doc",
    "blk",
    "ver",
    "rnd",
    "grp",
    "crd",
    "prp",
    "opt",
    "mry",
    "mat",
    "ide",
    "msg",
    "sum",
    "trc",
    "reg",
    "pkt",
    "clm",
    "evt",
}


def test_prefix_table_matches_directory() -> None:
    assert ALL_PREFIXES == EXPECTED_PREFIXES


def test_event_log_prefix_is_available() -> None:
    assert EVENT == "evt"
    assert EVENT in ALL_PREFIXES


def test_unknown_prefix_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知的 id 前缀"):
        TimestampIdGenerator().new_id("nope")


def test_generated_id_shape() -> None:
    identifier = TimestampIdGenerator().new_id(EVENT)
    prefix, stamp, suffix = identifier.split("_")
    assert prefix == "evt"
    assert len(stamp) == len("20260915T101010")
    assert len(suffix) == 8


def test_ids_do_not_collide_within_the_same_second() -> None:
    generator = TimestampIdGenerator()
    generated = [generator.new_id(EVENT) for _ in range(500)]
    assert len(set(generated)) == 500
