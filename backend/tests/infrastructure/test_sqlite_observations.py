"""L2 观察的 SQLite 存储：写入 + 最近观察（去重 / 排序 / 隔离）。

`get` / `search` **已删**（2026-10-17）：它们当初为"按需拉详情"预留，而 L2 索引定为
**详情内联**（`render_observations_index` 直接带 `detail`），两个方法 0 生产调用。
读取只剩 `recent`——它给**全部去重后的**（`limit=None`），截断与"共 N 条"的报数交给渲染层。
"""

from datetime import datetime
from pathlib import Path

import pytest

from pmstudio.application.context import render_observations_index
from pmstudio.domain.memory.observations import (
    ObservationType,
    RoundObservation,
)
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.observations_repo import SqliteObservationRepository

NOW = datetime(2026, 10, 8, 10, 0, 0)


def _obs(
    obs_id: str,
    title: str,
    detail: str = "detail",
    obs_type: ObservationType = ObservationType.SEAT_INSIGHT,
    source_ref: str = "seat.business_rules:O1",
    created_at: str = NOW.isoformat(),
) -> RoundObservation:
    return RoundObservation(
        obs_id=obs_id,
        round_id="r1",
        project_id="p1",
        obs_type=obs_type,
        title=title,
        detail=detail,
        source_ref=source_ref,
        created_at=created_at,
    )


@pytest.fixture
def repo(tmp_path):
    conn = database.connect(Path(tmp_path) / "test.db")
    repo = SqliteObservationRepository(conn)
    yield repo
    conn.close()


#: pre-v11 形状的 `observations`：`created_at` 还在，而且**可为 NULL**（v11 删、v14 加回来，
#: 但从那之前跑过来的老库里它是可空列，且留着 NULL）。
#: 新库的列是 `NOT NULL DEFAULT ''`，手插 NULL 会被直接拒——要复现"可空列上的 NULL"
#: 只能自己按老形状建表，这也正是测试要守住的那条真实路径。
LEGACY_NULLABLE_OBSERVATIONS_DDL = """
CREATE TABLE observations (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  owner_id   TEXT,
  round_id   TEXT NOT NULL,
  obs_type   TEXT NOT NULL,
  title      TEXT NOT NULL,
  detail     TEXT NOT NULL DEFAULT '',
  source_ref TEXT NOT NULL DEFAULT '',
  created_at TEXT
);
"""


def _downgrade_to_legacy_observations(connection) -> None:
    connection.execute("DROP TABLE observations")
    connection.executescript(LEGACY_NULLABLE_OBSERVATIONS_DDL)


@pytest.fixture
def legacy_repo(tmp_path):
    """`created_at` 可为 NULL 的老库 + 它的仓储（连带连接，手插 NULL 行要用）。"""

    conn = database.connect(Path(tmp_path) / "legacy.db")
    _downgrade_to_legacy_observations(conn)
    yield SqliteObservationRepository(conn), conn
    conn.close()


