"""FastAPI 依赖：从 cookie 解析当前用户，并把它的 owner 装进请求上下文。

`require_user` 做两件事——读 cookie、调 `GetMe`（过期/吊销/非 active 都在那里判），
拿不到就抛 `unauthenticated`（`api/errors.py` 映射 401）；拿到之后**把当前用户设进
owner 上下文**（D2），这样同一请求里的内容读/写都按这个归属过滤、盖章。请求结束清理
（生成器依赖的 `finally`：正常路径在响应前后各跑一次，绝不把归属漏给下一个请求）。

owner 上下文模块经 `app.state.owner_scope` 取（装配点在 `bootstrap/container.py` 注入）——
api 层不许 import infrastructure（`tests/architecture/test_layers.py` 守着）。

`require_admin` 在其上再验角色（→403）。
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request

from pmstudio.application.use_cases import UserView
from pmstudio.harness.errors import ApplicationError

#: cookie 名**定死**：登录/登出/`/me` 全用它，前端（Task 7）按此名读——改名即全链返工。
SESSION_COOKIE = "pm_session"


async def require_user(request: Request) -> AsyncIterator[UserView]:
    token = request.cookies.get(SESSION_COOKIE, "")
    user, renewed = await request.app.state.auth.get_me(token=token)
    # 滑动续期发生在 `GetMe` 里；端点（如 `/me`）据这个标记重发 cookie 让浏览器侧跟上
    request.state.session_renewed = renewed
    scope = request.app.state.owner_scope
    scope_token = scope.set_owner_scope(user.user_id)
    try:
        yield user
    finally:
        scope.clear_owner_scope(scope_token)


async def require_admin(
    user: Annotated[UserView, Depends(require_user)],
) -> UserView:
    if user.role != "admin":
        raise ApplicationError("需要管理员权限", code="forbidden")
    return user
