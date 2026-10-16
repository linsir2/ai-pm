"""users / sessions 表 + 仓储（多用户改造 Task 2）。

会话 token **明文永不入库**：测试自己算 sha256 后再交给仓储，并回查原始行确认
`token_hash` 就是该摘要——把"只存哈希"这条钉在测试里。
"""

import hashlib
from datetime import UTC, datetime

from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.users import (
    SqliteSessionRepository,
    SqliteUserRepository,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_register_then_login_lookup(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    repo = SqliteUserRepository(connection)
    user_id = repo.register(username="alice", password_hash="$argon2id$fake")
    assert repo.get_by_name("alice").status == "pending"
    assert repo.get_by_name("nope") is None
    assert repo.get_by_name("alice").user_id == user_id
    connection.close()


def test_session_round_trips_by_token_hash(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    users = SqliteUserRepository(connection)
    sessions = SqliteSessionRepository(connection)
    user_id = users.register(username="alice", password_hash="$argon2id$fake")
    token = "plain-token-abc"

    session = sessions.create(
        user_id=user_id, token_hash=_sha256(token), expires_at=NOW, now=NOW
    )

    row = connection.execute(
        "SELECT token_hash FROM sessions WHERE session_id = ?", (session.session_id,)
    ).fetchone()
    assert row["token_hash"] == _sha256(token)
    assert row["token_hash"] != token

    found = sessions.get_by_token_hash(_sha256(token))
    assert found.user_id == user_id
    assert sessions.get_by_token_hash(_sha256("other-token")) is None
    connection.close()


def test_touch_renews_session(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
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


def test_revoked_session_is_not_found(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    users = SqliteUserRepository(connection)
    sessions = SqliteSessionRepository(connection)
    user_id = users.register(username="alice", password_hash="$argon2id$fake")
    token_hash = _sha256("token-abc")
    sessions.create(user_id=user_id, token_hash=token_hash, expires_at=NOW, now=NOW)

    sessions.revoke_user(user_id)

    assert sessions.get_by_token_hash(token_hash) is None
    connection.close()
