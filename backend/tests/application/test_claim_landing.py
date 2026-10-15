"""主张要么被引用、要么机械落地（D3 向 claims 延伸，`02` §11.0.71）。

与 `test_answer_landing.py` 同构，差异只有三处（都是 claims 的形态决定的）：
① 编号直接取 `claim.eid`（答案要去账本里按题查，主张自带）；
② 文本原样（答案要拼选项文案，主张本来就是人话）；
③ 没有"空内容"守卫（`Claim.__post_init__` 禁空文本，写检查是死代码）。

现场：E6「必须 9 月 20 号之前办完」（constraint → s1）没被任何 proposal 引用，
s11 也是空的——它静默消失了，没有任何痕迹（`04-未决.md` §N1）。
"""

from pmstudio.application import assembly
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import EvidenceId, FeatureSlug
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from tests.support.builders import NOW, PROJECT, ROUND_ID, document_with_feature

_FIRST_EID = EvidenceId("E1")


def claim(
    text: str,
    *,
    target: Target,
    eid: EvidenceId | None = _FIRST_EID,
    label: ClaimLabel = ClaimLabel.CONSTRAINT,
) -> Claim:
    return Claim(
        claim_id="c1",
        text=text,
        label=label,
        target=target,
        raw_span=text,
        eid=eid,
    )


def round_with_claims(*claims: Claim) -> Round:
    return Round(
        round_id=ROUND_ID,
        project_id=PROJECT,
        user_input="社区中秋游园会",
        created_at=NOW,
        claims=tuple(claims),
    )


def document() -> Document:
    return document_with_feature()


def test_an_uncited_claim_lands_at_its_own_target_with_its_own_evidence():
    """E6 那条：去处是主张自己的去处（s1），文本一字不改，署自己的编号。"""

    landed = assembly.landed_claim_proposals(
        round_with_claims(
            claim("必须 9 月 20 号之前办完", target=Target.body("s1"))
        ),
        document=document(),
    )

    assert len(landed.proposals) == 1
    proposal = landed.proposals[0]
    assert proposal.op is WriteOp.ADD
    assert proposal.target == Target.body("s1")
    assert proposal.text == "必须 9 月 20 号之前办完"
    assert proposal.origin.refs == (EvidenceId("E1"),)
    assert proposal.origin.type.value == "user"
    assert "机械落地" in proposal.origin.rationale
    assert landed.notes == ()


def test_a_cited_claim_is_not_landed_twice():
    """整合者引用了这条主张 ⇒ 它自己会写进文档，机械通道不许再落一条。"""

    landed = assembly.landed_claim_proposals(
        round_with_claims(
            claim("必须 9 月 20 号之前办完", target=Target.body("s1"))
        ),
        document=document(),
        cited=frozenset({EvidenceId("E1")}),
    )

    assert landed.proposals == ()
    assert landed.notes == ()


def test_a_claim_without_evidence_is_not_landed():
    """认不出编号就没法署名（v1 到不了：Assigner 逐条登记；与答案同 guard）。"""

    landed = assembly.landed_claim_proposals(
        round_with_claims(
            claim("必须 9 月 20 号之前办完", target=Target.body("s1"), eid=None)
        ),
        document=document(),
    )

    assert landed.proposals == ()


def test_a_claim_about_an_unauthorized_new_feature_is_not_landed():
    """去处是没授权的功能 slug——落下去等于凭空建功能子树，撞硬约束（与答案同 guard）。"""

    landed = assembly.landed_claim_proposals(
        round_with_claims(
            claim(
                "给没授权的功能加个导出",
                target=Target.feature_field(
                    FeatureSlug("ghost"), "one_liner", home="s5"
                ),
                label=ClaimLabel.NEED,
            )
        ),
        document=document(),
    )

    assert landed.proposals == ()
    assert len(landed.notes) == 1
    assert "还没被授权" in landed.notes[0]


def test_a_claim_whose_target_is_illegal_is_skipped_with_a_note():
    """功能字段写错（`bogus-field`）时**不许把整轮打崩**：跳过 + 留一句话。

    `Target` 对功能字段只校验"非空"（与答案测试同构），所以这种主张真的存在；
    而这段落地代码跑在事务里，抛出去就是 500。
    """

    from pmstudio.domain.shared.ids import FeatureSlug
    from tests.support.builders import STD

    target = Target.feature_field(FeatureSlug("order-export"), "bogus-field", home=STD.feature_home)

    landed = assembly.landed_claim_proposals(
        round_with_claims(
            claim("写到不存在的字段", target=target)
        ),
        document=document(),
    )

    assert landed.proposals == ()
    assert len(landed.notes) == 1
    assert "bogus-field" in landed.notes[0]


def test_an_uncited_preference_claim_lands_at_preference():
    """偏好主张不特殊排除：偏好提案走同一条落库路径（`02` §11.0.71 钉住这个决定）。"""

    landed = assembly.landed_claim_proposals(
        round_with_claims(
            claim("家属希望有电话订餐", target=Target.preference(), label=ClaimLabel.PREFERENCE)
        ),
        document=document(),
    )

    assert len(landed.proposals) == 1
    assert landed.proposals[0].target == Target.preference()
    assert landed.proposals[0].origin.refs == (EvidenceId("E1"),)


def test_proposal_ids_continue_from_start_index():
    """与答案落点同计数空间（调用位用 `_next_proposal_index` 续号，不撞号）。"""

    landed = assembly.landed_claim_proposals(
        round_with_claims(
            claim("第一条", target=Target.body("s1")),
            claim("第二条", target=Target.body("s2")),
        ),
        document=document(),
        start_index=5,
    )

    assert [proposal.proposal_id for proposal in landed.proposals] == ["p05", "p06"]
