"""L4 轮次摘要：RoundDigest 数据类型 + build_round_digest 构建逻辑。"""

import pytest

from pmstudio.domain.memory.digest import RoundDigest


class TestRoundDigest:
    """RoundDigest: 不可变数据类。"""

    def test_frozen(self):
        d = RoundDigest(round_id="r1", project_id="p1", summary="摘要")
        with pytest.raises(AttributeError):
            d.summary = "新摘要"

    def test_default_fields(self):
        d = RoundDigest(round_id="r1", project_id="p1", summary="摘要")
        assert d.sections_touched == ()
        assert d.features_addressed == ()
        assert d.key_outcomes == ()

    def test_with_fields(self):
        d = RoundDigest(
            round_id="r1",
            project_id="p1",
            summary="触及 s1、s5，确认 2 条",
            sections_touched=("s1", "s5"),
            features_addressed=("group-buying",),
            key_outcomes=("定位=社区团购", "规则=7天退还"),
        )
        assert d.sections_touched == ("s1", "s5")
        assert d.features_addressed == ("group-buying",)
        assert len(d.key_outcomes) == 2
