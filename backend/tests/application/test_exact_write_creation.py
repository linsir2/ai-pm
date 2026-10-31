import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from pmstudio.application import assembly
from pmstudio.application.agents import schemas
from pmstudio.domain.authoring.blocks import Entry
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import DraftId, EntryId, EvidenceId, ProposalId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.harness.errors import PacketParseError
from tests.application.test_assembly import ledger_with_two_entries
from tests.domain.test_exact_write_locations import proposal, with_blocks, write
from tests.support.builders import ROUND_ID, STD, document_with_feature, make_document, make_round


def wire(**changes):
    result = dict(op="add", entry_id=None, target={"section_id": "s1"},
                  text="新增整句", evidence_refs=["E1"])
    result.update(changes)
    return result


def parse(*items):
    return schemas.parse_packet_lenient(json.dumps({"proposals": items}),
                                       schemas.IntegrationPacket, purpose="integration")[0]


def assemble(document, *items):
    return assembly.assemble(parse(*items), round_=make_round(), document=document,
                             ledger=ledger_with_two_entries())


@pytest.mark.parametrize("field,value,omit", [
    ("op", None, True), ("entry_id", None, True), ("op", None, False),
    ("op", "update", False), ("entry_id", "old", False),
])
def test_control_field_errors_reach_packet_reload_with_precise_path(field, value, omit):
    item = wire()
    if omit:
        del item[field]
    else:
        item[field] = value
    with pytest.raises(PacketParseError) as caught:
        parse(item)
    assert ("proposals", 0, field) in [e["loc"] for e in caught.value.details["errors"]]


@pytest.mark.parametrize("op", ["replace", "remove"])
@pytest.mark.parametrize("entry_id", [None, "", "  "])
def test_modify_operations_require_nonempty_id_at_boundary(op, entry_id):
    with pytest.raises(PacketParseError) as caught:
        parse(wire(op=op, entry_id=entry_id))
    assert ("proposals", 0, "entry_id") in [e["loc"] for e in caught.value.details["errors"]]


def test_one_schema_has_required_control_fields_and_nullable_add_id():
    schema = schemas.ProposalDraft.model_json_schema()
    assert {"op", "entry_id", "target", "text", "evidence_refs"} <= set(schema["required"])
    assert "default" not in schema["properties"]["op"]
    assert "default" not in schema["properties"]["entry_id"]
    assert parse(wire()).proposals[0].entry_id is None
    for op in ("replace", "remove"):
        assert parse(wire(op=op, entry_id="old", text="")).proposals[0].op == op
    with pytest.raises(ValidationError):
        schemas.ProposalDraft.model_validate(wire(entry_id="old"))


def test_normalize_rules_still_discard_placeholders_and_fill_optional_nulls():
    packet, _, discarded = schemas.parse_packet_lenient(
        json.dumps({"proposals": ["占位符", wire(origin_type=None)]}),
        schemas.IntegrationPacket, purpose="integration",
    )
    assert len(discarded) == 1
    assert packet.proposals[0].op == "add"
    assert packet.proposals[0].origin_type == "derived"


def test_empty_proposal_address_is_dropped_while_shared_question_default_survives():
    result = assemble(make_document(), wire(target={}), wire())
    assert [p.target.label() for p in result.proposals] == ["s1"]
    assert len(result.dropped) == 1
    assert "target" in result.dropped[0].reason
    assert schemas.target_of(schemas.TargetDraft(), adapter=STD).label() == "s5"


@pytest.mark.parametrize("op", ["replace", "remove"])
@pytest.mark.parametrize("target", [
    {"section_id": "s2"},
    {"section_id": "s5", "feature_slug": "order-export", "field": "acceptance"},
    {"zone": "preference"},
    {"section_id": "s2", "feature_slug": "order-export", "field": "one_liner"},
])
def test_model_target_id_mismatch_drops_only_bad_item_with_trace(op, target):
    result = assemble(document_with_feature(),
                      wire(op=op, entry_id="en-bootstrap", target=target), wire())
    assert [str(p.proposal_id) for p in result.proposals] == ["p02"]
    assert len(result.dropped) == 1
    assert result.dropped[0].handle == "提案 #1"


@pytest.mark.parametrize("text", ["", "任意误导文案（E2）"])
def test_remove_always_uses_real_old_text_and_actual_kind(text):
    document = document_with_feature()
    result = assemble(document, wire(op="remove", entry_id="en-bootstrap", text=text,
                      target={"section_id": "s5", "feature_slug": "order-export",
                              "field": "one_liner"}, kind="risk", rationale="撤下原因"))
    removed = result.proposals[0]
    assert removed.text == "把订单导出成表格"
    assert removed.kind is BlockKind.TEXT
    assert removed.origin.rationale == "撤下原因"
    assert removed.base_version_no == 2


