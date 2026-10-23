"""工具调用留样：与 `model_calls` 同一条纪律（默认不写、旁路、要清就 DELETE）。

**为什么要有它**（可观测性 v2）：Agent 系统的可观测单位是「一次外部调用」，
而本项目此前只有模型侧的账（`model_calls`）——工具调用只在**失败**时留一行自由文本
（`Round.dropped`）。于是「这一轮做了什么、哪一步慢、哪一类失败」没有统一答案：
只记失败答不出「这个工具慢不慢」。

**写不进去也不许影响这一轮**——记录是给人排查用的，不是业务事实（与模型留样逐字同款）。

**没有 `recent()`**：读侧（`SqliteRoundCallLog`）要跨 `model_calls` / `tool_calls`
两张表 `UNION ALL`，用不上单表读方法；留一个零生产消费者的读方法正是本仓明令反对的
「加了没人用」。
"""

import json
import logging
import sqlite3
from collections.abc import Mapping
from datetime import datetime

logger = logging.getLogger(__name__)


def _mentions_lock(text: str) -> bool:
    """这条 sqlite 错误是「等锁超时」吗。

    **与 `model_calls._mentions_lock` 逐字同款**（那一处另有与 `api/errors.py` 的关系说明）：
    这一层不许 import `api`（分层守卫），两边各留一份小判据，好过为了共享它去动分层规则。
    """

    lowered = text.lower()
    return "locked" in lowered or "busy" in lowered


class SqliteToolCallRecorder:
    """把每次工具调用写进 `tool_calls`。**旁路**：写不进去只 warning，不抛。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        #: 已留过一次痕。**按状态转换留、不按发生次数留**——争用是**持续**故障，
        #: 每次都留会把日志刷爆（形状照抄 `SqliteModelCallRecorder`）。
        self._warned = False

    def record(
        self,
        *,
        trace_id: str | None,
        tool_id: str,
        arguments: Mapping[str, object],
        result_note: str,
        duration_ms: int,
        created_at: datetime,
        ok: bool = True,
        error: str | None = None,
        error_kind: str | None = None,
    ) -> None:
        try:
            self._connection.execute(
                "INSERT INTO tool_calls (round_id, tool_id, arguments, result_note,"
                " duration_ms, created_at, ok, error, error_kind)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    # `trace_id`（协议侧的中性名）落进 `round_id` 列（存储侧的产品名）
                    trace_id,
                    tool_id,
                    json.dumps(dict(arguments), ensure_ascii=False),
                    result_note,
                    duration_ms,
                    created_at.isoformat(),
                    int(ok),
                    error,
                    error_kind,
                ),
            )
        except sqlite3.OperationalError as error:
            # **旁路语义不变**：记不上就算了，不许把这一轮搞失败。
            if _mentions_lock(str(error)):
                detail = (
                    "写争用（等锁超时）——多 worker 下的常态，与留样本身无关，"
                    "业务写也会遇到同样的争用"
                )
            else:
                detail = f"不是写争用，是部署/schema 问题：{type(error).__name__}"
            self._warn_once(f"工具留样写库失败：{detail}（{error}）")
            return
        except (sqlite3.Error, TypeError, ValueError) as error:
            # TypeError/ValueError 是防 `json.dumps` 撞上不可序列化的对象——
            # 那是**代码 bug**，不是容量问题，措辞要与上一个分支分开。
            self._warn_once(f"工具留样写库失败（多半是序列化）：{type(error).__name__}: {error}")
            return
        else:
            # **成功也要清标志**：否则进程跑几天之后那次偶发丢失就再也看不见了。
            self._warned = False

    def _warn_once(self, message: str) -> None:
        """按状态转换留痕，不按发生次数留（形状照抄模型留样）。"""

        if self._warned:
            return
        self._warned = True
        logger.warning("%s（后续同类失败不再重复留痕）", message)
