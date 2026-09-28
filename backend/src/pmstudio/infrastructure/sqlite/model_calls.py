"""模型留样：只增、旁路、可开关（`02` §5.3）。

写不进去也不许影响这一轮——记录是给人排查用的，不是业务事实
（P4 的 `O1` 就是靠重放才定性的）。提示词存**原文**（截断）：只存哈希复现不了，
而复现是这张表唯一的目的。
"""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

PROMPT_LIMIT = 20_000


@dataclass(frozen=True, slots=True)
class ModelCallRow:
    round_id: str | None
    purpose: str
    model: str
    prompt_sha: str
    prompt_text: str
    reply_text: str
    usage: dict[str, object]
    duration_ms: int
    created_at: datetime


class SqliteModelCallRecorder:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record(
        self,
        *,
        round_id: str | None,
        purpose: str,
        model: str,
        prompt_sha: str,
        prompt_text: str,
        reply_text: str,
        usage: Mapping[str, object],
        duration_ms: int,
        created_at: datetime,
    ) -> None:
        try:
            self._connection.execute(
                "INSERT INTO model_calls (round_id, purpose, model, prompt_sha, prompt_text,"
                " reply_text, usage_json, duration_ms, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    round_id,
                    purpose,
                    model,
                    prompt_sha,
                    prompt_text[:PROMPT_LIMIT],
                    reply_text,
                    json.dumps(dict(usage), ensure_ascii=False),
                    duration_ms,
                    created_at.isoformat(),
                ),
            )
        except (sqlite3.Error, TypeError, ValueError):
            # 旁路：记不上就算了，不许把这一轮搞失败。
            # TypeError/ValueError 是防 `json.dumps` 撞上不可序列化的对象（真模型踩过一次：
            # litellm 的 usage 里有 pydantic 包装对象，那一次整个轮次被留样搞失败了）。
            return

    def recent(
        self, *, round_id: str | None = None, limit: int = 50
    ) -> tuple[ModelCallRow, ...]:
        rows = self._connection.execute(
            "SELECT * FROM model_calls"
            + (" WHERE round_id = ?" if round_id else "")
            + " ORDER BY id DESC LIMIT ?",
            ((round_id, limit) if round_id else (limit,)),
        ).fetchall()
        return tuple(
            ModelCallRow(
                round_id=row["round_id"],
                purpose=row["purpose"],
                model=row["model"],
                prompt_sha=row["prompt_sha"],
                prompt_text=row["prompt_text"],
                reply_text=row["reply_text"],
                usage=json.loads(row["usage_json"]),
                duration_ms=row["duration_ms"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        )