def test_replace_card_kind_is_actual_block_not_requested_new_kind():
    document = make_document()
    write(document, "old", Target.body("s1"), "现有正文", kind=BlockKind.RISK)
    result = assemble(document, wire(op="replace", entry_id="old", kind="text"))
    assert result.proposals[0].kind is BlockKind.RISK


def test_single_add_converts_to_exact_replace_and_keeps_proposal_payload():
    document = document_with_feature()
    old = proposal(target=Target.body("s5", feature_slug="order-export", field="one_liner"),
                   text="新的整句", kind=BlockKind.RISK, overlap="3/4")
    normalized = assembly.normalize_proposal(old, document)
    assert normalized.op is WriteOp.REPLACE
    assert str(normalized.entry_id) == "en-bootstrap"
    assert normalized.proposal_id == old.proposal_id
    assert normalized.text == old.text
    assert normalized.origin is old.origin
    assert normalized.kind is BlockKind.TEXT
    assert normalized.overlap is None
    assert normalized.base_version_no == 2
    assert old.op is WriteOp.ADD
    assert document.current_version.version_no == 2


@pytest.mark.parametrize("target", [
    Target.body("s5", feature_slug="order-export", field="priority"),
    Target.body("s5", feature_slug="order-export", field="main_flow"),
    Target.body("s1"), Target.body("s8", field="cost"), Target.preference(),
])
def test_empty_single_or_true_append_is_not_converted(target):
    document = document_with_feature()
    if target.field != "priority":
        write(document, "other", target, "现有内容")
    result = assembly.normalize_proposal(proposal(target=target), document)
    assert result.op is WriteOp.ADD
    assert str(result.entry_id) == "new"
    assert result.base_version_no == document.current_version.version_no


def test_corrupt_single_with_multiple_entries_is_not_arbitrarily_upserted():
    document = document_with_feature()
    blocks = tuple(replace(b, entries=b.entries + (
        Entry(EntryId("second"), "另一个值", (EvidenceId("E1"),)),
    )) if b.entries else b for b in document.blocks)
    document = with_blocks(document, blocks)
    target = Target.body("s5", feature_slug="order-export", field="one_liner")
    with pytest.raises(ShapeError):
        assembly.normalize_proposal(proposal(target=target), document)
    result = assemble(document, wire(target={"section_id": "s5",
                      "feature_slug": "order-export", "field": "one_liner"}), wire())
    assert len(result.proposals) == 1
    assert len(result.dropped) == 1


@pytest.mark.parametrize("collision", [False, True])
def test_ai_remove_cannot_use_block_id_to_delete_subtree(collision):
    document = document_with_feature()
    if collision:
        write(document, "feat-order-export", Target.body("s1"), "撞块编号的条目")
    result = assemble(document, wire(op="remove", entry_id="feat-order-export",
                      target={"section_id": "s1"}), wire(text="安全内容"))
    assert len(result.proposals) == 1
    assert result.proposals[0].text == "安全内容"
    assert len(result.dropped) == 1
    assert document.feature("order-export") is not None


def test_ambiguous_entry_is_dropped_even_when_first_hit_matches_target():
    document = document_with_feature()
    duplicate = Entry(EntryId("en-bootstrap"), "偏好", (EvidenceId("E1"),))
    document = with_blocks(document, document.blocks, (duplicate,))
    result = assemble(document, wire(op="replace", entry_id="en-bootstrap",
                      target={"section_id": "s5", "feature_slug": "order-export",
                              "field": "one_liner"}), wire())
    assert len(result.proposals) == 1
    assert len(result.dropped) == 1


def test_real_slot_tilde2_and_preference_addresses_are_used_for_binding_and_dedup():
    document = make_document()
    write(document, "s8.cost", Target.body("s8", field="cost"), "删槽", op=WriteOp.REMOVE)
    write(document, "cost1", Target.body("s8", field="cost"), "一个月一百元")
    write(document, "pref1", Target.preference(), "短句")
    result = assemble(document,
        wire(target={"section_id": "s8", "field": "cost"}, text="一个月一百元（E1）"),
        wire(op="replace", entry_id="cost1", target={"section_id": "s8", "field": "cost"}),
        wire(op="remove", entry_id="pref1", target={"zone": "preference"}, text="误导"),
        wire(op="remove", entry_id="pref1", target={"section_id": "s1"}),
    )
    assert [str(p.proposal_id) for p in result.proposals] == ["p02", "p03"]
    assert result.proposals[0].target.label() == "s8.cost"
    assert result.proposals[1].text == "短句"
    assert result.proposals[1].kind is None
    assert len(result.dropped) == 1
    assert ("s8.cost", "一个月一百元") in assembly._existing_content(document)


