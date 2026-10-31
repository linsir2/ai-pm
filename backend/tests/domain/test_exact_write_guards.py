from copy import copy
from dataclasses import replace

import pytest

from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.ids import BlockId, EntryId, EvidenceId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import NOW, document_with_feature, make_document

REFS = (EvidenceId("E1"),)
S1 = Target.body("s1")
SINGLE = Target.body("s5", feature_slug="order-export", field="one_liner")
MULTI = Target.body("s5", feature_slug="order-export", field="exceptions")


def instruction(entry="new", text="新增", *, target=S1, op=WriteOp.ADD, base=2,
                refs=REFS, override=False):
    return WriteInstruction(entry, EntryId(entry), op, target, text, refs,
                            allow_manual_override=override, base_version_no=base)


def apply(document, *instructions):
    document.apply(instructions, now=NOW, summary="测试变更")


def seeded(target=S1):
    document = make_document()
    apply(document, instruction("old", "A", target=target, base=1))
    return document


def changed_blocks(document, transform):
    return Document(document_id=document.document_id, project_id=document.project_id,
                    title=document.title, blocks=tuple(transform(b) for b in document.blocks),
                    versions=document.versions, preference_entries=document.preference_entries,
                    created_at=NOW, template=document.template)


def assert_conflict(document, instructions, code, reason=None):
    before = document.snapshot(), document.versions
    with pytest.raises(DomainError) as caught:
        document.assert_write_preconditions(instructions)
    assert caught.value.code == code
    assert caught.value.details["proposal_id"] == instructions[-1].instruction_id
    assert caught.value.details["entry_id"] == str(instructions[-1].entry_id)
    assert caught.value.details["base_version_no"] == instructions[-1].base_version_no
    assert caught.value.details["current_version_no"] == document.current_version.version_no
    if reason:
        assert caught.value.details["reason"] == reason
    assert (document.snapshot(), document.versions) == before


@pytest.mark.parametrize("base", [None, 99])
def test_missing_and_unknown_baselines_are_conflicts_not_current_signatures(base):
    assert_conflict(seeded(), [instruction(base=base)], "baseline_missing")


@pytest.mark.parametrize("target", [S1, Target.preference()])
@pytest.mark.parametrize("op", [WriteOp.REPLACE, WriteOp.REMOVE])
@pytest.mark.parametrize("change", ["text", "refs"])
def test_entry_text_and_refs_are_preconditions_in_body_and_preference(target, op, change):
    document = seeded(target)
    apply(document, instruction("old", "B" if change == "text" else "A", target=target,
          op=WriteOp.REPLACE, refs=(EvidenceId("E2"),) if change == "refs" else REFS))
    assert_conflict(document, [instruction("old", op=op, target=target)], "proposal_stale")


@pytest.mark.parametrize("override", [False, True])
def test_override_never_bypasses_a_changed_d5_baseline(override):
    document = seeded()
    document = changed_blocks(document, lambda b: replace(b, manually_edited=True)
                              if b.entries else b)
    assert_conflict(document, [instruction("old", op=WriteOp.REPLACE, override=override)],
                    "proposal_stale")


def test_original_d5_is_protected_but_explicit_override_can_apply():
    document = seeded()
    document = changed_blocks(document, lambda b: replace(b, manually_edited=True)
                              if b.entries else b)
    apply(document, instruction("unrelated", "背景", target=Target.body("s2")))
    protected = instruction("old", op=WriteOp.REPLACE, base=3)
    document.assert_write_preconditions([protected])
    with pytest.raises(DomainError) as caught:
        apply(copy(document), protected)
    assert caught.value.code == "invariant:D5"
    candidate = copy(document)
    apply(candidate, replace(protected, allow_manual_override=True))
    assert candidate.locate_entry(EntryId("old")).entry.text == "新增"


@pytest.mark.parametrize("op", [WriteOp.ADD, WriteOp.REPLACE, WriteOp.REMOVE])
def test_ancestor_structure_changes_are_stale_even_when_entry_is_unchanged(op):
    document = seeded()
    document = changed_blocks(document, lambda b: replace(b, manually_edited=True)
                              if str(b.block_id) == "s1" else b)
    assert_conflict(document, [instruction("new" if op is WriteOp.ADD else "old", op=op)],
                    "proposal_stale")


def test_same_field_other_entry_replace_does_not_block_append():
    document = document_with_feature()
    apply(document, instruction("a", "A", target=MULTI), instruction("b", "B", target=MULTI))
    pending = instruction("c", "C", target=MULTI, base=3)
    apply(document, instruction("b", "B2", target=MULTI, op=WriteOp.REPLACE, base=3))
    document.assert_write_preconditions([pending])
    apply(document, pending)
    document.assert_no_added_duplicates([pending])
    entries = document.field_block("order-export", "exceptions").entries
    assert [e.text for e in entries] == ["A", "B2", "C"]


