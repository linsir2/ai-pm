"""归位建议的规范化：模型的笔误不该变成整轮失败，也不该丢内容。

真模型冒烟实测：analyzer 会把 feature_slug 挂到 s12 上（"月底必须上线"那条），
那会撞 D3（功能子树只能长在 s5）把整轮判死。规范化按领域规则纠正它。
"""

from pmstudio.application.agents.schemas import TargetDraft, normalize_claim_target
from pmstudio.domain.shared.errors import UnknownSection
from pmstudio.domain.shared.target import Target
from tests.support.builders import STD


def test_feature_slug_forces_section_s5():
    target = normalize_claim_target(
        TargetDraft(zone="body", section_id="s12", feature_slug="wechat-login"),
        label="need",
        adapter=STD,
    )
    assert target == Target.body("s5", feature_slug="wechat-login")


def test_feature_slug_with_field_keeps_both_in_s5():
    target = normalize_claim_target(
        TargetDraft(
            zone="body", section_id="s12", feature_slug="order-export", field="acceptance"
        ),
        label="need",
        adapter=STD,
    )
    assert target == Target.feature_field("order-export", "acceptance", home=STD.feature_home)


def test_field_without_feature_slug_is_dropped():
    target = normalize_claim_target(
        TargetDraft(zone="body", section_id="s8", field="acceptance"),
        label="need",
        adapter=STD,
    )
    assert target == Target.body("s8")


def test_preference_zone_wins_whatever_else_is_given():
    target = normalize_claim_target(
        TargetDraft(zone="preference", section_id="s3", feature_slug="login-page-ui"),
        label="preference",
        adapter=STD,
    )
    assert target == Target.preference()


def test_preference_label_wins_even_when_zone_says_body():
    """标签才是权威分类：模型把 zone 写成 body、去处给成功能字段时，仍按偏好处理。

    （真模型冒烟实测：`label=preference` + `zone=body` + 功能字段 → LB1 判死整轮。）
    """

    target = normalize_claim_target(
        TargetDraft(
            zone="body",
            section_id="s5",
            feature_slug="order-export",
            field="rules",
        ),
        label="preference",
        adapter=STD,
    )
    assert target == Target.preference()


def test_unknown_section_still_fails_loudly():
    import pytest

    with pytest.raises(UnknownSection):
        normalize_claim_target(
            TargetDraft(zone="body", section_id="s99"), label="need", adapter=STD
        )
