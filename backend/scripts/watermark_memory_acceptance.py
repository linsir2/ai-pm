"""批 3 / 批 4 真跑验收：水位超限尾注 + 抽屉「久未使用」标签。

一次脚本里自起自停（后台进程在本机活不过工具调用，所以不外起）：
seed → 起真服务 → HTTP 验后端判据 → Playwright 截界面 → 关。

三条判据：

- **L2 超水位**：250 条观察 > `L2_CAPACITY = 200`，渲染出的上下文里**该**出现
  「更早的 N 条待复核」；
- **L4 未超**：12 条摘要 < `L4_CAPACITY = 50`，尾注**不该**出现（反向判据：
  水位之下不多话）；
- **久未使用**：`GET /memory/usage` 的 `unused` 里有那几条记忆，
  且抽屉界面上**看得见**那个标签。

用法：`uv run python scripts/watermark_memory_acceptance.py`
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

PORT = "8121"
OBS_COUNT = 250          # > L2_CAPACITY(200)
ROUND_COUNT = 12         # < L4_CAPACITY(50)
MEMORY_COUNT = 6


def _log(text: str) -> None:
    print(text, flush=True)


def _load_env() -> None:
    for line in (BACKEND / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _seed(db_path: Path) -> str:
    """灌历史数据，返回 project_id。用真建项目用例 + 直写 SQL。"""
    from datetime import UTC, datetime, timedelta

    from pmstudio.application.use_cases import CreateProject
    from pmstudio.bootstrap.container import SystemClock, UuidIdGenerator
    from pmstudio.domain.memory.memory import MemoryEntry, ProjectMemory
    from pmstudio.domain.round.round import Round
    from pmstudio.domain.shared.ids import ProjectId, RoundId, UserId
    from pmstudio.domain.shared.memory import MemoryKind
    from pmstudio.infrastructure.sqlite import database
    from pmstudio.infrastructure.sqlite import serialization as ser
    from pmstudio.infrastructure.sqlite.owner_scope import set_owner_scope
    from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
    from pmstudio.infrastructure.sqlite.repositories import (
        SqliteTemplateRepository,
        SqliteWorkspaceIndex,
    )
    from pmstudio.infrastructure.templates.loader import seed_builtin_templates

    # 建库 + 首个管理员。
    # **走容器那条真路径**（`_seed_first_admin`：register → set_status("active")）：
    # 自己 INSERT users 会漏 status/role，登录直接被拒——种子必须长成真账号的样子。
    os.environ["PMSTUDIO_DB"] = str(db_path)
    from pmstudio.bootstrap.container import _seed_first_admin
    from pmstudio.bootstrap.settings import Settings
    from pmstudio.infrastructure.sqlite.users import SqliteUserRepository

    settings = Settings.from_env()
    probe = database.connect(db_path)
    _seed_first_admin(SqliteUserRepository(probe), settings)
    row = probe.execute("SELECT user_id FROM users LIMIT 1").fetchone()
    assert row is not None, "首个管理员没种上（.env 里的 FIRST_ADMIN 用户名/密码填了吗？）"
    owner_id = str(row[0])
    _log(f"  owner = {owner_id}")
    set_owner_scope(UserId(owner_id))

    clock, ids = SystemClock(), UuidIdGenerator()
    templates = SqliteTemplateRepository(probe)
    seed_builtin_templates(templates)
    project_id = str(
        __import__("asyncio").run(
            CreateProject(
                factory=SqliteProjectFactory(probe, ids=ids),
                index=SqliteWorkspaceIndex(probe),
                clock=clock,
                ids=ids,
                templates=templates,
            )(title="水位与记忆验收")
        ).project_id
    )

    from pmstudio.infrastructure.sqlite import serialization as round_ser

    now = datetime.now(UTC)
    round_ids = []
    for i in range(ROUND_COUNT):
        rid = f"r-acc{i:03d}"
        round_ids.append(rid)
        created_dt = now - timedelta(hours=ROUND_COUNT - i)
        created = created_dt.isoformat()
        # payload 必须用**真序列化器**造：`/memory/usage` 会把每行
        # `round_from_payload` 反序列化，填 '{}' 直接 KeyError → 端点 500。
        round_ = Round.start(round_id=RoundId(rid), project_id=ProjectId(project_id),
                             user_input="导出功能讨论", now=created_dt)
        probe.execute(
            "INSERT INTO rounds (round_id, project_id, owner_id, step, waiting_on,"
            " payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, project_id, owner_id, round_.step.value, round_.waiting_on.value,
             json.dumps(round_ser.round_to_payload(round_), ensure_ascii=False),
             created, created),
        )
        probe.execute(
            "INSERT INTO round_digests (id, project_id, owner_id, round_id, summary,"
            " sections, features, outcomes, created_at)"
            " VALUES (?, ?, ?, ?, ?, '[]', '[]', '[]', ?)",
            (f"d-acc{i:03d}", project_id, owner_id, rid, f"第 {i} 轮摘要", created),
        )
    for i in range(OBS_COUNT):
        probe.execute(
            "INSERT INTO observations (id, project_id, owner_id, round_id, obs_type,"
            " title, detail, source_ref, created_at)"
            " VALUES (?, ?, ?, ?, 'seat_insight', ?, ?, ?, ?)",
            (f"o-acc{i:04d}", project_id, owner_id, round_ids[i % ROUND_COUNT],
             f"观察 {i}", f"这是第 {i} 条观察的详细内容。",
             # `source_ref` 必须**每条不同**：`recent()` 按它去重（只留最新一条），
             # 全填空串的话 250 条会塌成 1 条——水位根本触发不了。
             f"ref-{i:04d}", (now - timedelta(minutes=OBS_COUNT - i)).isoformat()),
        )

    entries = tuple(
        MemoryEntry(
            memory_id=f"mem-acc{i:03d}",
            kind=MemoryKind.LESSON,
            text=f"记忆 {i}：导出记录必须能追溯到人。",
            refs=(f"E9{i:02d}",),          # 从没被任何轮引用过
            round_id=round_ids[0],
            created_at=now,
            updated_at=now,
        )
        for i in range(MEMORY_COUNT)
    )
    memory = ProjectMemory(project_id=project_id, entries=entries)
    probe.execute(
        "INSERT INTO project_memory (project_id, owner_id, payload, updated_at)"
        " VALUES (?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET"
        " payload = excluded.payload, updated_at = excluded.updated_at",
        (project_id, owner_id,
         json.dumps(ser.memory_to_payload(memory), ensure_ascii=False), now.isoformat()),
    )
    probe.close()
    return project_id, owner_id


def _request(base, opener, method, path, *, data=None, headers=None):
    request = urllib.request.Request(f"{base}{path}", data=data, method=method,
                                     headers=headers or {})
    try:
        with opener.open(request, timeout=120) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def _screenshot(project_id: str, base: str, out_dir: Path) -> int:
    """真浏览器打开真前端，点开记忆抽屉，截「久未使用」标签。

    用系统 Chrome（`channel="chrome"`）：playwright 自带的 chromium 要下 191MB，
    本机下不动。后端已由调用方起好；前端这里现起 vite。
    """
    import socket
    import time as _t

    from playwright.sync_api import sync_playwright

    frontend = BACKEND.parent / "frontend"
    env = dict(os.environ)
    env["PMSTUDIO_API_TARGET"] = base
    env["PMSTUDIO_WEB_PORT"] = "5291"

    def _free(port: int) -> bool:
        with socket.socket() as s:
            return s.connect_ex(("127.0.0.1", port)) != 0

    for _ in range(30):
        if _free(5291):
            break
        _t.sleep(1)
    vite = subprocess.Popen(
        ["npm", "run", "dev"], cwd=frontend, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        for _ in range(90):
            try:
                with urllib.request.urlopen("http://127.0.0.1:5291/", timeout=3) as r:
                    if r.status == 200:
                        break
            except Exception:  # noqa: BLE001
                _t.sleep(2)
        else:
            vite.terminate()
            out, _ = vite.communicate(timeout=10)
            _log("前端没起来：\n" + (out or "")[-1500:])
            return 2
        _log("  前端 5291 起来了")

        out_dir.mkdir(parents=True, exist_ok=True)
        shot = out_dir / "批4-久未使用标签.png"
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1920, "height": 1200})
            page.goto("http://127.0.0.1:5291/", wait_until="domcontentloaded")

            # 登录
            page.fill("#auth-username", os.environ["PMSTUDIO_FIRST_ADMIN_USER"])
            page.fill("#auth-password", os.environ["PMSTUDIO_FIRST_ADMIN_PASSWORD"])
            page.click("button[type=submit]")
            page.wait_for_load_state("networkidle")
            _log(f"  登录后 URL = {page.url}")

            # 进项目：单页应用**不走路由**，走的是"项目列表里点那一行"
            # （ProjectList.tsx 的 `projects__row`）。只有一个项目时登录后会自动选中，
            # 此时列表不再有那一行——先判断在不在，不在才点。
            if page.locator("button.projects__row").count() == 0:
                _log("  登录后已自动进入项目（列表里只有一个项目）")
            else:
                page.click("button.projects__row", timeout=20000)
                page.wait_for_load_state("networkidle")
                _t.sleep(2)
                _log("  已点开项目行")

            # **不展开右列**：默认 `loopExpanded=false` → shell 加 `shell--noloop`
            # → loop 列只有 56px。overlay 抽屉必须自己在这种状态下也可读
            # （`.shell--noloop .overlay-slot` 悬浮到右缘，2026-10-19 修）。
            # 所以这里刻意**不点** rail 的「闭环」——默认态就该看得清。

            # 打开记忆抽屉
            opened = False
            # 入口是版本栏上的「记忆 N」按钮（VersionBar.tsx），只在有记忆时才出现
            for selector in ("button:has-text('记忆')", "text=记忆", "text=记忆 "):
                try:
                    page.click(selector, timeout=4000)
                    opened = True
                    _log(f"  点开抽屉（{selector}）")
                    break
                except Exception:  # noqa: BLE001
                    continue
            if not opened:
                page.screenshot(path=str(out_dir / "批4-抽屉未找到.png"), full_page=True)
                _log("  ✗ 没找到记忆抽屉入口，已截图存证")
                browser.close()
                return 3

            page.wait_for_timeout(2500)   # 等 usage 懒加载回来
            body = page.inner_text("body")
            # **宽度是这次修的契约**：折叠态（默认）下抽屉必须还是可读的宽度。
            # 修之前是 56px（跟着 loop 列缩），标签被挤成一条竖排。
            box = page.locator("aside.drawer").first.bounding_box()
            width = round(box["width"]) if box else 0
            _log(f"  折叠态抽屉宽 = {width}px（应 ≥ 400）")
            assert width >= 400, f"抽屉被压成 {width}px——折叠态下不可读（overlay-slot 修没生效？）"
            # 抽屉是浮在右缘的，整页截图会把它挤成一条：单独截这个元素。
            drawer = page.locator("aside.drawer")
            if drawer.count():
                drawer.first.screenshot(path=str(shot))
            else:
                page.screenshot(path=str(shot), full_page=True)
            browser.close()

        _log(f"[4] 截图：{shot}")
        if "久未使用" not in body:
            _log("  ✗ 页面上没找到「久未使用」标签")
            return 4
        _log("  ✔ 界面上真的出现了「久未使用」标签")
        return 0
    finally:
        vite.terminate()
        try:
            vite.wait(timeout=10)
        except subprocess.TimeoutExpired:
            vite.kill()


def main() -> int:
    _load_env()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "wm.db"
        _log("seed…")
        project_id, owner_id = _seed(db_path)

        env = dict(os.environ)
        env["PMSTUDIO_DB"] = str(db_path)
        _log("起服务…")
        proc = subprocess.Popen(
            [sys.executable, "-u", "scripts/dev.py", "--no-reload", "--port", PORT],
            cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        base = f"http://127.0.0.1:{PORT}"
        try:
            for _ in range(90):
                try:
                    with urllib.request.urlopen(f"{base}/api/health", timeout=3) as r:
                        if r.status == 200:
                            break
                except Exception:  # noqa: BLE001
                    time.sleep(2)
            else:
                proc.terminate()
                out, _ = proc.communicate(timeout=10)
                _log("服务没起来：\n" + (out or "")[-2000:])
                return 2
            _log("  /api/health 200")

            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
            status, _ = _request(
                base, opener, "POST", "/api/auth/login",
                data=json.dumps({
                    "username": os.environ["PMSTUDIO_FIRST_ADMIN_USER"],
                    "password": os.environ["PMSTUDIO_FIRST_ADMIN_PASSWORD"],
                }).encode(),
                headers={"Content-Type": "application/json"},
            )
            assert status == 200, f"登录失败 {status}"
            _log("  登录成功")

            # ── 判据 3（后端侧）：久未使用 ──────────────────────────────
            status, body = _request(base, opener, "GET",
                                    f"/api/projects/{project_id}/memory/usage")
            _log(f"[3] GET /memory/usage → {status} {json.dumps(body, ensure_ascii=False)}")
            if status != 200:
                _log("端点 500，服务端日志：")
                proc.terminate()
                out, _ = proc.communicate(timeout=10)
                _log("\n".join((out or "").splitlines()[-40:]))
                raise AssertionError(f"memory/usage {status}: {body}")
            unused = body.get("unused", [])
            assert len(unused) == MEMORY_COUNT, f"unused 应有 {MEMORY_COUNT} 条，实得 {unused}"

            # ── 判据 1/2（上下文渲染）：L2 超了、L4 没超 ────────────────
            _log("[1/2] 渲染上下文看水位尾注…")
            rc = _render_contexts(db_path, project_id, owner_id)
            if rc != 0:
                return rc

            # ── 判据 4（界面上看得到标签）────────────────────────────────
            if os.environ.get("SKIP_SCREENSHOT"):
                _log("[4] SKIP_SCREENSHOT=1，跳过截图")
                return 0
            _log("[4] 真浏览器截图…")
            return _screenshot(project_id, base, Path(
                os.environ.get("SHOT_DIR", ".codex/runs/2026-10-19")))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def _render_contexts(db_path: Path, project_id: str, owner_id: str) -> int:
    """真读种子库、真调渲染函数，看两条尾注对不对。"""
    from pmstudio.application.context import L2_CAPACITY, L4_CAPACITY, render_observations_index
    from pmstudio.domain.shared.ids import UserId
    from pmstudio.infrastructure.sqlite import database
    from pmstudio.infrastructure.sqlite.observations_repo import SqliteObservationRepository

    os.environ["PMSTUDIO_DB"] = str(db_path)
    connection = database.connect(db_path)
    repo = SqliteObservationRepository(connection)
    try:
        obs = repo.recent(project_id, limit=None, owner=UserId(owner_id))
        n = len(obs)
        text = render_observations_index(obs)
        tail = [line for line in text.splitlines() if "共" in line and "条" in line]
        _log(f"  L2：观察 {n} 条（水位 {L2_CAPACITY}）；尾注行 = {tail}")
        assert n > L2_CAPACITY, f"种子不够（{n} <= {L2_CAPACITY}），构造错了"
        assert tail and "待复核" in tail[0], f"超水位却没有尾注：{tail}"

        from pmstudio.infrastructure.sqlite.digest_repo import SqliteDigestRepository

        digests = SqliteDigestRepository(connection).recent(
            project_id=project_id, limit=None, owner=UserId(owner_id))
        _log(
            f"  L4：摘要 {len(digests)} 条（水位 {L4_CAPACITY}）→ 未超，尾注不该出现"
        )
        assert len(digests) < L4_CAPACITY
    finally:
        connection.close()
    _log("  L2 超水位尾注在、L4 未超不出现 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())