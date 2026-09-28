"""版本差异：纯函数，按 entry_id 比对（`01` F3 / `04` F1）。

只读 `Snapshot`，不碰聚合、不碰 IO、不调模型——同一对快照永远算出同一个结果。
块的移动 / 重排不在这一版的范围（现在没有任何写入路径会移动块，做了也没东西可测）。
"""

from collections.abc import Mapping
from dataclasses import dataclass

from pmstudio.domain.authoring.blocks import Block, Entry
from pmstudio.domain.authoring.schema import field_spec
from pmstudio.domain.authoring.version import Snapshot
from pmstudio.domain.shared.ids import BlockId, EntryId, EvidenceId, FeatureSlug, SectionId
from pmstudio.domain.shared.sections import SECTION_BY_ID, SECTIONS, section_slot
from pmstudio.domain.shared.writing import BlockKind

PREFERENCE_LABEL = "偏好区（不进交付物）"
_SECTION_INDEX: dict[SectionId, int] = {
    spec.section_id: index for index, spec in enumerate(SECTIONS, start=1)
}


@dataclass(frozen=True, slots=True)
class EntrySnapshotView:
    entry_id: EntryId
    text: str
    refs: tuple[EvidenceId, ...]


@dataclass(frozen=True, slots=True)
class ChangedEntry:
    before: EntrySnapshotView
    after: EntrySnapshotView


@dataclass(frozen=True, slots=True)
class DiffGroup:
    target_label: str
    added: tuple[EntrySnapshotView, ...] = ()
    removed: tuple[EntrySnapshotView, ...] = ()
    changed: tuple[ChangedEntry, ...] = ()


def diff_snapshots(before: Snapshot, after: Snapshot) -> tuple[DiffGroup, ...]:
    """两个快照之间：新增 / 删除 / 改写，按去处分组。"""

    # 功能名（「名称」字段）两版之间可能变，而它是分组标签的一部分。
    # 两边共用一张表（after 优先、before 兜底），否则改个名字会让同一批条目
    # 在两侧落到不同组里、看着像"全删了又全加了"。
    names = _feature_names(before, after)
    left = _marks(before, names=names)
    right = _marks(after, names=names)
    groups: list[DiffGroup] = []
    for label in sorted({*left, *right}, key=_order_key):
        old = left.get(label, {})
        new = right.get(label, {})
        added = tuple(_view(new[key]) for key in sorted(new, key=str) if key not in old)
        removed = tuple(_view(old[key]) for key in sorted(old, key=str) if key not in new)
        changed = tuple(
            ChangedEntry(before=_view(old[key]), after=_view(new[key]))
            for key in sorted(old, key=str)
            if key in new and _differs(old[key], new[key])
        )
        if added or removed or changed:
            groups.append(
                DiffGroup(target_label=label, added=added, removed=removed, changed=changed)
            )
    return tuple(groups)


def _view(entry: Entry) -> EntrySnapshotView:
    return EntrySnapshotView(entry_id=entry.entry_id, text=entry.text, refs=entry.refs)


def _differs(before: Entry, after: Entry) -> bool:
    """内容或出处变了都算改写：出处变了也是"谁说的"变了。"""

    return before.text != after.text or before.refs != after.refs


def _marks(
    snapshot: Snapshot, *, names: Mapping[FeatureSlug, str]
) -> dict[str, dict[EntryId, Entry]]:
    """快照 → {去处标签: {entry_id: 条目}}。"""

    by_id = {block.block_id: block for block in snapshot.blocks}
    marks: dict[str, dict[EntryId, Entry]] = {}
    for block in snapshot.blocks:
        if not block.entries:
            continue
        marks.setdefault(_label(block, by_id=by_id, names=names), {}).update(
            {entry.entry_id: entry for entry in block.entries}
        )
    if snapshot.preference_entries:
        marks[PREFERENCE_LABEL] = {
            entry.entry_id: entry for entry in snapshot.preference_entries
        }
    return marks


def _feature_names(*snapshots: Snapshot) -> dict[FeatureSlug, str]:
    """功能的中文名：取「名称」字段的第一条（没写就不进表，回落 slug，不替它编）。

    后面的快照覆盖前面的：`diff_snapshots(before, after)` 里 `after` 的名字优先。
    """

    names: dict[FeatureSlug, str] = {}
    for snapshot in snapshots:
        by_id = {block.block_id: block for block in snapshot.blocks}
        for block in by_id.values():
            if block.kind is not BlockKind.FEATURE or block.feature_slug is None:
                continue
            for child in by_id.values():
                if (
                    child.parent_id == block.block_id
                    and child.field == "name"
                    and child.entries
                ):
                    names[block.feature_slug] = child.entries[0].text
                    break
    return names


def _label(
    block: Block, *, by_id: Mapping[BlockId, Block], names: Mapping[FeatureSlug, str]
) -> str:
    if block.parent_id is None:
        # 章节根自己就挂内容（章节级内容落在 `sN.bK` 上，根上一般没有；有也要能标出来）
        return _section_label(str(block.block_id))
    parent = by_id.get(block.parent_id)
    if block.kind is BlockKind.FEATURE and block.feature_slug is not None:
        return f"{_section_label(str(block.parent_id))} · {_feature_name(block, names)}"
    if block.feature_slug is not None:
        # 功能字段：章节要从**功能根**往上找（字段块的 parent 是功能根，不是 s5）
        root = _feature_root(block, by_id=by_id)
        section_id = str(root.parent_id) if root.parent_id is not None else str(root.block_id)
        title = field_spec(block.field).title if block.field is not None else ""
        return f"{_section_label(section_id)} · {_feature_name(root, names)} · {title}"
    section_id = str(block.parent_id)
    if block.field is not None:
        slot = section_slot(section_id, block.field)
        if slot is not None:
            return f"{_section_label(section_id)} · {slot.title}"
    if parent is not None and parent.kind is BlockKind.SECTION:
        return _section_label(section_id)
    return f"{_section_label(section_id)} · {block.block_id}"


def _feature_root(block: Block, *, by_id: Mapping[BlockId, Block]) -> Block:
    current = block
    while current.parent_id is not None:
        parent = by_id.get(current.parent_id)
        if parent is None:
            return current
        if parent.kind is BlockKind.FEATURE:
            return parent
        current = parent
    return current


def _feature_name(block: Block, names: Mapping[FeatureSlug, str]) -> str:
    if block.feature_slug is None:
        return str(block.block_id)
    return names.get(block.feature_slug, str(block.feature_slug))


def _section_label(section_id: str) -> str:
    spec = SECTION_BY_ID.get(SectionId(section_id))
    if spec is None:
        return section_id
    return f"{_SECTION_INDEX[spec.section_id]}. {spec.title}"


def _order_key(label: str) -> tuple[int, str]:
    """章节顺序在前，偏好区永远排最后。"""

    if label == PREFERENCE_LABEL:
        return (len(SECTIONS) + 1, label)
    head = label.split(".", 1)[0]
    return (int(head) if head.isdigit() else len(SECTIONS), label)
