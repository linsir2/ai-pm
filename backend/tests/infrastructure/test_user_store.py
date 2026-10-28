"""users / sessions 表 + 仓储（多用户改造 Task 2）。

会话 token **明文永不入库**：测试自己算 sha256 后再交给仓储，并回查原始行确认
`token_hash` 就是该摘要——把"只存哈希"这条钉在测试里。
"""

import hashlib
from datetime import UTC, datetime

from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.postgres.users import (
    SqliteSessionRepository,
    SqliteUserRepository,
)
from tests.support.postgres import open_direct

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_register_then_login_lookup():
    connection = open_direct()
    repo = SqliteUserRepository(connection)
    user_id = repo.register(username="alice", password_hash="$argon2id$fake")
    assert repo.get_by_name("alice").status == "pending"
    assert repo.get_by_name("nope") is None
    assert repo.get_by_name("alice").user_id == user_id
    connection.close()


def test_session_round_trips_by_token_hash():
    connection = open_direct()
    users = SqliteUserRepository(connection)
    sessions = SqliteSessionRepository(connection)
    user_id = users.register(username="alice", password_hash="$argon2id$fake")
    token = "plain-token-abc"

    session = sessions.create(
        user_id=user_id, token_hash=_sha256(token), expires_at=NOW, now=NOW
    )

    row = connection.execute(
        "SELECT token_hash FROM sessions WHERE session_id = %s", (session.session_id,)
    ).fetchone()
    assert row["token_hash"] == _sha256(token)
    assert row["token_hash"] != token

    found = sessions.get_by_token_hash(_sha256(token))
    assert found.user_id == user_id
    assert sessions.get_by_token_hash(_sha256("other-token")) is None
    connection.close()


def test_touch_renews_session():
    connection = open_direct()
    users = SqliteUserRepository(connection)
    sessions = SqliteSessionRepository(connection)
    user_id = users.register(username="alice", password_hash="$argon2id$fake")
    token_hash = _sha256("token-abc")
    sessions.create(user_id=user_id, token_hash=token_hash, expires_at=NOW, now=NOW)

    later = datetime(2026, 2, 1, tzinfo=UTC)
    sessions.touch(token_hash, now=later, expires_at=later)

    renewed = sessions.get_by_token_hash(token_hash)
    assert renewed.expires_at == later
    assert renewed.last_seen_at == later
    connection.close()


def test_get_by_id_round_trips():
    connection = open_direct()
    repo = SqliteUserRepository(connection)
    user_id = repo.register(username="alice", password_hash="$argon2id$fake")

    found = repo.get_by_id(user_id)
    assert found is not None
    assert found.user_id == user_id
    assert found.username == "alice"
    assert repo.get_by_id(UserId("u-nope")) is None
    connection.close()


def test_list_all_returns_every_user():
    connection = open_direct()
    repo = SqliteUserRepository(connection)
    repo.register(username="alice", password_hash="$argon2id$fake")
    repo.register(username="bob", password_hash="$argon2id$fake", role="admin")

    assert {record.username for record in repo.list_all()} == {"alice", "bob"}
    connection.close()


def test_revoked_session_is_not_found():
    connection = open_direct()
    users = SqliteUserRepository(connection)
    sessions = SqliteSessionRepository(connection)
    user_id = users.register(username="alice", password_hash="$argon2id$fake")
    token_hash = _sha256("token-abc")
    sessions.create(user_id=user_id, token_hash=token_hash, expires_at=NOW, now=NOW)

    sessions.revoke_user(user_id)

    assert sessions.get_by_token_hash(token_hash) is None
    connection.close()


def test_revoke_by_token_hash_leaves_other_sessions_alone():
    """按单条会话吊销（Ruling 18）：只踢命中的那条，同一用户的另一条照旧能用。"""

    connection = open_direct()
    users = SqliteUserRepository(connection)
    sessions = SqliteSessionRepository(connection)
    user_id = users.register(username="alice", password_hash="$argon2id$fake")
    first = _sha256("token-one")
    second = _sha256("token-two")
    sessions.create(user_id=user_id, token_hash=first, expires_at=NOW, now=NOW)
    sessions.create(user_id=user_id, token_hash=second, expires_at=NOW, now=NOW)

    sessions.revoke_by_token_hash(first)

    assert sessions.get_by_token_hash(first) is None
    assert sessions.get_by_token_hash(second) is not None
    connection.close()
