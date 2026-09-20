"""DTO 映射测试：内部契约 → 前端 DTO。

只暴露前端需要的字段。内部实现细节（base_versions、dropped 等）不暴露。
"""

from datetime import UTC, datetime

from pmstudio.contracts.enums import (
    BlockOpKind,
    CardGroupState,
    CardKind,
    RoundEntry,
    RoundPhase,
)
from pmstudio.contracts.models.card import Card, CardOption, Proposal
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.contracts.models.document import Block, Document, DocumentSnapshot, DocumentVersion
from pmstudio.contracts.models.project import Project, ProjectConfig
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import RoundRegion
from pmstudio.contracts.skeleton.events import (
    DocChangedPayload,
    Event,
    EventType,
    RoundUpdatedPayload,
)
from web_api.dto import (
    block_to_dto,
    card_group_to_dto,
    card_to_dto,
    document_to_dto,
    event_to_dto,
    project_to_dto,
    round_to_dto,
    version_to_dto,
)


class TestProjectMapping:
    def test_project_to_dto(self) -> None:
        project = Project(
            project_id="prj_1",
            name="测试项目",
            template_id="reg_tpl_initial",
            config=ProjectConfig(output_reserve_tokens=4096, system_overhead_tokens=512),
            created_at=datetime(2026, 9, 18, 10, 0, tzinfo=UTC),
        )
        dto = project_to_dto(project)
        assert dto.project_id == "prj_1"
        assert dto.name == "测试项目"
        assert dto.template_id == "reg_tpl_initial"


class TestRoundMapping:
    def test_round_to_dto_with_card_group(self) -> None:
        round_region = RoundRegion(
            round_id="rnd_1",
            project_id="prj_1",
            entry=RoundEntry.MAIN,
            user_input="细化功能清单",
            scope=Scope(selected_fields=("功能清单",)),
            phase=RoundPhase.AWAITING_USER,
        )
        card_group = CardGroup(
            group_id="grp_1",
            round_id="rnd_1",
            cards=(
                Card(
                    card_id="crd_1",
                    kind=CardKind.UNDERSTANDING,
                    prompt="理解",
                ),
            ),
            state=CardGroupState.ANSWERING,
        )
        dto = round_to_dto(round_region, card_group)
        assert dto.round_id == "rnd_1"
        assert dto.phase == "awaiting_user"
        assert dto.selected_fields == ["功能清单"]
        assert dto.card_group is not None
        assert len(dto.card_group.cards) == 1

    def test_round_to_dto_without_card_group(self) -> None:
        round_region = RoundRegion(
            round_id="rnd_1",
            project_id="prj_1",
            entry=RoundEntry.MAIN,
            user_input="细化功能清单",
            scope=Scope(selected_fields=("功能清单",)),
            phase=RoundPhase.ASSEMBLING,
        )
        dto = round_to_dto(round_region, None)
        assert dto.card_group is None

    def test_round_dto_does_not_expose_base_versions(self) -> None:
        round_region = RoundRegion(
            round_id="rnd_1",
            project_id="prj_1",
            entry=RoundEntry.MAIN,
            user_input="test",
            scope=Scope(
                selected_fields=(),
                base_versions={"blk_1": 1},
            ),
            phase=RoundPhase.ASSEMBLING,
        )
        dto = round_to_dto(round_region, None)
        assert not hasattr(dto, "base_versions")

    def test_round_dto_exposes_end_reason_when_finished(self) -> None:
        round_region = RoundRegion(
            round_id="rnd_1",
            project_id="prj_1",
            entry=RoundEntry.MAIN,
            user_input="test",
            scope=Scope(),
            phase=RoundPhase.DONE,
            ended_at=datetime(2026, 9, 18, 10, 0, tzinfo=UTC),
            end_reason="completed",
        )
        dto = round_to_dto(round_region, None)
        assert dto.end_reason == "completed"


class TestCardMapping:
    def test_card_to_dto_understanding(self) -> None:
        card = Card(
            card_id="crd_1",
            kind=CardKind.UNDERSTANDING,
            prompt="理解",
        )
        dto = card_to_dto(card)
        assert dto.card_id == "crd_1"
        assert dto.kind == "understanding"
        assert dto.options is None
        assert dto.proposals is None

    def test_card_to_dto_conflict(self) -> None:
        card = Card(
            card_id="crd_1",
            kind=CardKind.CONFLICT,
            prompt="分歧",
            options=(
                CardOption(option_id="opt_1", text="方案A"),
            ),
        )
        dto = card_to_dto(card)
        assert dto.options is not None
        assert len(dto.options) == 1
        assert dto.options[0].text == "方案A"

    def test_card_to_dto_fill(self) -> None:
        card = Card(
            card_id="crd_1",
            kind=CardKind.FILL,
            prompt="填充",
            proposals=(
                Proposal(
                    proposal_id="prp_1",
                    target_label="功能清单",
                    op=BlockOpKind.APPEND,
                    content="内容",
                ),
            ),
        )
        dto = card_to_dto(card)
        assert dto.proposals is not None
        assert len(dto.proposals) == 1
        assert dto.proposals[0].target_label == "功能清单"


