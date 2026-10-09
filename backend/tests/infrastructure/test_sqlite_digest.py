"""L4 轮次摘要的 SQLite 存储。"""

from datetime import datetime
from pathlib import Path

import pytest

from pmstudio.domain.memory.digest import RoundDigest
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.digest_repo import SqliteDigestRepository

NOW = datetime(2026, 10, 8, 10, 0, 0)


def _digest(digest_id: str, round_id: str = "r1", summary: str = "摘要") -> RoundDigest:
    return RoundDigest(
        round_id=digest_id,
        project_id="p1",
        summary=summary,
        sections_touched=("s1", "s5"),
        features_addressed=("group-buying",),
        key_outcomes=("定位=社区团购", "规则=7天退还"),
    )


@pytest.fixture
def repo(tmp_path):
    conn = database.connect(Path(tmp_path) / "test.db")
    r = SqliteDigestRepository(conn)
    yield r
    conn.close()


class TestSqliteDigestRepository:
    def test_save_and_get(self, repo):
        d = _digest("digest-1")
        repo.save(d)
        loaded = repo.get("digest-1")
        assert loaded is not None
        assert loaded.summary == "摘要"
        assert loaded.sections_touched == ("s1", "s5")

    def test_get_missing_returns_none(self, repo):
        assert repo.get("nonexistent") is None

    def test_save_replaces_on_conflict(self, repo):
        repo.save(_digest("digest-1", summary="原始"))
        repo.save(_digest("digest-1", summary="更新"))
        loaded = repo.get("digest-1")
        assert loaded.summary == "更新"

    def test_recent_limit(self, repo):
        repo.save(_digest("d-1", "r1"))
        repo.save(_digest("d-2", "r2"))
        repo.save(_digest("d-3", "r3"))
        recent = repo.recent("p1", limit=2)
        assert len(recent) == 2

    def test_recent_filters_by_project(self, repo):
        repo.save(_digest("d-1", "r1"))
        # 不同 project
        repo.save(RoundDigest(round_id="r2", project_id="p-other", summary="其他"))
        recent = repo.recent("p1")
        assert all(d.project_id == "p1" for d in recent)
