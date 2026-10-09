"""L3 灵感档案的 SQLite 存储。"""

from datetime import datetime
from pathlib import Path

import pytest

from pmstudio.domain.memory.inspiration import InspirationItem, InspirationOrigin
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.inspiration_repo import SqliteInspirationRepository

NOW = datetime(2026, 10, 8, 10, 0, 0)


def _item(
    item_id: str,
    origin: InspirationOrigin = InspirationOrigin.DISCUSSION,
    statement: str = "灵感",
) -> InspirationItem:
    return InspirationItem(
        item_id=item_id,
        project_id="p1",
        origin=origin,
        statement=statement,
    )


@pytest.fixture
def repo(tmp_path):
    conn = database.connect(Path(tmp_path) / "test.db")
    r = SqliteInspirationRepository(conn)
    yield r
    conn.close()


class TestSqliteInspirationRepository:
    def test_save_and_get(self, repo):
        item = _item("ins-1", InspirationOrigin.DISCUSSION, "团长流失率15%")
        repo.save(item)
        loaded = repo.get("ins-1")
        assert loaded is not None
        assert loaded.statement == "团长流失率15%"
        assert loaded.origin == InspirationOrigin.DISCUSSION

    def test_get_missing_returns_none(self, repo):
        assert repo.get("nonexistent") is None

    def test_save_batch(self, repo):
        items = [_item("ins-1"), _item("ins-2"), _item("ins-3")]
        repo.save_batch(items)
        assert repo.get("ins-1") is not None
        assert repo.get("ins-2") is not None
        assert repo.get("ins-3") is not None

    def test_search(self, repo):
        repo.save(_item("ins-1", InspirationOrigin.DISCUSSION, "团长保证金制度"))
        repo.save(_item("ins-2", InspirationOrigin.EXPLORATION, "SKU数量未定"))
        repo.save(_item("ins-3", InspirationOrigin.SIM_INQUIRY, "品控标准化流程"))
        results = repo.search("p1", "保证金")
        assert len(results) >= 1
        assert any("保证金" in r.statement for r in results)

    def test_recent_limit(self, repo):
        for i in range(10):
            repo.save(_item(f"ins-{i}", statement=f"灵感{i}"))
        recent = repo.recent("p1", limit=5)
        assert len(recent) == 5

    def test_recent_filters_by_project(self, repo):
        repo.save(_item("ins-1", statement="A"))
        repo.save(
            InspirationItem(
                item_id="ins-other",
                project_id="p-other",
                origin=InspirationOrigin.DISCUSSION,
                statement="Other",
            )
        )
        recent = repo.recent("p1")
        assert all(i.project_id == "p1" for i in recent)
