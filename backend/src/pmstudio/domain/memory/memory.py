"""`ProjectMemory` 聚合：用户确认过的跨轮记忆（教训 / 已定口径 / 明确否掉）。

**原设计的现行版**（`.codex/docs/过程记录/记忆层与门禁条款.md`）：那份设计写着 PROJECT.md 有五块，
对着代码核过之后**只有一块真的缺**——

| 原设计的一块 | 现在谁承担 | 这里 |
|---|---|---|
| 章节状态表 | `ReadinessChecker` **现算** | 不存 |
| 阻塞项（未清的开放问题） | 已经落在 s11（`02` §11.0.10 / §11.0.16） | 不存 |
| 铁律 | 提示词与就绪度的硬规矩里 | 不存 |
| **教训 / 已定口径 / 明确否掉** | **无** | **就是这个聚合** |
| 引用计数（连续 5 轮未命中 → 删除候选） | 命中与否**现算**（扫最近 N 轮 payload） | 不存计数器 |

两条硬约束（其余都是它们的推论）：

1. **一条记忆必须带至少一个账本里存在的出处编号**——记忆会注入之后每一轮的提示词，
   没有出处的"教训"等于让模型凭一句话改掉以后所有判断；
2. **只有人的确认能改它**——`apply()` 是唯一入口，Curator 只能出 `MemoryChange`（提议）。

`screen_changes()` 是**提议侧**的机械筛（出草案那一刻跑，把不合法的那几条丢掉 + 留痕）；
`apply()` 是**写入侧**的严格版（同一个事务里跑，任何一条不合法就整批抛错、什么都不写）。
两处共用同一套判据 `_reject()`——一处实现，别写第二份。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

from pmstudio.domain.shared.errors import (
    DomainError,
    MemoryCapacityExceeded,
    ShapeError,
    UnknownMemoryEntry,
    UntraceableMemory,
)
from pmstudio.domain.shared.ids import EvidenceId, ProjectId
from pmstudio.domain.shared.memory import MemoryChange, MemoryKind, MemoryOp

if TYPE_CHECKING:
    from pmstudio.domain.ports import IdSource

#: 容量：与"教训区 ≤60 行"对齐（`记忆层与门禁条款.md` §2.3）。到顶必须合并或删除。
MEMORY_CAPACITY = 60

#: 命中窗口：最近 N 轮一次没被引用 = 未命中（现算，不落库，见 `unused_entries`）。
MEMORY_USAGE_WINDOW = 10

#: 一条最多两行（同 §2.3）：记忆是**被每一轮读**的东西，不是文档。
MAX_ENTRY_LINES = 2

MEMORY_ID_PREFIX = "mem"

#: 机械回填出处时最多盖几个（本轮账本编号按字典序取前几个）：够"查得出是哪来的"就停，
#: 不把整轮十几个编号都糊上去（提示词里每条记忆只带一行）。
FALLBACK_REF_LIMIT = 3


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    memory_id: str
    kind: MemoryKind
    text: str
    refs: tuple[EvidenceId, ...]
    round_id: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.memory_id.strip():
            raise ShapeError("记忆条目必须有 id")
        if not self.text.strip():
            raise ShapeError("记忆条目不能是空的", memory_id=self.memory_id)
        if not self.refs:
            raise UntraceableMemory(reason="没有出处编号")


def unused_entries(
    entries: Sequence[MemoryEntry],
    cited_by_round: Mapping[str, frozenset[str]],
    *,
    window: int,
    order: Sequence[str] = (),
) -> frozenset[str]:
    """最近 `window` 轮里**一次都没被引用过**的记忆条目 id。

    **现算，不落库**（`ProjectMemory` 的既有口径：删掉"连续 5 轮未命中"那类计数字段，
    命中与否现算）。`order` 是按时间**升序**的 `round_id`；只取尾部 `window` 轮——
    映射没有顺序，靠 `dict` 插入序是隐式契约，测试里说不出口。

    判据是**交集非空即命中**：记忆的 `refs` 只要出现在任何一轮"交代过的编号"里
    （`assembly.cited_eids` 的口径），就算它被用上了——模型多半引用它的出处而不是它本身。
    """

    if not entries:
        return frozenset()
    recent = order[-window:] if order else list(cited_by_round)[-window:]
    cited: frozenset[str] = frozenset().union(
        *(cited_by_round[round_id] for round_id in recent)
    )
    return frozenset(
        entry.memory_id for entry in entries
        if not {str(ref) for ref in entry.refs} & cited
    )


@dataclass(frozen=True, slots=True)
class MemoryRevision:
    """一次确认 = 一条版本（只增不改）。它只给审计与排查看，**不进提示词**。"""

    revision_no: int
    round_id: str
    changes: tuple[MemoryChange, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryDrop:
    """一条被机械筛掉的变更（留痕用）：哪条、为什么。"""

    change: MemoryChange
    reason: str


@dataclass(frozen=True, slots=True)
class ScreenedChanges:
    accepted: tuple[MemoryChange, ...]
    dropped: tuple[MemoryDrop, ...]

    @property
    def empty(self) -> bool:
        return not self.accepted


@dataclass(frozen=True, slots=True)
class ProjectMemory:
    """一个项目一份。`entries` 是事实，`revisions` 是它怎么变成现在这样的。"""

    project_id: ProjectId
    entries: tuple[MemoryEntry, ...] = ()
    revisions: tuple[MemoryRevision, ...] = ()

    @classmethod
    def empty(cls, project_id: ProjectId) -> "ProjectMemory":
        return cls(project_id=project_id)

    def apply(
        self,
        changes: Sequence[MemoryChange],
        *,
        round_id: str,
        known_refs: frozenset[str],
        now: datetime,
        ids: "IdSource",
    ) -> "ProjectMemory":
        """把这一批**用户勾选的**变更落成新版本。严格：任何一条不合法就整批抛错。

        为什么严格而不是"跳过坏的那条"：这一步在**写入事务里**跑（`ProposalCommitter`），
        静默跳一条等于"用户勾了、系统没写、还不说"。不合法的那几条在提议侧
        （`screen_changes`）早就被筛掉并留痕了——到得了这里就说明真有东西不对。
        """

        if not changes:
            return self

        known = {entry.memory_id for entry in self.entries}
        taken = set(_taken_keys(self.entries))
        for change in changes:
            rejected = _reject(
                change, known_entries=known, known_refs=known_refs, taken=frozenset(taken)
            )
            if rejected is not None:
                raise rejected[1]
            if change.op is MemoryOp.ADD:
                taken.add(_identity(change.kind, change.text))

        adds = sum(1 for change in changes if change.op is MemoryOp.ADD)
        deletes = sum(1 for change in changes if change.op is MemoryOp.DELETE)
        if len(self.entries) + adds - deletes > MEMORY_CAPACITY:
            raise MemoryCapacityExceeded(limit=MEMORY_CAPACITY)

        entries = list(self.entries)
        for change in changes:
            if change.op is MemoryOp.ADD:
                entries.append(
                    MemoryEntry(
                        memory_id=ids.new(MEMORY_ID_PREFIX),
                        kind=change.kind,
                        text=change.text.strip(),
                        refs=tuple(EvidenceId(ref) for ref in change.refs),
                        round_id=round_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
                continue
            index = next(
                position
                for position, entry in enumerate(entries)
                if entry.memory_id == change.target_id
            )
            if change.op is MemoryOp.DELETE:
                entries.pop(index)
            else:
                entries[index] = replace(
                    entries[index],
                    kind=change.kind,
                    text=change.text.strip(),
                    refs=tuple(EvidenceId(ref) for ref in change.refs),
                    updated_at=now,
                )

        revision = MemoryRevision(
            revision_no=len(self.revisions) + 1,
            round_id=round_id,
            changes=tuple(changes),
            created_at=now,
        )
        return ProjectMemory(
            project_id=self.project_id,
            entries=tuple(entries),
            revisions=self.revisions + (revision,),
        )

    def entry(self, memory_id: str) -> MemoryEntry | None:
        return next((item for item in self.entries if item.memory_id == memory_id), None)

    def delete(self, memory_id: str, *, now: datetime | None = None) -> "ProjectMemory":
        """用户手动删一条（记忆抽屉上那个按钮）。删不掉就抛 `UnknownMemoryEntry`。

        给了 `now` 就**追加一条版本**（留痕："谁在什么时候抹掉了它"）——
        记忆是"只增"的东西，删也要看得见。不给 `now` 就是纯函数式的删除（测试夹具用）。
        """

        entry = self.entry(memory_id)
        if entry is None:
            raise UnknownMemoryEntry(memory_id=memory_id)
        kept = tuple(item for item in self.entries if item.memory_id != memory_id)
        revisions = self.revisions
        if now is not None:
            revisions = revisions + (
                MemoryRevision(
                    revision_no=len(self.revisions) + 1,
                    round_id="",
                    changes=(
                        MemoryChange(
                            op=MemoryOp.DELETE,
                            kind=entry.kind,
                            text="",
                            target_id=memory_id,
                            reason="用户手动删除",
                        ),
                    ),
                    created_at=now,
                ),
            )
        return ProjectMemory(
            project_id=self.project_id, entries=kept, revisions=revisions
        )


def screen_changes(
    changes: Sequence[MemoryChange],
    *,
    memory: ProjectMemory,
    known_refs: frozenset[str],
    fallback_refs: frozenset[str] = frozenset(),
) -> ScreenedChanges:
    """提议侧：把 Curator 交回的指令逐条机械核一遍（不合法就丢 + 留痕，不抛错）。

    容量是**整批**判的：装不下就整批拒收（而不是偷偷少写几条）——
    "这一批必须带合并或删除"是给模型的硬规矩，也是给人的可见后果。
    """

    known = {entry.memory_id for entry in memory.entries}
    taken = set(_taken_keys(memory.entries))
    accepted: list[MemoryChange] = []
    dropped: list[MemoryDrop] = []
    for change in changes:
        # **机械回填出处**（M6e）：模型连着两趟都不肯写 `refs`（真跑 L1 第 1 轮，
        # `qwen-plus` 两次回包都把这一栏省掉），而"出处"这件事**系统自己算得出来**——
        # 就是这一轮的账本编号。所以模型只决定"记哪一条"，出处由装配层盖上
        # （与矛盾题的 `kind=conflict` 机械盖章同一先例：判断做一次、结构机械落）。
        # 回填**只用本轮真实存在的编号**，一条都没有就照旧丢掉——绝不编。
        if change.op is not MemoryOp.DELETE and not change.refs and fallback_refs:
            change = replace(change, refs=tuple(sorted(fallback_refs))[:FALLBACK_REF_LIMIT])
        rejected = _reject(
            change, known_entries=known, known_refs=known_refs, taken=frozenset(taken)
        )
        if rejected is None:
            accepted.append(change)
            if change.op is MemoryOp.ADD:
                taken.add(_identity(change.kind, change.text))
        else:
            dropped.append(MemoryDrop(change=change, reason=rejected[0]))

    adds = sum(1 for change in accepted if change.op is MemoryOp.ADD)
    deletes = sum(1 for change in accepted if change.op is MemoryOp.DELETE)
    if len(memory.entries) + adds - deletes > MEMORY_CAPACITY:
        reason = f"记忆最多 {MEMORY_CAPACITY} 条，加不进去了：这一批必须带合并或删除"
        dropped.extend(MemoryDrop(change=change, reason=reason) for change in accepted)
        accepted = []

    return ScreenedChanges(accepted=tuple(accepted), dropped=tuple(dropped))


#: 比较"是不是同一条记忆"时剥掉的首尾标点（全角 + 半角）。只剥首尾。
_EDGE_PUNCTUATION = "，。、；：！？,.!?;:"


def normalized_text(text: str) -> str:
    """归一化后比较"是不是同一条"：去全部空白 + 剥首尾标点。

    **只剥首尾**——中间的问号/逗号是内容的一部分。这与 `assembly._asked_how` 只共享
    **同一种精神**（两边都只剥首尾、都不吃内部标点），**不是同一口径**：标点集、空白处理
    （这里是去**全部**空白，那边是压成一个空格）都**本处另定**，不共享常量、也不保证同结果。
    只做**完全重复**，不做相似度（`02 §11.0.16`：相似度不做，要动得单独拍板）。
    """

    squeezed = "".join(text.split())
    return squeezed.strip(_EDGE_PUNCTUATION)


def _identity(kind: MemoryKind, text: str) -> tuple[str, str]:
    """一条记忆的判重身份 `(类别, 归一化文本)`。

    **只此一处构造**：提议侧（`screen_changes`）与写入侧（`apply`）靠它对齐"这是不是同一条"，
    任何一侧换个写法，两边就会静默对不上——这正是本批要防的漂。
    """

    return (str(kind), normalized_text(text))


def _taken_keys(entries: Sequence[MemoryEntry]) -> frozenset[tuple[str, str]]:
    """已有记忆的判重身份集合——去重判据的输入，一处算。"""

    return frozenset(_identity(entry.kind, entry.text) for entry in entries)


def _reject(
    change: MemoryChange,
    *,
    known_entries: set[str],
    known_refs: frozenset[str],
    taken: frozenset[tuple[str, str]],
) -> tuple[str, DomainError] | None:
    """一条变更不合法 → `(留痕理由, 该抛的错)`；合法 → `None`。留痕与抛错同一处判、同一句话。"""

    if change.op is not MemoryOp.DELETE:
        if not change.refs:
            reason = "记忆条目必须带至少一个出处编号"
            return reason, UntraceableMemory(reason="没有出处编号")
        unknown = [ref for ref in change.refs if ref not in known_refs]
        if unknown:
            reason = f"出处编号不在账本里：{'、'.join(unknown)}"
            return reason, UntraceableMemory(reason=reason)
        if not change.text.strip():
            reason = "记忆条目不能是空的"
            return reason, ShapeError(reason)
        if len(change.text.strip().splitlines()) > MAX_ENTRY_LINES:
            reason = "记忆条目最多两行"
            return reason, ShapeError(reason)
        # 去重只看**完全重复**（同类别 + 归一化后一模一样）——相似度不做（`02 §11.0.16`）。
        if change.op is MemoryOp.ADD and _identity(change.kind, change.text) in taken:
            reason = "与已有记忆完全重复"
            return reason, ShapeError(reason)

    if change.op in (MemoryOp.UPDATE, MemoryOp.MERGE, MemoryOp.DELETE):
        if change.target_id is None:
            reason = "改 / 合并 / 删都必须点名是哪一条"
            return reason, ShapeError(reason)
        if change.target_id not in known_entries:
            reason = f"点名的记忆条目不存在：{change.target_id}"
            return reason, UnknownMemoryEntry(memory_id=change.target_id)

    return None


def kind_title(kind: MemoryKind) -> str:
    """给提示词与界面用的中文名（一处实现，别在两处各写一份）。"""

    return {
        MemoryKind.LESSON: "教训",
        MemoryKind.DECISION: "已定口径",
        MemoryKind.REJECTED: "已否掉",
    }[kind]
