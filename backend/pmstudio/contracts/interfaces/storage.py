"""L7 存储底座对外暴露什么。

**这里只有流水的写口。** L7 的其余部分（各实体的读写）文档只给了"各实体的读写"一句话，
没有签名——按 DECISIONS §11 的裁决，谁先当消费者谁定自己那一小块：

- M0.3（事件流）要的是"把一条事件追加进去" → `EventLogPort`
- M0.4（黑板）要的是工作台表与"和账面同一个事务" → 到时候加在这里
- M1（编排与工具）要的是业务读写 → 到时候加

不要为了让这个文件"看起来完整"而预先发明接口：说不出消费者的方法一律不写。
"""

from typing import Protocol, runtime_checkable

from pmstudio.contracts.enums import RegionName
from pmstudio.contracts.skeleton.board import RegionValue, RoundRegion
from pmstudio.contracts.skeleton.events import EventLogEntry


@runtime_checkable
class EventLogPort(Protocol):
    """事件流水：只增不改。"""

    async def append(self, entry: EventLogEntry) -> None: ...


@runtime_checkable
class BoardStorePort(Protocol):
    """工作台：`rounds`（永久）＋ `board_regions`（只装当前轮的四个区块）。

    **`round` 区块的家就是 `rounds` 表**（轮末删区块，`rounds` 那一条留着），
    其余四个区块在 `board_regions`。所以这里有两对读写口。
    """

    async def write_round(self, region: RoundRegion) -> None: ...

    async def read_round(self, round_id: str) -> RoundRegion | None: ...

    async def write_region(self, round_id: str, region: RegionName, value: RegionValue) -> None: ...

    async def read_region(self, round_id: str, region: RegionName) -> RegionValue | None: ...

    async def drop_regions(self, round_id: str) -> None: ...

    async def drop_other_regions(self, round_id: str) -> None: ...
