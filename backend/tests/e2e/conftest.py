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
from tests.support.doubles import ScriptedModelGateway


def build_app(
    tmp_path: Path,
    *,
    replies: dict[str, str | Sequence[str]],
    seat_replies: dict[str, str | Sequence[str]] | None = None,
) -> tuple[FastAPI, ScriptedModelGateway]:
    gateway = ScriptedModelGateway(replies=replies, seat_replies=seat_replies or {})
    settings = Settings(
        db_path=tmp_path / "pmstudio.db", model="scripted", api_key="test-key", api_base=None
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
