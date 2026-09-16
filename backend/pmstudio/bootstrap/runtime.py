"""把实现接到接口上，交出一个能用的运行时。

启动顺序是钉死的：**开库（拿单实例锁）→ 恢复上次没跑完的轮 → 才交出运行时**。
顺序反了会出事：新一轮要是先开，`open_round` 会把失败轮的区块清掉，那就再也看不到"跑到哪了"。

**运行时里没有写句柄**（`BoardEditor`）。I19 说黑板只由编排层写；把它放进一个谁都能拿的
对象里，等于装配这一步自己把规矩破掉。写句柄留在 `build_runtime` 内部，只交给需要它的组件
（当前是恢复流程，R1 再单独给编排层开一个入口）。
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from pmstudio.common.clock import Clock, SystemClock
from pmstudio.common.ids import IdGenerator, TimestampIdGenerator
from pmstudio.communication.board import Blackboard, BoardReader
from pmstudio.communication.event_bus import EventBus
from pmstudio.storage.board_store import BoardStore
from pmstudio.storage.db import Database
from pmstudio.storage.event_log import EventLog
from pmstudio.storage.ledger import Ledger


@dataclass(frozen=True)
class Runtime:
    """装配的结果。各层从这里拿接口，不拿实现——除了 L7 自己的两个仓库。"""

    db: Database
    clock: Clock
    ids: IdGenerator
    ledger: Ledger
    event_log: EventLog
    event_bus: EventBus
    board: BoardReader
    recovered_rounds: tuple[str, ...] = field(default=())

    def close(self) -> None:
        """关库并放锁。可以重复调。"""
        self.db.close()


async def build_runtime(
    db_path: str | Path,
    *,
    clock: Clock | None = None,
    ids: IdGenerator | None = None,
) -> Runtime:
    """开库、拿锁、恢复、装配。"""
    resolved_clock = clock or SystemClock()
    resolved_ids = ids or TimestampIdGenerator()

    db = Database.open(db_path)
    try:
        store = BoardStore(db)
        event_log = EventLog(db)
        event_bus = EventBus(event_log, ids=resolved_ids)
        blackboard = Blackboard(store, event_bus, resolved_clock)
        recovered = await blackboard.recover_unfinished_rounds()
    except BaseException:
        db.close()
        raise

    return Runtime(
        db=db,
        clock=resolved_clock,
        ids=resolved_ids,
        ledger=Ledger(db, resolved_ids),
        event_log=event_log,
        event_bus=event_bus,
        board=BoardReader(blackboard),
        recovered_rounds=recovered,
    )


def build_runtime_sync(db_path: str | Path, **kwargs: object) -> Runtime:
    """给命令行与测试用的同步外壳。已经在事件循环里的时候用 `build_runtime`。"""
    return asyncio.run(build_runtime(db_path, **kwargs))  # type: ignore[arg-type]
