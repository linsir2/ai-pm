"""跨轮记忆的落库（M6a）：往返、坏行跳过、老库自动补表。

口径：`docs/plans/2026-10-03-M6-跨轮记忆本体.md` §6.3 / §8 / §9（I1、I2）。
"""

import json
import sqlite3
from datetime import UTC, datetime

import pytest

from pmstudio.domain.shared.errors import UnknownMemoryEntry
from pmstudio.domain.shared.ids import ProjectId
from pmstudio.domain.shared.memory import MemoryChange, MemoryKind, MemoryOp
from pmstudio.infrastructure.sqlite import database, serialization
from pmstudio.infrastructure.sqlite.repositories import SqliteMemoryRepository
from tests.support.doubles import SeqIds

NOW = datetime(2026, 10, 3, 20, 0, 0, tzinfo=UTC)
PROJECT = ProjectId("p-memory")
KNOWN = frozenset({"E1", "E2"})

#: 加 `project_memory` 之前的建表语句（用来验"老库自动补表"）
OLD_ROUNDS_DDL = """
CREATE TABLE rounds (
  round_id   TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  step       TEXT NOT NULL,
  waiting_on TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def add(text: str, *, refs: tuple[str, ...] = ("E1",)) -> MemoryChange:
    return MemoryChange(op=MemoryOp.ADD, kind=MemoryKind.LESSON, text=text, refs=refs)


def memory_with_one_entry():
    from pmstudio.domain.memory.memory import ProjectMemory

    return ProjectMemory.empty(PROJECT).apply(
        (add("失败必须显式声明"),), round_id="r1", known_refs=KNOWN, now=NOW, ids=SeqIds()
    )


def test_an_empty_project_reads_back_as_an_empty_memory(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    repository = SqliteMemoryRepository(connection)

    memory = repository.get(PROJECT)

    assert memory.entries == ()
    assert memory.revisions == ()
    connection.close()


def test_memory_round_trips_entries_and_revisions(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    repository = SqliteMemoryRepository(connection)
    memory = memory_with_one_entry()

    repository.save(memory)
    loaded = repository.get(PROJECT)

    assert [(item.kind, item.text, item.refs) for item in loaded.entries] == [
        (MemoryKind.LESSON, "失败必须显式声明", ("E1",))
    ]
    assert loaded.entries[0].created_at == NOW
    assert [(item.revision_no, item.round_id) for item in loaded.revisions] == [(1, "r1")]
    assert loaded.revisions[0].changes[0].text == "失败必须显式声明"
    connection.close()


def test_saving_again_replaces_the_whole_payload(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    repository = SqliteMemoryRepository(connection)
    memory = memory_with_one_entry()
    repository.save(memory)

    repository.save(memory.delete(memory.entries[0].memory_id))

    assert repository.get(PROJECT).entries == ()
    assert len(repository.get(PROJECT).revisions) == 1   # 版本不被这次删除抹掉
    connection.close()


def test_deleting_an_unknown_entry_is_refused():
    memory = memory_with_one_entry()

    with pytest.raises(UnknownMemoryEntry):
        memory.delete("mem-404")


def test_a_broken_payload_is_skipped_not_raised(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    connection.execute(
        "INSERT INTO project_memory (project_id, payload, updated_at) VALUES (?, ?, ?)",
        (
            str(PROJECT),
            json.dumps(
                {
                    "entries": [
                        {
                            "memory_id": "mem1",
                            "kind": "lesson",
                            "text": "这条是好的",
                            "refs": ["E1"],
                            "round_id": "r1",
                            "created_at": NOW.isoformat(),
                            "updated_at": NOW.isoformat(),
                        },
                        {"memory_id": "mem2", "kind": "不认识的类别", "text": "坏的"},
                        {"memory_id": "mem3", "kind": "lesson", "text": "没出处"},
                    ],
                    "revisions": "不是数组",
                }
            ),
            NOW.isoformat(),
        ),
    )

    memory = SqliteMemoryRepository(connection).get(PROJECT)

    assert [item.text for item in memory.entries] == ["这条是好的"]
    assert memory.revisions == ()
    connection.close()


def test_a_payload_that_is_not_json_reads_back_empty(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    connection.execute(
        "INSERT INTO project_memory (project_id, payload, updated_at) VALUES (?, ?, ?)",
        (str(PROJECT), "{不是 JSON", NOW.isoformat()),
    )

    memory = SqliteMemoryRepository(connection).get(PROJECT)

    assert memory.entries == ()
    connection.close()


def test_an_old_database_gets_the_table_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(OLD_ROUNDS_DDL)
    old.execute(
        "INSERT INTO rounds (round_id, project_id, step, waiting_on, payload, created_at,"
        " updated_at) VALUES ('r-old', 'p-memory', 'done', 'none', '{}', ?, ?)",
        (NOW.isoformat(), NOW.isoformat()),
    )
    old.commit()
    old.close()

    connection = database.connect(path)

    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in rows}
    assert "project_memory" in tables
    assert connection.execute("PRAGMA user_version").fetchone()[0] == database.USER_VERSION
    assert SqliteMemoryRepository(connection).get(PROJECT).entries == ()
    rows = connection.execute("SELECT round_id FROM rounds").fetchall()
    assert [row["round_id"] for row in rows] == ["r-old"]
    connection.close()

    database.connect(path).close()      # 幂等：再连一次不许炸


def test_memory_payload_keeps_ids_and_timestamps_verbatim():
    memory = memory_with_one_entry()

    payload = serialization.memory_to_payload(memory)
    again = serialization.memory_from_payload(payload, project_id=PROJECT)

    assert again == memory
