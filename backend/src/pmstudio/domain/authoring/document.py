"""Document 聚合：13 章骨架、block 树、版本快照、偏好区。

写入只走 apply()：先在候选状态上跑完全部指令，全部成功才换上——
所以任一条校验失败，文档一个字节都不会变。
"""

from dataclasses import replace
from datetime import datetime

from pmstudio.domain.authoring.blocks import Block, Entry, assert_unique_fields
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.schema import REQUIRED_FEATURE_FIELDS, field_spec
from pmstudio.domain.authoring.version import Snapshot, Version, VersionTrigger
from pmstudio.domain.authoring.views import BlockView, DocumentView, Gap, SectionView
from pmstudio.domain.shared.errors import (
    BlockManuallyEdited,
    DuplicateBlockId,
    EmptyApply,
    FeatureNotAllowedHere,
    FeatureRequiresUserNaming,
    SectionOrderViolation,
    SectionSetViolation,
    ShapeError,
    UnknownBlock,
    UnknownEntry,
    UnknownVersion,
)
from pmstudio.domain.shared.ids import (
    PREFERENCE_AREA_ID,
    BlockId,
    DocumentId,
    EntryId,
    FeatureSlug,
    ProjectId,
    SectionId,
)
from pmstudio.domain.shared.label import Zone
from pmstudio.domain.shared.sections import (
    FEATURE_HOME,
    SECTION_IDS,
    SECTION_SLOTS,
    SECTIONS,
    section_slot,
    slot_block_id,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp


class Document:
    def __init__(
        self,
        *,
        document_id: DocumentId,
        project_id: ProjectId,
        title: str,
        blocks: tuple[Block, ...],
        versions: tuple[Version, ...],
        preference_entries: tuple[Entry, ...] = (),
        tombstones: tuple[Block, ...] = (),
        created_at: datetime,
    ) -> None:
        self._document_id = document_id
        self._project_id = project_id
        self._title = title
        self._blocks = tuple(blocks)
        self._preference_entries = tuple(preference_entries)
        self._versions = tuple(versions)
        self._tombstones = tuple(tombstones)
        self._created_at = created_at
        self._validate()

    @classmethod
    def new(
        cls,
        *,
        document_id: DocumentId,
        project_id: ProjectId,
        title: str,
        now: datetime,
    ) -> "Document":
        # 13 章根 + 章节槽（s8 的六块）：槽是骨架，空的，不算内容
        blocks = tuple(Block.section(spec.section_id) for spec in SECTIONS) + tuple(
            Block(
                block_id=slot_block_id(section_id, slot.name),
                parent_id=BlockId(str(section_id)),
                kind=slot.kind,
                field=slot.name,
            )
            for section_id, slots in SECTION_SLOTS.items()
            for slot in slots
        )
        version = Version(
            version_no=1,
            trigger=VersionTrigger.MANUAL,
            summary="初始化 13 章骨架",
            changed_block_ids=tuple(block.block_id for block in blocks),
            created_at=now,
            snapshot=Snapshot(blocks=blocks),
        )
        return cls(
            document_id=document_id,
            project_id=project_id,
            title=title,
            blocks=blocks,
            versions=(version,),
            created_at=now,
        )

    # —— 只读 ——
    @property
    def document_id(self) -> DocumentId:
        return self._document_id

    @property
    def project_id(self) -> ProjectId:
        return self._project_id

    @property
    def title(self) -> str:
        return self._title

    @property
    def blocks(self) -> tuple[Block, ...]:
        return self._blocks

    @property
    def preference_entries(self) -> tuple[Entry, ...]:
        return self._preference_entries

    @property
    def tombstones(self) -> tuple[Block, ...]:
        return self._tombstones

    @property
    def versions(self) -> tuple[Version, ...]:
        return self._versions

    @property
    def current_version(self) -> Version:
        return self._versions[-1]

    def version(self, version_no: int) -> Version:
        for version in self._versions:
            if version.version_no == version_no:
                return version
        raise UnknownVersion(version_no)

    def block(self, block_id: BlockId) -> Block:
        for block in self._blocks:
            if block.block_id == block_id:
                return block
        raise UnknownBlock(block_id)

    def features(self) -> tuple[Block, ...]:
        return tuple(block for block in self._blocks if block.kind is BlockKind.FEATURE)

    def feature(self, slug: FeatureSlug) -> Block | None:
        for block in self.features():
            if block.feature_slug == slug:
                return block
        return None

    def field_block(self, slug: FeatureSlug, field: str) -> Block | None:
        root = self.feature(slug)
        if root is None:
            return None
        return self._field_block(root.block_id, field)

    def gaps(self) -> tuple[Gap, ...]:
        gaps: list[Gap] = []
        for feature in self.features():
            for spec in REQUIRED_FEATURE_FIELDS:
                block = self._field_block(feature.block_id, spec.name)
                if block is None or not any(entry.text.strip() for entry in block.entries):
                    gaps.append(
                        Gap(
                            feature_slug=feature.feature_slug,
                            field=spec.name,
                            title=spec.title,
                        )
                    )
        return tuple(gaps)

    def view(self) -> DocumentView:
        children: dict[BlockId | None, list[Block]] = {}
        for block in self._blocks:
            children.setdefault(block.parent_id, []).append(block)

        def build(block: Block) -> BlockView:
            spec = None
            field_title: str | None = None
            if block.field is not None:
                if block.feature_slug is not None:
                    spec = field_spec(block.field)
                    field_title = spec.title
                else:
                    slot = section_slot(block.parent_id, block.field)
                    field_title = None if slot is None else slot.title
            return BlockView(
                block_id=block.block_id,
                kind=block.kind,
                entries=block.entries,
                children=tuple(build(child) for child in children.get(block.block_id, ())),
                field=block.field,
                field_title=field_title,
                feature_slug=block.feature_slug,
                manually_edited=block.manually_edited,
                multiple=True if spec is None else spec.multiple,
            )

        sections = tuple(
            SectionView(
                section_id=spec.section_id,
                title=spec.title,
                blocks=tuple(
                    build(block) for block in children.get(BlockId(spec.section_id), ())
                ),
            )
            for spec in SECTIONS
        )
        return DocumentView(
            document_id=self._document_id,
            project_id=self._project_id,
            title=self._title,
            version_no=self.current_version.version_no,
            sections=sections,
            preference_entries=self._preference_entries,
            feature_gaps=self.gaps(),
        )

    # —— 写入 ——
    def apply(
        self,
        instructions: tuple[WriteInstruction, ...] | list[WriteInstruction],
        *,
        now: datetime,
        summary: str,
        trigger: VersionTrigger = VersionTrigger.CONFIRM,
        authorized_new_features: frozenset[FeatureSlug] = frozenset(),
    ) -> Version:
        instructions = tuple(instructions)
        if not instructions:
            raise EmptyApply()
        working = _WorkingTree(
            self._blocks,
            self._preference_entries,
            tombstone_ids=frozenset(block.block_id for block in self._tombstones),
        )
        for instruction in instructions:
            working.apply(instruction, authorized_new_features=authorized_new_features)
        # 候选状态也要过一遍校验：不然会出现"写得进去、读不出来"——
        # 校验只在 Document 构造时跑，写坏了要等下一个人读它才发现。
        Document(
            document_id=self._document_id,
            project_id=self._project_id,
            title=self._title,
            blocks=working.blocks,
            versions=self._versions,
            preference_entries=working.preference_entries,
            tombstones=self._tombstones + working.tombstones,
            created_at=self._created_at,
        )
        version = Version(
            version_no=self.current_version.version_no + 1,
            trigger=trigger,
            summary=summary,
            changed_block_ids=working.changed_block_ids,
            created_at=now,
            snapshot=Snapshot(
                blocks=working.blocks, preference_entries=working.preference_entries
            ),
        )
        self._blocks = working.blocks
        self._preference_entries = working.preference_entries
        self._tombstones = self._tombstones + working.tombstones
        self._versions = self._versions + (version,)
        return version

    def rollback_to(self, version_no: int, *, now: datetime, summary: str) -> Version:
        """回到某一版：产生**新**一版，旧版本一个都不删（D6）。

        两处必须一起做，否则会写出"落库没事、读回来炸"的文档（P5b §0.1 发现 1）：

        1. **重算墓碑**：活过来的块不能再算墓碑——`_validate` 要求"活块 id"与"墓碑 id"
           不重叠，重叠的话下次重建聚合就抛 DuplicateBlockId，整个项目打不开；
        2. **候选状态先过校验**：与 `apply()` 同一条规矩，坏状态不许落进聚合。
        """

        target = self.version(version_no)
        restored = target.snapshot.blocks
        preference_entries = target.snapshot.preference_entries
        alive = {block.block_id for block in restored}
        tombstones = tuple(
            block for block in self._tombstones if block.block_id not in alive
        )
        Document(
            document_id=self._document_id,
            project_id=self._project_id,
            title=self._title,
            blocks=restored,
            versions=self._versions,
            preference_entries=preference_entries,
            tombstones=tombstones,
            created_at=self._created_at,
        )
        before = _signature(self._blocks, self._preference_entries)
        self._blocks = restored
        self._preference_entries = preference_entries
        self._tombstones = tombstones
        after = _signature(self._blocks, self._preference_entries)
        changed = tuple(
            sorted(
                (
                    BlockId(key)
                    for key in set(before) | set(after)
                    if before.get(key) != after.get(key)
                ),
                key=str,
            )
        )
        version = Version(
            version_no=self.current_version.version_no + 1,
            trigger=VersionTrigger.ROLLBACK,
            summary=summary,
            changed_block_ids=changed,
            created_at=now,
            snapshot=Snapshot(blocks=self._blocks, preference_entries=self._preference_entries),
        )
        self._versions = self._versions + (version,)
        return version

    # —— 校验 ——
    def _field_block(self, parent_id: BlockId, field: str) -> Block | None:
        for block in self._blocks:
            if block.parent_id == parent_id and block.field == field:
                return block
        return None

    def _validate(self) -> None:
        ids = [block.block_id for block in self._blocks]
        ids += [block.block_id for block in self._tombstones]
        if len(set(ids)) != len(ids):
            raise DuplicateBlockId(block_ids=[str(value) for value in ids])
        if not self._versions:
            raise ShapeError("文档至少要有一个版本")
        roots = [block for block in self._blocks if block.kind is BlockKind.SECTION]
        root_ids = [str(block.block_id) for block in roots]
        expected = [str(section_id) for section_id in SECTION_IDS]
        if sorted(root_ids) != sorted(expected):
            raise SectionSetViolation(roots=root_ids)
        if root_ids != expected:
            raise SectionOrderViolation(roots=root_ids)
        by_id = {block.block_id: block for block in self._blocks}
        slugs = [
            block.feature_slug for block in self._blocks if block.kind is BlockKind.FEATURE
        ]
        if len(set(slugs)) != len(slugs):
            raise ShapeError(
                "同一文档里功能 slug 必须唯一", slugs=[str(slug) for slug in slugs]
            )
        for block in self._blocks:
            if block.parent_id is not None and block.parent_id not in by_id:
                raise UnknownBlock(block.parent_id)
            if block.kind is BlockKind.FEATURE:
                if block.feature_slug is None:
                    raise ShapeError("功能根缺少 feature_slug", block_id=str(block.block_id))
                if block.parent_id != FEATURE_HOME:
                    raise FeatureNotAllowedHere(
                        section_id=block.parent_id, feature_slug=block.feature_slug
                    )
            if block.field is not None:
                parent = by_id.get(block.parent_id) if block.parent_id is not None else None
                if parent is not None and parent.kind is BlockKind.FEATURE:
                    field_spec(block.field)
                    continue
                # 章节槽（s8 的六块）：field 名必须命中该章的槽表
                if (
                    parent is not None
                    and parent.kind is BlockKind.SECTION
                    and section_slot(parent.block_id, block.field) is not None
                ):
                    continue
                if parent is None:
                    raise ShapeError("字段块必须挂在功能根或章节槽下", block_id=str(block.block_id))
                if parent.kind is BlockKind.SECTION:
                    raise ShapeError("字段块必须挂在功能根下", block_id=str(block.block_id))
                raise ShapeError("字段块必须挂在功能根下", block_id=str(block.block_id))
        self._assert_acyclic(by_id)
        assert_unique_fields(self._blocks)

    def _assert_acyclic(self, by_id: dict[BlockId, Block]) -> None:
        for block in self._blocks:
            seen: set[BlockId] = set()
            current = block.parent_id
            while current is not None:
                if current in seen:
                    raise ShapeError("block 树出现环", block_id=str(block.block_id))
                seen.add(current)
                parent = by_id.get(current)
                current = parent.parent_id if parent is not None else None


def _signature(
    blocks: tuple[Block, ...], preference_entries: tuple[Entry, ...]
) -> dict[str, object]:
    signature: dict[str, object] = {str(block.block_id): block.entries for block in blocks}
    signature[str(PREFERENCE_AREA_ID)] = preference_entries
    return signature


class _WorkingTree:
    """一次 apply 的候选状态。指令在这里跑完，Document 最后才整体换上。"""

    def __init__(
        self,
        blocks: tuple[Block, ...],
        preference_entries: tuple[Entry, ...],
        *,
        tombstone_ids: frozenset[BlockId],
    ) -> None:
        self._blocks: dict[BlockId, Block] = {block.block_id: block for block in blocks}
        self._order: list[BlockId] = [block.block_id for block in blocks]
        self._preferences: list[Entry] = list(preference_entries)
        self._tombstone_ids: set[BlockId] = set(tombstone_ids)
        self._new_tombstones: list[Block] = []
        self._changed: set[BlockId] = set()

    @property
    def blocks(self) -> tuple[Block, ...]:
        return tuple(self._blocks[key] for key in self._order if key in self._blocks)

    @property
    def preference_entries(self) -> tuple[Entry, ...]:
        return tuple(self._preferences)

    @property
    def tombstones(self) -> tuple[Block, ...]:
        return tuple(self._new_tombstones)

    @property
    def changed_block_ids(self) -> tuple[BlockId, ...]:
        return tuple(sorted(self._changed, key=str))

    def apply(
        self, instruction: WriteInstruction, *, authorized_new_features: frozenset[FeatureSlug]
    ) -> None:
        """跑一条指令。人写的（`manual`）落到的块会留痕：之后 AI 动不了它。"""

        touched = self._apply_one(instruction, authorized_new_features=authorized_new_features)
        if instruction.manual and touched is not None:
            self.mark_edited(touched)

    def mark_edited(self, block_id: BlockId) -> None:
        block = self._blocks.get(block_id)
        if block is None:
            raise UnknownBlock(block_id)
        self._replace(replace(block, manually_edited=True))

    def _apply_one(
        self, instruction: WriteInstruction, *, authorized_new_features: frozenset[FeatureSlug]
    ) -> BlockId | None:
        if instruction.target.zone is Zone.PREFERENCE:
            self._apply_preference(instruction)
            return None  # 偏好区不是块，没有"留痕"这回事
        if instruction.op is WriteOp.ADD:
            return self._apply_add(instruction, authorized_new_features=authorized_new_features)
        if instruction.op is WriteOp.REPLACE:
            return self._apply_replace(instruction)
        return self._apply_remove(instruction)

    # —— 偏好区 ——
    def _apply_preference(self, instruction: WriteInstruction) -> None:
        if instruction.op is WriteOp.ADD:
            self._preferences.append(
                Entry(
                    entry_id=instruction.entry_id,
                    text=instruction.text,
                    refs=instruction.refs,
                )
            )
            self._changed.add(PREFERENCE_AREA_ID)
            return
        for index, entry in enumerate(self._preferences):
            if entry.entry_id != instruction.entry_id:
                continue
            if instruction.op is WriteOp.REPLACE:
                self._preferences[index] = Entry(
                    entry_id=entry.entry_id, text=instruction.text, refs=instruction.refs
                )
            else:
                self._preferences.pop(index)
            self._changed.add(PREFERENCE_AREA_ID)
            return
        raise UnknownEntry(instruction.entry_id)

    # —— 正文 ——
    def _apply_add(
        self, instruction: WriteInstruction, *, authorized_new_features: frozenset[FeatureSlug]
    ) -> BlockId:
        target = instruction.target
        if target.section_id is None:
            raise ShapeError("正文写入必须带章节", instruction_id=instruction.instruction_id)
        entry = Entry(
            entry_id=instruction.entry_id, text=instruction.text, refs=instruction.refs
        )
        if target.feature_slug is None and target.field is None:
            kind = instruction.kind or BlockKind.TEXT
            existing = self._find_section_content_block(target.section_id, kind)
            if existing is not None:
                self._replace(existing.with_entries(existing.entries + (entry,)))
                return existing.block_id
            block_id = self._next_section_block_id(target.section_id)
            self._add(
                Block.section_content(
                    block_id=block_id,
                    section_id=target.section_id,
                    kind=kind,
                    entries=(entry,),
                )
            )
            return block_id
        parent_id = self._parent_for_write(
            target, authorized_new_features=authorized_new_features
        )
        parent = self._blocks[parent_id]
        if target.field is None or target.feature_slug is None:
            # 章节槽与章节级内容都可以多条（槽是可生长的表态）
            multiple = True
        else:
            multiple = field_spec(target.field).multiple
        # D5 的意思是"不许被**静默**覆盖"：AI 动不了人写过的块，人自己可以接着改；
        # 用户显式确认过的评测提案（`allow_manual_override`）也算"人自己"的决定。
        if (
            parent.manually_edited
            and not multiple
            and not instruction.manual
            and not instruction.allow_manual_override
        ):
            raise BlockManuallyEdited(block_id=parent.block_id)
        entries = parent.entries + (entry,) if multiple else (entry,)
        self._replace(parent.with_entries(entries))
        return parent.block_id

    def _apply_replace(self, instruction: WriteInstruction) -> BlockId:
        block_id, index = self._locate_entry(instruction.entry_id)
        block = self._blocks[block_id]
        if (
            block.manually_edited
            and not instruction.manual
            and not instruction.allow_manual_override
        ):
            raise BlockManuallyEdited(block_id=block.block_id)
        entries = list(block.entries)
        entries[index] = Entry(
            entry_id=instruction.entry_id, text=instruction.text, refs=instruction.refs
        )
        self._replace(block.with_entries(entries))
        return block.block_id

    def _apply_remove(self, instruction: WriteInstruction) -> BlockId:
        block_id = BlockId(str(instruction.entry_id))
        if block_id in self._blocks:
            self._tombstone(block_id)
            return block_id
        found_block_id, index = self._locate_entry(instruction.entry_id)
        block = self._blocks[found_block_id]
        if (
            block.manually_edited
            and not instruction.manual
            and not instruction.allow_manual_override
        ):
            raise BlockManuallyEdited(block_id=block.block_id)
        entries = list(block.entries)
        entries.pop(index)
        self._replace(block.with_entries(entries))
        return found_block_id

    def _parent_for_write(
        self, target: Target, *, authorized_new_features: frozenset[FeatureSlug]
    ) -> BlockId:
        if target.feature_slug is None:
            if target.field is not None:
                return self._section_slot_parent(target)
            return BlockId(str(target.section_id))
        parent_id = self._feature_parent(
            target, authorized_new_features=authorized_new_features
        )
        if target.field is None:
            return parent_id
        spec = field_spec(target.field)
        existing = self._field_block(parent_id, target.field)
        if existing is not None:
            return existing.block_id
        block_id = self._free_id(BlockId(f"{parent_id}.{spec.name}"))
        self._add(
            Block.field_block(
                block_id=block_id,
                parent_id=parent_id,
                spec=spec,
                feature_slug=target.feature_slug,
            )
        )
        return block_id

    def _section_slot_parent(self, target: Target) -> BlockId:
        """章节槽（s8 的六块）：有就复用，没有就按槽表建一个。"""

        section_id = str(target.section_id)
        slot = section_slot(section_id, target.field or "")
        assert slot is not None  # Target 已经校验过
        existing = self._field_block(BlockId(section_id), slot.name)
        if existing is not None:
            return existing.block_id
        block_id = self._free_id(slot_block_id(section_id, slot.name))
        self._add(
            Block(
                block_id=block_id,
                parent_id=BlockId(section_id),
                kind=slot.kind,
                field=slot.name,
            )
        )
        return block_id

    def _feature_parent(
        self, target: Target, *, authorized_new_features: frozenset[FeatureSlug]
    ) -> BlockId:
        slug = target.feature_slug
        assert slug is not None
        for block in self._blocks.values():
            if block.kind is BlockKind.FEATURE and block.feature_slug == slug:
                return block.block_id
        if slug not in authorized_new_features:
            raise FeatureRequiresUserNaming(feature_slug=slug)
        if target.section_id != FEATURE_HOME:
            raise FeatureNotAllowedHere(section_id=target.section_id, feature_slug=slug)
        block_id = self._free_id(BlockId(f"feat-{slug}"))
        self._add(Block.feature_root(block_id=block_id, slug=slug))
        return block_id

    def _field_block(self, parent_id: BlockId, field: str) -> Block | None:
        for block in self._blocks.values():
            if block.parent_id == parent_id and block.field == field:
                return block
        return None

    def _locate_entry(self, entry_id: EntryId) -> tuple[BlockId, int]:
        for key in self._order:
            block = self._blocks.get(key)
            if block is None:
                continue
            for index, entry in enumerate(block.entries):
                if entry.entry_id == entry_id:
                    return block.block_id, index
        raise UnknownEntry(entry_id)

    def _next_section_block_id(self, section_id: SectionId) -> BlockId:
        prefix = f"{section_id}.b"
        used = {str(key) for key in self._blocks}
        used |= {str(key) for key in self._tombstone_ids}
        number = 1
        while f"{prefix}{number}" in used:
            number += 1
        return BlockId(f"{prefix}{number}")

    def _find_section_content_block(
        self, section_id: SectionId, kind: BlockKind
    ) -> Block | None:
        """找同章节、同类型、无 feature、无手改的最后一个章节内容块（F8）。

        找不到返回 None，由调用方走"新建"路径。不碰手改块——AI 不 append 到人写过的块。
        """
        section_str = str(section_id)
        candidates = [
            block
            for block in self._blocks.values()
            if str(block.parent_id) == section_str
            and block.feature_slug is None
            and block.field is None
            and block.kind == kind
            and not block.manually_edited
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda b: str(b.block_id))

    def _free_id(self, base: BlockId) -> BlockId:
        if base not in self._blocks and base not in self._tombstone_ids:
            return base
        number = 2
        while True:
            candidate = BlockId(f"{base}~{number}")
            if candidate not in self._blocks and candidate not in self._tombstone_ids:
                return candidate
            number += 1

    def _add(self, block: Block) -> None:
        self._blocks[block.block_id] = block
        self._order.append(block.block_id)
        self._changed.add(block.block_id)

    def _replace(self, block: Block) -> None:
        self._blocks[block.block_id] = block
        self._changed.add(block.block_id)

    def _tombstone(self, block_id: BlockId) -> None:
        stack = [block_id]
        while stack:
            current = stack.pop()
            block = self._blocks.get(current)
            if block is None:
                continue
            children = [
                candidate.block_id
                for candidate in self._blocks.values()
                if candidate.parent_id == current
            ]
            self._tombstone_ids.add(current)
            self._new_tombstones.append(block)
            self._changed.add(current)
            del self._blocks[current]
            stack.extend(children)
