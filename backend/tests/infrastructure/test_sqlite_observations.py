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
) -> RoundObservation:
    return RoundObservation(
        obs_id=obs_id,
        round_id="r1",
        project_id="p1",
        obs_type=obs_type,
        title=title,
        detail=detail,
        source_ref=source_ref,
    )


@pytest.fixture
def repo(tmp_path):
    conn = database.connect(Path(tmp_path) / "test.db")
    repo = SqliteObservationRepository(conn)
    yield repo
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
            )
        )

        recent = repo.recent("p1")

        assert all(o.project_id == "p1" for o in recent)

    def test_recent_dedups_by_source_ref(self, repo):
        """同 source_ref 只保留最新一条（id 最大）。"""
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
