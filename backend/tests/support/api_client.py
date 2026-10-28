"""测试用的 ASGI 客户端。

不用 `fastapi.testclient.TestClient`：它在当前沙箱里会挂死（连空 FastAPI 应用都挂），
因为它把 ASGI 应用跑在另一个线程的 portal 里，而本环境的线程化执行不可靠。
`httpx.ASGITransport` 在**同一个事件循环**里跑，行为一致且不会挂。

多用户改造 Task 6 / Ruling 3 的公共入口在这里：`require_user` 挂上以后业务端点都要身份，
这里给两种测试应用各装一份**测试身份**，既有用例不必逐个补登录：

- **真容器应用**（`build_app`，有 `app.state.auth`）：`async_client` 把 `app.state.test_cookie`
  （`tests/e2e/conftest.py` 种好的会话）当默认 cookie 带上——走真 cookie→session→用户链路。
  auth 用例传 `autologin=False`，应用没有 `test_cookie`，就照旧无身份。
- **静态 Harness 应用**（`create_app(harness.api_services(...))`，没有认证栈）：覆盖
  `require_user` 依赖为一个固定 UserView。它只存在于测试进程里，生产 `build_app` 不提供。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from pmstudio.api.deps import require_user
from pmstudio.application.use_cases import UserView
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.postgres import owner_scope

#: 静态 Harness 应用里的固定身份（只有内部一致性有意义；不参与跨用户断言）。
_HARNESS_USER = UserView(
    user_id=UserId("u-harness"), username="harness", role="admin", status="active"
)


def _harness_user() -> UserView:
    return _HARNESS_USER


@asynccontextmanager
async def async_client(
    app: FastAPI, *, raise_app_exceptions: bool = True
) -> AsyncIterator[httpx.AsyncClient]:
    if not hasattr(app.state, "auth"):
        # 静态 Harness 应用：显式装一个测试身份 + owner 上下文（真链接口要用）。
        app.dependency_overrides.setdefault(require_user, _harness_user)
        if not hasattr(app.state, "owner_scope"):
            app.state.owner_scope = owner_scope
    cookies = getattr(app.state, "test_cookie", None)
    transport = httpx.ASGITransport(
        app=app, raise_app_exceptions=raise_app_exceptions
    )
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        yield client