class TestObservationRepository:
    def test_saved_observation_is_readable_via_recent(self, repo):
        repo.save(_obs("obs-1", "保证金"))

        loaded = repo.recent("p1")

        assert len(loaded) == 1
        assert loaded[0].title == "保证金"
        assert loaded[0].obs_type is ObservationType.SEAT_INSIGHT

    def test_save_batch(self, repo):
        repo.save_batch(
            (
                _obs("obs-1", "A", source_ref="seat.a:O1"),
                _obs("obs-2", "B", source_ref="seat.b:O1"),
                _obs("obs-3", "C", source_ref="seat.c:O1"),
            )
        )

        assert {o.title for o in repo.recent("p1")} == {"A", "B", "C"}

    def test_save_replaces_on_conflict(self, repo):
        repo.save(_obs("obs-1", "原始", source_ref="seat.x:O1"))
        repo.save(_obs("obs-1", "更新", source_ref="seat.x:O1"))

        loaded = repo.recent("p1")
        assert len(loaded) == 1
        assert loaded[0].title == "更新"

    def test_recent_returns_latest_first(self, repo):
        repo.save(_obs("obs-1", "A", source_ref="seat.a:O1"))
        repo.save(_obs("obs-2", "B", source_ref="seat.b:O1"))
        repo.save(_obs("obs-3", "C", source_ref="seat.c:O1"))
        recent = repo.recent("p1", limit=2)
        assert len(recent) == 2
        # 最新在前
        assert recent[0].obs_id == "obs-3"
        assert recent[1].obs_id == "obs-2"

    def test_recent_filters_by_project(self, repo):
        repo.save(_obs("obs-1", "A"))
        repo.save(_obs("obs-2", "B"))
        repo.save(
            RoundObservation(
                obs_id="obs-other",
                round_id="r1",
                project_id="p-other",
                obs_type=ObservationType.SEAT_INSIGHT,
                title="Other",
                detail="",
                source_ref="",
                created_at=NOW.isoformat(),
            )
        )

        recent = repo.recent("p1")

        assert all(o.project_id == "p1" for o in recent)

    def test_recent_dedups_by_source_ref(self, repo):
        """同 source_ref 只保留最新一条（`created_at` 较新者；同一时刻才由 id 倒序破平）。"""
        repo.save(_obs("obs-1", "旧判断", source_ref="seat.data_scale:O1"))
        repo.save(_obs("obs-2", "新判断", source_ref="seat.data_scale:O1"))
        repo.save(_obs("obs-3", "另一席位", source_ref="seat.ux:O1"))
        recent = repo.recent("p1", limit=5)
        # seat.data_scale:O1 只保留最新（obs-2），seat.ux:O1 保留
        assert len(recent) == 2
        refs = {o.source_ref: o for o in recent}
        assert "seat.data_scale:O1" in refs
        assert "seat.ux:O1" in refs
        assert refs["seat.data_scale:O1"].title == "新判断"

    def test_recent_dedup_respects_limit(self, repo):
        """去重后仍受 limit 约束。"""
        for i in range(10):
            repo.save(_obs(f"obs-{i}", f"判断{i}", source_ref=f"seat.s{i}:O1"))
        recent = repo.recent("p1", limit=3)
        assert len(recent) == 3
        # 最新的 3 个 source_ref
        titles = {o.title for o in recent}
        assert "判断9" in titles
        assert "判断8" in titles
        assert "判断7" in titles

    def test_recent_without_limit_returns_every_deduped_observation(self, repo):
        """R：`limit=None`（默认）返回**全部**去重后的——索引层要拿它算"共 N 条"。"""

        for i in range(8):
            repo.save(_obs(f"obs-{i}", f"判断{i}", source_ref=f"seat.s{i}:O1"))

        assert len(repo.recent("p1")) == 8
        assert len(repo.recent("p1", limit=5)) == 5

    def test_the_index_reports_how_many_were_left_out(self, repo):
        """R：`recent()` 给全部 + 渲染层截断 → "共 N 条，只带了前 5 条"**真的出现**。

        改前调用点先 `limit=5` 截断，渲染层的报数分支永不触发（死路）。
        """

        for i in range(8):
            repo.save(_obs(f"obs-{i}", f"判断{i}", source_ref=f"seat.s{i}:O1"))

        text = render_observations_index(repo.recent("p1"))

        assert "共 8 条" in text
        assert "只带了前 5 条" in text


