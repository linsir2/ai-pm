"""L2 观察的 SQLite 存储：CRUD + FTS5 搜索 + 相关性淘汰。"""

from datetime import datetime
from pathlib import Path

import pytest

from pmstudio.domain.memory.observations import (
    ObservationType,
    RoundObservation,
)
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.observations_repo import SqliteObservationRepository

NOW = datetime(2026, 10, 8, 10, 0, 0)


def _obs(
    obs_id: str, title: str, detail: str = "detail",
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
        created_at=NOW,
    )


@pytest.fixture
def repo(tmp_path):
    conn = database.connect(Path(tmp_path) / "test.db")
    repo = SqliteObservationRepository(conn)
    yield repo
    conn.close()


class TestObservationRepository:
    def test_save_and_get(self, repo):
        obs = _obs("obs-1", "保证金")
        repo.save(obs)
        loaded = repo.get("obs-1")
        assert loaded is not None
        assert loaded.title == "保证金"
        assert loaded.obs_type == ObservationType.SEAT_INSIGHT

    def test_get_missing_returns_none(self, repo):
        assert repo.get("nonexistent") is None

    def test_save_batch(self, repo):
        obs = (_obs("obs-1", "A"), _obs("obs-2", "B"), _obs("obs-3", "C"))
        repo.save_batch(obs)
        assert repo.get("obs-1") is not None
        assert repo.get("obs-2") is not None
        assert repo.get("obs-3") is not None

    def test_save_replaces_on_conflict(self, repo):
        repo.save(_obs("obs-1", "原始"))
        repo.save(_obs("obs-1", "更新"))
        loaded = repo.get("obs-1")
        assert loaded.title == "更新"

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
        # 不同 project_id 的观察
        repo.save(RoundObservation(
            obs_id="obs-other",
            round_id="r1",
            project_id="p-other",
            obs_type=ObservationType.SEAT_INSIGHT,
            title="Other",
            detail="",
            source_ref="",
            created_at=NOW,
        ))
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

    def test_search_fts(self, repo):
        repo.save(_obs("obs-1", "团长保证金制度"))
        repo.save(_obs("obs-2", "SKU数量未定"))
        repo.save(_obs("obs-3", "品控标准化流程"))
        results = repo.search("p1", "保证金")
        assert len(results) >= 1
        assert any("保证金" in r.title for r in results)

    def test_search_no_match(self, repo):
        repo.save(_obs("obs-1", "保证金"))
        results = repo.search("p1", "不存在的关键词")
        assert results == ()
