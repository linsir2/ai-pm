from pmstudio.domain.round.draft import Origin, OriginType, Proposal
from pmstudio.domain.services.scope_guard import OUTSIDE_ROUND_SCOPE, ScopeGuard
from pmstudio.domain.shared.ids import EntryId, EvidenceId, ProposalId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from tests.support.builders import STD

REF = EvidenceId("E1")


def proposal(target: Target, origin_type: OriginType = OriginType.DERIVED) -> Proposal:
    return Proposal(
        proposal_id=ProposalId(f"p-{target.label()}"),
        entry_id=EntryId("en1"),
        op=WriteOp.ADD,
        target=target,
        text="内容",
        origin=Origin(origin_type, (REF,), "理由"),
    )


def test_no_violation_when_inside_scope():
    scope = frozenset({Target.body("s5")})
    report = ScopeGuard().check((proposal(Target.body("s5")),), write_scope=scope)
    assert report == ()


def test_feature_field_inside_section_scope_is_ok():
    scope = frozenset({Target.body("s5")})
    report = ScopeGuard().check(
        (proposal(Target.feature_field(
                      "order-export", "acceptance",
                      home=STD.feature_home
                  )),), write_scope=scope
    )
    assert report == ()


def test_outside_scope_is_reported():
    scope = frozenset({Target.body("s5")})
    report = ScopeGuard().check((proposal(Target.body("s7")),), write_scope=scope)
    assert [item.code for item in report] == [OUTSIDE_ROUND_SCOPE]
    assert report[0].target == Target.body("s7")


def test_system_default_is_exempt():
    scope = frozenset({Target.body("s5")})
    report = ScopeGuard().check(
        (proposal(Target.body("s11"), OriginType.SYSTEM_DEFAULT),), write_scope=scope
    )
    assert report == ()


def test_preference_zone_needs_preference_claim():
    scope = frozenset({Target.body("s5")})
    report = ScopeGuard().check(
        (proposal(Target.preference(), OriginType.USER),), write_scope=scope
    )
    assert [item.code for item in report] == [OUTSIDE_ROUND_SCOPE]
    report = ScopeGuard().check(
        (proposal(Target.preference(), OriginType.USER),),
        write_scope=frozenset({Target.preference()}),
    )
    assert report == ()
