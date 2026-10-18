"""L3 灵感档案的 SQLite 存储。"""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from pmstudio.domain.memory.inspiration import InspirationItem, InspirationOrigin
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.inspiration_repo import SqliteInspirationRepository
from pmstudio.infrastructure.sqlite.owner_scope import require_owner

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


def _insert_item_at(conn, item_id: str, created_at: str | None) -> None:
    """直接写行，好把 `created_at` 钉成指定值（`save` 自己盖秒级 `_now()`）。"""
    conn.execute(
        "INSERT INTO inspirations (id, project_id, owner_id, origin, statement,"
        " created_at) VALUES (?, 'p1', ?, 'discussion', '灵感', ?)",
        (item_id, str(require_owner(None)), created_at),
    )


@pytest.fixture
def repo(tmp_path):
    conn = database.connect(Path(tmp_path) / "test.db")
    r = SqliteInspirationRepository(conn)
    yield r
    conn.close()


@pytest.fixture
def repo_and_conn(tmp_path):
    """要写死时间的用例用这个（与 observations 测试里 `legacy_repo` 同形）。"""
    conn = database.connect(Path(tmp_path) / "test.db")
    r = SqliteInspirationRepository(conn)
    yield r, conn
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

    def test_recent_defaults_to_all_so_the_renderer_can_count(self, repo):
        """默认**不截断**：渲染层要拿总数报"共 N 条 / 更早的待复核"（2026-10-18 批 3）。

        默认给 5 的话，渲染层只看得见 5，报出来的"共 5 条"就是假话。
        """
        for i in range(10):
            repo.save(_item(f"ins-{i}", statement=f"灵感{i}"))
        assert len(repo.recent("p1")) == 10

    def test_recent_orders_by_created_at_and_sinks_the_timeless_row(self, repo_and_conn):
        """R：`recent()` 按 `created_at` 排，**无时间的行沉最后**。

        id 是 `ins-disc-<卡 id>-<6 位随机 hex>`，里面没有时间，`ORDER BY id DESC` 等于按
        卡的序号 + 随机数排——跨轮谁"最近"完全是运气。这里三行 id 的字典序与时间序相反。
        """
        repo, conn = repo_and_conn
        _insert_item_at(conn, "ins-disc-0000000f-aaaaaa", "2026-10-01T00:00:00+00:00")
        _insert_item_at(conn, "ins-disc-0000000a-aaaaaa", "2026-10-18T00:00:00+00:00")
        _insert_item_at(conn, "ins-disc-0000000c-aaaaaa", "")

        assert [i.item_id for i in repo.recent("p1")] == [
            "ins-disc-0000000a-aaaaaa",
            "ins-disc-0000000f-aaaaaa",
            "ins-disc-0000000c-aaaaaa",
        ]

    def test_recent_limit_returns_the_newest_prefix(self, repo_and_conn):
        """R：`limit=N` 截断后给的是**最新的 N 条**，`limit=None` 给全部。

        两条断言钉的是同一个事实：排序键换了之后，"最近"仍然是最近。
        这里 id 的字典序与时间序**相反**（`…-01` 反而最新），否则按 id 排也能蒙对。
        """
        repo, conn = repo_and_conn
        _insert_item_at(conn, "ins-disc-00000001-aaaaaa", "2026-10-04T00:00:00+00:00")
        _insert_item_at(conn, "ins-disc-00000002-aaaaaa", "2026-10-03T00:00:00+00:00")
        _insert_item_at(conn, "ins-disc-00000003-aaaaaa", "2026-10-02T00:00:00+00:00")
        _insert_item_at(conn, "ins-disc-00000004-aaaaaa", "2026-10-01T00:00:00+00:00")

        every = [i.item_id for i in repo.recent("p1")]
        newest_two = [i.item_id for i in repo.recent("p1", limit=2)]

        assert every == [
            "ins-disc-00000001-aaaaaa",
            "ins-disc-00000002-aaaaaa",
            "ins-disc-00000003-aaaaaa",
            "ins-disc-00000004-aaaaaa",
        ]
        assert newest_two == ["ins-disc-00000001-aaaaaa", "ins-disc-00000002-aaaaaa"]

    def test_created_at_column_refuses_null(self, repo_and_conn):
        """这一列是 `NOT NULL`，所以"无时间"只能是 `''`，不是 NULL。

        钉住的是形状：哪天真把它放宽成可空，排序里"无时间沉底"的判据得跟着换。
        """
        repo, conn = repo_and_conn
        notnull = {
            row["name"]: row["notnull"]
            for row in conn.execute("PRAGMA table_info(inspirations)")
        }
        assert notnull["created_at"] == 1

        with pytest.raises(sqlite3.IntegrityError):
            _insert_item_at(conn, "ins-disc-0000000b-aaaaaa", None)

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
