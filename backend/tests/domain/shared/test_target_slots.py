"""Target 的 field 有两个命名空间：有 feature_slug 取功能字段，没有取该章的章节槽。"""

import pytest

from pmstudio.domain.shared.errors import TargetShapeError
from pmstudio.domain.shared.target import Target


def test_section_slot_target_is_legal():
    target = Target.body("s8", field="performance")
    assert target.section_id == "s8"
    assert target.feature_slug is None
    assert target.field == "performance"
    assert target.label() == "s8.performance"


def test_field_without_feature_must_be_a_slot_of_that_section():
    with pytest.raises(TargetShapeError):
        Target.body("s1", field="performance")  # s1 没有槽
    with pytest.raises(TargetShapeError):
        Target.body("s8", field="acceptance")  # 功能字段名不是章节槽


def test_feature_field_still_works():
    target = Target.feature_field("order-export", "acceptance")
    assert target.feature_slug == "order-export"
    assert target.label() == "s5/order-export.acceptance"
