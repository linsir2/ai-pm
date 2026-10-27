"""真 PostgreSQL：0001 schema、原生类型、seq、FTS/vector 索引与迁移账本。

每个模块用一个**独立空 schema** 跑迁移：表集/列集/类型必须精确，不能夹带别的
用例留下的表；共享的 `pmstudio_test` 只用来建/删这个 schema。
"""

from __future__ import annotations

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest
from psycopg.errors import CheckViolation, UniqueViolation
from scripts.migrate import apply_migrations, discover_migrations

from pmstudio.infrastructure.postgres.database import connect
from tests.support.postgres import (
    DATA_TABLES,
    PRESERVED_TABLES,
    PgTestDatabase,
    load_environment,
    prepare,
    schema_url,
)

TEXT = ("text", "text")
TIMESTAMPTZ = ("timestamp with time zone", "timestamptz")
JSONB = ("jsonb", "jsonb")
TEXT_ARRAY = ("ARRAY", "_text")
BOOLEAN = ("boolean", "bool")
BYTEA = ("bytea", "bytea")
BIGINT = ("bigint", "int8")
INTEGER = ("integer", "int4")
SMALLINT = ("smallint", "int2")
VECTOR = ("USER-DEFINED", "vector")
TSVECTOR = ("tsvector", "tsvector")

