"""端到端夹具：真 SQLite、真用例、真图、真席位目录；只有模型是脚本化的。

脚本化模型是**测试专用**：生产容器（`build_app`）里没有"用假模型"的开关。
两次 `build_app(tmp_path, ...)` 用同一个 db 文件 = **换一个进程接着跑**（内存里什么都没留下）。
"""

import hashlib
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI

from pmstudio.api.deps import SESSION_COOKIE
from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import Settings
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.security import hash_password
from pmstudio.infrastructure.sqlite import owner_scope
from tests.support.doubles import ScriptedModelGateway

#: e2e 默认 owner（多用户改造 Task 5 / D2）：真 SQLite 的读写都要有归属。
#: 生产里由 `require_user`（Task 6）在每个请求里设置；这里在装配时给整条链路装一个，
#: 让既有 e2e 无需逐处传 owner。contextvar 在测试体（同 async 任务）里设，会传给 await 的 app。
E2E_OWNER = UserId("u-e2e-owner")

#: 自动登录用户（多用户改造 Task 6 / Ruling 3）：`require_user` 挂上以后，既有 e2e 若不登录
#: 会全变 401。这里在装配时种一个 active 用户 + 一个会话，`async_client` 把它作为默认 cookie
#: 带上——**一处**公共入口，既有 e2e 不必逐个改。auth 用例自己传 `autologin=False` 关掉。
E2E_USERNAME = "e2e-harness"
E2E_PASSWORD = "e2e-harness-pw"


def _ensure_authed_user(app: FastAPI) -> None:
    """幂等地种一个"测试操作者"并造一条会话，把 cookie 放到 `app.state.test_cookie`。

    幂等是为了 `test_resume` 那种"同一个 db 换进程重建"——第二次不能重名注册。
    """

    users = app.state.auth.users
    record = users.get_by_name(E2E_USERNAME)
    if record is None:
        user_id = users.register(
            username=E2E_USERNAME, password_hash=hash_password(E2E_PASSWORD), role="admin"
        )
        users.set_status(user_id, "active")
    else:
        user_id = record.user_id
    token = secrets.token_urlsafe(24)
    now = datetime.now(UTC)
    app.state.auth.sessions.create(
        user_id=user_id,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        expires_at=now + timedelta(days=1),
        now=now,
    )
    app.state.test_cookie = {SESSION_COOKIE: token}


def build_app(
    tmp_path: Path,
    *,
    replies: dict[str, str | Sequence[str]],
    seat_replies: dict[str, str | Sequence[str]] | None = None,
    first_admin: bool = False,
    autologin: bool = True,
    login_ip_max_attempts: int = 10_000,
    login_username_max_attempts: int = 10_000,
    trace_model: bool = False,
    tavily_api_key: str | None = None,
) -> tuple[FastAPI, ScriptedModelGateway]:
    owner_scope.set_owner_scope(E2E_OWNER)
    gateway = ScriptedModelGateway(replies=replies, seat_replies=seat_replies or {})
    settings = Settings(
        db_path=tmp_path / "pmstudio.db",
        model="scripted",
        api_key="test-key",
        api_base=None,
        # v13 起存量内容必须有归属：换进程重启的用例得有个 admin 让回填接得上。
        # 默认不种——auth 用例要的是"干净的用户表"。
        first_admin_username="admin" if first_admin else None,
        first_admin_password="test-admin-password" if first_admin else None,
        # **e2e 默认把尝试闸放大到不可能触发**：否则多个用例从同一个
        # "testclient" IP 连打登录时会互相限流——测试被自己的防护卡死，
        # 而且这种失败看起来像产品坏了，不像像测试没配好。
        # 要验限流的用例自己传一个小的进去。
        login_ip_max_attempts=login_ip_max_attempts,
        login_username_max_attempts=login_username_max_attempts,
        # 留样（可观测性 v2）：默认关，与生产一致；要验工具留样的用例显式传 True。
        trace_model=trace_model,
        # 联网检索 key：给了才会让 `tool.web_search` 可用（真网关会出网，
        # 所以只给 key、再把网关实例换成替身，见 `test_call_observability.py`）。
        tavily_api_key=tavily_api_key,
    )
    container = build_container(settings, gateway=gateway)
    if autologin:
        _ensure_authed_user(container.app)
    return container.app, gateway


@pytest.fixture()
def make_app(tmp_path):
    def _make(
        *,
        replies: dict[str, str | Sequence[str]],
        seat_replies: dict[str, str | Sequence[str]] | None = None,
        autologin: bool = True,
        login_ip_max_attempts: int = 10_000,
        login_username_max_attempts: int = 10_000,
        trace_model: bool = False,
        tavily_api_key: str | None = None,
    ) -> tuple[FastAPI, ScriptedModelGateway]:
        return build_app(
            tmp_path,
            replies=replies,
            seat_replies=seat_replies,
            autologin=autologin,
            login_ip_max_attempts=login_ip_max_attempts,
            login_username_max_attempts=login_username_max_attempts,
            trace_model=trace_model,
            tavily_api_key=tavily_api_key,
        )

    return _make