def test_e_cleaning_precedes_exact_dedup_but_new_multiline_append_keeps_overlap():
    document = document_with_feature()
    write(document, "flow", Target.body("s5", feature_slug="order-export", field="main_flow"),
          "第一步\n第二步\n第三步")
    target = {"section_id": "s5", "feature_slug": "order-export", "field": "main_flow"}
    result = assemble(document,
        wire(target=target, text="第一步（E1）\n第二步\n第三步"),
        wire(target=target, text="第一步（E1）\n第二步\n第三步\n第四步"),
        wire(target=target, text="相似但并非同文"),
    )
    assert [str(p.proposal_id) for p in result.proposals] == ["p02", "p03"]
    assert result.proposals[0].op is WriteOp.ADD
    assert result.proposals[0].overlap == "3/4"
    assert result.proposals[1].overlap is None
    assert result.dropped == ()
    assert all(p.base_version_no == 3 for p in result.proposals)


def test_already_stamped_proposal_is_preserved_entirely_against_new_input():
    document = document_with_feature()
    stamped = proposal(base_version_no=1, overlap="3/4", kind=BlockKind.RISK,
                       target=Target.body("s5", feature_slug="order-export", field="one_liner"))
    assert assembly.normalize_proposal(stamped, document) is stamped


def test_draft_creation_keeps_different_baseline_removes_and_model_stamp():
    items = tuple(proposal(proposal_id=ProposalId(f"p{i}"), op=WriteOp.REMOVE,
                           entry_id=EntryId("old"), base_version_no=base)
                  for i, base in enumerate((2, 3, 2), 1))
    draft = assembly.draft_of(ROUND_ID, DraftId("d1"), items, ())
    assert draft.proposals == items[:2]


@pytest.mark.parametrize("op", ["replace", "remove"])
def test_wrong_new_feature_address_is_dropped_not_turned_into_naming_question(op):
    result = assemble(document_with_feature(), wire(
        op=op, entry_id="en-bootstrap", target={"section_id": "s5",
        "feature_slug": "nonexistent", "field": "one_liner"},
    ), wire())
    assert [str(p.proposal_id) for p in result.proposals] == ["p02"]
    assert len(result.dropped) == 1
    assert result.questions == ()


def test_replace_uses_actual_kind_even_when_model_kind_is_unknown():
    document = make_document()
    write(document, "old", Target.body("s1"), "现有正文", kind=BlockKind.RISK)
    result = assemble(document, wire(op="replace", entry_id="old", kind="not-a-kind"))
    assert result.proposals[0].kind is BlockKind.RISK


def test_different_baseline_remove_does_not_inherit_other_override_at_creation():
    first = proposal(op=WriteOp.REMOVE, base_version_no=2)
    second = replace(first, proposal_id=ProposalId("p2"), base_version_no=3,
                     allow_manual_override=True)
    draft = assembly.draft_of(ROUND_ID, DraftId("d1"), (first, second), ())
    assert draft.proposals == (first, second)
    assert not draft.proposals[0].allow_manual_override


@pytest.mark.parametrize("slug", ["order-export", "new-feature"], ids=["existing", "new"])
def test_unknown_feature_field_drops_only_bad_add_without_a_naming_question(slug):
    result = assemble(document_with_feature(),
        wire(target={"section_id": "s5", "feature_slug": slug, "field": "not-a-field"},
             text="新增内容"),
        wire(text="合法内容"),
    )

    assert [
        (str(p.proposal_id), p.op, p.target.label(), p.text, p.origin.refs, p.base_version_no)
        for p in result.proposals
    ] == [("p02", WriteOp.ADD, "s1", "合法内容", (EvidenceId("E1"),), 2)]
    assert result.questions == ()
    assert len(result.dropped) == 1
    assert result.dropped[0].handle == "提案 #1"
    assert result.dropped[0].source.value == "assembly"
    assert "去处不合法" in result.dropped[0].reason
    assert "not-a-field" in result.dropped[0].reason
    assert slug in result.dropped[0].reason


def test_unknown_refs_with_valid_feature_target_still_fail_the_whole_packet():
    with pytest.raises(PacketParseError) as caught:
        assemble(document_with_feature(), wire(text="合法内容"),
            wire(target={"section_id": "s5", "feature_slug": "order-export",
                         "field": "acceptance"}, evidence_refs=["E999"]),
        )

    assert caught.value.code == "model_packet_unparsable"
    assert caught.value.purpose == "integration"
    assert "E999" in caught.value.message
