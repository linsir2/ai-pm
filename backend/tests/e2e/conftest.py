"""端到端夹具：真 SQLite、真用例、真图、真席位目录；只有模型是脚本化的。

脚本化模型是**测试专用**：生产容器（`build_app`）里没有"用假模型"的开关。
两次 `build_app(tmp_path, ...)` 用同一个 db 文件 = **换一个进程接着跑**（内存里什么都没留下）。
"""

from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi import FastAPI

from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import Settings
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.sqlite import owner_scope
from tests.support.doubles import ScriptedModelGateway

#: e2e 默认 owner（多用户改造 Task 5 / D2）：真 SQLite 的读写都要有归属。
#: 生产里由 `require_user`（Task 6）在每个请求里设置；这里在装配时给整条链路装一个，
#: 让既有 e2e 无需逐处传 owner。contextvar 在测试体（同 async 任务）里设，会传给 await 的 app。
E2E_OWNER = UserId("u-e2e-owner")


def build_app(
    tmp_path: Path,
    *,
    replies: dict[str, str | Sequence[str]],
    seat_replies: dict[str, str | Sequence[str]] | None = None,
    first_admin: bool = False,
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
    )
    container = build_container(settings, gateway=gateway)
    return container.app, gateway


@pytest.fixture()
def make_app(tmp_path):
    def _make(
        *,
        replies: dict[str, str | Sequence[str]],
        seat_replies: dict[str, str | Sequence[str]] | None = None,
    ) -> tuple[FastAPI, ScriptedModelGateway]:
        return build_app(tmp_path, replies=replies, seat_replies=seat_replies)

    return _make
