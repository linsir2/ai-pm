"""用户跳过席位的真路径 e2e（`tests/api/test_skip_seat_api.py` 的补充，不是重复）。

## 为什么还要这一份

api 那份跑的是 harness-app（内存仓储 + `FakeTransaction`）——它验 HTTP 映射，
但验不到**真 SQLite 的写串行**，而"跳过与交回无 TOCTOU"的证明恰恰立在
`SqliteUnitOfWork` 的 `BEGIN IMMEDIATE` 上。内存仓储里两个并发写 Doe 不存在锁，
那个证明在 api 那份里是**没被执行过的**。

所以这里用真容器（`build_container` + 落盘库 + 真认证 + 真 HTTP，唯一非生产件是
脚本化网关——文档化的测试注入口，`test_no_fakes_in_production` 守着生产路径）。
席位调用挂住（事件门），在真锁上复现"任务先起 → 用户跳过 → 任务再结束"。
"""

import asyncio
from pathlib import Path

from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import Settings
from pmstudio.infrastructure.sqlite import owner_scope  # noqa: E402
from tests.api.test_routes import QUESTION_REPLY  # noqa: E402
from tests.application.test_seat_round import (
    SEAT_REPLY,
    claims_reply,
)
from tests.e2e.conftest import E2E_OWNER
from tests.e2e.test_isolation_matrix import _seed_active_user  # noqa: E402
from tests.support.api_client import async_client
from tests.support.doubles import ScriptedModelGateway

PW = "skip-flow-pw"


class _BlockingGateway(ScriptedModelGateway):
    """第一次席位调用挂住等放行，之后照脚本回。只活在测试里。"""

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self.release = asyncio.Event()
        self._hung_once = False

    async def complete(self, request):  # noqa: ANN001, ANN202
        if request.purpose == "seat_review" and not self._hung_once:
            self._hung_once = True
            await self.release.wait()
        return await super().complete(request)


def _build_real_app(tmp_path: Path):
    """真容器 + 落盘库。照抄 `tests/e2e/conftest.build_app`，只换网关。"""

    owner_scope.set_owner_scope(E2E_OWNER)
    gateway = _BlockingGateway(
        replies={"claims": claims_reply(), "integration": QUESTION_REPLY},
        seat_replies={
            "seat.data_scale": SEAT_REPLY,
            "seat.permissions": SEAT_REPLY,
        },
    )
    settings = Settings(
        db_path=tmp_path / "pmstudio.db",
        model="scripted",
        api_key="test-key",
        api_base=None,
    )
    container = build_container(settings, gateway=gateway)
    return container.app, gateway


async def test_真锁上_跳过与迟交两序都闭合(tmp_path):
    """R：与单测 R6 同一幕，但在真 SQLite 写串行上重演。

    I：opening 在后台挂起（真锁）；P：跳过 → 放行 → opening 跑完；
    R：200 + 包变 skipped + 迟交被丢弃（`current_reviews` 无它）+
    留痕 2 条（跳过 + 迟交）+ 轮继续往下走（问卷出来了）。
    """

    app, gateway = _build_real_app(tmp_path)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)
        await client.post("/api/auth/login", json={"username": "alice", "password": PW})
        project_id = (
            await client.post("/api/projects", json={"title": "跳过真跑"})
        ).json()["project_id"]
        round_id = (
            await client.post(
                f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
            )
        ).json()["round_id"]

        opening = asyncio.create_task(
            client.post(
                f"/api/rounds/{round_id}/opening",
                json={"seats": ["seat.data_scale", "seat.permissions"]},
            )
        )
        await asyncio.sleep(0.05)
        try:
            skipped = await client.post(
                f"/api/rounds/{round_id}/skip-seat",
                json={"seat_id": "seat.data_scale"},
            )
        finally:
            gateway.release.set()
        opened = await opening
        view = (await client.get(f"/api/rounds/{round_id}")).json()

    assert skipped.status_code == 200, skipped.text[:300]
    packet = next(
        p for p in skipped.json()["seat_packets"] if p["seat_id"] == "seat.data_scale"
    )
    assert packet["status"] == "skipped"
    # 放行后 opening 照常走完（问卷出来了）：跳过没把轮搞死。
    assert opened.status_code == 200, opened.text[:300]
    assert opened.json()["waiting_on"] == "questionnaire"

    assert [p["seat_id"] for p in view["seat_packets"] if p["status"] == "completed"] == [
        "seat.permissions"
    ]
    notes = [d for d in view["dropped"] if d["seat_id"] == "seat.data_scale"]
    assert len(notes) == 2, [d["reason"] for d in notes]
    assert {d["source"] for d in notes} == {"user_skipped"}
    assert view["step"] == "awaiting_answers"
