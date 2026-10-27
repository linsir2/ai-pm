"""T9：旧 SQLite → 真 PostgreSQL 的 ETL 与切换演练。

覆盖三件事，缺一不可：

1. **真旧库形状**：v15（无 lease/tool_calls/round_skips/vector_meta）与 v17
   （有这三样）都跑；枚举、skip、未知表、重复 sha、重复 running 都按设计。
2. **真类型**：BLOB、中文、`%`/`_`、空串/空数组、0/1、naive/aware 时间、JSONB、
   `seq`/`id` + setval、tokens 回填、向量重嵌入（测试用确定性假 embedder）。
3. **切换动作**：`--schema-only` 后 ETL → `--finalize` → 真 app 登录读项目；
   以及 ETL 结果的真 `pg_dump`/`pg_restore` scratch 恢复。

本文件是 T10 no-sqlite 白名单里的 ETL 测试；SQLite 只在这里出现。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
from psycopg import connect as psycopg_connect
from scripts.init_db import finalize_database
from scripts.migrate_sqlite_to_pg import EtlError, main, migrate

from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import AppSettings, OpsSettings
from pmstudio.domain.shared.ids import ProjectId, UserId
from pmstudio.domain.shared.template import standard_template
from pmstudio.infrastructure.postgres.database import connect
from pmstudio.infrastructure.postgres.materials import SqliteFtsRetriever
from pmstudio.infrastructure.postgres.serialization import template_to_json
from pmstudio.infrastructure.postgres.tokenize import index_source
from pmstudio.infrastructure.security import hash_password
from tests.support.embedding import FakeEmbedder
from tests.support.postgres import load_environment, open_direct, schema_url

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REAL_V15 = BACKEND_ROOT / "data" / "pmstudio.db"
PASSWORD = "cutover-pass"


# —— 旧库 fixture（v15 / v17 两种形状）────────────────────────────────────────


def _base_schema() -> str:
    return """
