"""只读投影：界面只认这些结构，不认识聚合内部。"""

from dataclasses import dataclass

from pmstudio.domain.authoring.blocks import Entry
from pmstudio.domain.shared.ids import (
    BlockId,
    DocumentId,
    FeatureSlug,
    ProjectId,
    SectionId,
)
from pmstudio.domain.shared.writing import BlockKind


@dataclass(frozen=True, slots=True)
class Gap:
    feature_slug: FeatureSlug
    field: str
    title: str


@dataclass(frozen=True, slots=True)
class BlockView:
    block_id: BlockId
    kind: BlockKind
    entries: tuple[Entry, ...]
    children: tuple["BlockView", ...] = ()
    field: str | None = None
    feature_slug: FeatureSlug | None = None
    manually_edited: bool = False


@dataclass(frozen=True, slots=True)
class SectionView:
    section_id: SectionId
    title: str
    blocks: tuple[BlockView, ...]


@dataclass(frozen=True, slots=True)
class DocumentView:
    document_id: DocumentId
    project_id: ProjectId
    title: str
    version_no: int
    sections: tuple[SectionView, ...]
    preference_entries: tuple[Entry, ...] = ()
