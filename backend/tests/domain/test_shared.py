import pytest

from pmstudio.domain.shared.errors import FeatureNotAllowedHere, ShapeError, UnknownSection
from pmstudio.domain.shared.ids import evidence_id, evidence_seq
from pmstudio.domain.shared.invariants import DInvariant
from pmstudio.domain.shared.label import BODY_LABELS, ClaimLabel, Zone, zone_for
from pmstudio.domain.shared.sections import SECTION_IDS, section_spec
from pmstudio.domain.shared.target import Target


def test_evidence_id_roundtrip():
    assert evidence_seq(evidence_id(12)) == 12


def test_evidence_id_rejects_bad_format():
    with pytest.raises(ValueError):
        evidence_seq("X12")


def test_seven_labels_have_a_zone():
    assert {label: zone_for(label) for label in ClaimLabel} == {
        ClaimLabel.FACT: Zone.BODY,
        ClaimLabel.NEED: Zone.BODY,
        ClaimLabel.GOAL: Zone.BODY,
        ClaimLabel.CONSTRAINT: Zone.BODY,
        ClaimLabel.PREFERENCE: Zone.PREFERENCE,
        ClaimLabel.GUESS: Zone.BODY,
        ClaimLabel.DECISION: Zone.BODY,
    }
    assert ClaimLabel.PREFERENCE not in BODY_LABELS
    assert len(ClaimLabel) == 7


@pytest.mark.parametrize("section_id", SECTION_IDS)
def test_D1_ok_target_accepts_every_section(section_id):
    assert Target(section_id=section_id).section_id == section_id


def test_D1_bad_unknown_section_in_target():
    with pytest.raises(UnknownSection) as exc:
        Target(section_id="s14")
    assert exc.value.invariant == DInvariant.D1


def test_sections_have_titles_and_only_s5_grows_features():
    assert len(SECTION_IDS) == 13
    assert section_spec(SECTION_IDS[0]).title == "背景与问题"
    assert section_spec("s5").allows_features is True
    assert [
        spec.section_id for spec in (section_spec(s) for s in SECTION_IDS) if spec.allows_features
    ] == ["s5"]


def test_D3_bad_feature_outside_s5():
    with pytest.raises(FeatureNotAllowedHere) as exc:
        Target(section_id="s3", feature_slug="order-export")
    assert exc.value.invariant == DInvariant.D3


def test_shape_bad_field_without_feature():
    with pytest.raises(ShapeError):
        Target(section_id="s5", field="one_liner")


def test_target_is_within_supports_subtree_and_preference_zone():
    section_scope = Target(section_id="s5")
    feature_scope = Target(section_id="s5", feature_slug="order-export")
    assert Target(section_id="s5", feature_slug="order-export", field="one_liner").is_within(
        section_scope
    )
    assert Target(section_id="s5", feature_slug="order-export", field="one_liner").is_within(
        feature_scope
    )
    assert not Target(section_id="s8").is_within(section_scope)
    assert Target.preference().is_within(Target.preference())
    assert not Target.preference().is_within(section_scope)


def test_target_label_is_stable_for_ui():
    assert Target(section_id="s5", feature_slug="order-export", field="one_liner").label() == (
        "s5/order-export.one_liner"
    )
    assert Target(section_id="s8").label() == "s8"
    assert Target.preference().label() == "preference"
