"""探索运行的跨进程租约（P3b Task 3）：真 PostgreSQL、真 HTTP、两个 app 抢同一项目。

这里钉住四件事：

1. `RunInquiry.create` 在 INSERT 的同一个 UoW 里写 lease；`ExplorationRunner.start`
   不再二次 acquire，第二个 worker 在唯一的 running 行上拿不到锁 → 409。
2. `clear_stale` 只清过期 / 老库无过期时间的 running，活租约不动；查询不受 20 条限制。
3. `RetryExploration.resume` 同 UoW 置 RUNNING + acquire；两个 worker 同时 retry 只有一个跑。
4. `KeepCandidate` / `DismissCandidate` 是非 running 的短控制写，不要求租约；
   失租的 runner 返回 409 `exploration_lease_lost`。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime

import pytest
from psycopg.types.json import Jsonb

from pmstudio.application.errors import ExplorationBusy, LeaseLost
from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import AppSettings
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationScope,
    ScopeKind,
)
from pmstudio.domain.shared.ids import ExplorationId, ProjectId, UserId
from pmstudio.domain.shared.lease import LeaseKind
from pmstudio.infrastructure.postgres.leases import PgLeaseStore
from pmstudio.infrastructure.sqlite.repositories import SqliteExplorationRepository
from tests.e2e.conftest import _ensure_authed_user
from tests.support.api_client import async_client
from tests.support.doubles import ScriptedModelGateway
from tests.support.postgres import open_direct


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


INQUIRY_REPLY = json.dumps(
    {
        "basis": "读了文档",
        "hypotheses": [
            {
                "local_id": "H1",
                "statement": "运营按客户名找单",
                "grounding": "invented",
                "based_on": [],
                "falsification": "访谈没人这么找",
                "evidence_needed": "两个访谈",
            }
        ],
        "blind_spots": [
            {"local_id": "B1", "statement": "归属未知", "next_step": "问数据组"}
        ],
    },
    ensure_ascii=False,
)


class BlockingInquiryGateway(ScriptedModelGateway):
    """第一次 inquiry 调用卡住：把探索钉在 running，好测跨进程互斥与失租。"""

    def __init__(self) -> None:
        super().__init__(replies={"inquiry": INQUIRY_REPLY})
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.blocked = 0

    async def complete(self, request):  # type: ignore[no-untyped-def]
        if request.purpose == "inquiry":
            self.blocked += 1
            self.entered.set()
            await self.release.wait()
        return await super().complete(request)


def _app(gateway: ScriptedModelGateway):  # type: ignore[no-untyped-def]
    settings = AppSettings(
        database_url=os.environ["PMSTUDIO_DATABASE_URL"],
        model="scripted",
        api_key="test-key",
        api_base=None,
        login_ip_max_attempts=10_000,
        login_username_max_attempts=10_000,
    )
    from pmstudio.infrastructure.postgres.repositories import SqliteTemplateRepository
    from pmstudio.infrastructure.templates.loader import seed_builtin_templates

    with open_direct() as connection:
        seed_builtin_templates(SqliteTemplateRepository(connection))
    app = build_container(settings, gateway=gateway).app
    _ensure_authed_user(app)
    return app


async def _project(client) -> str:  # type: ignore[no-untyped-def]
    project_id = (
        await client.post("/api/projects", json={"title": "探索租约"})
    ).json()["project_id"]
    edited = await client.post(
        f"/api/projects/{project_id}/edits",
        json={"op": "add", "target": {"zone": "body", "section_id": "s2"}, "text": "整理成文档"},
    )
    assert edited.status_code == 200, edited.text
    return str(project_id)


def _owner_of(project_id: str) -> str:
    connection = open_direct()
    try:
        row = connection.execute(
            "SELECT owner_id FROM documents WHERE project_id = %s", (project_id,)
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return str(row["owner_id"])


def _insert_exploration(
    *,
    exploration_id: str,
    project_id: str,
    owner: str,
    status: str = "running",
    created_at: str = "2020-01-01T00:00:00+00:00",
    lease_owner: str | None = None,
    lease_expires_at: int | None = None,
) -> None:
    payload = (
        {"failure": {"reason": "上次跑挂了"}} if status == "failed" else {}
    )
    connection = open_direct()
    try:
        connection.execute(
            "INSERT INTO explorations (exploration_id, project_id, owner_id, kind, status,"
            " payload, created_at, updated_at, lease_owner, lease_expires_at)"
            " VALUES (%s, %s, %s, 'inquiry', %s, %s, %s, %s, %s, %s)",
            (
                exploration_id,
                project_id,
                owner,
                status,
                Jsonb(payload),
                created_at,
                created_at,
                lease_owner,
                lease_expires_at,
            ),
        )
    finally:
        connection.close()


def _row(exploration_id: str) -> dict:
    connection = open_direct()
    try:
        row = connection.execute(
            "SELECT status, lease_owner, lease_token, lease_expires_at, owner_id"
            " FROM explorations WHERE exploration_id = %s",
            (exploration_id,),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return row


def _running_count(project_id: str) -> int:
    connection = open_direct()
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM explorations WHERE project_id = %s"
                " AND status = 'running'",
                (project_id,),
            ).fetchone()["n"]
        )
    finally:
        connection.close()


async def test_start_writes_lease_and_keep_dismiss_do_not_need_one() -> None:
    """C2：真 SQLite 的 M8 不再 500；收下/删掉是 done 行上的短控制写。"""

    app = _app(ScriptedModelGateway(replies={"inquiry": INQUIRY_REPLY}))

    async with async_client(app) as client:
        project_id = await _project(client)
        started = await client.post(
            f"/api/projects/{project_id}/explorations",
            json={"kind": "inquiry", "scope": {"kind": "document"}},
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["status"] == "done"
        xid = body["exploration_id"]

        # 租约只在跑的时候存在；跑完必须释放（0 行不是错误，但这里应该正好释放成功）
        row = _row(xid)
        assert row["status"] == "done"
        assert row["lease_owner"] is None
        assert row["lease_token"] == 1

        kept = await client.post(
            f"/api/explorations/{xid}/candidates/H1/keep", json={"as": "seed"}
        )
        assert kept.status_code == 200, kept.text
        dismissed = await client.post(
            f"/api/explorations/{xid}/candidates/B1/dismiss"
        )
        assert dismissed.status_code == 200, dismissed.text


async def test_two_workers_start_only_one_runs_the_other_is_409() -> None:
    gateway = BlockingInquiryGateway()
    first_app = _app(gateway)
    second_gateway = ScriptedModelGateway(replies={"inquiry": INQUIRY_REPLY})
    second_app = _app(second_gateway)

    async with async_client(first_app) as first, async_client(second_app) as second:
        project_id = await _project(first)
        pending = asyncio.create_task(
            first.post(
                f"/api/projects/{project_id}/explorations",
                json={"kind": "inquiry", "scope": {"kind": "document"}},
            )
        )
        await asyncio.wait_for(gateway.entered.wait(), timeout=5)

        connection = open_direct()
        try:
            running = connection.execute(
                "SELECT exploration_id, lease_owner, lease_token FROM explorations"
                " WHERE project_id = %s AND status = 'running'",
                (project_id,),
            ).fetchall()
        finally:
            connection.close()
        assert len(running) == 1
        assert running[0]["lease_owner"] is not None
        assert running[0]["lease_token"] == 1  # INSERT 同 UoW 的 acquire：token 从 1 开始

        busy = await second.post(
            f"/api/projects/{project_id}/explorations",
            json={"kind": "inquiry", "scope": {"kind": "document"}},
        )
        assert busy.status_code == 409, busy.text
        assert busy.json()["code"] == "exploration_busy"
        assert gateway.blocked == 1
        assert second_gateway.requests == []

        gateway.release.set()
        finished = await asyncio.wait_for(pending, timeout=5)
        assert finished.status_code == 200, finished.text
        assert finished.json()["status"] == "done"
        assert _running_count(project_id) == 0


async def test_legacy_running_beyond_the_20_row_window_is_still_cleared() -> None:
    """`clear_stale` 直接查 running，不借 `list_for_project(limit=20)`。"""

    app = _app(ScriptedModelGateway(replies={"inquiry": INQUIRY_REPLY}))

    async with async_client(app) as client:
        project_id = await _project(client)
        owner = _owner_of(project_id)

        # 唯一一条 running 是最老的，且是 v17 之前的老行（没有过期时间）：
        # 按 20 条窗口会被 20 条更新的 done 挤出视野，start 就会撞唯一索引。
        _insert_exploration(
            exploration_id="x-legacy",
            project_id=project_id,
            owner=owner,
            lease_owner="dead-worker",
            lease_expires_at=None,
        )
        for index in range(20):
            _insert_exploration(
                exploration_id=f"x-done-{index:02d}",
                project_id=project_id,
                owner=owner,
                status="done",
                created_at=f"2026-01-01T00:{index:02d}:00+00:00",
            )

        started = await client.post(
            f"/api/projects/{project_id}/explorations",
            json={"kind": "inquiry", "scope": {"kind": "document"}},
        )
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "done"
        assert _row("x-legacy")["status"] == "abandoned"


async def test_clear_stale_keeps_a_live_lease_and_start_is_busy() -> None:
    """M8 与研究共用一个项目锁：活租约的 running 不被 clear，只回 409。"""

    app = _app(ScriptedModelGateway(replies={"inquiry": INQUIRY_REPLY}))

    async with async_client(app) as client:
        project_id = await _project(client)
        owner = _owner_of(project_id)
        _insert_exploration(
            exploration_id="x-live",
            project_id=project_id,
            owner=owner,
            lease_owner="another-worker",
            lease_expires_at=_now_ms() + 90_000,
        )

        busy = await client.post(
            f"/api/projects/{project_id}/explorations",
            json={"kind": "inquiry", "scope": {"kind": "document"}},
        )
        assert busy.status_code == 409, busy.text
        assert busy.json()["code"] == "exploration_busy"

        row = _row("x-live")
        assert row["status"] == "running"
        assert row["lease_owner"] == "another-worker"


async def test_retry_clears_stale_first_and_holds_the_lease() -> None:
    """retry 先 clear_stale（同项目僵壳），再在同一 UoW 把 failed 置 RUNNING + acquire。"""

    gateway = BlockingInquiryGateway()
    first_app = _app(gateway)
    second_gateway = ScriptedModelGateway(replies={"inquiry": INQUIRY_REPLY})
    second_app = _app(second_gateway)

    async with async_client(first_app) as first, async_client(second_app) as second:
        project_id = await _project(first)
        owner = _owner_of(project_id)
        _insert_exploration(
            exploration_id="x-failed",
            project_id=project_id,
            owner=owner,
            status="failed",
        )
        _insert_exploration(
            exploration_id="x-stale",
            project_id=project_id,
            owner=owner,
            lease_owner="dead-worker",
            lease_expires_at=_now_ms() - 1_000,
        )

        pending = asyncio.create_task(
            first.post("/api/explorations/x-failed/retry")
        )
        await asyncio.wait_for(gateway.entered.wait(), timeout=5)

        row = _row("x-failed")
        assert row["status"] == "running"
        assert row["lease_owner"] is not None
        assert row["lease_token"] == 1
        assert _row("x-stale")["status"] == "abandoned"
        assert _running_count(project_id) == 1

        busy = await second.post("/api/explorations/x-failed/retry")
        assert busy.status_code == 409, busy.text
        assert busy.json()["code"] == "exploration_busy"
        assert gateway.blocked == 1
        assert second_gateway.requests == []

        gateway.release.set()
        finished = await asyncio.wait_for(pending, timeout=5)
        assert finished.status_code == 200, finished.text
        assert finished.json()["status"] == "done"


async def test_abandon_force_acquires_and_fences_the_running_worker() -> None:
    """放弃走 force_acquire：同一 UoW 写 ABANDONED + 释放；在跑的 worker 被失租停下。"""

    gateway = BlockingInquiryGateway()
    first_app = _app(gateway)
    first_app.state.services.exploration_runner._lease_heartbeat_seconds = 0.02
    second_app = _app(ScriptedModelGateway(replies={"inquiry": INQUIRY_REPLY}))

    async with async_client(first_app) as first, async_client(second_app) as second:
        project_id = await _project(first)
        pending = asyncio.create_task(
            first.post(
                f"/api/projects/{project_id}/explorations",
                json={"kind": "inquiry", "scope": {"kind": "document"}},
            )
        )
        await asyncio.wait_for(gateway.entered.wait(), timeout=5)
        connection = open_direct()
        try:
            xid = connection.execute(
                "SELECT exploration_id FROM explorations"
                " WHERE project_id = %s AND status = 'running'",
                (project_id,),
            ).fetchone()["exploration_id"]
        finally:
            connection.close()

        abandoned = await second.post(f"/api/explorations/{xid}/abandon")
        assert abandoned.status_code == 200, abandoned.text
        assert abandoned.json()["status"] == "abandoned"
        assert _row(str(xid))["status"] == "abandoned"

        lost = await asyncio.wait_for(pending, timeout=5)

    gateway.release.set()
    assert lost.status_code == 409, lost.text
    assert lost.json()["code"] == "exploration_lease_lost"


async def test_retry_resume_hitting_another_running_row_is_409() -> None:
    """resume 把 failed 置 RUNNING 时撞同项目另一条 running：唯一索引 → ExplorationBusy。"""

    app = _app(ScriptedModelGateway(replies={"inquiry": INQUIRY_REPLY}))

    async with async_client(app) as client:
        project_id = await _project(client)
        owner = _owner_of(project_id)
        _insert_exploration(
            exploration_id="x-live",
            project_id=project_id,
            owner=owner,
            lease_owner="another-worker",
            lease_expires_at=_now_ms() + 90_000,
        )
        _insert_exploration(
            exploration_id="x-failed",
            project_id=project_id,
            owner=owner,
            status="failed",
        )

        busy = await client.post("/api/explorations/x-failed/retry")
        assert busy.status_code == 409, busy.text
        assert busy.json()["code"] == "exploration_busy"
        assert _row("x-failed")["status"] == "failed"
        assert _row("x-live")["status"] == "running"


async def test_lost_lease_returns_409_exploration_lease_lost() -> None:
    gateway = BlockingInquiryGateway()
    app = _app(gateway)
    app.state.services.exploration_runner._lease_heartbeat_seconds = 0.02

    async with async_client(app) as client:
        project_id = await _project(client)
        pending = asyncio.create_task(
            client.post(
                f"/api/projects/{project_id}/explorations",
                json={"kind": "inquiry", "scope": {"kind": "document"}},
            )
        )
        await asyncio.wait_for(gateway.entered.wait(), timeout=5)

        connection = open_direct()
        try:
            row = connection.execute(
                "SELECT exploration_id, owner_id FROM explorations"
                " WHERE project_id = %s AND status = 'running'",
                (project_id,),
            ).fetchone()
        finally:
            connection.close()
        xid, owner = row["exploration_id"], row["owner_id"]

        thief = open_direct()
        try:
            PgLeaseStore(thief).force_acquire(
                LeaseKind.EXPLORATION,
                str(xid),
                UserId(owner),
                "thief",
                90_000,
            )
        finally:
            thief.close()

        lost = await asyncio.wait_for(pending, timeout=5)

    gateway.release.set()
    assert lost.status_code == 409, lost.text
    assert lost.json()["code"] == "exploration_lease_lost"
    assert _row(str(xid))["lease_owner"] == "thief"


def test_done_rows_are_exempt_but_running_rows_are_fenced() -> None:
    """仓储 fence：done 行（收下/删掉）不要求租约；running 行必须持有。"""

    connection = open_direct()
    try:
        repo = SqliteExplorationRepository(connection)
        run = Exploration.start(
            exploration_id=ExplorationId("x-1"),
            project_id=ProjectId("p1"),
            scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
            now=datetime.now(UTC),
        )
        repo.save(run)  # 新 INSERT 允许无租约
        run.abandon("进程崩了")
        with pytest.raises(LeaseLost):
            repo.save(run)  # running -> abandoned 仍受 fence 约束

        first = repo.get(ExplorationId("x-1"))
        connection.execute(
            "UPDATE explorations SET status = 'done' WHERE exploration_id = 'x-1'"
        )
        done = repo.get(ExplorationId("x-1"))
        repo.save(done)  # done -> done 豁免
        assert first.status.value == "running"
        assert repo.get(ExplorationId("x-1")).status.value == "done"
    finally:
        connection.close()


def test_two_running_rows_in_one_project_raise_exploration_busy() -> None:
    connection = open_direct()
    try:
        repo = SqliteExplorationRepository(connection)
        first = Exploration.start(
            exploration_id=ExplorationId("x-1"),
            project_id=ProjectId("p1"),
            scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
            now=datetime.now(UTC),
        )
        second = Exploration.start(
            exploration_id=ExplorationId("x-2"),
            project_id=ProjectId("p1"),
            scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
            now=datetime.now(UTC),
        )
        repo.save(first)
        with pytest.raises(ExplorationBusy):
            repo.save(second)
    finally:
        connection.close()
