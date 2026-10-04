"""模型留样：只增、旁路、可开关（`02` §5.3）。

写不进去也不许影响这一轮——记录是给人排查用的，不是业务事实
（P4 的 `O1` 就是靠重放才定性的）。提示词存**原文**（截断）：只存哈希复现不了，
而复现是这张表唯一的目的。

**为什么这张表留在 infrastructure 而没搬进 harness**：它是 SQLite 仓库，搬进去等于
让控制平面拥有持久化；而查询侧 `recent()` 零生产调用，没有任何强制力把它拉进内层。
**为什么列名仍是 `round_id`**（协议侧已改名 `trace_id`，见 `harness/model/contracts.py`）：
那列里装的就是 `r-xxxx`，改名叫 trace_id 等于让列名撒谎。
"""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from pmstudio.harness.model.prompt_log import keep_head_and_tail


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
    attempt: int = 1
    ok: bool = True
    error: str | None = None


class SqliteModelCallRecorder:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record(
        self,
        *,
        trace_id: str | None,
        purpose: str,
        model: str,
        prompt_sha: str,
        prompt_text: str,
        reply_text: str,
        usage: Mapping[str, object],
        duration_ms: int,
        created_at: datetime,
        attempt: int = 1,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        try:
            self._connection.execute(
                "INSERT INTO model_calls (round_id, purpose, model, prompt_sha, prompt_text,"
                " reply_text, usage_json, duration_ms, created_at, attempt, ok, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    # `trace_id`（协议侧的中性名）落进 `round_id` 列（存储侧的产品名）
                    trace_id,
                    purpose,
                    model,
                    prompt_sha,
                    keep_head_and_tail(prompt_text),
                    reply_text,
                    json.dumps(dict(usage), ensure_ascii=False),
                    duration_ms,
                    created_at.isoformat(),
                    attempt,
                    int(ok),
                    error,
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
                attempt=row["attempt"],
                ok=bool(row["ok"]),
                error=row["error"],
            )
            for row in rows
        )