EXPECTED_COLUMNS: dict[str, dict[str, tuple[str, str]]] = {
    "documents": {
        "project_id": TEXT,
        "owner_id": TEXT,
        "document_id": TEXT,
        "title": TEXT,
        "template": JSONB,
        "blocks": JSONB,
        "preference_entries": JSONB,
        "tombstones": JSONB,
        "created_at": TIMESTAMPTZ,
        "updated_at": TIMESTAMPTZ,
    },
    "document_versions": {
        "project_id": TEXT,
        "owner_id": TEXT,
        "version_no": INTEGER,
        "trigger": TEXT,
        "summary": TEXT,
        "changed_block_ids": TEXT_ARRAY,
        "snapshot": JSONB,
        "created_at": TIMESTAMPTZ,
    },
    "rounds": {
        "round_id": TEXT,
        "project_id": TEXT,
        "owner_id": TEXT,
        "step": TEXT,
        "waiting_on": TEXT,
        "payload": JSONB,
        "created_at": TIMESTAMPTZ,
        "updated_at": TIMESTAMPTZ,
        "lease_owner": TEXT,
        "lease_expires_at": BIGINT,
        "lease_token": INTEGER,
        "seq": BIGINT,
    },
    "round_skips": {
        "round_id": TEXT,
        "owner_id": TEXT,
        "seat_id": TEXT,
        "pass_index": INTEGER,
        "reason": TEXT,
        "source": TEXT,
        "requested_at": BIGINT,
        "seq": BIGINT,
    },
    "templates": {
        "template_id": TEXT,
        "owner_id": TEXT,
        "title": TEXT,
        "spec": JSONB,
        "builtin": BOOLEAN,
        "created_at": TIMESTAMPTZ,
        "updated_at": TIMESTAMPTZ,
    },
    "evidence_ledgers": {
        "project_id": TEXT,
        "owner_id": TEXT,
        "payload": JSONB,
        "updated_at": TIMESTAMPTZ,
    },
    "materials": {
        "material_id": TEXT,
        "project_id": TEXT,
        "owner_id": TEXT,
        "origin": TEXT,
        "title": TEXT,
        "source_ref": TEXT,
        "media_type": TEXT,
        "bytes_len": INTEGER,
        "sha256": TEXT,
        "parser_id": TEXT,
        "parser_version": TEXT,
        "schema_version": INTEGER,
        "truncated": BOOLEAN,
        "warnings": TEXT_ARRAY,
        "content": BYTEA,
        "created_at": TIMESTAMPTZ,
    },
    "material_chunks": {
        "chunk_id": TEXT,
        "material_id": TEXT,
        "owner_id": TEXT,
        "ordinal": INTEGER,
        "text": TEXT,
        "heading_path": TEXT_ARRAY,
        "anchor": TEXT,
        "tokens": TEXT,
        "search_tsv": TSVECTOR,
        "embedding": VECTOR,
    },
    "explorations": {
        "exploration_id": TEXT,
        "project_id": TEXT,
        "owner_id": TEXT,
        "kind": TEXT,
        "status": TEXT,
        "payload": JSONB,
        "created_at": TIMESTAMPTZ,
        "updated_at": TIMESTAMPTZ,
        "lease_owner": TEXT,
        "lease_expires_at": BIGINT,
        "lease_token": INTEGER,
        "seq": BIGINT,
    },
    "discussions": {
        "discussion_id": TEXT,
        "project_id": TEXT,
        "owner_id": TEXT,
        "status": TEXT,
        "payload": JSONB,
        "created_at": TIMESTAMPTZ,
        "updated_at": TIMESTAMPTZ,
        "seq": BIGINT,
    },
    "project_memory": {
        "project_id": TEXT,
        "owner_id": TEXT,
        "payload": JSONB,
        "updated_at": TIMESTAMPTZ,
    },
    "model_calls": {
        "id": BIGINT,
        "round_id": TEXT,
        "purpose": TEXT,
        "model": TEXT,
        "prompt_sha": TEXT,
        "prompt_text": TEXT,
        "reply_text": TEXT,
        "usage_json": JSONB,
        "duration_ms": INTEGER,
        "created_at": TIMESTAMPTZ,
        "attempt": INTEGER,
        "ok": BOOLEAN,
        "error": TEXT,
    },
    "tool_calls": {
        "id": BIGINT,
        "round_id": TEXT,
        "tool_id": TEXT,
        "arguments": JSONB,
        "result_note": TEXT,
        "duration_ms": INTEGER,
        "created_at": TIMESTAMPTZ,
        "ok": BOOLEAN,
        "error": TEXT,
        "error_kind": TEXT,
    },
    "observations": {
        "id": TEXT,
        "project_id": TEXT,
        "owner_id": TEXT,
        "round_id": TEXT,
        "obs_type": TEXT,
        "title": TEXT,
        "detail": TEXT,
        "source_ref": TEXT,
        "created_at": TEXT,
        "seq": BIGINT,
    },
    "round_digests": {
        "id": TEXT,
        "project_id": TEXT,
        "owner_id": TEXT,
        "round_id": TEXT,
        "summary": TEXT,
        "sections": TEXT_ARRAY,
        "features": TEXT_ARRAY,
        "outcomes": TEXT_ARRAY,
        "created_at": TEXT,
        "seq": BIGINT,
    },
    "users": {
        "user_id": TEXT,
        "username": TEXT,
        "password_hash": TEXT,
        "status": TEXT,
        "role": TEXT,
        "approved_by": TEXT,
        "approved_at": TIMESTAMPTZ,
        "created_at": TIMESTAMPTZ,
        "seq": BIGINT,
    },
    "sessions": {
        "session_id": TEXT,
        "user_id": TEXT,
        "token_hash": TEXT,
        "expires_at": TIMESTAMPTZ,
        "created_at": TIMESTAMPTZ,
        "last_seen_at": TIMESTAMPTZ,
        "ip": TEXT,
        "user_agent": TEXT,
        "revoked_at": TIMESTAMPTZ,
    },
    "inspirations": {
        "id": TEXT,
        "project_id": TEXT,
        "owner_id": TEXT,
        "origin": TEXT,
        "statement": TEXT,
        "created_at": TEXT,
        "seq": BIGINT,
    },
    "vector_index_meta": {
        "id": SMALLINT,
        "profile": TEXT,
        "dimensions": INTEGER,
        "model": TEXT,
    },
    "schema_migrations": {
        "version": INTEGER,
        "applied_at": TIMESTAMPTZ,
    },
}

NULLABLE_COLUMNS = frozenset(
    {
        ("documents", "owner_id"),
        ("document_versions", "owner_id"),
        ("rounds", "owner_id"),
        ("rounds", "lease_owner"),
        ("rounds", "lease_expires_at"),
        ("templates", "owner_id"),
        ("evidence_ledgers", "owner_id"),
        ("materials", "owner_id"),
        ("material_chunks", "owner_id"),
        ("material_chunks", "embedding"),
        ("explorations", "owner_id"),
        ("explorations", "lease_owner"),
        ("explorations", "lease_expires_at"),
        ("discussions", "owner_id"),
        ("project_memory", "owner_id"),
        ("model_calls", "round_id"),
        ("model_calls", "error"),
        ("tool_calls", "round_id"),
        ("tool_calls", "error"),
        ("tool_calls", "error_kind"),
        ("observations", "owner_id"),
        ("round_digests", "owner_id"),
        ("users", "approved_by"),
        ("users", "approved_at"),
        ("sessions", "revoked_at"),
        ("inspirations", "owner_id"),
    }
)

