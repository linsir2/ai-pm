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
    # 字段的中文名（功能字段或章节槽的标题）。真相源在后端的字段表/槽表，
    # 界面不再自己抄一份（`06-前端.md` §5 记的那处重复可以退休了）。
    field_title: str | None = None
    feature_slug: FeatureSlug | None = None
    manually_edited: bool = False
    # 这一块"能不能有多条"。单值字段（名称、一句话描述、优先级、用户故事）只能改写；
    # 其余（清单、验收标准、章节正文）可以加与删。来自字段表，界面据此决定给哪些按钮。
    multiple: bool = True


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