class TestRecentOrdersByTime:
    """`recent()` 的"最新"判据是 `created_at`，不是 id（批 3/2 修字典序 bug）。

    这组用例的每行都得给**自己的** `source_ref`：`recent()` 按 `source_ref` 去重，
    撞了就只剩一行，排序断言还没轮到就塌了（brief 原文漏了这一步）。
    """

    def test_recent_orders_by_created_at_not_the_id_alphabet(self, repo):
        """R：`obs-9-…` 不许排在 `obs-10-…` 前面（字典序≠时间序，真库实证）。"""

        # 第十条时间上更晚。旧实现按 id 字典序会把 obs-9 当"最新"——这正是真库上的 bug
        # （真库既有 obs-10-5cf824 也有 obs-9-792a3a）。两行时间**不能**写成一样：
        # 一样就由 id 破平，而 `'obs-9-bbbb' > 'obs-10-aaaa'` 字典序仍然把第九条排前面，
        # 那就不是在测"按时间排"了。
        repo.save(
            _obs(
                "obs-10-aaaa",
                "同一轮第十条",
                source_ref="seat.a:O1",
                created_at="2026-10-18T10:00:00+00:00",
            )
        )
        repo.save(
            _obs(
                "obs-9-bbbb",
                "同一轮第九条",
                source_ref="seat.b:O1",
                created_at="2026-10-18T09:00:00+00:00",
            )
        )
        titles = [o.title for o in repo.recent("p1")]
        assert titles.index("同一轮第十条") < titles.index("同一轮第九条")

    def test_recent_puts_older_observations_first_in_return(self, repo):
        """R：跨轮按时间倒序（最新在前）。"""

        repo.save(
            _obs(
                "obs-1-aaaa",
                "旧轮",
                source_ref="seat.a:O1",
                created_at="2026-10-01T00:00:00+00:00",
            )
        )
        repo.save(
            _obs(
                "obs-2-bbbb",
                "新轮",
                source_ref="seat.b:O1",
                created_at="2026-10-18T00:00:00+00:00",
            )
        )
        assert [o.title for o in repo.recent("p1")] == ["新轮", "旧轮"]

    def test_recent_sorts_timeless_legacy_rows_last(self, repo):
        """R：老行没时间戳（`created_at=''`）排最后，不炸、不挤掉有时间的。"""

        repo.save(_obs("obs-9-zzzz", "老行无时间", source_ref="seat.a:O1", created_at=""))
        repo.save(
            _obs(
                "obs-1-aaaa",
                "新轮",
                source_ref="seat.b:O1",
                created_at="2026-10-18T00:00:00+00:00",
            )
        )
        titles = [o.title for o in repo.recent("p1")]
        assert titles[0] == "新轮" and titles[-1] == "老行无时间"

    def test_dedup_keeps_the_newest_variant_by_time(self, repo):
        """R：同 `source_ref` 留**时间较新**那条（不再按 id）。"""

        repo.save(
            _obs(
                "obs-9-aaaa",
                "旧判断",
                source_ref="seat.x:O1",
                created_at="2026-10-01T00:00:00+00:00",
            )
        )
        repo.save(
            _obs(
                "obs-1-bbbb",
                "新判断",
                source_ref="seat.x:O1",
                created_at="2026-10-18T00:00:00+00:00",
            )
        )
        recent = repo.recent("p1")
        assert len(recent) == 1 and recent[0].title == "新判断"


class TestTimelessRowsSink:
    """无时间的行（`created_at` 为 `''` **或** NULL）必须沉底，不许浮到有时间戳的行前面。"""

    def test_a_null_created_at_row_sorts_after_rows_with_a_real_time(self, legacy_repo):
        """M1：pre-v11 老库那一列可空，NULL 行同样沉底。

        这里守的是 `recent()` 排序键里"无时间"那一项：`(NULL = '')` 求值为 NULL，
        而 NULL 在 ASC 里排**最前**——不 COALESCE 的话，"最该沉底的存量"会浮到顶上，
        正好把意图做反。
        """

        repo, conn = legacy_repo
        repo.save(
            _obs(
                "obs-9-aaaa",
                "有时间",
                source_ref="seat.a:O1",
                created_at="2026-10-18T00:00:00+00:00",
            )
        )
        conn.execute(
            "INSERT INTO observations (id, project_id, owner_id, round_id, obs_type, title,"
            " detail, source_ref, created_at)"
            " VALUES ('obs-1-null', 'p1', 'u-owner-scope-test', 'r1', 'seat_insight',"
            " '无时间NULL', 'd', 'seat.b:O1', NULL)"
        )

        titles = [o.title for o in repo.recent("p1")]

        assert titles == ["有时间", "无时间NULL"]

    def test_connect_backfills_null_created_at_on_a_legacy_observations_table(self, tmp_path):
        """M2：老库的 NULL 必须在 `connect()` 的迁移里被填成 `''`，不留给排序去应付。

        迁移**不猜**这些观察的时间（宁可沉底也不冒充最新），但"没有"与"空的"要统一成
        一个值：留 NULL 等于把同一个"无时间"状态存成两种，其中一种还会排到最前。
        """

        path = Path(tmp_path) / "legacy_null.db"
        conn = database.connect(path)
        _downgrade_to_legacy_observations(conn)
        conn.execute(
            "INSERT INTO observations (id, project_id, owner_id, round_id, obs_type, title,"
            " detail, source_ref, created_at)"
            " VALUES ('obs-1-null', 'p1', 'u-owner-scope-test', 'r1', 'seat_insight',"
            " '老行', 'd', 'seat.b:O1', NULL)"
        )
        conn.commit()
        conn.close()

        migrated = database.connect(path)

        assert (
            migrated.execute(
                "SELECT count(*) FROM observations WHERE created_at IS NULL"
            ).fetchone()[0]
            == 0
        )
        assert (
            migrated.execute(
                "SELECT created_at FROM observations WHERE id = 'obs-1-null'"
            ).fetchone()["created_at"]
            == ""
        )
        assert migrated.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
        migrated.close()
