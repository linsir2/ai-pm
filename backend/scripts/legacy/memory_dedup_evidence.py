#!/usr/bin/env python3
"""B 组批 1 证据：记忆机械去重（只做**完全重复**）跑一遍、把数字打出来。

## 它证明什么

记忆去重是**机械 exact**（同类别 + 归一化后完全相同），判据只有一处
（`memory.py::_reject`），两处共用：

- **提议侧** `screen_changes()`：不合法的那几条**丢掉 + 留痕**（不抛错）；
- **写入侧** `apply()`：**整批抛 `ShapeError`**（什么都不写）。

**相似度不做**（`02 §11.0.16` / `07 §7.4` 明令）——只在末尾多几个字的不是重复。

## 数据与真库纪律

数据是**合成的**：一个项目、3 条既有记忆 + 8 条提议（其中 3 条是既有条目的完全重复、
1 条是"看着像但不一样"的近义），经**真实仓储**写进 `--db` 指定的库再读回来跑筛。

真库**只读**：默认写在 `$TMPDIR/bg1_memory_dedup.db`；本脚本**拒绝**把
`backend/data/pmstudio.db`（或 `PMSTUDIO_DB` 指到的那份）当成写入目标。
想拿真数据当底稿，先 `sqlite3 <真库> ".backup <副本>"`，再 `--db <副本>`。

## 跑法

    cd backend && python3 scripts/memory_dedup_evidence.py [--db /tmp/bg1.db]

任一断言不过就非零退出。
"""

import argparse
import itertools
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from pmstudio.domain.memory.memory import (  # noqa: E402
    MemoryChange,
    MemoryEntry,
    MemoryKind,
    MemoryOp,
    ProjectMemory,
    screen_changes,
)
from pmstudio.domain.shared.errors import ShapeError  # noqa: E402
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, UserId  # noqa: E402
from pmstudio.infrastructure.sqlite import database  # noqa: E402
from pmstudio.infrastructure.sqlite.repositories import (  # noqa: E402
    SqliteMemoryRepository,
)

DUPLICATE_REASON = "与已有记忆完全重复"

PROJECT = ProjectId("p-bg1-evidence")
OWNER = UserId("u-bg1-evidence")
REFS = frozenset({"E1", "E2"})
NOW = datetime(2026, 10, 18, tzinfo=UTC)


class _SeqIds:
    def __init__(self) -> None:
        self._n = itertools.count(1)

    def new(self, prefix: str) -> str:
        return f"{prefix}{next(self._n)}"


def _working_db() -> Path:
    """真库路径（用于**禁止**写入）：`PMSTUDIO_DB` > `backend/data/pmstudio.db`。"""

    env = os.environ.get("PMSTUDIO_DB", "").strip()
    if env:
        path = Path(env)
        return path if path.is_absolute() else (BACKEND / path)
    return BACKEND / "data" / "pmstudio.db"


def _entry(memory_id: str, text: str, kind: MemoryKind) -> MemoryEntry:
    return MemoryEntry(
        memory_id=memory_id,
        kind=kind,
        text=text,
        refs=(EvidenceId("E1"),),
        round_id="r-existing",
        created_at=NOW,
        updated_at=NOW,
    )


def _add(text: str, kind: MemoryKind) -> MemoryChange:
    return MemoryChange(op=MemoryOp.ADD, kind=kind, text=text, refs=("E1",))


def _existing_memory() -> ProjectMemory:
    """合成的既有记忆：3 条，供 3 条 exact 重复命中。"""

    return ProjectMemory(
        project_id=PROJECT,
        entries=(
            _entry("mem-1", "不要做多租户", MemoryKind.LESSON),
            _entry("mem-2", "排期以两周为一迭代。", MemoryKind.DECISION),
            _entry("mem-3", "先不做导出", MemoryKind.REJECTED),
        ),
    )


