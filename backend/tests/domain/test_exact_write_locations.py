from dataclasses import FrozenInstanceError, replace

import pytest

from pmstudio.domain.authoring.blocks import Block, Entry
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.round.draft import Origin, OriginType, Proposal, merge_duplicate_removes
from pmstudio.domain.services.committer import to_write_instruction
from pmstudio.domain.shared.errors import ShapeError, UnknownBlock, UnknownEntry, UnknownVersion
from pmstudio.domain.shared.ids import BlockId, EntryId, EvidenceId, ProposalId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import NOW, document_with_feature, make_document


def proposal(**changes) -> Proposal:
    values = dict(
        proposal_id=ProposalId("p1"), entry_id=EntryId("new"), op=WriteOp.ADD,
        target=Target.body("s1"), text="新增内容",
        origin=Origin(OriginType.USER, (EvidenceId("E1"),)),
    )
    values.update(changes)
    return Proposal(**values)


def write(document, entry_id, target, text, *, op=WriteOp.ADD, kind=None):
    document.apply(
        [WriteInstruction("i", EntryId(entry_id), op, target, text, (EvidenceId("E1"),), kind)],
        now=NOW, summary="测试写入",
    )


def with_blocks(document, blocks, preferences=()) -> Document:
    return Document(
        document_id=document.document_id, project_id=document.project_id,
        title=document.title, blocks=blocks, versions=document.versions,
        preference_entries=preferences, created_at=NOW, template=document.template,
    )


@pytest.mark.parametrize("base", [0, -1, True, 1.5, "2"])
@pytest.mark.parametrize("factory", [proposal, lambda **kw: to_write_instruction(proposal(**kw))])
def test_provided_baseline_is_a_positive_integer(base, factory):
    with pytest.raises(ShapeError) as caught:
        factory(base_version_no=base)
    assert caught.value.details["base_version_no"] == base


@pytest.mark.parametrize("base", [None, 1, 17])
def test_baseline_reaches_write_instruction_without_guessing(base):
    assert to_write_instruction(proposal(base_version_no=base)).base_version_no == base


def test_instruction_independently_validates_baseline():
    with pytest.raises(ShapeError):
        WriteInstruction("i", EntryId("e"), WriteOp.ADD, Target.body("s1"), "正文",
                         (EvidenceId("E1"),), base_version_no=False)


def test_historical_entry_location_reads_immutable_snapshot_and_actual_kind():
    document = document_with_feature()
    write(document, "en-bootstrap", Target.body("s5", feature_slug="order-export",
          field="one_liner"), "新的整句", op=WriteOp.REPLACE)
    old = document.locate_entry(EntryId("en-bootstrap"), version_no=2)
    current = document.locate_entry(EntryId("en-bootstrap"))
    assert old.entry.text == "把订单导出成表格"
    assert current.entry.text == "新的整句"
    assert old.target == Target.body("s5", feature_slug="order-export", field="one_liner")
    assert old.block.kind is BlockKind.TEXT
    assert document.target_of_block(old.block.block_id, version_no=2) == old.target
    with pytest.raises(FrozenInstanceError):
        old.target = Target.body("s2")
    with pytest.raises(UnknownVersion):
        document.locate_entry(EntryId("en-bootstrap"), version_no=99)
    with pytest.raises(UnknownEntry):
        document.locate_entry(EntryId("missing"))
    with pytest.raises(UnknownBlock):
        document.target_of_block(BlockId("missing"))


def test_tombstoned_slot_recreated_as_tilde2_keeps_logical_address():
    document = make_document()
    write(document, "s8.cost", Target.body("s8", field="cost"), "删槽", op=WriteOp.REMOVE)
    write(document, "cost1", Target.body("s8", field="cost"), "一个月一百元")
    location = document.locate_entry(EntryId("cost1"))
    assert str(location.block.block_id) == "s8.cost~2"
    assert location.target == Target.body("s8", field="cost")
    assert document.target_of_block(BlockId("s8.cost"), version_no=1) == location.target
    with pytest.raises(UnknownBlock):
        document.target_of_block(BlockId("s8.cost"))


def test_preference_location_has_no_block_and_reads_history():
    document = make_document()
    write(document, "pref1", Target.preference(), "短句")
    write(document, "pref1", Target.preference(), "长句", op=WriteOp.REPLACE)
    old = document.locate_entry(EntryId("pref1"), version_no=2)
    assert old.block is None
    assert old.target == Target.preference()
    assert old.entry.text == "短句"


@pytest.mark.parametrize("duplicate", ["same_block", "other_block", "preference"])
def test_entry_lookup_rejects_multiple_hits_instead_of_taking_first(duplicate):
    document = document_with_feature()
    blocks = list(document.blocks)
    old = next(b for b in blocks if b.entries)
    entry = Entry(EntryId("en-bootstrap"), "不同内容", (EvidenceId("E1"),))
    preferences = ()
    if duplicate == "same_block":
        blocks[blocks.index(old)] = replace(old, entries=old.entries + (entry,))
    elif duplicate == "other_block":
        blocks.append(Block.section_content(block_id=BlockId("s1.b1"), section_id="s1",
                                            kind=BlockKind.TEXT, entries=(entry,)))
    else:
        preferences = (entry,)
    ambiguous = with_blocks(document, tuple(blocks), preferences)
    with pytest.raises(ShapeError) as caught:
        ambiguous.locate_entry(EntryId("en-bootstrap"))
    assert caught.value.details["entry_id"] == "en-bootstrap"
    assert caught.value.details["reason"] == "ambiguous_entry"


def test_target_lookup_rejects_duplicate_blocks_in_historical_snapshot():
    document = make_document()
    snapshot = document.current_version.snapshot
    corrupted = replace(snapshot, blocks=snapshot.blocks + (snapshot.blocks[0],))
    document = with_blocks(document, document.blocks)
    document._versions = (replace(document.current_version, snapshot=corrupted),)
    with pytest.raises(ShapeError):
        document.target_of_block(BlockId("s1"), version_no=1)


def test_nested_entry_target_uses_real_field_and_feature_ancestors():
    document = document_with_feature()
    field = next(b for b in document.blocks if b.entries)
    nested = Block(block_id=BlockId("nested"), parent_id=field.block_id,
                   kind=BlockKind.TEXT,
                   entries=(Entry(EntryId("nested-entry"), "子内容", (EvidenceId("E1"),)),))
    document = with_blocks(document, document.blocks + (nested,))
    assert document.locate_entry(EntryId("nested-entry")).target == Target.body(
        "s5", feature_slug="order-export", field="one_liner",
    )


def test_remove_merging_creation_requires_same_baseline_but_confirmation_does_not():
    removes = tuple(proposal(proposal_id=ProposalId(f"p{i}"), op=WriteOp.REMOVE,
                             entry_id=EntryId("old"), base_version_no=base)
                    for i, base in enumerate((2, 3, 2, None, None), 1))
    kept, merged = merge_duplicate_removes(removes, same_baseline=True)
    assert [str(p.proposal_id) for p in kept] == ["p1", "p2", "p4"]
    assert [str(p) for p in merged] == ["p3", "p5"]
    kept, merged = merge_duplicate_removes(removes)
    assert kept == (removes[0],)
    assert len(merged) == 4
    replacements = tuple(replace(p, op=WriteOp.REPLACE) for p in removes)
    assert merge_duplicate_removes(replacements, same_baseline=True) == (replacements, ())
