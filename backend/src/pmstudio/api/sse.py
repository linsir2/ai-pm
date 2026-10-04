"""SSE：只推事件，不做业务。心跳保证连接不被中间层掐掉。"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum

from pmstudio.harness.events import EventStream

DEFAULT_HEARTBEAT_SECONDS = 15.0


def as_json(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: as_json(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): as_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [as_json(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


async def event_stream(
    stream: EventStream, *, heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS
) -> AsyncIterator[str]:
    iterator = stream.subscribe()
    # 建流先发一行注释：`EventSource` 的 onopen 因此立刻到，顶栏不用等 15 秒心跳
    # 才显示"已连接"（`04-未决.md` 界面侧-2）。
    yield ": ready\n\n"
    while True:
        try:
            event = await asyncio.wait_for(anext(iterator), timeout=heartbeat_seconds)
        except TimeoutError:
            yield ": ping\n\n"
            continue
        except StopAsyncIteration:
            return
        payload = json.dumps(as_json(event), ensure_ascii=False)
        yield f"event: {event.name}\ndata: {payload}\n\n"
