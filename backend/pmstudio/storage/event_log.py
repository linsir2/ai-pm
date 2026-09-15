"""事件流水仓库：只增不改，按轮次可查。

端口是 async（P3：IO 端口全 async），内部是单线程 `sqlite3`，没有真正的并发等待点。
将来换成线程池或 aiosqlite 时，形状不用改。
"""

import sqlite3

from pmstudio.contracts.enums import EventType, ProducerIdentity
from pmstudio.contracts.skeleton.events import PAYLOAD_FOR, EventLogEntry
from pmstudio.storage.db import Database


class EventLog:
    """`events` 表的读写入口。没有 update，也没有 delete——只增不改。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(self, entry: EventLogEntry) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO events"
                " (event_id, at, type, round_id, project_id, producer, payload_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.event_id,
                    entry.at.isoformat(),
                    entry.type.value,
                    entry.round_id,
                    entry.project_id,
                    entry.producer.value,
                    entry.payload.model_dump_json(),
                ),
            )

    def read_by_round(self, round_id: str) -> list[EventLogEntry]:
        """按轮次读，顺序就是落库顺序（rowid）。

        消费者是复盘（M7 观测）与验收测试。查询投影 / 分页等留给真要用的时候再加。
        """
        rows = self._db._connection.execute(
            "SELECT * FROM events WHERE round_id = ? ORDER BY rowid", (round_id,)
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> EventLogEntry:
        event_type = EventType(row["type"])
        return EventLogEntry(
            event_id=row["event_id"],
            at=row["at"],
            type=event_type,
            producer=ProducerIdentity(row["producer"]),
            payload=PAYLOAD_FOR[event_type].model_validate_json(row["payload_json"]),
            round_id=row["round_id"],
            project_id=row["project_id"],
        )
