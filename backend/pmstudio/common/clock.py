"""时间端口。

测试里注入可控时钟——断言里出现真实时间就没法复现（directory.md §3.2）。
"""

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """现在几点。"""

    def now(self) -> datetime: ...


class SystemClock:
    """真实现：UTC。"""

    def now(self) -> datetime:
        return datetime.now(UTC)
