"""版本与快照。v1 存整棵树（块是 frozen，快照即是深拷贝）；diff 在展示时算。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pmstudio.domain.authoring.blocks import Block, Entry
from pmstudio.domain.shared.ids import BlockId


class VersionTrigger(StrEnum):
    CONFIRM = "confirm"  # 草案确认写入
    MANUAL = "manual"  # 建项目、将来的手工编辑
    ROLLBACK = "rollback"  # 回退产生的新版本


@dataclass(frozen=True, slots=True)
class Snapshot:
    blocks: tuple[Block, ...]
    preference_entries: tuple[Entry, ...] = ()


@dataclass(frozen=True, slots=True)
class Version:
    version_no: int
    trigger: VersionTrigger
    summary: str
    changed_block_ids: tuple[BlockId, ...]
    created_at: datetime
    snapshot: Snapshot
