"""真 PostgreSQL：T3 简单仓储（users/sessions/call/model/tool/observation/digest/
inspiration/templates/usage）。

这些用例直接打 0001 schema：原生 timestamptz / jsonb / text[] / boolean / identity 与
psycopg `%s` 一并验证。旧 SQLite 用例留给 T8 统一改；本文件只覆盖本任务范围。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.errors import NotNullViolation, UniqueViolation
from psycopg.types.json import Jsonb
from scripts.migrate import apply_migrations

from pmstudio.application.errors import DigestAlreadyWritten, UnknownTemplate
from pmstudio.application.ports import AdminStatsStore
from pmstudio.domain.memory.digest import RoundDigest
from pmstudio.domain.memory.inspiration import InspirationItem, InspirationOrigin
from pmstudio.domain.memory.observations import ObservationType, RoundObservation
from pmstudio.domain.shared.ids import UserId
from pmstudio.domain.shared.template import standard_template
from pmstudio.infrastructure.postgres.database import connect
from pmstudio.infrastructure.sqlite.call_log import SqliteRoundCallLog
from pmstudio.infrastructure.sqlite.digest_repo import SqliteDigestRepository
from pmstudio.infrastructure.sqlite.inspiration_repo import SqliteInspirationRepository
from pmstudio.infrastructure.sqlite.model_calls import SqliteModelCallRecorder
from pmstudio.infrastructure.sqlite.observations_repo import SqliteObservationRepository
from pmstudio.infrastructure.sqlite.repositories import SqliteTemplateRepository
from pmstudio.infrastructure.sqlite.tool_calls import SqliteToolCallRecorder
from pmstudio.infrastructure.sqlite.usage import (
    SqliteAdminStatsRepository,
    SqliteUsageRepository,
)
from pmstudio.infrastructure.sqlite.users import (
    SqliteSessionRepository,
    SqliteUserRepository,
)
from pmstudio.infrastructure.templates.loader import (
    TEMPLATES_DIR,
    load_template_file,
    seed_builtin_templates,
)
from tests.support.postgres import (
    PgTestDatabase,
    load_environment,
    prepare,
    truncate_data_tables,
)

NOW = datetime(2026, 10, 8, 10, 0, 0, tzinfo=UTC)
OWNER = UserId("u-owner-scope-test")


@dataclass(frozen=True, slots=True)
class _Database:
    pg: PgTestDatabase


@pytest.fixture(scope="module")
def database() -> _Database:
    pg = load_environment()
    prepare(pg)
    apply_migrations(pg.direct_url)
    return _Database(pg)


@pytest.fixture()
def conn(database: _Database) -> Iterator[object]:
    truncate_data_tables(database.pg)
    with connect(database.pg.direct_url) as connection:
        yield connection
    truncate_data_tables(database.pg)


def _second(database: _Database):
    return connect(database.pg.direct_url)


# ── users / sessions ────────────────────────────────────────────────────────


def test_users_round_trip_native_timestamps(conn) -> None:
    users = SqliteUserRepository(conn)
    user_id = users.register(username="alice", password_hash="$argon2id$fake")

    record = users.get_by_name("alice")
    assert record is not None
    assert record.user_id == user_id
    assert record.created_at.tzinfo is not None

    users.set_status(
        user_id,
        "active",
        approved_by="u-admin",
        approved_at=NOW,
    )
    approved = users.get_by_id(user_id)
    assert approved is not None
    assert approved.approved_at == NOW
    assert approved.approved_by == "u-admin"
    assert [item.user_id for item in users.list_all()] == [user_id]
    assert users.get_by_name("nope") is None


def test_two_connections_share_one_user_and_reject_a_duplicate_username(
    database: _Database, conn
) -> None:
    first = SqliteUserRepository(conn)
    user_id = first.register(username="alice", password_hash="$argon2id$fake")

    with _second(database) as other:
        assert SqliteUserRepository(other).get_by_name("alice").user_id == user_id
        with pytest.raises(UniqueViolation) as caught:
            SqliteUserRepository(other).register(
                username="alice", password_hash="$argon2id$other"
            )
        assert caught.value.sqlstate == "23505"
        # autocommit：唯一冲突后连接没有停在 aborted 事务里。
        assert SqliteUserRepository(other).get_by_name("alice").password_hash == (
            "$argon2id$fake"
        )


def test_sessions_round_trip_across_connections_and_revoke(database: _Database, conn) -> None:
    users = SqliteUserRepository(conn)
    sessions = SqliteSessionRepository(conn)
    user_id = users.register(username="alice", password_hash="$argon2id$fake")
    token_hash = hashlib.sha256(b"plain-token").hexdigest()

    sessions.create(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=NOW + timedelta(days=7),
        now=NOW,
        ip="127.0.0.1",
        user_agent="pytest",
    )

    with _second(database) as other:
        seen = SqliteSessionRepository(other).get_by_token_hash(token_hash)
        assert seen is not None
        assert seen.user_id == user_id
        assert seen.expires_at == NOW + timedelta(days=7)
        assert seen.created_at == NOW
        assert seen.revoked_at is None

    later = NOW + timedelta(hours=1)
    sessions.touch(token_hash, now=later, expires_at=later + timedelta(days=7))
    renewed = sessions.get_by_token_hash(token_hash)
    assert renewed is not None
    assert renewed.last_seen_at == later
    assert renewed.expires_at == later + timedelta(days=7)

    sessions.revoke_by_token_hash(token_hash)
    assert sessions.get_by_token_hash(token_hash) is None


def test_revoke_user_leaves_other_users_sessions_alone(conn) -> None:
    users = SqliteUserRepository(conn)
    sessions = SqliteSessionRepository(conn)
    alice = users.register(username="alice", password_hash="$argon2id$fake")
    bob = users.register(username="bob", password_hash="$argon2id$fake")
    alice_token = hashlib.sha256(b"alice").hexdigest()
    bob_token = hashlib.sha256(b"bob").hexdigest()
    sessions.create(user_id=alice, token_hash=alice_token, expires_at=NOW, now=NOW)
    sessions.create(user_id=bob, token_hash=bob_token, expires_at=NOW, now=NOW)

    sessions.revoke_user(alice)

    assert sessions.get_by_token_hash(alice_token) is None
    assert sessions.get_by_token_hash(bob_token) is not None


# ── model_calls / tool_calls / call_log ─────────────────────────────────────


def test_model_calls_use_jsonb_bool_and_timestamptz(conn) -> None:
    recorder = SqliteModelCallRecorder(conn)
    recorder.record(
        trace_id="r1",
        purpose="integration",
        model="m",
        prompt_sha="abc",
        prompt_text="提示词",
        reply_text="{}",
        usage={"total_tokens": 12, "completion_tokens_details": {"reasoning": 3}},
        duration_ms=1234,
        created_at=NOW,
        attempt=2,
        ok=False,
        error="Timeout: 90s",
    )
    recorder.record(
        trace_id="r2",
        purpose="claims",
        model="m",
        prompt_sha="def",
        prompt_text="p",
        reply_text="{}",
        usage={"total_tokens": 1},
        duration_ms=1,
        created_at=NOW + timedelta(seconds=1),
    )

    rows = recorder.recent(round_id="r1")
    assert len(rows) == 1
    row = rows[0]
    assert row.usage == {
        "total_tokens": 12,
        "completion_tokens_details": {"reasoning": 3},
    }
    assert row.created_at == NOW
    assert row.ok is False
    assert (row.attempt, row.error) == (2, "Timeout: 90s")
    assert [item.prompt_sha for item in recorder.recent()] == ["def", "abc"]


def test_model_call_recorder_is_a_bypass_on_write_errors(monkeypatch) -> None:
    class _Broken:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise psycopg.OperationalError("write failed")

    recorder = SqliteModelCallRecorder(_Broken())  # type: ignore[arg-type]
    recorder.record(
        trace_id="r1",
        purpose="claims",
        model="m",
        prompt_sha="x",
        prompt_text="p",
        reply_text="{}",
        usage={},
        duration_ms=1,
        created_at=NOW,
    )


def test_model_call_recorder_swallows_unserializable_usage(conn) -> None:
    class _Weird:
        pass

    recorder = SqliteModelCallRecorder(conn)
    recorder.record(
        trace_id="r1",
        purpose="claims",
        model="m",
        prompt_sha="x",
        prompt_text="p",
        reply_text="{}",
        usage={"details": _Weird()},
        duration_ms=1,
        created_at=NOW,
    )

    assert recorder.recent(round_id="r1") == ()


def test_tool_calls_use_jsonb_bool_and_timestamptz(conn) -> None:
    recorder = SqliteToolCallRecorder(conn)
    recorder.record(
        trace_id="r1",
        tool_id="web_search",
        arguments={"query": "RBAC", "limit": 5},
        result_note="12 条",
        duration_ms=812,
        created_at=NOW,
        ok=False,
        error="Timeout: boom",
        error_kind="timeout",
    )

    row = conn.execute(
        "SELECT round_id, tool_id, arguments, result_note, duration_ms, ok,"
        " error, error_kind, created_at FROM tool_calls WHERE round_id = 'r1'"
    ).fetchone()
    assert row is not None
    assert row["arguments"] == {"query": "RBAC", "limit": 5}
    assert row["result_note"] == "12 条"
    assert row["duration_ms"] == 812
    assert row["ok"] is False
    assert row["error_kind"] == "timeout"
    assert row["created_at"] == NOW
    assert not hasattr(recorder, "recent")


def test_tool_call_recorder_swallows_write_and_serialization_errors(conn) -> None:
    class _Broken:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise psycopg.OperationalError("write failed")

    arguments = {"query": "RBAC"}
    SqliteToolCallRecorder(_Broken()).record(  # type: ignore[arg-type]
        trace_id="r1",
        tool_id="web_search",
        arguments=arguments,
        result_note="x",
        duration_ms=1,
        created_at=NOW,
    )
    SqliteToolCallRecorder(conn).record(
        trace_id="r1",
        tool_id="web_search",
        arguments={"bad": object()},
        result_note="x",
        duration_ms=1,
        created_at=NOW,
    )


def test_round_call_log_merges_both_sides_as_datetimes(conn) -> None:
    SqliteModelCallRecorder(conn).record(
        trace_id="r1",
        purpose="claims",
        model="m",
        prompt_sha="1",
        prompt_text="p",
        reply_text="{}",
        usage={},
        duration_ms=10,
        created_at=NOW,
    )
    SqliteToolCallRecorder(conn).record(
        trace_id="r1",
        tool_id="web_search",
        arguments={},
        result_note="x",
        duration_ms=900,
        created_at=NOW + timedelta(seconds=1),
        ok=False,
        error_kind="timeout",
    )
    SqliteModelCallRecorder(conn).record(
        trace_id="r1",
        purpose="integration",
        model="m",
        prompt_sha="2",
        prompt_text="p",
        reply_text="{}",
        usage={},
        duration_ms=20,
        created_at=NOW + timedelta(seconds=2),
        ok=False,
    )

    entries = SqliteRoundCallLog(conn).round_calls("r1")
    assert [(entry.kind, entry.name, entry.ok) for entry in entries] == [
        ("model", "claims", True),
        ("tool", "web_search", False),
        ("model", "integration", False),
    ]
    assert [entry.error_kind for entry in entries] == [None, "timeout", None]
    assert all(isinstance(entry.at, datetime) for entry in entries)
    assert entries[0].at == NOW


# ── observations / digests / inspirations ───────────────────────────────────


def _obs(
    obs_id: str,
    title: str,
    *,
    source_ref: str = "seat.a:O1",
    created_at: str = NOW.isoformat(),
) -> RoundObservation:
    return RoundObservation(
        obs_id=obs_id,
        round_id="r1",
        project_id="p1",
        obs_type=ObservationType.SEAT_INSIGHT,
        title=title,
        detail="detail",
        source_ref=source_ref,
        created_at=created_at,
    )


def test_observations_round_trip_dedupe_and_limit(conn) -> None:
    repo = SqliteObservationRepository(conn)
    repo.save(_obs("obs-1", "旧", source_ref="seat.a:O1"))
    repo.save(_obs("obs-2", "新", source_ref="seat.a:O1"))
    repo.save(_obs("obs-3", "另一席位", source_ref="seat.b:O1"))
    repo.save(
        RoundObservation(
            obs_id="obs-other",
            round_id="r1",
            project_id="p-other",
            obs_type=ObservationType.GAP_IDENTIFIED,
            title="Other",
            detail="",
            source_ref="",
            created_at=NOW.isoformat(),
        )
    )

    assert [item.title for item in repo.recent("p1", limit=5)] == ["另一席位", "新"]
    assert len(repo.recent("p1")) == 2
    assert [item.obs_id for item in repo.recent("p1", limit=1)] == ["obs-3"]
    assert all(item.project_id == "p1" for item in repo.recent("p1"))


def test_observations_order_by_text_time_then_seq(conn) -> None:
    repo = SqliteObservationRepository(conn)
    repo.save(
        _obs("obs-9", "九点", source_ref="seat.a:O1", created_at="2026-10-18T09:00:00+00:00")
    )
    repo.save(
        _obs("obs-10", "十点", source_ref="seat.b:O1", created_at="2026-10-18T10:00:00+00:00")
    )
    assert [item.title for item in repo.recent("p1")] == ["十点", "九点"]

    repo.save(
        _obs("obs-a", "同刻先", source_ref="seat.c:O1", created_at="2026-10-18T10:00:00+00:00")
    )
    repo.save(
        _obs("obs-b", "同刻后", source_ref="seat.d:O1", created_at="2026-10-18T10:00:00+00:00")
    )
    assert [item.title for item in repo.recent("p1")][:2] == ["同刻后", "同刻先"]


def test_observations_sink_the_empty_sentinel_and_reject_null(conn) -> None:
    repo = SqliteObservationRepository(conn)
    repo.save(_obs("obs-old", "老行", source_ref="seat.a:O1", created_at=""))
    repo.save(
        _obs("obs-new", "新行", source_ref="seat.b:O1", created_at="2026-10-18T10:00:00+00:00")
    )
    assert [item.title for item in repo.recent("p1")] == ["新行", "老行"]

    with pytest.raises(NotNullViolation):
        conn.execute(
            "INSERT INTO observations"
            " (id, project_id, owner_id, round_id, obs_type, title, created_at)"
            " VALUES ('obs-null', 'p1', %s, 'r1', 'seat_insight', '坏', NULL)",
            (str(OWNER),),
        )


def test_observations_are_owner_scoped(conn) -> None:
    repo = SqliteObservationRepository(conn)
    repo.save(_obs("obs-a", "A"), owner=UserId("u-a"))
    repo.save(_obs("obs-b", "B"), owner=UserId("u-b"))
    assert [item.obs_id for item in repo.recent("p1", owner=UserId("u-a"))] == ["obs-a"]
    assert [item.obs_id for item in repo.recent("p1", owner=UserId("u-b"))] == ["obs-b"]


def _digest(summary: str = "摘要", round_id: str = "r1") -> RoundDigest:
    return RoundDigest(
        round_id=round_id,
        project_id="p1",
        summary=summary,
        sections_touched=("s1", "s5"),
        features_addressed=("group-buying",),
        key_outcomes=("定位=社区团购", "规则=7天退还"),
    )


def test_digest_round_trips_text_arrays(conn) -> None:
    repo = SqliteDigestRepository(conn)
    repo.save(_digest())
    loaded = repo.recent("p1")
    assert len(loaded) == 1
    assert loaded[0].sections_touched == ("s1", "s5")
    assert loaded[0].features_addressed == ("group-buying",)
    assert loaded[0].key_outcomes == ("定位=社区团购", "规则=7天退还")


def test_digest_append_only_raises_and_recovers(conn) -> None:
    repo = SqliteDigestRepository(conn)
    repo.save(_digest("原始"))
    with pytest.raises(DigestAlreadyWritten):
        repo.save(_digest("更新"))
    assert repo.recent("p1")[0].summary == "原始"


def test_digest_order_limit_and_empty_sentinel(conn) -> None:
    repo = SqliteDigestRepository(conn)
    conn.execute(
        "INSERT INTO round_digests"
        " (id, project_id, owner_id, round_id, summary, sections, features, outcomes,"
        " created_at) VALUES ('d-1', 'p1', %s, 'r1', '一', %s, %s, %s,"
        " '2026-10-04T00:00:00+00:00')",
        (str(OWNER), ["s1"], [], []),
    )
    conn.execute(
        "INSERT INTO round_digests"
        " (id, project_id, owner_id, round_id, summary, sections, features, outcomes,"
        " created_at) VALUES ('d-2', 'p1', %s, 'r2', '二', %s, %s, %s,"
        " '2026-10-03T00:00:00+00:00')",
        (str(OWNER), [], [], []),
    )
    conn.execute(
        "INSERT INTO round_digests"
        " (id, project_id, owner_id, round_id, summary, sections, features, outcomes,"
        " created_at) VALUES ('d-3', 'p1', %s, 'r3', '老', %s, %s, %s, '')",
        (str(OWNER), [], [], []),
    )
    assert [item.round_id for item in repo.recent("p1")] == ["r1", "r2", "r3"]
    assert [item.round_id for item in repo.recent("p1", limit=2)] == ["r1", "r2"]


def test_inspiration_round_trip_upsert_and_limit(conn) -> None:
    repo = SqliteInspirationRepository(conn)
    repo.save(
        InspirationItem(
            item_id="ins-1",
            project_id="p1",
            origin=InspirationOrigin.DISCUSSION,
            statement="原始",
        )
    )
    repo.save(
        InspirationItem(
            item_id="ins-1",
            project_id="p1",
            origin=InspirationOrigin.DISCUSSION,
            statement="更新",
        )
    )
    assert repo.recent("p1")[0].statement == "更新"

    for index in range(5):
        repo.save(
            InspirationItem(
                item_id=f"ins-{index + 2}",
                project_id="p1",
                origin=InspirationOrigin.EXPLORATION,
                statement=f"灵感{index}",
            )
        )
    assert len(repo.recent("p1")) == 6
    assert len(repo.recent("p1", limit=2)) == 2


def test_inspiration_order_sentinel_and_owner_scope(conn) -> None:
    repo = SqliteInspirationRepository(conn)
    conn.execute(
        "INSERT INTO inspirations (id, project_id, owner_id, origin, statement,"
        " created_at) VALUES ('ins-a', 'p1', %s, 'discussion', '早',"
        " '2026-10-18T09:00:00+00:00')",
        (str(OWNER),),
    )
    conn.execute(
        "INSERT INTO inspirations (id, project_id, owner_id, origin, statement,"
        " created_at) VALUES ('ins-b', 'p1', %s, 'discussion', '晚',"
        " '2026-10-18T10:00:00+00:00')",
        (str(OWNER),),
    )
    conn.execute(
        "INSERT INTO inspirations (id, project_id, owner_id, origin, statement,"
        " created_at) VALUES ('ins-old', 'p1', %s, 'discussion', '老', '')",
        (str(OWNER),),
    )
    assert [item.statement for item in repo.recent("p1")] == ["晚", "早", "老"]

    repo.save(
        InspirationItem(
            item_id="ins-other",
            project_id="p1",
            origin=InspirationOrigin.DISCUSSION,
            statement="别人的",
        ),
        owner=UserId("u-other"),
    )
    assert [item.item_id for item in repo.recent("p1")] == ["ins-b", "ins-a", "ins-old"]


# ── templates ───────────────────────────────────────────────────────────────


def test_templates_round_trip_and_builtin_flag(conn) -> None:
    repo = SqliteTemplateRepository(conn)
    repo.save(standard_template(), builtin=True)
    repo.save(load_template_file(TEMPLATES_DIR / "light.toml"), builtin=True)
    repo.save(_custom_template(), builtin=False)

    listed = repo.list()
    assert [item.template.template_id for item in listed] == ["standard", "light", "tpl-custom"]
    assert {item.template.template_id: item.builtin for item in listed} == {
        "standard": True,
        "light": True,
        "tpl-custom": False,
    }
    assert repo.get("standard").template == standard_template()
    assert repo.get("tpl-custom").builtin is False


def test_builtin_templates_seed_is_idempotent(conn) -> None:
    repo = SqliteTemplateRepository(conn)
    repo.save(_custom_template(), builtin=False)

    first = seed_builtin_templates(repo)
    second = seed_builtin_templates(repo)

    assert first == second
    assert {"standard", "light"} <= set(first)
    assert repo.get("tpl-custom").template.template_id == "tpl-custom"
    assert len(repo.list()) == 3


def test_a_custom_template_cannot_overwrite_another_owners_row(conn) -> None:
    first = SqliteTemplateRepository(conn)
    first.save(_custom_template(title="A 的"), builtin=False, owner=UserId("u-a"))

    second = SqliteTemplateRepository(conn)
    second.save(_custom_template(title="B 的"), builtin=False, owner=UserId("u-b"))

    assert first.get("tpl-custom", owner=UserId("u-a")).template.title == "A 的"
    with pytest.raises(UnknownTemplate):
        second.get("tpl-custom", owner=UserId("u-b"))


def test_builtin_template_cannot_be_deleted_and_unknown_delete_is_404(conn) -> None:
    repo = SqliteTemplateRepository(conn)
    repo.save(standard_template(), builtin=True)

    with pytest.raises(UnknownTemplate):
        repo.delete("standard")
    with pytest.raises(UnknownTemplate):
        repo.delete("tpl-nope")
    assert repo.get("standard").builtin is True


# ── usage / admin stats ─────────────────────────────────────────────────────


def _seed_usage(conn) -> tuple[UserId, UserId]:
    human = UserId("u-alice")
    other = UserId("u-bob")
    conn.execute(
        "INSERT INTO users (user_id, username, password_hash, status, role, created_at)"
        " VALUES (%s, 'alice', 'h', 'active', 'admin', %s)",
        (str(human), NOW),
    )
    conn.execute(
        "INSERT INTO users (user_id, username, password_hash, status, role, created_at)"
        " VALUES (%s, 'bob', 'h', 'active', 'user', %s)",
        (str(other), NOW + timedelta(seconds=1)),
    )
    conn.execute(
        "INSERT INTO documents (project_id, owner_id, document_id, title, template,"
        " blocks, preference_entries, tombstones, created_at, updated_at)"
        " VALUES ('p1', %s, 'doc-1', '项目', %s, %s, %s, %s, %s, %s)",
        (str(human), Jsonb({}), Jsonb([]), Jsonb([]), Jsonb([]), NOW, NOW),
    )
    conn.execute(
        "INSERT INTO rounds (round_id, project_id, owner_id, step, waiting_on, payload,"
        " created_at, updated_at) VALUES ('r1', 'p1', %s, 'done', 'none', %s, %s, %s)",
        (str(human), Jsonb({}), NOW, NOW),
    )
    conn.execute(
        "INSERT INTO model_calls (round_id, purpose, model, prompt_sha, prompt_text,"
        " reply_text, usage_json, duration_ms, created_at, ok)"
        " VALUES ('r1', 'integration', 'm', 'a', 'p', 'r', %s, 1000, %s, true)",
        (Jsonb({"total_tokens": 100}), NOW),
    )
    conn.execute(
        "INSERT INTO model_calls (round_id, purpose, model, prompt_sha, prompt_text,"
        " reply_text, usage_json, duration_ms, created_at, ok)"
        " VALUES ('r1', 'integration', 'm', 'b', 'p', 'r', %s, 3000, %s, false)",
        (Jsonb({"total_tokens": 50}), NOW + timedelta(seconds=1)),
    )
    conn.execute(
        "INSERT INTO model_calls (round_id, purpose, model, prompt_sha, prompt_text,"
        " reply_text, usage_json, duration_ms, created_at, ok)"
        " VALUES ('r1', 'integration', 'm', 'c', 'p', 'r', %s, 2000, %s, true)",
        (Jsonb({"total_tokens": "not-a-number"}), NOW + timedelta(seconds=2)),
    )
    conn.execute(
        "INSERT INTO model_calls (round_id, purpose, model, prompt_sha, prompt_text,"
        " reply_text, usage_json, duration_ms, created_at, ok)"
        " VALUES ('x-orphan', 'exploration', 'm', 'd', 'p', 'r', %s, 10, %s, true)",
        (Jsonb({"total_tokens": 999}), NOW),
    )
    conn.execute(
        "INSERT INTO tool_calls (round_id, tool_id, arguments, result_note, duration_ms,"
        " created_at, ok) VALUES ('r1', 'web_search', %s, 'x', 500, %s, true)",
        (Jsonb({}), NOW),
    )
    conn.execute(
        "INSERT INTO tool_calls (round_id, tool_id, arguments, result_note, duration_ms,"
        " created_at, ok) VALUES ('r1', 'web_search', %s, 'x', 1500, %s, false)",
        (Jsonb({}), NOW + timedelta(seconds=1)),
    )
    conn.execute(
        "INSERT INTO sessions (session_id, user_id, token_hash, expires_at, created_at,"
        " last_seen_at, ip, user_agent)"
        " VALUES ('s1', %s, 'h1', %s, %s, %s, '', '')",
        (str(human), NOW + timedelta(days=1), NOW, NOW),
    )
    conn.execute(
        "INSERT INTO sessions (session_id, user_id, token_hash, expires_at, created_at,"
        " last_seen_at, ip, user_agent)"
        " VALUES ('s2', %s, 'h2', %s, %s, %s, '', '')",
        (str(human), NOW - timedelta(days=1), NOW, NOW - timedelta(days=2)),
    )
    conn.execute(
        "INSERT INTO sessions (session_id, user_id, token_hash, expires_at, created_at,"
        " last_seen_at, ip, user_agent, revoked_at)"
        " VALUES ('s3', %s, 'h3', %s, %s, %s, '', '', %s)",
        (str(human), NOW + timedelta(days=1), NOW, NOW, NOW),
    )
    return human, other


def test_admin_stats_aggregates_without_fanout_and_sums_jsonb_tokens(conn) -> None:
    human, _ = _seed_usage(conn)
    repository = SqliteAdminStatsRepository(conn)
    assert isinstance(repository, AdminStatsStore)

    rows = {row.user_id: row for row in repository.rows(now=NOW)}
    row = rows[str(human)]
    assert row.projects == 1
    assert row.rounds == 1
    assert row.calls == 3
    assert row.failed == 1
    assert row.total_tokens == 150
    assert row.tool_calls == 2
    assert row.tool_failed == 1
    assert row.avg_duration_ms == 2000
    assert row.tool_avg_duration_ms == 1000
    assert row.active_sessions == 1
    assert row.last_seen_at == NOW
    assert row.created_at == NOW
    assert row.approved_at is None


def test_usage_counts_model_calls_by_owner(conn) -> None:
    human, _ = _seed_usage(conn)
    assert SqliteUsageRepository(conn).count_model_calls_by_owner() == {human: 3}


def _custom_template(title: str = "自建"):
    from pmstudio.domain.shared.template import template_from_dict

    return template_from_dict(
        {
            "template_id": "tpl-custom",
            "title": title,
            "sections": [
                {"id": "s1", "title": "背景", "purpose": "p", "role": "plain"},
                {"id": "s5", "title": "功能", "purpose": "p", "role": "feature"},
                {"id": "s11", "title": "未决", "purpose": "p", "role": "open_questions"},
                {"id": "s13", "title": "版本", "purpose": "p", "role": "version_history"},
            ],
        }
    )
