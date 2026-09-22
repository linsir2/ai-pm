"""块与条目。条目是最小可引用单位：每条内容都带着自己的出处编号。"""

from collections.abc import Iterable
from dataclasses import dataclass, replace

from pmstudio.domain.authoring.schema import FieldSpec
from pmstudio.domain.shared.errors import (
    ContentWithoutEvidence,
    DuplicateFieldName,
    ShapeError,
)
from pmstudio.domain.shared.ids import BlockId, EntryId, EvidenceId, FeatureSlug, SectionId
from pmstudio.domain.shared.writing import BlockKind


@dataclass(frozen=True, slots=True)
class Entry:
    entry_id: EntryId
    text: str
    refs: tuple[EvidenceId, ...] = ()

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ShapeError("条目内容不能为空", entry_id=str(self.entry_id))
        if not self.refs:
            raise ContentWithoutEvidence(what=f"条目 {self.entry_id}")


@dataclass(frozen=True, slots=True)
class Block:
    block_id: BlockId
    parent_id: BlockId | None
    kind: BlockKind
    entries: tuple[Entry, ...] = ()
    field: str | None = None
    feature_slug: FeatureSlug | None = None
    manually_edited: bool = False

    @classmethod
    def section(cls, section_id: SectionId | str) -> "Block":
        return cls(block_id=BlockId(str(section_id)), parent_id=None, kind=BlockKind.SECTION)

    @classmethod
    def feature_root(cls, *, block_id: BlockId, slug: FeatureSlug) -> "Block":
        return cls(
            block_id=block_id,
            parent_id=BlockId("s5"),
            kind=BlockKind.FEATURE,
            feature_slug=slug,
        )

    @classmethod
    def field_block(
        cls,
        *,
        block_id: BlockId,
        parent_id: BlockId,
        spec: FieldSpec,
        feature_slug: FeatureSlug,
    ) -> "Block":
        return cls(
            block_id=block_id,
            parent_id=parent_id,
            kind=spec.kind,
            field=spec.name,
            feature_slug=feature_slug,
        )

    @classmethod
    def section_content(
        cls,
        *,
        block_id: BlockId,
        section_id: SectionId | str,
        kind: BlockKind,
        entries: tuple[Entry, ...] = (),
    ) -> "Block":
        return cls(
            block_id=block_id,
            parent_id=BlockId(str(section_id)),
            kind=kind,
            entries=entries,
        )

    def with_entries(self, entries: Iterable[Entry]) -> "Block":
        return replace(self, entries=tuple(entries))

    def entry(self, entry_id: EntryId) -> Entry | None:
        for entry in self.entries:
            if entry.entry_id == entry_id:
                return entry
        return None


def assert_unique_fields(blocks: Iterable[Block]) -> None:
    """D4：同一父节点下字段名唯一（只有带字段名的块参与判定）。"""

    seen: dict[tuple[BlockId | None, str], BlockId] = {}
    for block in blocks:
        if block.field is None:
            continue
        key = (block.parent_id, block.field)
        if key in seen:
            raise DuplicateFieldName(
                field=block.field,
                parent_id=str(block.parent_id),
                first=str(seen[key]),
                second=str(block.block_id),
            )
        seen[key] = block.block_id
