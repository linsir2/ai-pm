import pytest

from pmstudio.domain.authoring.blocks import Block, Entry, assert_unique_fields
from pmstudio.domain.authoring.schema import (
    FEATURE_FIELDS,
    REQUIRED_FEATURE_FIELDS,
    field_spec,
)
from pmstudio.domain.shared.errors import (
    ContentWithoutEvidence,
    DuplicateFieldName,
    UnknownFeatureField,
)
from pmstudio.domain.shared.ids import BlockId, EntryId, EvidenceId, FeatureSlug
from pmstudio.domain.shared.invariants import DInvariant, GlobalInvariant
from pmstudio.domain.shared.writing import BlockKind

REF = EvidenceId("E1")


def test_GB1_ok_entry_with_refs():
    entry = Entry(entry_id=EntryId("en1"), text="运营每天都要用", refs=(REF,))
    assert entry.refs == (REF,)


def test_GB1_bad_entry_without_refs():
    with pytest.raises(ContentWithoutEvidence) as exc:
        Entry(entry_id=EntryId("en1"), text="运营每天都要用", refs=())
    assert exc.value.invariant == GlobalInvariant.GB1


def test_D4_ok_two_fields_under_one_feature():
    root = Block.feature_root(
        block_id=BlockId("feat-order-export"),
        slug=FeatureSlug("order-export"),
        parent=BlockId("s5"),
    )
    blocks = (
        root,
        Block.field_block(
            block_id=BlockId("feat-order-export.name"),
            parent_id=root.block_id,
            spec=field_spec("name"),
            feature_slug=FeatureSlug("order-export"),
        ),
        Block.field_block(
            block_id=BlockId("feat-order-export.priority"),
            parent_id=root.block_id,
            spec=field_spec("priority"),
            feature_slug=FeatureSlug("order-export"),
        ),
    )
    assert_unique_fields(blocks)


def test_D4_bad_duplicate_field_under_same_parent():
    root = Block.feature_root(
        block_id=BlockId("feat-order-export"),
        slug=FeatureSlug("order-export"),
        parent=BlockId("s5"),
    )
    field = {
        "parent_id": root.block_id,
        "spec": field_spec("one_liner"),
        "feature_slug": FeatureSlug("order-export"),
    }
    blocks = (
        root,
        Block.field_block(block_id=BlockId("feat-order-export.one_liner"), **field),
        Block.field_block(block_id=BlockId("feat-order-export.one_liner~2"), **field),
    )
    with pytest.raises(DuplicateFieldName) as exc:
        assert_unique_fields(blocks)
    assert exc.value.invariant == DInvariant.D4


def test_schema_has_the_twelve_fields_from_the_spec():
    assert [spec.name for spec in FEATURE_FIELDS] == [
        "name",
        "one_liner",
        "priority",
        "goal",
        "audience",
        "user_story",
        "main_flow",
        "exceptions",
        "io",
        "rules",
        "acceptance",
        "dependencies",
    ]


def test_schema_marks_required_fields_and_multi_value_fields():
    assert {spec.name for spec in REQUIRED_FEATURE_FIELDS} == {
        "name",
        "one_liner",
        "goal",
        "audience",
        "exceptions",
        "acceptance",
    }
    assert field_spec("acceptance").multiple is True
    assert field_spec("acceptance").kind is BlockKind.ACCEPTANCE
    assert field_spec("one_liner").multiple is False


def test_unknown_field_name_is_rejected():
    with pytest.raises(UnknownFeatureField):
        field_spec("whatever")


def test_section_root_and_section_content_blocks():
    root = Block.section(section_id="s5")
    assert root.block_id == BlockId("s5")
    assert root.parent_id is None
    entry = Entry(entry_id=EntryId("en1"), text="内容", refs=(REF,))
    content = Block.section_content(
        block_id=BlockId("s5.b1"), section_id="s5", kind=BlockKind.NFR, entries=(entry,)
    )
    assert content.parent_id == BlockId("s5")
    assert content.entry(EntryId("en1")) == entry
    assert content.with_entries(()).entries == ()