def test_entry_and_structure_aba_restore_current_values_is_allowed():
    document = seeded()
    pending = instruction("old", "C", op=WriteOp.REPLACE)
    apply(document, instruction("old", "B", op=WriteOp.REPLACE))
    document.rollback_to(2, now=NOW, summary="完全恢复")
    document.assert_write_preconditions([pending])
    apply(document, pending)
    assert document.locate_entry(EntryId("old")).entry.text == "C"


@pytest.mark.parametrize("change", ["appeared", "parent_changed"])
def test_missing_target_compares_existing_ancestors_and_requires_regeneration(change):
    document = document_with_feature()
    pending = instruction(target=MULTI)
    if change == "appeared":
        apply(document, instruction("other", target=MULTI))
    else:
        document = changed_blocks(document, lambda b: replace(b, manually_edited=True)
                                  if b.kind is BlockKind.FEATURE else b)
    assert_conflict(document, [pending], "proposal_stale")


def test_two_empty_single_adds_conflict_before_any_candidate_can_overwrite():
    document = document_with_feature()
    empty = Target.body("s5", feature_slug="order-export", field="priority")
    first = instruction("a", "P1", target=empty)
    second = instruction("b", "P2", target=empty)
    document.assert_write_preconditions([first])
    document.assert_write_preconditions([second])
    assert_conflict(document, [first, second], "batch_conflict", "single_add_competition")


def test_single_add_becomes_stale_when_empty_field_is_filled():
    document = document_with_feature()
    empty = Target.body("s5", feature_slug="order-export", field="priority")
    pending = instruction(target=empty)
    apply(document, instruction("other", "P1", target=empty))
    assert_conflict(document, [pending], "proposal_stale")


@pytest.mark.parametrize("failure", ["unknown", "ambiguous", "block_collision", "wrong_target"])
def test_entry_address_cannot_silently_pick_first_or_delete_a_subtree(failure):
    document = seeded()
    entry = "old"
    target = S1
    if failure == "unknown":
        entry = "absent"
    elif failure == "block_collision":
        entry = "s1"
    elif failure == "wrong_target":
        target = Target.body("s2")
    else:
        block = next(b for b in document.blocks if b.entries)
        document = changed_blocks(document, lambda b: replace(b, entries=b.entries * 2)
                                  if b.block_id == block.block_id else b)
    assert_conflict(document, [instruction(entry, target=target, op=WriteOp.REMOVE)],
                    "proposal_stale")
    assert document.block(BlockId("s1")) is not None


def test_add_id_must_be_new_and_distinct_within_batch():
    document = seeded()
    assert_conflict(document, [instruction("old")], "proposal_stale")
    assert_conflict(document, [instruction(), instruction(text="另一个")], "batch_conflict")


@pytest.mark.parametrize("operations,allowed", [
    (("remove", "A"), True), (("replace", "A"), True),
    (("replace", "B"), False), (("none", " a \n"), False),
])
def test_only_final_added_duplicates_are_checked(operations, allowed):
    document = seeded()
    op, added_text = operations
    selected = []
    if op != "none":
        selected.append(instruction("old", "B", op=WriteOp(op)))
    selected.append(instruction("new", added_text))
    document.assert_write_preconditions(selected)
    candidate = copy(document)
    apply(candidate, *selected)
    if allowed:
        candidate.assert_no_added_duplicates(selected)
    else:
        with pytest.raises(DomainError) as caught:
            candidate.assert_no_added_duplicates(selected)
        assert caught.value.code == "batch_conflict"
        assert caught.value.details["reason"] == "added_duplicate"
    assert document.locate_entry(EntryId("old")).entry.text == "A"


def test_two_adds_same_normalized_text_conflict_but_different_targets_do_not():
    document = seeded()
    selected = [instruction("a", "B"), instruction("b", " b ")]
    document.assert_write_preconditions(selected)
    candidate = copy(document)
    apply(candidate, *selected)
    with pytest.raises(DomainError) as caught:
        candidate.assert_no_added_duplicates(selected)
    assert caught.value.code == "batch_conflict"
    selected[1] = replace(selected[1], target=Target.body("s2"))
    candidate = copy(document)
    apply(candidate, *selected)
    candidate.assert_no_added_duplicates(selected)


def test_old_duplicates_and_replace_semantics_are_not_cleaned_or_rejected():
    document = seeded()
    apply(document, instruction("duplicate", "A"))
    selected = [instruction("old", "A", op=WriteOp.REPLACE, base=3),
                instruction("new", "unique", base=3)]
    document.assert_write_preconditions(selected)
    apply(document, *selected)
    document.assert_no_added_duplicates(selected)
    assert sum(e.text == "A" for b in document.blocks for e in b.entries) == 2


def test_rebuilt_section_slot_has_same_logical_target_but_different_physical_baseline():
    document = make_document()
    target = Target.body("s8", field="cost")
    pending = instruction(target=target, base=1)
    apply(document, instruction("s8.cost", "移除槽", target=target, op=WriteOp.REMOVE, base=1))
    apply(document, instruction("other", "成本", target=target))
    assert_conflict(document, [pending], "proposal_stale")