class TestCardGroupMapping:
    def test_card_group_to_dto(self) -> None:
        group = CardGroup(
            group_id="grp_1",
            round_id="rnd_1",
            cards=(
                Card(card_id="crd_1", kind=CardKind.UNDERSTANDING, prompt="理解"),
            ),
            state=CardGroupState.ANSWERING,
        )
        dto = card_group_to_dto(group)
        assert dto.group_id == "grp_1"
        assert dto.round_id == "rnd_1"
        assert dto.state == "answering"
        assert len(dto.cards) == 1


class TestDocumentMapping:
    def test_document_to_dto(self) -> None:
        doc = Document(
            doc_id="doc_1",
            project_id="prj_1",
            template_id="reg_tpl_initial",
        )
        blocks = [
            Block(block_id="blk_1", schema_label="功能清单", content="内容", version=1),
        ]
        dto = document_to_dto(doc, blocks)
        assert dto.doc_id == "doc_1"
        assert dto.project_id == "prj_1"
        assert len(dto.blocks) == 1
        assert dto.blocks[0].schema_label == "功能清单"

    def test_block_dto_does_not_expose_parent_id(self) -> None:
        block = Block(
            block_id="blk_1",
            schema_label="功能清单",
            content="内容",
            parent_id="blk_0",
        )
        dto = block_to_dto(block)
        assert not hasattr(dto, "parent_id")

    def test_block_dto_exposes_source_card_id(self) -> None:
        block = Block(
            block_id="blk_1",
            schema_label="功能清单",
            content="内容",
            version=2,
            source_card_id="crd_1",
        )
        dto = block_to_dto(block)
        assert dto.source_card_id == "crd_1"
        assert dto.version == 2


class TestEventMapping:
    def test_event_to_dto(self) -> None:
        event = Event(
            type=EventType.ROUND_UPDATED,
            payload=RoundUpdatedPayload(
                round_id="rnd_1",
                project_id="prj_1",
                entry=RoundEntry.MAIN,
                phase=RoundPhase.DONE,
                from_phase=RoundPhase.AWAITING_USER,
                end_reason="completed",
            ),
            at=datetime(2026, 9, 18, 10, 0, tzinfo=UTC),
        )
        dto = event_to_dto(event)
        assert dto.type == "round.updated"
        assert dto.round_id == "rnd_1"
        assert dto.at is not None

    def test_event_dto_round_id_from_payload(self) -> None:
        event = Event(
            type=EventType.DOC_CHANGED,
            payload=DocChangedPayload(
                doc_id="doc_1",
                version_id="ver_1",
                seq=1,
                trigger="submit",
                block_ids=("blk_1",),
                round_id="rnd_1",
            ),
            at=datetime(2026, 9, 18, 10, 0, tzinfo=UTC),
        )
        dto = event_to_dto(event)
        assert dto.round_id == "rnd_1"


class TestVersionMapping:
    def test_version_to_dto(self) -> None:
        version = DocumentVersion(
            version_id="ver_1",
            doc_id="doc_1",
            seq=1,
            trigger="submit",
            group_id="grp_1",
            snapshot=DocumentSnapshot(blocks=()),
        )
        dto = version_to_dto(version)
        assert dto.version_id == "ver_1"
        assert dto.seq == 1
        assert dto.trigger == "submit"
        assert dto.group_id == "grp_1"

    def test_version_dto_does_not_expose_snapshot(self) -> None:
        version = DocumentVersion(
            version_id="ver_1",
            doc_id="doc_1",
            seq=1,
            trigger="manual",
            snapshot=DocumentSnapshot(blocks=()),
        )
        dto = version_to_dto(version)
        assert not hasattr(dto, "snapshot")

    def test_version_dto_rollback_has_target(self) -> None:
        version = DocumentVersion(
            version_id="ver_2",
            doc_id="doc_1",
            seq=2,
            trigger="rollback",
            target_version_id="ver_1",
            snapshot=DocumentSnapshot(blocks=()),
        )
        dto = version_to_dto(version)
        assert dto.target_version_id == "ver_1"