def _proposals() -> tuple[MemoryChange, ...]:
    """8 条提议：前 3 条是既有记忆的完全重复，后 5 条应当收下。"""

    return (
        _add("不要做多租户", MemoryKind.LESSON),          # 重复 mem-1
        _add("排期以两周为一迭代", MemoryKind.DECISION),  # 重复 mem-2（归一化：去尾句号）
        _add("先不做导出", MemoryKind.REJECTED),          # 重复 mem-3
        _add("不要做多租户登录", MemoryKind.LESSON),      # 近义但**不**重复（相似度不做）
        _add("文档一版一存", MemoryKind.DECISION),
        _add("不做多语言", MemoryKind.REJECTED),
        _add("每个功能必须有验收口径", MemoryKind.LESSON),
        _add("确认后才落库", MemoryKind.DECISION),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="记忆机械去重的真跑证据")
    default_db = Path(tempfile.gettempdir()) / "bg1_memory_dedup.db"
    parser.add_argument("--db", default=str(default_db), help="写哪份库（默认 $TMPDIR 副本）")
    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser()
    working = _working_db()
    if db_path.resolve() == working.resolve():
        print(f"拒绝：--db 指向真库 {working}；真库只读，请先 .backup 到别的路径。",
              file=sys.stderr)
        return 2

    try:
        # 经**真实仓储**落库再读回：证据覆盖的不只是纯函数，还有存取一圈。
        connection = database.connect(db_path)
        repo = SqliteMemoryRepository(connection)
        repo.save(_existing_memory(), owner=OWNER)
        memory = repo.get(PROJECT, owner=OWNER)
        assert len(memory.entries) == 3, f"读回的既有记忆应是 3 条，实为 {len(memory.entries)}"

        proposals = _proposals()
        result = screen_changes(proposals, memory=memory, known_refs=REFS)

        accepted, dropped = len(result.accepted), len(result.dropped)
        assert accepted == 5, f"应收下 5 条，实为 {accepted}"
        assert dropped == 3, f"应丢掉 3 条，实为 {dropped}"
        reasons = {drop.reason for drop in result.dropped}
        assert reasons == {DUPLICATE_REASON}, f"丢掉的理由应是唯一那条，实为 {reasons}"

        # 近义那条**必须**还在（相似度不做——R 的负控）。
        kept_texts = {change.text for change in result.accepted}
        assert "不要做多租户登录" in kept_texts, "近义条目应保留（相似度不做）"

        print(f"库：{db_path}（真库 {working} 未动）")
        print(
            f"提议 {len(proposals)} 条 → 收 {accepted} 条 / 丢 {dropped} 条"
            f"（全部 reason={DUPLICATE_REASON}）"
        )

        # 写入侧用**同一条**判据：完全重复整批抛 ShapeError（一处实现，两处共用）。
        dup = _add("不要做多租户", MemoryKind.LESSON)
        try:
            memory.apply(
                (dup,), round_id="r-evidence", known_refs=REFS, now=NOW, ids=_SeqIds()
            )
        except ShapeError as exc:
            assert DUPLICATE_REASON in str(exc), f"抛错理由不对：{exc}"
            print(f"写入侧：apply 对完全重复整批抛 ShapeError（reason={DUPLICATE_REASON}）")
        else:
            raise AssertionError("apply 本应对完全重复抛 ShapeError，却放行了")

        # 工作集在**批内**长大：同一批里两条一样的，第二条也丢。
        batch = screen_changes(
            (_add("同一条", MemoryKind.LESSON), _add("同一条", MemoryKind.LESSON)),
            memory=ProjectMemory.empty(PROJECT),
            known_refs=REFS,
        )
        assert len(batch.accepted) == 1 and len(batch.dropped) == 1, "批内重复判据不对"
        print("批内：同一批两条一样 → 收 1 丢 1（工作集批内长大）")

        connection.close()
    except AssertionError as exc:
        print(f"断言失败：{exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
