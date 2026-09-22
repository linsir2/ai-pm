"""测试用的 ASGI 客户端。

不用 `fastapi.testclient.TestClient`：它在当前沙箱里会挂死（连空 FastAPI 应用都挂），
因为它把 ASGI 应用跑在另一个线程的 portal 里，而本环境的线程化执行不可靠。
`httpx.ASGITransport` 在**同一个事件循环**里跑，行为一致且不会挂。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI


@asynccontextmanager
async def async_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
