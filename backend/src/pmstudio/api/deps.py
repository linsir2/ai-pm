"""FastAPI 依赖：从 cookie 解析当前用户。

`require_user` 只做一件事——读 cookie、调 `GetMe`（过期/吊销/非 active 都在那里判），
拿不到就抛 `unauthenticated`（`api/errors.py` 映射 401）。`require_admin` 在其上再验角色。
"""

from typing import Annotated

from fastapi import Depends, Request

from pmstudio.application.use_cases import UserView
from pmstudio.harness.errors import ApplicationError

#: cookie 名**定死**：登录/登出/`/me` 全用它，前端（Task 7）按此名读——改名即全链返工。
SESSION_COOKIE = "pm_session"


async def require_user(request: Request) -> UserView:
    token = request.cookies.get(SESSION_COOKIE, "")
    return await request.app.state.auth.get_me(token=token)


async def require_admin(
    user: Annotated[UserView, Depends(require_user)],
) -> UserView:
    if user.role != "admin":
        raise ApplicationError("需要管理员权限", code="forbidden")
    return user
