"""`Target.label()` 的反函数：s11 条目要把去处带走，读回来得还原成 Target（P11 C1）。

回落原则：**认不出来就返回 None**（调用方回落 s11）。用户手写的条目、老格式的条目都可能长别的样子，
解析失败绝不许猜——猜错等于把一条未决内容塞进别人家章节。
"""

import pytest

from pmstudio.domain.shared.ids import FeatureSlug
from pmstudio.domain.shared.target import Target, target_from_label
from tests.support.builders import STD


@pytest.mark.parametrize(
    "target",
    [
        Target.body("s1"),
        Target.body("s10"),
        Target.body("s11"),
        Target.body("s8", field="compatibility"),  # 章节槽
        Target.body("s5", feature_slug="order-export"),  # 功能级
        Target.body("s5", feature_slug="order-export", field="acceptance"),  # 功能字段
        Target.preference(),
    ],
)
def test_label_roundtrips(target: Target):
    assert target_from_label(target.label()) == target


@pytest.mark.parametrize(
    "label",
    ["", "   ", "s5/", "s5/order-export."],
)
def test_an_unparsable_label_returns_none(label: str):
    """形状残缺：从来就没有猜测的余地（P11 C1）。"""

    assert target_from_label(label) is None


@pytest.mark.parametrize("label", ["s99", "s99/x.y", "随便一句话", "preference.x"])
def test_a_label_whose_chapter_the_template_lacks_returns_none(label: str):
    """成员不认识（`02` §11.0.69）：给了适配器就由它判——回落未决章，绝不猜。

    不给适配器时形状完整就照构造（Target 不再知道有哪些章）。`preference.x` 也在
    这一组：偏好区不配字段是**成员**问题（`preference` 不是一章），不是形状残缺。
    """

    assert target_from_label(label, STD) is None


def test_an_unknown_feature_field_is_still_a_target():
    """字段表是**内容层**的（`_kind_for` 才管），这里只管形状——不在这里替内容层判死。"""

    target = target_from_label("s5/order-export.bogus")

    assert target is not None
    assert target.label() == "s5/order-export.bogus"


def test_a_feature_field_is_not_a_chapter_slot():
    """`s8` 的槽和功能字段是两套命名空间：槽必须解析成"没有 feature_slug"的 target。"""

    target = target_from_label("s8.compatibility")

    assert target == Target.body("s8", field="compatibility")
    assert target is not None
    assert target.feature_slug is None


def test_the_slug_survives_the_roundtrip():
    target = target_from_label("s5/inspection-record.main_flow")

    assert target is not None
    assert target.feature_slug == FeatureSlug("inspection-record")
    assert target.field == "main_flow"
