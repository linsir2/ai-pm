"""L4 轮次摘要的 PostgreSQL 存储。"""

from datetime import datetime

import pytest
from psycopg.errors import NotNullViolation

from pmstudio.application.errors import DigestAlreadyWritten
from pmstudio.domain.memory.digest import RoundDigest
from pmstudio.infrastructure.postgres.digest_repo import SqliteDigestRepository
from pmstudio.infrastructure.postgres.owner_scope import require_owner
from tests.support.postgres import open_direct

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


def _insert_digest_at(conn, digest_id: str, created_at: str) -> None:
    """直接写行，好把 `created_at` 钉成指定值。

    `save` 自己盖 `_now()`（秒级），连着存几行时间会全一样，排不出"谁更新"的序。
    """
    conn.execute(
        "INSERT INTO round_digests"
        " (id, project_id, owner_id, round_id, summary, sections, features, outcomes,"
        " created_at) VALUES (%s, 'p1', %s, %s, '摘要', '{}', '{}', '{}', %s)",
        (digest_id, str(require_owner(None)), digest_id.removeprefix("digest-"), created_at),
    )


@pytest.fixture
def repo():
    conn = open_direct()
    r = SqliteDigestRepository(conn)
    yield r
    conn.close()


@pytest.fixture
def repo_and_conn():
    """要写死时间的用例用这个（与 observations 测试里 `legacy_repo` 同形）。"""
    conn = open_direct()
    r = SqliteDigestRepository(conn)
    yield r, conn
    conn.close()


class TestPgDigestRepository:
    def test_saved_digest_is_readable_via_recent(self, repo):
        """`get` 已删（详情内联定案，0 生产者）——存了就能从 `recent` 里读回。"""
        d = _digest("digest-1")
        repo.save(d)
        loaded = repo.recent("p1")
        assert len(loaded) == 1
        assert loaded[0].summary == "摘要"
        assert loaded[0].sections_touched == ("s1", "s5")

    def test_missing_digest_is_absent_from_recent(self, repo):
        assert repo.recent("p1") == ()

    def test_save_refuses_to_rewrite_what_is_already_written(self, repo):
        """同一个 round 的摘要只追加、不改写（`07 §7.14`）。

        **断言语意被有意改写**：原断言是 `loaded.summary == "更新"`（钉的是
        `ON CONFLICT DO UPDATE` 的覆盖行为），而那正是本轮要废掉的——摘要会进后面每一轮
        的提示词（`$digests` 带最近 3 轮），回头改一个已写下的摘要就是改历史。
        规则本身一个字没变，变的是"写第二次会怎样"。
        """

        repo.save(_digest("digest-1", summary="原始"))

        with pytest.raises(DigestAlreadyWritten):
            repo.save(_digest("digest-1", summary="更新"))

        assert repo.recent("p1")[0].summary == "原始"

    def test_recent_limit(self, repo):
        repo.save(_digest("d-1", "r1"))
        repo.save(_digest("d-2", "r2"))
        repo.save(_digest("d-3", "r3"))
        recent = repo.recent("p1", limit=2)
        assert len(recent) == 2

    def test_recent_defaults_to_all_so_the_renderer_can_count(self, repo):
        """默认**不截断**：渲染层要拿总数报"共几轮 / 更早的待复核"（2026-10-18 批 3）。

        默认给 3 的话，渲染层只看得见 3，报出来的"共 3 轮"就是假话。
        """
        for i in range(6):
            repo.save(_digest(f"d-{i}", f"r{i}"))
        assert len(repo.recent("p1")) == 6

    def test_recent_orders_by_created_at_and_sinks_the_timeless_row(self, repo_and_conn):
        """R：`recent()` 按 `created_at` 排，**无时间的行沉最后**。

        id 是 `digest-r-<8 位随机 hex>`——里面根本没有时间，`ORDER BY id DESC` 给的是
        **随机序**，"最近 3 轮摘要"会取到任意 3 轮。这里三行的 id 字典序刻意与时间序相反。
        """
        repo, conn = repo_and_conn
        _insert_digest_at(conn, "digest-r-0000000f", "2026-10-01T00:00:00+00:00")
        _insert_digest_at(conn, "digest-r-0000000a", "2026-10-18T00:00:00+00:00")
        _insert_digest_at(conn, "digest-r-0000000c", "")

        assert [d.round_id for d in repo.recent("p1")] == [
            "r-0000000a",
            "r-0000000f",
            "r-0000000c",
        ]

    def test_recent_limit_returns_the_newest_prefix(self, repo_and_conn):
        """R：`limit=N` 截断后给的是**最新的 N 轮**，`limit=None` 给全部。

        两条断言钉的是同一个事实：排序键换了之后，"最近"仍然是最近。
        这里 id 的字典序与时间序**相反**（`…-01` 反而最新），否则按 id 排也能蒙对。
        """
        repo, conn = repo_and_conn
        _insert_digest_at(conn, "digest-r-00000001", "2026-10-04T00:00:00+00:00")
        _insert_digest_at(conn, "digest-r-00000002", "2026-10-03T00:00:00+00:00")
        _insert_digest_at(conn, "digest-r-00000003", "2026-10-02T00:00:00+00:00")
        _insert_digest_at(conn, "digest-r-00000004", "2026-10-01T00:00:00+00:00")

        every = [d.round_id for d in repo.recent("p1")]
        newest_two = [d.round_id for d in repo.recent("p1", limit=2)]

        assert every == ["r-00000001", "r-00000002", "r-00000003", "r-00000004"]
        assert newest_two == ["r-00000001", "r-00000002"]

    def test_recent_orders_ties_by_insertion_not_by_the_id_alphabet(self, repo_and_conn):
        """R：`created_at` 相同（`save` 盖的是秒级 `_now()`）时按**落库先后**排。

        破平键是 `rowid` 而不是 id：摘要 id 是随机 hex，字典序与写的先后无关。
        这里三行的落库顺序是 `03 → 01 → 02`，而 `id DESC` 会给成 `03 → 02 → 01`——
        两者**不同**，按 id 破平就露馅。
        """
        repo, conn = repo_and_conn
        _insert_digest_at(conn, "digest-r-00000003", "2026-10-18T10:00:00+00:00")
        _insert_digest_at(conn, "digest-r-00000001", "2026-10-18T10:00:00+00:00")
        _insert_digest_at(conn, "digest-r-00000002", "2026-10-18T10:00:00+00:00")

        assert [d.round_id for d in repo.recent("p1")] == [
            "r-00000002",
            "r-00000001",
            "r-00000003",
        ]

    def test_created_at_column_refuses_null(self, repo_and_conn):
        """这一列是 `NOT NULL`，所以"无时间"只能是 `''`，不是 NULL。

        钉住的是形状：哪天真把它放宽成可空，排序里"无时间沉底"的判据得跟着换。
        """
        repo, conn = repo_and_conn
        row = conn.execute(
            "SELECT is_nullable FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = 'round_digests'"
            " AND column_name = 'created_at'"
        ).fetchone()
        assert row["is_nullable"] == "NO"

        with pytest.raises(NotNullViolation):
            _insert_digest_at(conn, "digest-r-0000000b", None)

    def test_recent_filters_by_project(self, repo):
        repo.save(_digest("d-1", "r1"))
        # 不同 project
        repo.save(RoundDigest(round_id="r2", project_id="p-other", summary="其他"))
        recent = repo.recent("p1")
        assert all(d.project_id == "p1" for d in recent)