CREATE TABLE users (
  user_id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  role TEXT NOT NULL DEFAULT 'user',
  approved_by TEXT,
  approved_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE documents (
  project_id TEXT PRIMARY KEY,
  owner_id TEXT,
  document_id TEXT NOT NULL,
  title TEXT NOT NULL,
  template TEXT NOT NULL DEFAULT '{}',
  blocks TEXT NOT NULL,
  preference_entries TEXT NOT NULL,
  tombstones TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE document_versions (
  project_id TEXT NOT NULL,
  owner_id TEXT,
  version_no INTEGER NOT NULL,
  "trigger" TEXT NOT NULL,
  summary TEXT NOT NULL,
  changed_block_ids TEXT NOT NULL,
  snapshot TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (project_id, version_no)
);
CREATE TABLE rounds (
  round_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  owner_id TEXT,
  step TEXT NOT NULL,
  waiting_on TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE templates (
  template_id TEXT PRIMARY KEY,
  owner_id TEXT,
  title TEXT NOT NULL,
  spec TEXT NOT NULL,
  builtin INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE evidence_ledgers (
  project_id TEXT PRIMARY KEY,
  owner_id TEXT,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE materials (
  material_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  owner_id TEXT,
  origin TEXT NOT NULL,
  title TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  media_type TEXT NOT NULL,
  bytes_len INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  parser_id TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  truncated INTEGER NOT NULL,
  warnings TEXT NOT NULL,
  content BLOB NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE material_chunks (
  chunk_id TEXT PRIMARY KEY,
  material_id TEXT NOT NULL,
  owner_id TEXT,
  ordinal INTEGER NOT NULL,
  text TEXT NOT NULL,
  heading_path TEXT NOT NULL,
  anchor TEXT NOT NULL
);
CREATE TABLE explorations (
  exploration_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  owner_id TEXT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE discussions (
  discussion_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  owner_id TEXT,
  status TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE project_memory (
  project_id TEXT PRIMARY KEY,
  owner_id TEXT,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE model_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  round_id TEXT,
  purpose TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_sha TEXT NOT NULL,
  prompt_text TEXT NOT NULL,
  reply_text TEXT NOT NULL,
  usage_json TEXT NOT NULL,
  duration_ms INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  ok INTEGER NOT NULL DEFAULT 1,
  error TEXT
);
CREATE TABLE observations (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  owner_id TEXT,
  round_id TEXT NOT NULL,
  obs_type TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  source_ref TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE round_digests (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  owner_id TEXT,
  round_id TEXT NOT NULL,
  summary TEXT NOT NULL,
  sections TEXT NOT NULL DEFAULT '[]',
  features TEXT NOT NULL DEFAULT '[]',
  outcomes TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  token_hash TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  ip TEXT NOT NULL DEFAULT '',
  user_agent TEXT NOT NULL DEFAULT '',
  revoked_at TEXT
);
CREATE TABLE inspirations (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  owner_id TEXT,
  origin TEXT NOT NULL,
  statement TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def _v17_extra() -> str:
    return """
ALTER TABLE rounds ADD COLUMN lease_owner TEXT;
ALTER TABLE rounds ADD COLUMN lease_expires_at INTEGER;
ALTER TABLE rounds ADD COLUMN lease_token INTEGER NOT NULL DEFAULT 0;
ALTER TABLE explorations ADD COLUMN lease_owner TEXT;
ALTER TABLE explorations ADD COLUMN lease_expires_at INTEGER;
ALTER TABLE explorations ADD COLUMN lease_token INTEGER NOT NULL DEFAULT 0;
CREATE TABLE round_skips (
  round_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  seat_id TEXT NOT NULL,
  pass_index INTEGER NOT NULL,
  reason TEXT NOT NULL,
  source TEXT NOT NULL,
  requested_at INTEGER NOT NULL,
  PRIMARY KEY (round_id, seat_id)
);
CREATE TABLE tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  round_id TEXT,
  tool_id TEXT NOT NULL,
  arguments TEXT NOT NULL,
  result_note TEXT NOT NULL,
  duration_ms INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  ok INTEGER NOT NULL DEFAULT 1,
  error TEXT,
  error_kind TEXT
);
CREATE TABLE vector_index_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def _schema(version: int) -> str:
    return _base_schema() + (_v17_extra() if version == 17 else "")


def _block(section: str = "s1", *, with_entry: bool = False) -> dict:
    entries = (
        [{"entry_id": "s1.b1", "text": "退货时限是七天", "refs": ["E1"]}]
        if with_entry
        else []
    )
    return {
        "block_id": section,
        "parent_id": None,
        "kind": "section",
        "entries": entries,
        "field": None,
        "feature_slug": None,
        "manually_edited": False,
    }


def _standard_blocks(*, with_entry: bool = True) -> list[dict]:
    sections = [f"s{i}" for i in range(1, 14)]
    return [
        _block(section, with_entry=with_entry and section == "s1")
        for section in sections
    ]


def _round_payload() -> dict:
    return {
        "user_input": "退货时限是七天",
        "claims": [],
        "seats": [],
        "seat_reviews": [],
        "questionnaire": None,
        "answer_sets": [],
        "draft": None,
        "seat_round_count": 1,
        "authorized_new_features": {},
        "failure": None,
        "confirmed_version_no": None,
        "web_queries": [{"query": "退货", "why": "确认时限"}],
        "web_queries_chosen": ["退货"],
    }


def _exploration_payload(text: str) -> dict:
    return {
        "scope": {"kind": "document", "ref": None, "text": text},
        "hypotheses": [],
        "blind_spots": [],
        "states": {},
        "kept": {},
        "kept_ref": {},
        "failure": None,
        "abandon_reason": None,
        "overflow_count": 0,
    }


def _discussion_payload() -> dict:
    return {
        "topic": "退货流程",
        "participants": [
            {
                "role_id": "seat.business_rules",
                "role_title": "业务规则",
                "joined_round": 1,
            }
        ],
        "question": None,
        "findings": [],
        "turns": [],
        "summaries": [],
        "cards": [],
        "states": {},
        "kept_as": {},
        "kept_ref": {},
    }


def _evidence_payload() -> list[dict]:
    return [
        {
            "eid": "E1",
            "kind": "user_input",
            "text": "退货时限是七天",
            "source": {
                "round_id": "r-1",
                "raw_span": "退货时限是七天",
                "question_id": None,
                "seat_id": None,
                "local_id": None,
                "derived_from": [],
                "rationale": None,
                "external_ref": None,
            },
            "created_at": "2026-01-02T03:04:05+00:00",
            "supersedes": None,
        }
    ]


def _make_fixture(
    tmp_path: Path,
    *,
    version: int = 15,
    duplicate_sha: bool = False,
    unknown_table: bool = False,
) -> Path:
    path = tmp_path / f"legacy-v{version}.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(_schema(version))
        connection.execute(f"PRAGMA user_version = {version}")
        now = datetime.now(UTC).isoformat()
        later = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        connection.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("u-admin", "cutover-admin", hash_password(PASSWORD), "active", "admin",
             None, None, now),
        )
        connection.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("u-user", "cutover-user", hash_password(PASSWORD), "active", "user",
             "u-admin", now, now),
        )
        blocks = json.dumps(_standard_blocks(), ensure_ascii=False)
        snapshot = json.dumps(
            {"blocks": _standard_blocks(), "preference_entries": []},
            ensure_ascii=False,
        )
        empty_blocks = json.dumps(_standard_blocks(with_entry=False), ensure_ascii=False)
        empty_snapshot = json.dumps(
            {"blocks": _standard_blocks(with_entry=False), "preference_entries": []},
            ensure_ascii=False,
        )
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("p-1", "u-admin", "doc-1", "退货_流程 100%", "{}", blocks, "[]", "[]",
             now, now),
        )
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("p-2", "u-user", "doc-2", "中文项目", "{}", empty_blocks, "[]", "[]",
             "2026-01-02T03:04:05", "2026-01-02T03:04:05"),
        )
        connection.execute(
            "INSERT INTO document_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("p-1", "u-admin", 1, "manual", "初始化", '["s1"]', snapshot, now),
        )
        connection.execute(
            "INSERT INTO document_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("p-2", "u-user", 1, "manual", "初始化", "[]", empty_snapshot, now),
        )
        connection.execute(
            "INSERT INTO rounds (round_id, project_id, owner_id, step, waiting_on,"
            " payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("r-1", "p-1", "u-admin", "failed", "none",
             json.dumps(_round_payload(), ensure_ascii=False), now, now),
        )
        template_spec = json.dumps(
            template_to_json(standard_template()), ensure_ascii=False
        )
        connection.execute(
            "INSERT INTO templates VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("standard", None, "标准版", template_spec, 1, now, now),
        )
        connection.execute(
            "INSERT INTO evidence_ledgers VALUES (?, ?, ?, ?)",
            ("p-1", "u-admin", json.dumps(_evidence_payload(), ensure_ascii=False), now),
        )
        content = b"\x00\xff binary"
        connection.execute(
            "INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "m-1", "p-1", "u-admin", "upload", "售后_政策 100%",
                "file:售后_政策 100%.txt", "text/plain", len(content),
                "sha-1", "parser", "1", 1, 0,
                json.dumps(["含 % 与 _ 的告警"], ensure_ascii=False),
                content, now,
            ),
        )
        connection.execute(
            "INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "m-2", "p-1", "u-admin", "upload", "退款说明", "file:退款说明.txt",
                "text/plain", 3, "sha-2", "parser", "1", 1, 0, "[]", b"abc", now,
            ),
        )
        if duplicate_sha:
            connection.execute(
                "INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "m-dup", "p-1", "u-admin", "upload", "重复 sha",
                    "file:dup.txt", "text/plain", 3, "sha-1", "parser", "1", 1, 0,
                    "[]", b"dup", now,
                ),
            )
        connection.execute(
            "INSERT INTO material_chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("c-1", "m-1", "u-admin", 0, "退货时限是七天。", "", "p1"),
        )
        connection.execute(
            "INSERT INTO material_chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "c-2", "m-1", "u-admin", 1, "系统会在三日内退款。",
                json.dumps(["售后_政策", "退款%流程"], ensure_ascii=False), "p2",
            ),
        )
        connection.execute(
            "INSERT INTO explorations (exploration_id, project_id, owner_id, kind,"
            " status, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("x-old", "p-1", "u-admin", "inquiry", "running",
             json.dumps(_exploration_payload("旧"), ensure_ascii=False), now, now),
        )
        connection.execute(
            "INSERT INTO explorations (exploration_id, project_id, owner_id, kind,"
            " status, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("x-new", "p-1", "u-admin", "inquiry", "running",
             json.dumps(_exploration_payload("新"), ensure_ascii=False), later, later),
        )
        connection.execute(
            "INSERT INTO discussions VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("d-1", "p-1", "u-admin", "open",
             json.dumps(_discussion_payload(), ensure_ascii=False), now, now),
        )
        connection.execute(
            "INSERT INTO discussions VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("d-null", "p-1", None, "open",
             json.dumps(_discussion_payload(), ensure_ascii=False), now, now),
        )
        connection.execute(
            "INSERT INTO project_memory VALUES (?, ?, ?, ?)",
            ("p-1", "u-admin", json.dumps({"entries": [], "revisions": []}), now),
        )
        connection.execute(
            "INSERT INTO model_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, None, "claims", "model", "sha", "prompt", "", "{}", 12, now, 2, 0,
             None),
        )
        connection.execute(
            "INSERT INTO model_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, "r-1", "seat_review", "model", "sha", "prompt", "reply",
             json.dumps({"total_tokens": 12}), 13, now, 1, 1, None),
        )
        connection.execute(
            "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("obs-1", "p-1", "u-admin", "r-1", "seat_insight", "无时间观察",
             "详情", "ref", ""),
        )
        connection.execute(
            "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("obs-2", "p-1", "u-admin", "r-1", "seat_insight", "有时间观察",
             "详情", "ref", now),
        )
        connection.execute(
            "INSERT INTO round_digests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("digest-1", "p-1", "u-admin", "r-1", "小结", "", '["s1"]', "", ""),
        )
        connection.execute(
            "INSERT INTO inspirations VALUES (?, ?, ?, ?, ?, ?)",
            ("ins-1", "p-1", "u-admin", "discussion", "灵感", ""),
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("s-1", "u-admin", "hash", now, now, now, "", "", None),
        )
        if version == 17:
            connection.execute(
                "INSERT INTO round_skips VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("r-1", "u-admin", "seat.data_scale", 1, "不需要", "user", 1700000000000),
            )
            connection.execute(
                "INSERT INTO tool_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, None, "web_search", json.dumps({"query": "退货"}, ensure_ascii=False),
                 "0 条", 5, now, 0, "失败", "timeout"),
            )
            connection.execute(
                "INSERT INTO vector_index_meta VALUES (?, ?)",
                ("embed_profile", "BAAI/bge-small-zh-v1.5/natural"),
            )
        if unknown_table:
            connection.execute("CREATE TABLE surprise (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()
    return path


# —— 断言工具 ——


def _count(table: str) -> int:
    with open_direct() as connection:
        return int(connection.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"])


def _fetch_one(sql: str, params: tuple = ()) -> dict:
    with open_direct() as connection:
        row = connection.execute(sql, params).fetchone()
    assert row is not None
    return dict(row)


def _all_checks(report) -> None:  # type: ignore[no-untyped-def]
    assert report.checks == {
        "row_counts": True,
        "values": True,
        "json": True,
        "primary_keys": True,
        "tokens": True,
        "roundtrip": True,
    }, report.checks


# —— 测试 ——


def test_v15_fixture_migrates_native_types_dedupes_and_backfills_tokens(tmp_path: Path) -> None:
    source = _make_fixture(tmp_path, version=15)
    report = migrate(source, load_environment().direct_url, truncate=True,
                     embedder=FakeEmbedder(512))

    assert report.user_version == 15
    assert report.deduped_running == [("p-1", "x-old")]
    assert report.skipped_sessions == 1
    assert report.counts["documents"] == 2
    assert report.counts["model_calls"] == 2
    assert report.counts["materials"] == 2
    assert report.embedded_chunks == 2
    _all_checks(report)

    assert _count("sessions") == 0
    assert _count("tool_calls") == 0
    assert _count("round_skips") == 0
    assert _count("vector_index_meta") == 0

    material = _fetch_one(
        "SELECT content, warnings, created_at FROM materials WHERE material_id = 'm-1'"
    )
    assert bytes(material["content"]) == b"\x00\xff binary"
    assert material["warnings"] == ["含 % 与 _ 的告警"]
    assert material["created_at"].tzinfo is not None

    naive = _fetch_one("SELECT created_at FROM documents WHERE project_id = 'p-2'")
    assert naive["created_at"] == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    chunk = _fetch_one(
        "SELECT text, heading_path, tokens FROM material_chunks WHERE chunk_id = 'c-1'"
    )
    assert chunk["heading_path"] == []
    assert chunk["tokens"] == index_source(
        title="售后_政策 100%", heading_path=[], text=chunk["text"]
    )
    chunk2 = _fetch_one("SELECT heading_path, tokens FROM material_chunks WHERE chunk_id = 'c-2'")
    assert chunk2["heading_path"] == ["售后_政策", "退款%流程"]
    assert chunk2["tokens"] == index_source(
        title="售后_政策 100%", heading_path=chunk2["heading_path"], text="系统会在三日内退款。"
    )

    model = _fetch_one("SELECT reply_text, ok, usage_json, round_id FROM model_calls WHERE id = 1")
    assert model["reply_text"] == ""
    assert model["ok"] is False
    assert model["usage_json"] == {}
    assert model["round_id"] is None
    assert _count("model_calls") == 2

    users = _fetch_one("SELECT approved_by, approved_at FROM users WHERE user_id = 'u-admin'")
    assert users["approved_by"] is None and users["approved_at"] is None

    statuses = {
        row["exploration_id"]: row["status"]
        for row in _fetch_all("SELECT exploration_id, status FROM explorations")
    }
    assert statuses == {"x-old": "abandoned", "x-new": "running"}

    round_row = _fetch_one("SELECT seq, lease_token FROM rounds WHERE round_id = 'r-1'")
    assert isinstance(round_row["seq"], int) and round_row["seq"] >= 1
    assert round_row["lease_token"] == 0

    arrays = _fetch_one(
        "SELECT sections, features, outcomes, created_at FROM round_digests"
    )
    assert arrays["sections"] == [] and arrays["features"] == ["s1"]
    assert arrays["outcomes"] == [] and arrays["created_at"] == ""

    # FTS smoke（不是 recall@5）：中文 query 能命中，带 % / _ 的标题原样保留；
    # T9 的检索验收口径是 tokens 相等，recall 挂 T5/T10 hard gate。
    with open_direct() as connection:
        hits = SqliteFtsRetriever(connection).search(
            project_id=ProjectId("p-1"), query="退货时限", limit=5,
            owner=UserId("u-admin"),
        )
    assert hits and hits[0].material_id == "m-1"
    assert _fetch_one("SELECT title FROM materials WHERE material_id = 'm-1'")["title"] == (
        "售后_政策 100%"
    )

    # rowid → seq 之后 setval：下一行从 max+1 开始，不撞旧主键。
    with open_direct() as connection:
        sequence = connection.execute(
            "SELECT pg_get_serial_sequence('rounds', 'seq') AS seq"
        ).fetchone()["seq"]
        assert sequence
        next_value = connection.execute(
            "SELECT nextval(%s) AS value", (str(sequence),)
        ).fetchone()["value"]
    assert int(next_value) > int(round_row["seq"])


def test_v17_fixture_migrates_tool_calls_round_skips_and_vector_meta(tmp_path: Path) -> None:
    source = _make_fixture(tmp_path, version=17)
    report = migrate(source, load_environment().direct_url, truncate=True,
                     embedder=FakeEmbedder(512))
    _all_checks(report)
    assert report.vector_profile == {
        "id": 1,
        "profile": "BAAI/bge-small-zh-v1.5/natural",
        "dimensions": 512,
        "model": "BAAI/bge-small-zh-v1.5",
    }
    assert report.embedded_chunks == 2

    tool = _fetch_one("SELECT arguments, ok, error_kind FROM tool_calls")
    assert tool["arguments"] == {"query": "退货"}
    assert tool["ok"] is False and tool["error_kind"] == "timeout"
    skip = _fetch_one("SELECT requested_at, owner_id FROM round_skips")
    assert skip["requested_at"] == 1700000000000 and skip["owner_id"] == "u-admin"
    meta = _fetch_one("SELECT profile, dimensions, model FROM vector_index_meta")
    assert meta["dimensions"] == 512
    assert _count("sessions") == 0
    for table in ("model_calls", "tool_calls"):
        sequence = _fetch_one(
            "SELECT pg_get_serial_sequence(%s, 'id') AS seq", (table,)
        )["seq"]
        maximum = _fetch_one(f'SELECT MAX(id) AS n FROM "{table}"')["n"]
        with open_direct() as connection:
            next_value = connection.execute(
                "SELECT nextval(%s) AS value", (str(sequence),)
            ).fetchone()["value"]
        assert int(next_value) > int(maximum)


def test_rerun_needs_truncate_and_is_idempotent(tmp_path: Path) -> None:
    source = _make_fixture(tmp_path, version=15)
    first = migrate(
        source, load_environment().direct_url, truncate=True, embedder=FakeEmbedder(512)
    )
    with pytest.raises(EtlError, match="目标表非空"):
        migrate(source, load_environment().direct_url, embedder=FakeEmbedder(512))
    second = migrate(
        source, load_environment().direct_url, truncate=True, embedder=FakeEmbedder(512)
    )
    assert second.counts == first.counts
    assert _count("documents") == 2
    assert _count("sessions") == 0


def test_duplicate_material_sha_aborts_before_writing(tmp_path: Path) -> None:
    source = _make_fixture(tmp_path, version=15, duplicate_sha=True)
    with pytest.raises(EtlError, match="重复 \\(project_id, sha256\\)"):
        migrate(source, load_environment().direct_url, truncate=True)
    assert _count("documents") == 0


def test_unknown_source_table_aborts_before_writing(tmp_path: Path) -> None:
    source = _make_fixture(tmp_path, version=15, unknown_table=True)
    with pytest.raises(EtlError, match="未知表"):
        migrate(source, load_environment().direct_url, truncate=True)
    assert _count("documents") == 0


def test_unknown_source_column_aborts_before_writing(tmp_path: Path) -> None:
    source = _make_fixture(tmp_path, version=15)
    with sqlite3.connect(source) as connection:
        connection.execute("ALTER TABLE documents ADD COLUMN surprise TEXT")
        connection.commit()
    with pytest.raises(EtlError, match="不认识的源列"):
        migrate(source, load_environment().direct_url, truncate=True)
    assert _count("documents") == 0


def test_unsupported_user_version_aborts(tmp_path: Path) -> None:
    source = _make_fixture(tmp_path, version=17)
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA user_version = 18")
        connection.commit()
    with pytest.raises(EtlError, match="user_version 15/17"):
        migrate(source, load_environment().direct_url, truncate=True)
    assert _count("documents") == 0


@pytest.mark.parametrize(
    ("statement", "params", "match"),
    [
        (
            "UPDATE documents SET title = ? WHERE project_id = 'p-1'",
            ("bad\x00name",),
            "NUL",
        ),
        (
            "UPDATE model_calls SET usage_json = 'NaN' WHERE id = 1",
            (),
            "JSON",
        ),
        (
            "UPDATE model_calls SET usage_json = '' WHERE id = 1",
            (),
            "JSONB",
        ),
        (
            "UPDATE users SET approved_at = '' WHERE user_id = 'u-admin'",
            (),
            "approved_at",
        ),
        (
            "UPDATE materials SET warnings = '{\"x\": 1}' WHERE material_id = 'm-1'",
            (),
            "warnings",
        ),
    ],
)
def test_bad_values_abort_with_context(
    tmp_path: Path, statement: str, params: tuple, match: str
) -> None:
    source = _make_fixture(tmp_path, version=15)
    with sqlite3.connect(source) as connection:
        connection.execute(statement, params)
        connection.commit()
    with pytest.raises(EtlError, match=match):
        migrate(source, load_environment().direct_url, truncate=True)
    assert _count("documents") == 0


async def test_cutover_schema_only_etl_finalize_then_app_login(tmp_path: Path) -> None:
    database = load_environment()
    source = _make_fixture(tmp_path, version=17)
    migrate(source, database.direct_url, truncate=True, embedder=FakeEmbedder(512))
    finalize_database(
        database.direct_url,
        OpsSettings(
            direct_database_url=database.direct_url,
            first_admin_username="fallback-admin",
            first_admin_password="fallback-pass",
        ),
    )
    assert _count("sessions") == 0
    assert _fetch_one(
        "SELECT owner_id FROM discussions WHERE discussion_id = 'd-null'"
    )["owner_id"] == "u-admin"
    profile = _fetch_one("SELECT dimensions FROM vector_index_meta")
    assert profile["dimensions"] == 512

    settings = AppSettings(
        database_url=database.app_url,
        model="openai/test-model",
        api_key="test-key",
        api_base=None,
    )
    app = build_container(settings).app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://cutover"
    ) as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": "cutover-admin", "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        projects = await client.get("/api/projects")
        assert projects.status_code == 200, projects.text
        titles = {item["title"] for item in projects.json()}
        assert "退货_流程 100%" in titles
        document = await client.get("/api/projects/p-1/document")
        assert document.status_code == 200, document.text
        assert document.json()["title"] == "退货_流程 100%"
    assert _count("sessions") == 1


def test_etl_result_survives_real_pg_dump_and_restore(tmp_path: Path) -> None:
    env = _pg18_environment()
    database = load_environment()
    source = _make_fixture(tmp_path, version=15)
    migrate(source, database.direct_url, truncate=True, embedder=FakeEmbedder(512))

    base = _without_query(database.direct_url)
    parts = urlsplit(base)
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    scratch = f"pmstudio_etl_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    try:
        with psycopg_connect(admin_url, autocommit=True) as connection:
            if not connection.execute(
                "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user"
            ).fetchone()[0]:
                pytest.skip("测试账号没有 CREATEDB，跑不了备份恢复用例")
            connection.execute(f'CREATE DATABASE "{scratch}"')
        scratch_base = _replace_database(base, scratch)
        with psycopg_connect(scratch_base, autocommit=True) as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")

        expected = _all_counts()
        dump = tmp_path / "cutover.dump"
        subprocess.run(
            [
                shutil.which("pg_dump", path=env["PATH"]),
                "--format=custom",
                "--schema",
                database.schema,
                "--no-owner",
                "--file",
                str(dump),
                base,
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                shutil.which("pg_restore", path=env["PATH"]),
                "--exit-on-error",
                "--no-owner",
                "--dbname",
                scratch_base,
                str(dump),
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        with connect(schema_url(scratch_base, database.schema)) as connection:
            restored = {
                table: int(
                    connection.execute(
                        f'SELECT COUNT(*) AS n FROM "{table}"'
                    ).fetchone()["n"]
                )
                for table in expected
            }
        assert restored == expected
    finally:
        with psycopg_connect(admin_url, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')


@pytest.mark.skipif(not REAL_V15.is_file(), reason="本机没有真实 v15 旧库副本")
def test_real_v15_library_copy_has_the_expected_counts(tmp_path: Path) -> None:
    """本机真跑证据：9.3MB / user_version=15 的实际数据库（CI 无此文件则 skip）。"""

    copy = tmp_path / "pmstudio-v15.db"
    with sqlite3.connect(REAL_V15) as source, sqlite3.connect(copy) as target:
        source.backup(target)
    report = migrate(copy, load_environment().direct_url, truncate=True,
                     embedder=FakeEmbedder(512))
    assert report.user_version == 15
    assert report.counts == {
        "documents": 7,
        "document_versions": 17,
        "users": 2,
        "templates": 2,
        "evidence_ledgers": 7,
        "materials": 0,
        "material_chunks": 0,
        "rounds": 21,
        "round_skips": 0,
        "explorations": 1,
        "discussions": 0,
        "project_memory": 0,
        "model_calls": 183,
        "tool_calls": 0,
        "observations": 46,
        "round_digests": 0,
        "inspirations": 0,
        "vector_index_meta": 0,
    }
    assert report.skipped_sessions == 7030
    assert report.embedded_chunks == 0
    _all_checks(report)
    assert _fetch_one("SELECT COUNT(*) AS n FROM model_calls WHERE reply_text = ''")["n"] == 15
    assert _count("sessions") == 0

    database = load_environment()
    finalize_database(
        database.direct_url,
        OpsSettings(
            direct_database_url=database.direct_url,
            first_admin_username="unused",
            first_admin_password="unused",
        ),
    )
    assert _fetch_one("SELECT dimensions FROM vector_index_meta")["dimensions"] == 512


def _fetch_all(sql: str) -> list[dict]:
    with open_direct() as connection:
        return [dict(row) for row in connection.execute(sql).fetchall()]


def _all_counts() -> dict[str, int]:
    tables = (
        "documents", "document_versions", "rounds", "round_skips", "templates",
        "evidence_ledgers", "materials", "material_chunks", "explorations",
        "discussions", "project_memory", "model_calls", "tool_calls", "observations",
        "round_digests", "users", "sessions", "inspirations", "vector_index_meta",
    )
    return {table: _count(table) for table in tables}


def _without_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _replace_database(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))


def _pg18_environment() -> dict[str, str]:
    env = dict(os.environ)
    override = env.get("PMSTUDIO_TEST_PG_BIN", "").strip()
    if override:
        env["PATH"] = override + os.pathsep + env.get("PATH", "")
    for name in ("pg_dump", "pg_restore"):
        path = shutil.which(name, path=env["PATH"])
        if path is None:
            pytest.skip(f"缺 {name}；用 PMSTUDIO_TEST_PG_BIN 指向 PG18 客户端")
        output = subprocess.run(
            [path, "--version"], capture_output=True, text=True, check=False
        ).stdout
        if not output.startswith(f"{name} (PostgreSQL) 18."):
            pytest.skip(f"{name} 不是 18：{output.strip()}")
    return env


def test_etl_script_is_not_copied_into_the_backend_image() -> None:
    text = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")
    logical: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        buffer += (" " if buffer else "") + line
        if buffer.endswith("\\"):
            buffer = buffer[:-1].rstrip()
            continue
        logical.append(buffer)
        buffer = ""
    copies = [line for line in logical if line.upper().startswith("COPY")]
    assert copies
    assert all("migrate_sqlite_to_pg.py" not in line for line in copies)
    assert any("scripts/dev.py" in line for line in copies)
    assert any("scripts/init_db.py" in line for line in copies)


def test_cli_prints_report_on_empty_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "empty-v15.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(_schema(15))
        connection.execute("PRAGMA user_version = 15")
        connection.commit()
    code = main(
        [
            "--sqlite",
            str(path),
            "--database-url",
            load_environment().direct_url,
            "--truncate",
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "documents: 0" in output
    assert "校验：" in output