IDENTITY_COLUMNS = frozenset(
    {
        ("model_calls", "id"),
        ("tool_calls", "id"),
        ("users", "seq"),
        ("rounds", "seq"),
        ("round_skips", "seq"),
        ("explorations", "seq"),
        ("discussions", "seq"),
        ("observations", "seq"),
        ("round_digests", "seq"),
        ("inspirations", "seq"),
    }
)

EXPECTED_INDEXES = frozenset(
    {
        "idx_rounds_project",
        "idx_round_skips_owner",
        "idx_materials_dedupe",
        "idx_materials_project",
        "idx_material_chunks_material",
        "idx_explorations_project",
        "idx_explorations_running",
        "idx_discussions_project",
        "idx_model_calls_round",
        "idx_tool_calls_round",
        "idx_observations_project",
        "idx_observations_round",
        "idx_round_digests_project",
        "idx_sessions_user",
        "idx_inspirations_project",
        "idx_documents_owner",
        "idx_document_versions_owner",
        "idx_rounds_owner",
        "idx_evidence_ledgers_owner",
        "idx_materials_owner",
        "idx_material_chunks_owner",
        "idx_explorations_owner",
        "idx_discussions_owner",
        "idx_project_memory_owner",
        "idx_observations_owner",
        "idx_round_digests_owner",
        "idx_inspirations_owner",
        "idx_templates_owner",
        "idx_material_chunks_search_tsv",
        "idx_material_chunks_embedding",
    }
)

EXPECTED_FOREIGN_KEYS = {
    "document_versions_project_id_fkey": (
        "FOREIGN KEY (project_id) REFERENCES documents(project_id) ON DELETE CASCADE"
    ),
    "evidence_ledgers_project_id_fkey": (
        "FOREIGN KEY (project_id) REFERENCES documents(project_id) ON DELETE CASCADE"
    ),
    "material_chunks_material_id_fkey": (
        "FOREIGN KEY (material_id) REFERENCES materials(material_id) ON DELETE CASCADE"
    ),
    "sessions_user_id_fkey": (
        "FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"
    ),
}


@dataclass(frozen=True, slots=True)
class MigratedSchema:
    name: str
    url: str


def _create_schema(database: PgTestDatabase) -> MigratedSchema:
    schema = f"pmstudio_schema_{uuid.uuid4().hex[:12]}"
    with connect(database.direct_url) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
    return MigratedSchema(name=schema, url=schema_url(database.direct_url, schema))


def _drop_schema(database: PgTestDatabase, schema: MigratedSchema) -> None:
    with connect(database.direct_url) as connection:
        connection.execute(f'DROP SCHEMA IF EXISTS "{schema.name}" CASCADE')


@pytest.fixture(scope="module")
def database() -> PgTestDatabase:
    pg = load_environment()
    prepare(pg)
    return pg


@pytest.fixture(scope="module")
def migrated(database: PgTestDatabase):
    schema = _create_schema(database)
    try:
        assert apply_migrations(schema.url) == [1]
        yield schema
    finally:
        _drop_schema(database, schema)


def _rows(migrated: MigratedSchema, query: str, params: tuple = ()) -> list[dict]:
    with connect(migrated.url) as connection:
        return connection.execute(query, params).fetchall()


def _columns(migrated: MigratedSchema) -> dict[tuple[str, str], dict]:
    rows = _rows(
        migrated,
        """
        SELECT table_name, column_name, data_type, udt_name, is_nullable,
               is_identity, identity_generation, is_generated, generation_expression
          FROM information_schema.columns
         WHERE table_schema = %s
        """,
        (migrated.name,),
    )
    return {(str(row["table_name"]), str(row["column_name"])): row for row in rows}


