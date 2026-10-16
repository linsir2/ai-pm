"""认证与账号端点（8 个）：注册 / 登录 / 登出 / me / 审批通过 / 拒绝 / 封禁 / 用户列表。

只解析请求、调用例、回 JSON——业务判断一个都不在这儿。用例从 `app.state.auth` 取
（`api/app.py` 里与 `routes.router` 一起挂上 `/api`）。
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from pmstudio.api.deps import SESSION_COOKIE, require_admin, require_user
from pmstudio.application.ports import SessionStore, UserStore
from pmstudio.application.use_cases import (
    ApproveUser,
    AuthenticateUser,
    BanUser,
    GetMe,
    GetUsage,
    ListUsers,
    LogoutUser,
    RegisterUser,
    RejectUser,
    UsageView,
    UserView,
)
from pmstudio.domain.shared.ids import UserId

router = APIRouter(tags=["auth"])


@dataclass(frozen=True, slots=True)
class AuthServices:
    """认证用例的组装结果（`bootstrap/container.py` 造，挂到 `app.state.auth`）。"""

    register_user: RegisterUser
    authenticate_user: AuthenticateUser
    get_me: GetMe
    logout: LogoutUser
    approve_user: ApproveUser
    reject_user: RejectUser
    ban_user: BanUser
    list_users: ListUsers
    get_usage: GetUsage
    users: UserStore
    sessions: SessionStore
    cookie_max_age: int


def _auth(request: Request) -> AuthServices:
    return request.app.state.auth


def _user_json(view: UserView) -> dict[str, str]:
    return {
        "user_id": str(view.user_id),
        "username": view.username,
        "role": view.role,
        "status": view.status,
    }


def _usage_json(view: UsageView) -> dict[str, object]:
    return {
        "user_id": str(view.user_id),
        "username": view.username,
        "calls": view.calls,
    }


def _is_secure(request: Request) -> bool:
    """`Secure` 属性按请求实际协议给：**本地 dev / http 测试传输**（`ASGITransport`）不带，
    **公网 https**（Caddy 反代发的 `X-Forwarded-Proto: https`）才带——写死 `secure=True`
    会让本地 http 下的 cookie 根本不发回（登录后下一次请求就 401），写死 `False` 又会让公网
    session 贴着明文过线；按协议判是唯一两者都对的写法。前提：生产 uvicorn 要信任代理头
    （`--proxy-headers`），否则 Caddy 后面 `url.scheme` 恒为 http，这条就退化成永久不带 Secure。
    """

    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    return scheme == "https"


class CredentialsRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class UserIdRequest(BaseModel):
    user_id: str = Field(min_length=1)


@router.post("/auth/register", status_code=201)
async def register(request: Request, body: CredentialsRequest) -> dict[str, str]:
    view = await _auth(request).register_user(
        username=body.username, password=body.password
    )
    return _user_json(view)


@router.post("/auth/login")
async def login(
    request: Request, response: Response, body: CredentialsRequest
) -> dict[str, str]:
    client = request.client
    view, token = await _auth(request).authenticate_user(
        username=body.username,
        password=body.password,
        ip=client.host if client else "",
        user_agent=request.headers.get("user-agent", ""),
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        # 条件化理由见 `_is_secure`：http 测试传输/本地 dev 不带 Secure，生产 https 才带
        secure=_is_secure(request),
        samesite="lax",
        max_age=_auth(request).cookie_max_age,
    )
    return _user_json(view)


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    await _auth(request).logout(token=request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "ok"}


@router.get("/auth/me")
async def me(user: Annotated[UserView, Depends(require_user)]) -> dict[str, str]:
    return _user_json(user)


@router.get("/auth/users")
async def list_users(
    request: Request, actor: Annotated[UserView, Depends(require_admin)]
) -> dict[str, list[dict[str, str]]]:
    listing = await _auth(request).list_users(actor=actor)
    return {"users": [_user_json(view) for view in listing]}


@router.get("/auth/usage")
async def usage(
    request: Request, actor: Annotated[UserView, Depends(require_admin)]
) -> dict[str, list[dict[str, object]]]:
    """管理员：每个用户的用量（其项目下轮次的模型调用行数）。仅 admin。"""

    listing = await _auth(request).get_usage(actor=actor)
    return {"usage": [_usage_json(view) for view in listing]}


@router.post("/auth/approve")
async def approve(
    request: Request, body: UserIdRequest, actor: Annotated[UserView, Depends(require_admin)]
) -> dict[str, str]:
    view = await _auth(request).approve_user(
        actor=actor, user_id=UserId(body.user_id)
    )
    return _user_json(view)


@router.post("/auth/reject")
async def reject(
    request: Request, body: UserIdRequest, actor: Annotated[UserView, Depends(require_admin)]
) -> dict[str, str]:
    view = await _auth(request).reject_user(
        actor=actor, user_id=UserId(body.user_id)
    )
    return _user_json(view)


@router.post("/auth/ban")
async def ban(
    request: Request, body: UserIdRequest, actor: Annotated[UserView, Depends(require_admin)]
) -> dict[str, str]:
    view = await _auth(request).ban_user(actor=actor, user_id=UserId(body.user_id))
    return _user_json(view)
