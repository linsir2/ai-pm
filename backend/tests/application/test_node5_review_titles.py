"""节点5：改/删的提案若用户这轮没提它的真实去处标题，机械标记待过目。

判据是纯字符串包含：标题（FieldSpec / 槽 / 章节的既有标题）或字段名出现在
``round.user_input`` 里就不标记；找不到标题时保守不标记。
"""

import pytest

from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import (
    DraftId,
    EntryId,
    EvidenceId,
    ProposalId,
    RoundId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.domain.test_exact_write_guards import apply, instruction
from tests.support.builders import (
    NOW,
    PROJECT,
    document_with_feature,
    make_claim,
    make_seat,
)
from tests.support.harness import Harness

FLOW = Target.body("s5", feature_slug="order-export", field="main_flow")
ONE_LINER = Target.body("s5", feature_slug="order-export", field="one_liner")
RULES = Target.body("s5", feature_slug="order-export", field="rules")
EXCEPTIONS = Target.body("s5", feature_slug="order-export", field="exceptions")
PREFERENCE = Target.preference()
S6 = Target.body("s6")
REFS = (EvidenceId("E1"),)


def _document():
    document = document_with_feature()
    document.apply(
        (
            WriteInstruction(
                "flow-1", EntryId("flow-1"), WriteOp.ADD, FLOW,
                "1. 筛选最近7天的订单", REFS,
            ),
            WriteInstruction(
                "rules-1", EntryId("rules-1"), WriteOp.ADD, RULES,
                "导出文件保留7天", REFS,
            ),
            WriteInstruction(
                "pref-1", EntryId("pref-1"), WriteOp.ADD, PREFERENCE,
                "文档要简洁", REFS,
            ),
        ),
        now=NOW,
        summary="节点5夹具",
    )
    return document


def _proposal(
    proposal_id: str,
    entry_id: str,
    target: Target,
    *,
    op: WriteOp = WriteOp.REPLACE,
    text: str = "改后的正文",
    base: int | None = 3,
    origin_type: OriginType = OriginType.USER,
    kind: BlockKind | None = None,
) -> Proposal:
    return Proposal(
        ProposalId(proposal_id),
        EntryId(entry_id),
        op,
        target,
        text,
        Origin(origin_type, REFS),
        kind=kind,
        base_version_no=base,
    )


async def _card(proposals, user_input: str, document=None, claim_target=None):
    harness = Harness(replies={})
    harness.seed_project()
    harness.documents.seed(document or _document())
    round_ = Round.start(
        round_id=RoundId("r-node5"),
        project_id=PROJECT,
        user_input=user_input,
        now=NOW,
    )
    round_.attach_claims(
        (make_claim(eid=EvidenceId("E1"), target=claim_target),)
    )
    round_.submit_opening(seats=(make_seat().seat_id,))
    round_.integrate(
        draft=Draft(DraftId("d-node5"), round_.round_id, tuple(proposals))
    )
    harness.rounds.seed(round_)
    view = await harness.get_round()(round_id=round_.round_id)
    return view.draft.proposals


async def test_mentioned_main_flow_title_is_not_flagged_by_this_rule():
    cards = await _card(
        [_proposal("p1", "flow-1", FLOW)],
        "把主流程的第一步改成最近30天",
    )
    assert cards[0].requires_review is False
    assert "这轮输入没有提到" not in (cards[0].warning or "")


async def test_unmentioned_one_liner_is_flagged_with_title_and_entry_id():
    cards = await _card(
        [_proposal("p1", "en-bootstrap", ONE_LINER)],
        "把主流程的第一步改成最近30天",
    )
    card = cards[0]
    assert card.requires_review is True
    assert "一句话描述" in card.warning
    assert "en-bootstrap" in card.warning
    assert "这轮输入没有提到" in card.warning


async def test_add_is_not_flagged_for_an_unmentioned_title():
    cards = await _card(
        [_proposal("p1", "new", FLOW, op=WriteOp.ADD, text="2. 新增一步")],
        "把主流程的第一步改成最近30天",
    )
    assert cards[0].requires_review is False
    assert "这轮输入没有提到" not in (cards[0].warning or "")


async def test_remove_is_flagged_when_its_title_is_unmentioned():
    cards = await _card(
        [_proposal("p1", "rules-1", RULES, op=WriteOp.REMOVE, text="导出文件保留7天")],
        "把主流程的第一步改成最近30天",
    )
    card = cards[0]
    assert card.requires_review is True
    assert "状态与规则" in card.warning
    assert "rules-1" in card.warning


async def test_preference_zone_uses_the_preference_title():
    cards = await _card(
        [_proposal("p1", "pref-1", PREFERENCE, op=WriteOp.REMOVE, text="文档要简洁")],
        "把主流程的第一步改成最近30天",
    )
    card = cards[0]
    assert card.requires_review is True
    assert "偏好" in card.warning
    assert "pref-1" in card.warning


@pytest.mark.parametrize("mode", ["stale", "competition"])
async def test_existing_reason_survives_and_new_reason_comes_after(mode):
    document = None
    if mode == "stale":
        document = _document()
        apply(
            document,
            instruction("en-bootstrap", "别人先改了", target=ONE_LINER, base=3),
        )
        proposals = [_proposal("p1", "en-bootstrap", ONE_LINER)]
        existing = "proposal_stale"
    else:
        proposals = [
            _proposal("p1", "en-bootstrap", ONE_LINER),
            _proposal("p2", "en-bootstrap", ONE_LINER, text="另一种改法"),
        ]
        existing = "同一条目有竞争动作"
    cards = await _card(
        proposals, "把主流程的第一步改成最近30天", document=document,
    )
    for card in cards:
        assert card.requires_review is True
        assert existing in card.warning
        assert "这轮输入没有提到" in card.warning
        assert card.warning.index(existing) < card.warning.index("这轮输入没有提到")


async def test_multiple_titles_mentioned_in_one_input_are_all_exempt():
    cards = await _card(
        [
            _proposal("p1", "en-bootstrap", ONE_LINER),
            _proposal("p2", "flow-1", FLOW),
        ],
        "把一句话描述和主流程都改一下",
    )
    for card in cards:
        assert card.requires_review is False
        assert "这轮输入没有提到" not in (card.warning or "")


async def test_out_of_scope_add_requires_review_and_keeps_scope_warning():
    cards = await _card(
        [_proposal("p1", "new-s6", Target.body("s6"), op=WriteOp.ADD,
                   text="本轮没涉及的新章节内容")],
        "把主流程的第一步改成最近30天",
    )
    card = cards[0]
    assert card.requires_review is True
    assert "不在本轮去处里" in card.warning


async def test_in_scope_add_is_not_flagged_by_the_scope_rule():
    cards = await _card(
        [_proposal("p1", "new-exc", EXCEPTIONS, op=WriteOp.ADD, text="新增一条例外")],
        "把主流程的第一步改成最近30天",
    )
    card = cards[0]
    assert card.requires_review is False
    assert "不在本轮去处里" not in (card.warning or "")


async def test_same_label_normalized_equal_adds_all_require_review_with_ids():
    cards = await _card(
        [
            _proposal("p1", "new-a", EXCEPTIONS, op=WriteOp.ADD, text="  新增一条例外  "),
            _proposal("p2", "new-b", EXCEPTIONS, op=WriteOp.ADD, text="新增一条例外"),
        ],
        "把主流程的第一步改成最近30天",
    )
    for card in cards:
        assert card.requires_review is True
        assert "本批有 2 条几乎相同的新增（p1、p2）" in card.warning
        assert "全选会冲突；保留一条即可" in card.warning


async def test_same_label_different_text_adds_are_not_batch_duplicates():
    cards = await _card(
        [
            _proposal("p1", "new-a", EXCEPTIONS, op=WriteOp.ADD, text="新增甲"),
            _proposal("p2", "new-b", EXCEPTIONS, op=WriteOp.ADD, text="新增乙"),
        ],
        "把主流程的第一步改成最近30天",
    )
    for card in cards:
        assert card.requires_review is False
        assert "本批有" not in (card.warning or "")


async def test_same_text_at_different_labels_is_not_a_batch_duplicate():
    cards = await _card(
        [
            _proposal("p1", "new-flow", FLOW, op=WriteOp.ADD, text="同样一句新增"),
            _proposal("p2", "new-exc", EXCEPTIONS, op=WriteOp.ADD, text="同样一句新增"),
        ],
        "把主流程的第一步改成最近30天",
    )
    for card in cards:
        assert card.requires_review is False
        assert "本批有" not in (card.warning or "")


async def test_existing_single_add_competition_survives_beside_batch_duplicate():
    name = Target.body("s5", feature_slug="order-export", field="name")
    cards = await _card(
        [
            _proposal("p1", "new-a", name, op=WriteOp.ADD, text="同一个名字"),
            _proposal("p2", "new-b", name, op=WriteOp.ADD, text="同一个名字"),
        ],
        "把主流程的第一步改成最近30天",
    )
    for card in cards:
        assert card.requires_review is True
        assert "single_add_competition" in card.warning
        assert "本批有 2 条几乎相同的新增（p1、p2）" in card.warning
        assert card.warning.index("single_add_competition") < card.warning.index("本批有")


@pytest.mark.parametrize("op", [WriteOp.REPLACE, WriteOp.REMOVE])
async def test_replace_or_remove_with_the_same_text_does_not_join_the_batch_group(op):
    cards = await _card(
        [
            _proposal("p1", "new-a", FLOW, op=WriteOp.ADD, text="同一句话"),
            _proposal("p2", "flow-1", FLOW, op=op, text="同一句话"),
        ],
        "把主流程改成同一句话",
    )
    for card in cards:
        assert "本批有" not in (card.warning or "")


def _document_with_existing_field(target: Target = EXCEPTIONS):
    document = _document()
    apply(document, instruction("existing", "已有内容", target=target, base=3))
    return document


async def test_add_to_existing_field_with_unmentioned_title_is_flagged():
    document = _document_with_existing_field()
    cards = await _card(
        [_proposal("p1", "new", EXCEPTIONS, op=WriteOp.ADD,
                   text="新增一条例外", base=4)],
        "把主流程的第一步改成最近30天",
        document=document,
    )
    card = cards[0]
    assert card.requires_review is True
    assert "异常与边界" in card.warning
    assert "这轮输入没有提到" in card.warning


async def test_add_to_empty_field_with_unmentioned_title_is_not_flagged():
    cards = await _card(
        [_proposal("p1", "new", EXCEPTIONS, op=WriteOp.ADD, text="新增一条例外")],
        "把主流程的第一步改成最近30天",
    )
    card = cards[0]
    assert card.requires_review is False
    assert "这轮输入没有提到" not in (card.warning or "")


async def test_system_default_add_is_not_flagged_even_when_field_has_content():
    document = _document_with_existing_field()
    cards = await _card(
        [_proposal("p1", "new", EXCEPTIONS, op=WriteOp.ADD,
                   text="系统生成的一条新增", base=4,
                   origin_type=OriginType.SYSTEM_DEFAULT)],
        "把主流程的第一步改成最近30天",
        document=document,
        claim_target=S6,
    )
    card = cards[0]
    assert card.requires_review is False
    assert "这轮输入没有提到" not in (card.warning or "")


async def test_add_reason_is_appended_after_batch_duplicate_reason():
    document = _document_with_existing_field()
    cards = await _card(
        [
            _proposal("p1", "new-a", EXCEPTIONS, op=WriteOp.ADD,
                      text="新增一条例外", base=4),
            _proposal("p2", "new-b", EXCEPTIONS, op=WriteOp.ADD,
                      text="新增一条例外", base=4),
        ],
        "把主流程的第一步改成最近30天",
        document=document,
    )
    for card in cards:
        assert card.requires_review is True
        assert "本批有 2 条几乎相同的新增" in card.warning
        assert "这轮输入没有提到" in card.warning
        assert card.warning.index("本批有") < card.warning.index("这轮输入没有提到")


async def test_title_only_in_negative_clause_is_treated_as_unmentioned():
    cards = await _card(
        [_proposal("p1", "flow-1", FLOW)],
        "不要改动主流程，只改一句话描述",
    )
    card = cards[0]
    assert card.requires_review is True
    assert "主流程" in card.warning
    assert "这轮输入没有提到" in card.warning


async def test_title_in_positive_clause_is_not_treated_as_unmentioned():
    cards = await _card(
        [_proposal("p1", "flow-1", FLOW)],
        "把主流程第一步改成最近30天",
    )
    assert cards[0].requires_review is False
    assert "这轮输入没有提到" not in (cards[0].warning or "")


async def test_negative_and_positive_title_mentions_keep_the_positive_one():
    cards = await _card(
        [_proposal("p1", "flow-1", FLOW)],
        "不要改动主流程；主流程第一步改成最近30天",
    )
    assert cards[0].requires_review is False
    assert "这轮输入没有提到" not in (cards[0].warning or "")


async def test_rules_add_matches_the_rule_fragment_of_the_title():
    document = _document_with_existing_field(RULES)
    cards = await _card(
        [_proposal("p1", "new-rule", RULES, op=WriteOp.ADD,
                   text="导出文件7天后自动删除", base=4)],
        "新增一条规则：导出文件7天后自动删除。",
        document=document,
    )
    card = cards[0]
    assert card.requires_review is False
    assert "这轮输入没有提到" not in (card.warning or "")


async def test_exceptions_add_with_no_exception_or_boundary_mention_is_flagged():
    document = _document_with_existing_field(EXCEPTIONS)
    cards = await _card(
        [_proposal("p1", "new-exc", EXCEPTIONS, op=WriteOp.ADD,
                   text="新增一条例外", base=4)],
        "新增一条规则：导出文件7天后自动删除。",
        document=document,
    )
    card = cards[0]
    assert card.requires_review is True
    assert "异常与边界" in card.warning
    assert "这轮输入没有提到" in card.warning


async def test_fragment_hit_inside_negative_context_is_still_flagged():
    document = _document_with_existing_field(RULES)
    cards = await _card(
        [_proposal("p1", "new-rule", RULES, op=WriteOp.ADD,
                   text="导出文件7天后自动删除", base=4)],
        "不要改规则，只改主流程。",
        document=document,
    )
    card = cards[0]
    assert card.requires_review is True
    assert "状态与规则" in card.warning
    assert "这轮输入没有提到" in card.warning


def _document_with_s6(kind: BlockKind, text: str):
    document = _document()
    document.apply(
        (
            WriteInstruction(
                "s6-existing", EntryId("s6-existing"), WriteOp.ADD, S6,
                text, REFS, kind=kind, base_version_no=3,
            ),
        ),
        now=NOW,
        summary="s6 夹具",
    )
    return document


async def test_steps_add_to_nonempty_steps_s6_is_flagged_when_title_unmentioned():
    document = _document_with_s6(BlockKind.STEPS, "1. 已有步骤")
    cards = await _card(
        [_proposal("p1", "new-steps", S6, op=WriteOp.ADD,
                   text="1. 新步骤", base=4, kind=BlockKind.STEPS)],
        "把主流程的第一步改成最近30天",
        document=document,
        claim_target=S6,
    )
    card = cards[0]
    assert card.requires_review is True
    assert "业务流程与交互" in card.warning
    assert "这轮输入没有提到" in card.warning


async def test_steps_add_to_text_only_s6_is_not_flagged():
    document = _document_with_s6(BlockKind.TEXT, "已有正文")
    cards = await _card(
        [_proposal("p1", "new-steps", S6, op=WriteOp.ADD,
                   text="1. 新步骤", base=4, kind=BlockKind.STEPS)],
        "把主流程的第一步改成最近30天",
        document=document,
        claim_target=S6,
    )
    card = cards[0]
    assert card.requires_review is False
    assert "这轮输入没有提到" not in (card.warning or "")


async def test_chinese_word_qu_bie_does_not_count_as_negative_context():
    cards = await _card(
        [_proposal("p1", "flow-1", FLOW)],
        "区别主流程的说明",
    )
    card = cards[0]
    assert card.requires_review is False
    assert "这轮输入没有提到" not in (card.warning or "")