def test_the_migration_creates_exactly_the_expected_tables(migrated: MigratedSchema) -> None:
    rows = _rows(
        migrated,
        """
        SELECT table_name
          FROM information_schema.tables
         WHERE table_schema = %s AND table_type = 'BASE TABLE'
        """,
        (migrated.name,),
    )
    assert {str(row["table_name"]) for row in rows} == set(EXPECTED_COLUMNS)
    assert len(EXPECTED_COLUMNS) == 20


def test_every_column_has_the_exact_native_type(migrated: MigratedSchema) -> None:
    columns = _columns(migrated)
    actual = {
        (table, column): (row["data_type"], row["udt_name"])
        for (table, column), row in columns.items()
    }
    expected = {
        (table, column): kind
        for table, definition in EXPECTED_COLUMNS.items()
        for column, kind in definition.items()
    }
    assert actual == expected


def test_nullability_is_explicit(migrated: MigratedSchema) -> None:
    columns = _columns(migrated)
    nullable = {
        key for key, row in columns.items() if row["is_nullable"] == "YES"
    }
    assert nullable == NULLABLE_COLUMNS


def test_identity_columns_are_generated_by_default(migrated: MigratedSchema) -> None:
    columns = _columns(migrated)
    identities = {
        key for key, row in columns.items() if row["is_identity"] == "YES"
    }
    assert identities == IDENTITY_COLUMNS
    for key in IDENTITY_COLUMNS:
        assert columns[key]["identity_generation"] == "BY DEFAULT", key


def test_search_tsv_is_a_generated_tsvector(migrated: MigratedSchema) -> None:
    row = _columns(migrated)[("material_chunks", "search_tsv")]
    assert row["is_generated"] == "ALWAYS"
    assert "to_tsvector" in str(row["generation_expression"])
    assert "tokens" in str(row["generation_expression"])
    assert row["is_nullable"] == "NO"


def test_expected_indexes_are_created(migrated: MigratedSchema) -> None:
    rows = _rows(
        migrated,
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s",
        (migrated.name,),
    )
    indexes = {str(row["indexname"]): str(row["indexdef"]) for row in rows}
    assert EXPECTED_INDEXES <= set(indexes)

    running = indexes["idx_explorations_running"]
    assert "CREATE UNIQUE INDEX" in running
    assert "WHERE (status = 'running'::text)" in running

    dedupe = indexes["idx_materials_dedupe"]
    assert "CREATE UNIQUE INDEX" in dedupe
    assert "(project_id, sha256)" in dedupe

    fts = indexes["idx_material_chunks_search_tsv"]
    assert "USING gin (search_tsv)" in fts

    vector = indexes["idx_material_chunks_embedding"]
    assert "USING hnsw (embedding vector_cosine_ops)" in vector


def test_constraints_cover_primary_keys_foreign_keys_and_single_row_meta(
    migrated: MigratedSchema,
) -> None:
    rows = _rows(
        migrated,
        """
        SELECT c.conname, c.contype, pg_get_constraintdef(c.oid) AS definition
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = %s
        """,
        (migrated.name,),
    )
    constraints = {str(row["conname"]): row for row in rows}
    primary_keys = [row for row in rows if row["contype"] == "p"]
    assert len(primary_keys) == 20

    for name, definition in EXPECTED_FOREIGN_KEYS.items():
        assert constraints[name]["contype"] == "f"
        assert definition in str(constraints[name]["definition"])

    definitions = {name: str(row["definition"]) for name, row in constraints.items()}
    assert "PRIMARY KEY (project_id, version_no)" in definitions[
        "document_versions_pkey"
    ]
    assert "PRIMARY KEY (round_id, seat_id)" in definitions["round_skips_pkey"]
    assert "UNIQUE (username)" in definitions["users_username_key"]
    assert "CHECK ((id = 1))" in definitions["vector_index_meta_id_check"]


def test_running_exploration_partial_unique_is_enforced(migrated: MigratedSchema) -> None:
    with connect(migrated.url) as connection:
        with pytest.raises(UniqueViolation):
            with connection.transaction():
                for exploration_id in ("x-1", "x-2"):
                    connection.execute(
                        """
                        INSERT INTO explorations
                            (exploration_id, project_id, kind, status, payload,
                             created_at, updated_at)
                        VALUES (%s, 'p-1', 'inquiry', 'running', '{}', now(), now())
                        """,
                        (exploration_id,),
                    )
        remaining = connection.execute("SELECT COUNT(*) AS n FROM explorations").fetchone()
        assert remaining is not None and remaining["n"] == 0


