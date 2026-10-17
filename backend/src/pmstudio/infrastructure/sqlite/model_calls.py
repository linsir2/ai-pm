"""模型留样：只增、旁路、可开关（`02` §5.3）。

写不进去也不许影响这一轮——记录是给人排查用的，不是业务事实
（P4 的 `O1` 就是靠重放才定性的）。提示词存**原文**（截断）：只存哈希复现不了，
而复现是这张表唯一的目的。

**为什么这张表留在 infrastructure 而没搬进 harness**：它是 SQLite 仓库，搬进去等于
让控制平面拥有持久化；而查询侧 `recent()` 零生产调用，没有任何强制力把它拉进内层。
**为什么列名仍是 `round_id`**（协议侧已改名 `trace_id`，见 `harness/model/contracts.py`）：
那列里装的是 trace id：轮次是 `r-xxxx`，探索是 `x-xxxx`
（M8.1：inquiry 的 metadata 只有 exploration_id，网关按 round_id → exploration_id 回退取值）。
列名不动——改列名要迁移，伤筋。
"""

import json
import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from pmstudio.harness.model.prompt_log import keep_head_and_tail

logger = logging.getLogger(__name__)


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


def _mentions_lock(text: str) -> bool:
    """这条 sqlite 错误是「等锁超时」吗。

    **与 `api/errors.py::is_busy_error` 同一条规则，但在这一层重写了一遍**——
    不是懒：层守卫 `ALLOWED_LAYER_IMPORTS` 规定 api 只能 import
    api/application/domain/harness，**api 拿不到 infrastructure 的东西**，
    反向 import 也不允许。两边各留一份小判据，好过为了共享它去动分层规则。
    """

    lowered = text.lower()
    return "locked" in lowered or "busy" in lowered


class SqliteModelCallRecorder:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        #: 已留过一次痕。**按状态转换留、不按发生次数留**——争用是**持续**故障，
        #: 每次都留会把日志刷爆。形状照抄 `redis_bus._mark_leg_down`。
        self._warned = False

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
        except sqlite3.OperationalError as error:
            # **旁路语义不变**：记不上就算了，不许把这一轮搞失败。
            # 它是遥测不是审计——OpenBao/K8s 那种"审计失败就让请求失败"的强度
            # 用在这里是反的（留样只是给排查用的记录，不是业务事实）。
            #
            # 但**不许静默**：多 worker 把写并发提上来之后，等锁超时（`OperationalError`）
            # 会**持续**发生，而序列化失败（下面那个分支）是**代码 bug**——
            # 改前两者混成一个 `except`，从外面看都是"没记录"，处理方式完全不同。
            # `OperationalError` 有两种常见成因，用消息分开说——运维要看这一行
            # 就知道该扩容还是该查部署：锁/忙 = 写争用（多 worker 的常态），
            # 别的（no such table / readonly）= 部署或 schema 问题。
            if _mentions_lock(str(error)):
                detail = (
                    "写争用（等锁超时）——多 worker 下的常态，与留样本身无关，"
                    "业务写也会遇到同样的争用"
                )
            else:
                detail = f"不是写争用，是部署/schema 问题：{type(error).__name__}"
            self._warn_once(f"模型留样写库失败：{detail}（{error}）")
            return
        except (sqlite3.Error, TypeError, ValueError) as error:
            # TypeError/ValueError 是防 `json.dumps` 撞上不可序列化的对象（真模型踩过一次：
            # litellm 的 usage 里有 pydantic 包装对象，那一次整个轮次被留样搞失败了）。
            # 那是**代码 bug**，不是容量问题——措辞要与上一个分支分开。
            self._warn_once(f"模型留样写库失败（多半是序列化）：{type(error).__name__}: {error}")
            return
        else:
            # **成功也要清标志**：否则进程跑几天之后那次偶发丢失就再也看不见了
            # （`redis_bus._mark_leg_up` 立下的同一条规矩）。
            self._warned = False

    def _warn_once(self, message: str) -> None:
        """按状态转换留痕，不按发生次数留。

        **刻意不加计数器**：计数住在这个对象里，而它每 worker 一个——
        `/api/auth/stats` 只会读到**那一个 worker** 的丢弃数却被当成全站数字，
        多 worker 下那是**主动误导**。日志才是真的消费方。
        """

        if self._warned:
            return
        self._warned = True
        logger.warning("%s（后续同类失败不再重复留痕）", message)

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