def test_vector_index_meta_is_single_row(migrated: MigratedSchema) -> None:
    with connect(migrated.url) as connection:
        with pytest.raises(CheckViolation):
            connection.execute(
                "INSERT INTO vector_index_meta (id, profile, dimensions, model)"
                " VALUES (2, 'p', 512, 'm')"
            )
        remaining = connection.execute(
            "SELECT COUNT(*) AS n FROM vector_index_meta"
        ).fetchone()
        assert remaining is not None and remaining["n"] == 0


def test_tokens_feed_search_tsv_and_embedding_has_512_dimensions(
    migrated: MigratedSchema,
) -> None:
    with connect(migrated.url) as connection:
        connection.execute(
            """
            INSERT INTO materials
                (material_id, project_id, origin, title, source_ref, media_type,
                 bytes_len, sha256, parser_id, parser_version, schema_version,
                 truncated, warnings, content, created_at)
            VALUES ('m-1', 'p-1', 'upload', '标题', 'ref', 'text/plain',
                    1, 'sha-1', 'parser', '1', 1, false, '{}', %s, now())
            """,
            (b"x",),
        )
        connection.execute(
            """
            INSERT INTO material_chunks
                (chunk_id, material_id, ordinal, text, heading_path, anchor, tokens)
            VALUES ('m-1#c01', 'm-1', 0, '正文', '{}', 'anchor', '产品 设计')
            """
        )
        row = connection.execute(
            "SELECT search_tsv::text AS tsv, embedding,"
            " search_tsv @@ to_tsquery('simple', '产品') AS matched"
            " FROM material_chunks WHERE chunk_id = 'm-1#c01'"
        ).fetchone()
        assert row is not None
        assert row["tsv"] == "'产品':1 '设计':2"
        assert row["matched"] is True
        assert row["embedding"] is None

        connection.execute(
            "UPDATE material_chunks SET embedding ="
            " array_fill(0.0::real, ARRAY[512])::vector WHERE chunk_id = 'm-1#c01'"
        )
        row = connection.execute(
            "SELECT vector_dims(embedding) AS dims FROM material_chunks"
            " WHERE chunk_id = 'm-1#c01'"
        ).fetchone()
        assert row is not None and row["dims"] == 512


def test_migrations_are_idempotent(migrated: MigratedSchema) -> None:
    before = _rows(
        migrated, "SELECT version, applied_at FROM schema_migrations ORDER BY version"
    )
    assert [row["version"] for row in before] == [1]
    assert apply_migrations(migrated.url) == []
    after = _rows(
        migrated, "SELECT version, applied_at FROM schema_migrations ORDER BY version"
    )
    assert after == before


def test_two_runners_serialize_on_the_advisory_lock(database: PgTestDatabase) -> None:
    schema = _create_schema(database)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: apply_migrations(schema.url), range(2)))
        assert sorted(results) == [[], [1]]
        with connect(schema.url) as connection:
            rows = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        assert [row["version"] for row in rows] == [1]
    finally:
        _drop_schema(database, schema)


def test_duplicate_migration_numbers_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "0001_first.sql").write_text("SELECT 1", encoding="utf-8")
    (tmp_path / "0001_second.sql").write_text("SELECT 1", encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        discover_migrations(tmp_path)


def test_0001_has_no_sqlite_only_syntax() -> None:
    sql = (
        Path(__file__).resolve().parents[2] / "migrations" / "0001_init.sql"
    ).read_text(encoding="utf-8")
    upper = sql.upper()
    for forbidden in (
        "FTS5",
        "VEC0",
        "ROWID",
        "AUTOINCREMENT",
        "INSERT OR",
        "PRAGMA",
        "SQLITE_",
    ):
        assert forbidden not in upper
    first_statement = next(
        part.strip() for part in re.split(r";", sql) if part.strip()
    )
    assert first_statement.upper().startswith("CREATE EXTENSION IF NOT EXISTS VECTOR")


def test_fixture_truncates_only_the_18_data_tables() -> None:
    assert len(DATA_TABLES) == 18
    assert PRESERVED_TABLES.isdisjoint(DATA_TABLES)
