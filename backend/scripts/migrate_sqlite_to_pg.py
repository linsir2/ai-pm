"""一次性把旧 SQLite 库导入 PostgreSQL（只读源库，不双写、不回写）。

执行顺序按 runbook：`init_db --bootstrap` → `init_db --schema-only` → 本脚本 →
`init_db --finalize`。本脚本**只做迁移**，不建表、不 seed、不跑 finalize。

边界（缺一不可）：

- 源库以只读 URI 打开；不改 SQLite 一个字节。
- 表从 `sqlite_master` 枚举；只跳过 `sessions`、`sqlite_%`、FTS/vec 影子表；
  未知表**报错**，不静默丢。
- 目标列按 `information_schema` 的**实际类型 + nullable** 转换，而不是按列名猜。
- 0001 已建唯一 running 索引：导入前把同一项目里除最新外的 `running` 探索改成
  `abandoned`（按 `created_at DESC, rowid DESC`），**全部行照插**。
- 重复 `(project_id, sha256)` 在写库前报错中止；不静默去重。
- `sessions` 不迁；旧 cookie 在切流后作废。`model_calls` 原样迁，`tool_calls` /
  `round_skips` / `vector_index_meta` 有则迁（v15 没有，v17 有）。
- 失败后目标表可用 `--truncate` 清空重跑；默认遇到非空目标直接报错，不合并。
- `tokens` 由 `index_source(title, heading_path, text)` 回填；向量按当前 profile 重嵌入
  （旧向量不是真相源）。旧 profile 与当前不一致时不清空重算，留给 finalize/维护任务。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC = BACKEND_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pgvector import Vector  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from pmstudio.bootstrap.settings import ConfigError, OpsSettings  # noqa: E402
from pmstudio.domain.discussion.thread import ThreadStatus  # noqa: E402
from pmstudio.domain.exploration.exploration import (  # noqa: E402
    ExplorationKind,
    ExplorationStatus,
)
from pmstudio.domain.round.step import Step, WaitingOn  # noqa: E402
from pmstudio.domain.shared.ids import (  # noqa: E402
    DiscussionId,
    ExplorationId,
    ProjectId,
    RoundId,
)
from pmstudio.infrastructure.postgres import serialization as ser  # noqa: E402
from pmstudio.infrastructure.postgres.database import connect  # noqa: E402
from pmstudio.infrastructure.postgres.embedding import (  # noqa: E402
    DIMENSIONS,
    EMBED_PROFILE,
    MODEL_NAME,
    FastEmbedEmbedder,
)
from pmstudio.infrastructure.postgres.tokenize import index_source, index_text  # noqa: E402
from pmstudio.infrastructure.postgres.vector_index import (  # noqa: E402
    blocks_within_budget,
    vector_char_budget,
)

#: 目标 schema 里的 19 张普通表（含 `vector_index_meta`）。`schema_migrations` 不碰。
TARGET_TABLES: tuple[str, ...] = (
    "documents",
    "document_versions",
    "rounds",
    "round_skips",
    "templates",
    "evidence_ledgers",
    "materials",
    "material_chunks",
    "explorations",
    "discussions",
    "project_memory",
    "model_calls",
    "tool_calls",
    "observations",
    "round_digests",
    "users",
    "sessions",
    "inspirations",
    "vector_index_meta",
)

#: 旧库必须存在的表。后三张（round_skips/tool_calls/vector_index_meta）按
#: `sqlite_master` feature-detect；`sessions` 存在但不迁。
REQUIRED_SOURCE_TABLES: frozenset[str] = frozenset(
    {
        "documents",
        "document_versions",
        "rounds",
        "templates",
        "evidence_ledgers",
        "materials",
        "material_chunks",
        "explorations",
        "discussions",
        "project_memory",
        "model_calls",
        "observations",
        "round_digests",
        "users",
        "inspirations",
    }
)

SKIPPED_TABLES: frozenset[str] = frozenset({"sessions"})
SKIPPED_PREFIXES: tuple[str, ...] = (
    "sqlite_",
    "material_chunks_fts",
    "material_chunks_vec",
)

#: 保留 `''` 哨兵的 TEXT 列（设计 §3 的明确例外）。
SENTINEL_TEXT: frozenset[tuple[str, str]] = frozenset(
    {
        ("observations", "created_at"),
        ("round_digests", "created_at"),
        ("inspirations", "created_at"),
    }
)

#: 主键列（用于重复检测/哈希/错误定位）。与 0001 一一对应。
PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "documents": ("project_id",),
    "document_versions": ("project_id", "version_no"),
    "rounds": ("round_id",),
    "round_skips": ("round_id", "seat_id"),
    "templates": ("template_id",),
    "evidence_ledgers": ("project_id",),
    "materials": ("material_id",),
    "material_chunks": ("chunk_id",),
    "explorations": ("exploration_id",),
    "discussions": ("discussion_id",),
    "project_memory": ("project_id",),
    "model_calls": ("id",),
    "tool_calls": ("id",),
    "observations": ("id",),
    "round_digests": ("id",),
    "users": ("user_id",),
    "inspirations": ("id",),
    "vector_index_meta": ("id",),
}

#: 目标 identity 列：`seq` 从旧 rowid 回填，`id` 从旧自增主键回填。
SEQ_COLUMNS: dict[str, str] = {
    "rounds": "seq",
    "round_skips": "seq",
    "explorations": "seq",
    "discussions": "seq",
    "observations": "seq",
    "round_digests": "seq",
    "users": "seq",
    "inspirations": "seq",
}
ID_COLUMNS: dict[str, str] = {
    "model_calls": "id",
    "tool_calls": "id",
}

#: 先父后子；`vector_index_meta` 最后（不依赖别人）。
INSERT_ORDER: tuple[str, ...] = (
    "documents",
    "document_versions",
    "users",
    "templates",
    "evidence_ledgers",
    "materials",
    "material_chunks",
    "rounds",
    "round_skips",
    "explorations",
    "discussions",
    "project_memory",
    "model_calls",
    "tool_calls",
    "observations",
    "round_digests",
    "inspirations",
    "vector_index_meta",
)


class EtlError(RuntimeError):
    """迁移失败；消息面向运维，带表/主键/列。"""


@dataclass(frozen=True, slots=True)
class TargetColumn:
    name: str
    data_type: str
    udt_name: str
    nullable: bool
    identity: bool
    generated: bool
    default: str | None


@dataclass(frozen=True, slots=True)
class PreparedRow:
    values: dict[str, object]
    compare: dict[str, object]
    pk: tuple[object, ...]


@dataclass(slots=True)
class EtlReport:
    source: Path
    user_version: int
    counts: dict[str, int] = field(default_factory=dict)
    skipped_sessions: int = 0
    deduped_running: list[tuple[str, str]] = field(default_factory=list)
    embedded_chunks: int = 0
    embedding_skipped_reason: str | None = None
    vector_profile: dict[str, object] | None = None
    checks: dict[str, bool] = field(default_factory=dict)


def _quote_sqlite(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _open_source(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise EtlError(f"找不到旧 SQLite 库：{path}")
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _user_version(source: sqlite3.Connection) -> int:
    row = source.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def _source_table_names(source: sqlite3.Connection) -> list[str]:
    names: list[str] = []
    unknown: list[str] = []
    for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        name = str(row["name"])
        if name in SKIPPED_TABLES:
            continue
        if any(name.startswith(prefix) for prefix in SKIPPED_PREFIXES):
            continue
        if name not in TARGET_TABLES:
            unknown.append(name)
            continue
        names.append(name)
    if unknown:
        raise EtlError(
            "旧库有未知表，拒绝猜测：" + "、".join(sorted(unknown))
            + "（只认识目标 19 张表以及显式 skip 的 FTS/vec/sessions）"
        )
    missing = sorted(REQUIRED_SOURCE_TABLES.difference(names))
    if missing:
        raise EtlError(
            "旧库缺少必需表：" + "、".join(missing) + "（只支持 v15 及以上的旧库）"
        )
    return sorted(names)


def _read_source_rows(
    source: sqlite3.Connection, table: str
) -> list[dict[str, object]]:
    """读整表，带 rowid。没有 rowid（WITHOUT ROWID）时按扫描顺序给 1..N。"""

    query = f"SELECT rowid AS __rowid, * FROM {_quote_sqlite(table)}"
    try:
        rows = source.execute(query).fetchall()
    except sqlite3.OperationalError:
        rows = source.execute(f"SELECT * FROM {_quote_sqlite(table)}").fetchall()
        return [{**dict(row), "__rowid": index + 1} for index, row in enumerate(rows)]
    return [dict(row) for row in rows]


def _target_columns(connection, table: str) -> list[TargetColumn]:  # type: ignore[no-untyped-def]
    rows = connection.execute(
        """
        SELECT column_name, data_type, udt_name, is_nullable, is_identity,
               is_generated, column_default
          FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = %s
         ORDER BY ordinal_position
        """,
        (table,),
    ).fetchall()
    if not rows:
        raise EtlError(
            f"目标 schema 缺表 {table}；先跑 init_db.py --schema-only，ETL 不建表"
        )
    return [
        TargetColumn(
            name=str(row["column_name"]),
            data_type=str(row["data_type"]),
            udt_name=str(row["udt_name"]),
            nullable=str(row["is_nullable"]) == "YES",
            identity=str(row["is_identity"]) == "YES",
            generated=str(row["is_generated"]) != "NEVER",
            default=None if row["column_default"] is None else str(row["column_default"]),
        )
        for row in rows
    ]


def _ensure_target(connection) -> None:  # type: ignore[no-untyped-def]
    for table in TARGET_TABLES:
        if connection.execute("SELECT to_regclass(%s) AS t", (table,)).fetchone()["t"] is None:
            raise EtlError(
                f"目标 schema 缺表 {table}；先跑 init_db.py --schema-only，ETL 不建表"
            )


def _target_counts(connection) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {
        table: int(
            connection.execute(f"SELECT COUNT(*) AS n FROM {_quote(table)}").fetchone()["n"]
        )
        for table in TARGET_TABLES
    }


def _truncate_target(connection) -> None:  # type: ignore[no-untyped-def]
    names = ", ".join(_quote(table) for table in TARGET_TABLES)
    connection.execute(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")


def _source_pk(table: str, row: dict[str, object]) -> tuple[object, ...]:
    keys = PRIMARY_KEYS[table]
    missing = [key for key in keys if key not in row]
    if missing:
        raise EtlError(f"{table} 源行缺主键列：{'、'.join(missing)}")
    return tuple(row[key] for key in keys)


def _pk_text(values: tuple[object, ...]) -> str:
    return "/".join(str(value) for value in values)


def _row_label(table: str, row: dict[str, object]) -> str:
    try:
        return f"{table} 行 {_pk_text(_source_pk(table, row))}"
    except EtlError:
        return table


def _reject_constant(value: str) -> object:
    raise ValueError(f"JSON 常量 {value!r} 不是合法 JSON")


def _parse_json_text(text: str, *, table: str, column: str) -> object:
    if "\x00" in text:
        raise EtlError(f"{table}.{column} 含 NUL（PostgreSQL 文本不能存 NUL）")
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as error:
        raise EtlError(f"{table}.{column} 不是合法 JSON：{error}") from error
    _ensure_json_safe(value, table=table, column=column)
    return value


def _ensure_json_safe(value: object, *, table: str, column: str) -> None:
    if isinstance(value, str):
        if "\x00" in value:
            raise EtlError(f"{table}.{column} 的 JSON 字符串含 NUL")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EtlError(f"{table}.{column} 的 JSON 含 NaN/Infinity")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise EtlError(f"{table}.{column} 的 JSON 键不是字符串：{key!r}")
            _ensure_json_safe(key, table=table, column=column)
            _ensure_json_safe(item, table=table, column=column)
        return
    if isinstance(value, list):
        for item in value:
            _ensure_json_safe(item, table=table, column=column)


def _text_value(
    value: object,
    *,
    nullable: bool,
    table: str,
    column: str,
    allow_empty: bool = True,
) -> str | None:
    if value is None:
        if nullable:
            return None
        raise EtlError(f"{table}.{column} 是 NOT NULL，但源值是 NULL")
    if not isinstance(value, str):
        raise EtlError(f"{table}.{column} 期望 TEXT，源值类型是 {type(value).__name__}")
    if "\x00" in value:
        raise EtlError(f"{table}.{column} 含 NUL（PostgreSQL 文本不能存 NUL）")
    if not allow_empty and value == "":
        raise EtlError(f"{table}.{column} 不允许空串")
    return value


def _timestamptz_value(
    value: object,
    *,
    nullable: bool,
    table: str,
    column: str,
) -> datetime | None:
    if value is None:
        if nullable:
            return None
        raise EtlError(f"{table}.{column} 是 NOT NULL，但源值是 NULL")
    if value == "":
        # 空串对 timestamptz 没有语义；nullable 也只允许 SQL NULL（设计 §0.2）。
        raise EtlError(f"{table}.{column} 是空串；TIMESTAMPTZ 只允许 NULL，不猜时间")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        if "\x00" in value:
            raise EtlError(f"{table}.{column} 含 NUL")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise EtlError(f"{table}.{column} 不是 ISO 时间：{value!r}") from error
    else:
        raise EtlError(
            f"{table}.{column} 期望 ISO 时间字符串，源值类型是 {type(value).__name__}"
        )
    if parsed.tzinfo is None:
        # 老库写的是 UTC ISO，但早期无时区的行按 UTC 解释（设计 §5）。
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _bool_value(
    value: object, *, nullable: bool, table: str, column: str
) -> bool | None:
    if value is None:
        if nullable:
            return None
        raise EtlError(f"{table}.{column} 是 NOT NULL，但源值是 NULL")
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in {"0", "1", "false", "true"}:
        return value.lower() in {"1", "true"}
    raise EtlError(f"{table}.{column} 期望 0/1 布尔值，实得 {value!r}")


def _int_value(value: object, *, nullable: bool, table: str, column: str) -> int | None:
    if value is None:
        if nullable:
            return None
        raise EtlError(f"{table}.{column} 是 NOT NULL，但源值是 NULL")
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text and (text.lstrip("+-").isdigit()):
            return int(text)
    raise EtlError(f"{table}.{column} 期望整数，实得 {value!r}")


def _bytes_value(
    value: object, *, nullable: bool, table: str, column: str
) -> bytes | None:
    if value is None:
        if nullable:
            return None
        raise EtlError(f"{table}.{column} 是 NOT NULL，但源值是 NULL")
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    raise EtlError(f"{table}.{column} 期望 BLOB，实得 {type(value).__name__}")


def _text_array_value(
    value: object, *, nullable: bool, table: str, column: str
) -> list[str] | None:
    if value is None:
        if nullable:
            return None
        raise EtlError(f"{table}.{column} 是 NOT NULL，但源值是 NULL")
    if isinstance(value, (list, tuple)):
        items: object = list(value)
    elif isinstance(value, str):
        if value == "":
            return []
        items = _parse_json_text(value, table=table, column=column)
    else:
        raise EtlError(f"{table}.{column} 期望 JSON 数组，实得 {type(value).__name__}")
    if not isinstance(items, list):
        raise EtlError(f"{table}.{column} 期望 JSON 数组，实得 {type(items).__name__}")
    result: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise EtlError(f"{table}.{column} 数组元素不是字符串：{item!r}")
        if "\x00" in item:
            raise EtlError(f"{table}.{column} 数组元素含 NUL")
        result.append(item)
    return result


def _jsonb_value(
    value: object, *, nullable: bool, table: str, column: str
) -> Jsonb | None:
    if value is None:
        if nullable:
            return None
        raise EtlError(f"{table}.{column} 是 NOT NULL，但源值是 NULL")
    if not isinstance(value, str):
        raise EtlError(f"{table}.{column} 期望 JSON 文本，实得 {type(value).__name__}")
    if value == "":
        raise EtlError(f"{table}.{column} 是空串；JSONB 只允许 NULL，不猜空对象")
    return Jsonb(_parse_json_text(value, table=table, column=column))


def _convert_value(
    spec: TargetColumn,
    value: object,
    *,
    table: str,
) -> tuple[object, object]:
    """返回 (写库值, 校验用普通值)。"""

    if spec.data_type == "text":
        text = _text_value(
            value, nullable=spec.nullable, table=table, column=spec.name
        )
        return text, text
    if spec.data_type == "timestamp with time zone":
        stamp = _timestamptz_value(
            value, nullable=spec.nullable, table=table, column=spec.name
        )
        return stamp, stamp
    if spec.data_type == "jsonb":
        parsed = _jsonb_value(
            value, nullable=spec.nullable, table=table, column=spec.name
        )
        plain = None if parsed is None else parsed.obj
        return parsed, plain
    if spec.data_type == "ARRAY" and spec.udt_name == "_text":
        items = _text_array_value(
            value, nullable=spec.nullable, table=table, column=spec.name
        )
        return items, items
    if spec.data_type == "boolean":
        flag = _bool_value(
            value, nullable=spec.nullable, table=table, column=spec.name
        )
        return flag, flag
    if spec.data_type == "bytea":
        content = _bytes_value(
            value, nullable=spec.nullable, table=table, column=spec.name
        )
        return content, content
    if spec.data_type in {"bigint", "integer", "smallint"}:
        number = _int_value(
            value, nullable=spec.nullable, table=table, column=spec.name
        )
        return number, number
    if spec.data_type == "USER-DEFINED" and spec.udt_name == "vector":
        # 向量一律重算，不从旧 vec0 影子表搬。
        return None, None
    raise EtlError(f"{table}.{spec.name} 的目标类型不支持：{spec.data_type}/{spec.udt_name}")


def _dedupe_running(
    project_rows: dict[str, list[dict[str, object]]],
) -> list[tuple[str, str]]:
    """重复 running 探索：每个项目除 `created_at DESC, rowid DESC` 最新一条外改
    `abandoned`。返回被改的行（保留全部行）。"""

    changed: list[tuple[str, str]] = []
    for project_id, group in project_rows.items():
        running = [row for row in group if str(row.get("status")) == "running"]
        if len(running) <= 1:
            continue

        def sort_key(row: dict[str, object]) -> tuple[datetime, int]:
            stamp = _timestamptz_value(
                row.get("created_at"),
                nullable=False,
                table="explorations",
                column="created_at",
            )
            rowid = row.get("__rowid")
            return stamp, int(rowid) if isinstance(rowid, int) else 0

        keep = max(running, key=sort_key)
        for row in running:
            if row is keep:
                continue
            row["status"] = "abandoned"
            changed.append((project_id, str(row.get("exploration_id"))))
    return changed


def _vector_meta_from_source(
    rows: list[dict[str, object]],
) -> dict[str, object] | None:
    if not rows:
        return None
    columns = set(rows[0])
    if {"profile", "dimensions", "model"}.issubset(columns):
        row = rows[0]
        if len(rows) != 1:
            raise EtlError("vector_index_meta 目标形状只允许一行")
        return {
            "id": 1,
            "profile": _text_value(
                row.get("profile"),
                nullable=False,
                table="vector_index_meta",
                column="profile",
            ),
            "dimensions": _int_value(
                row.get("dimensions"),
                nullable=False,
                table="vector_index_meta",
                column="dimensions",
            ),
            "model": _text_value(
                row.get("model"),
                nullable=False,
                table="vector_index_meta",
                column="model",
            ),
        }

    values: dict[str, str] = {}
    for row in rows:
        key = _text_value(
            row.get("key"),
            nullable=False,
            table="vector_index_meta",
            column="key",
        )
        value = _text_value(
            row.get("value"),
            nullable=False,
            table="vector_index_meta",
            column="value",
        )
        assert key is not None and value is not None
        values[key] = value

    profile = values.get("embed_profile") or values.get("profile")
    if not profile:
        raise EtlError("vector_index_meta 旧表里没有 embed_profile/profile")
    model = values.get("model") or (profile.rsplit("/", 1)[0] if "/" in profile else profile)
    dimensions_text = values.get("dimensions")
    dimensions = 512 if dimensions_text is None else _int_value(
        dimensions_text,
        nullable=False,
        table="vector_index_meta",
        column="dimensions",
    )
    return {"id": 1, "profile": profile, "model": model, "dimensions": dimensions}


def _prepare_table(
    table: str,
    source_rows: list[dict[str, object]],
    specs: list[TargetColumn],
    *,
    material_titles: dict[str, str],
) -> list[PreparedRow]:
    prepared: list[PreparedRow] = []
    for row in source_rows:
        values: dict[str, object] = {}
        compare: dict[str, object] = {}
        rowid = row.get("__rowid")
        for spec in specs:
            name = spec.name
            if spec.generated or name == "search_tsv":
                continue
            if spec.identity:
                if name == "seq":
                    seq_value = row.get("seq")
                    chosen = (
                        seq_value
                        if isinstance(seq_value, int) and not isinstance(seq_value, bool)
                        else (rowid if isinstance(rowid, int) else None)
                    )
                    if chosen is not None:
                        values[name] = chosen
                        compare[name] = chosen
                    continue
                source_value = row.get(name)
                number = _int_value(
                    source_value,
                    nullable=spec.nullable,
                    table=table,
                    column=name,
                )
                values[name] = number
                compare[name] = number
                continue
            if table == "material_chunks" and name == "embedding":
                values[name] = None
                continue
            if table == "material_chunks" and name == "tokens":
                material_id = str(row.get("material_id") or "")
                title = material_titles.get(material_id)
                if title is None:
                    raise EtlError(
                        f"{_row_label(table, row)} 指向不存在的材料 {material_id!r}"
                    )
                text = str(values.get("text") or row.get("text") or "")
                heading = values.get("heading_path") or []
                tokens = index_source(
                    title=title, heading_path=list(heading), text=text
                )
                values[name] = tokens
                compare[name] = tokens
                continue

            if name in row:
                insert_value, plain = _convert_value(
                    spec, row[name], table=table
                )
                values[name] = insert_value
                compare[name] = plain
                continue
            if spec.default is not None:
                continue  # DB 默认值（lease_token=0 等）
            if spec.nullable:
                values[name] = None
                compare[name] = None
                continue
            raise EtlError(
                f"{table}.{name} 是 NOT NULL 且没有默认值，但旧库没有这一列"
            )
        pk = tuple(values[key] for key in PRIMARY_KEYS[table])
        prepared.append(PreparedRow(values=values, compare=compare, pk=pk))
    return prepared


def _insert_prepared(connection, table: str, rows: list[PreparedRow]) -> None:  # type: ignore[no-untyped-def]
    if not rows:
        return
    columns = list(rows[0].values)
    if any(list(row.values) != columns for row in rows):
        raise EtlError(f"{table} 的行形状不一致，拒绝批量插入")
    column_sql = ", ".join(_quote(name) for name in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    query = f"INSERT INTO {_quote(table)} ({column_sql}) VALUES ({placeholders})"
    for row in rows:
        try:
            connection.execute(query, tuple(row.values[name] for name in columns))
        except Exception as error:  # noqa: BLE001 —— 带上表/主键再抛
            raise EtlError(
                f"插入 {table} 行 {_pk_text(row.pk)} 失败：{error}"
            ) from error


def _restart_identities(
    connection,  # type: ignore[no-untyped-def]
    prepared: dict[str, list[PreparedRow]],
) -> None:
    for table, column in {**SEQ_COLUMNS, **ID_COLUMNS}.items():
        rows = prepared.get(table, [])
        values = [
            row.values[column]
            for row in rows
            if isinstance(row.values.get(column), int)
        ]
        if not values:
            continue
        sequence = connection.execute(
            "SELECT pg_get_serial_sequence(%s, %s) AS seq", (table, column)
        ).fetchone()["seq"]
        if not sequence:
            raise EtlError(f"{table}.{column} 找不到 identity sequence")
        connection.execute("SELECT setval(%s, %s, true)", (str(sequence), max(values)))


def _unwrap(value: object) -> object:
    if isinstance(value, Jsonb):
        return value.obj
    if isinstance(value, memoryview):
        return value.tobytes()
    return value


def _verify(
    connection,  # type: ignore[no-untyped-def]
    report: EtlReport,
    prepared: dict[str, list[PreparedRow]],
    specs: dict[str, list[TargetColumn]],
) -> None:
    target_counts = _target_counts(connection)
    for table in TARGET_TABLES:
        expected = len(prepared.get(table, []))
        if table == "sessions":
            if target_counts[table] != 0:
                raise EtlError(f"sessions 不迁，但目标有 {target_counts[table]} 行")
            continue
        if target_counts[table] != expected:
            raise EtlError(
                f"行数不一致：{table} 源 {expected} / 目标 {target_counts[table]}"
            )
    report.checks["row_counts"] = True

    target_rows: dict[str, dict[tuple[object, ...], dict[str, object]]] = {}
    for table in TARGET_TABLES:
        if table == "sessions":
            continue
        rows = connection.execute(f"SELECT * FROM {_quote(table)}").fetchall()
        target_rows[table] = {
            tuple(row[column] for column in PRIMARY_KEYS[table]): dict(row)
            for row in rows
        }

    for table, rows in prepared.items():
        mapping = target_rows[table]
        for row in rows:
            target = mapping.get(row.pk)
            if target is None:
                raise EtlError(f"目标缺行：{table} {_pk_text(row.pk)}")
            for column, expected in row.compare.items():
                actual = _unwrap(target[column])
                if actual != expected:
                    raise EtlError(
                        f"值不一致：{table} {_pk_text(row.pk)}.{column} "
                        f"源 {expected!r} / 目标 {actual!r}"
                    )
    report.checks["values"] = True
    report.checks["json"] = True

    for table in prepared:
        source_hash = _hash_keys([row.pk for row in prepared[table]])
        target_hash = _hash_keys(list(target_rows[table]))
        if source_hash != target_hash:
            raise EtlError(f"主键哈希不一致：{table}")
    report.checks["primary_keys"] = True

    # tokens：目标 `material_chunks.tokens` 必须与 `index_source` 同口径。
    chunk_rows = connection.execute(
        "SELECT c.chunk_id, c.text, c.heading_path, c.tokens, m.title"
        " FROM material_chunks c JOIN materials m ON m.material_id = c.material_id"
    ).fetchall()
    for row in chunk_rows:
        expected = index_source(
            title=str(row["title"]),
            heading_path=list(row["heading_path"]),
            text=str(row["text"]),
        )
        if str(row["tokens"]) != expected:
            raise EtlError(f"tokens 回填不一致：{row['chunk_id']}")
    report.checks["tokens"] = True

    _roundtrip(connection)
    report.checks["roundtrip"] = True


def _hash_keys(keys: list[tuple[object, ...]]) -> str:
    normalized = sorted(tuple(str(item) for item in key) for key in keys)
    return hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _roundtrip(connection) -> None:  # type: ignore[no-untyped-def]
    """目标 JSON 必须能被生产读路径读回；读不回就是迁移坏，不是"跳过"。"""

    document_rows = connection.execute("SELECT * FROM documents").fetchall()
    version_rows = connection.execute("SELECT * FROM document_versions").fetchall()
    versions_by_project: dict[str, list[dict]] = defaultdict(list)
    for row in version_rows:
        versions_by_project[str(row["project_id"])].append(dict(row))
    for row in document_rows:
        version_list = sorted(
            versions_by_project.get(str(row["project_id"]), []),
            key=lambda item: int(item["version_no"]),
        )
        payload = dict(row)
        # 与仓储同口径：空 template（老库补列前的 `{}`）按缺失回 STANDARD。
        payload["template"] = payload.get("template") or None
        ser.document_from_row(
            payload, tuple(ser.version_from_row(item) for item in version_list)
        )

    for row in connection.execute("SELECT * FROM rounds").fetchall():
        round_ = ser.round_from_payload(
            row["payload"],
            round_id=RoundId(row["round_id"]),
            project_id=ProjectId(row["project_id"]),
            created_at=row["created_at"],
            step=Step(row["step"]),
            waiting_on=WaitingOn(row["waiting_on"]),
        )
        ser.round_to_payload(round_)

    for row in connection.execute("SELECT * FROM explorations").fetchall():
        ser.exploration_from_payload(
            row["payload"],
            exploration_id=ExplorationId(row["exploration_id"]),
            project_id=ProjectId(row["project_id"]),
            kind=ExplorationKind(row["kind"]),
            status=ExplorationStatus(row["status"]),
            created_at=row["created_at"],
        )
    for row in connection.execute("SELECT * FROM discussions").fetchall():
        ser.discussion_from_payload(
            row["payload"],
            discussion_id=DiscussionId(row["discussion_id"]),
            project_id=ProjectId(row["project_id"]),
            status=ThreadStatus(row["status"]),
            created_at=row["created_at"],
        )
    for row in connection.execute("SELECT * FROM project_memory").fetchall():
        ser.memory_from_payload(row["payload"], project_id=ProjectId(row["project_id"]))
    for row in connection.execute("SELECT * FROM evidence_ledgers").fetchall():
        ser.ledger_from_json(ProjectId(row["project_id"]), row["payload"])


def _embed_chunks(
    connection,  # type: ignore[no-untyped-def]
    embedder: object | None,
    *,
    allow: bool,
    reason: str | None,
) -> tuple[int, str | None]:
    if not allow:
        return 0, reason
    rows = connection.execute(
        "SELECT c.chunk_id, c.material_id, c.ordinal, c.text, c.heading_path,"
        " m.title, m.origin"
        " FROM material_chunks c JOIN materials m ON m.material_id = c.material_id"
        " ORDER BY c.material_id, c.ordinal"
    ).fetchall()
    if not rows:
        return 0, None
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["material_id"]), []).append(dict(row))
    selected: list[dict] = []
    for group in grouped.values():
        limit = blocks_within_budget(group, vector_char_budget(str(group[0]["origin"])))
        selected.extend(group[:limit])
    if not selected:
        return 0, None
    if embedder is None:
        embedder = FastEmbedEmbedder()
    batch_size = 32
    written = 0
    for start in range(0, len(selected), batch_size):
        batch = selected[start : start + batch_size]
        texts = [
            index_text(
                title=str(row["title"]),
                heading_path=list(row["heading_path"]),
                text=str(row["text"]),
            )
            for row in batch
        ]
        vectors = embedder.embed_many(texts)  # type: ignore[attr-defined]
        if len(vectors) != len(batch):
            raise EtlError(
                f"embedder 返回 {len(vectors)} 条向量，期望 {len(batch)}"
            )
        for row, vector in zip(batch, vectors, strict=True):
            values = list(vector)
            if len(values) != DIMENSIONS:
                raise EtlError(
                    f"{row['chunk_id']} 向量维度 {len(values)} ≠ {DIMENSIONS}"
                )
            connection.execute(
                "UPDATE material_chunks SET embedding = %s WHERE chunk_id = %s",
                (Vector(values), str(row["chunk_id"])),
            )
            written += 1
    return written, None


def migrate(
    sqlite_path: str | Path,
    database_url: str,
    *,
    truncate: bool = False,
    embedder: object | None = None,
) -> EtlReport:
    """把 `sqlite_path` 导入 `database_url` 指向的**已迁移空 schema**。"""

    path = Path(sqlite_path)
    source = _open_source(path)
    try:
        version = _user_version(source)
        if version < 15:
            raise EtlError(
                f"不支持 user_version={version} 的旧库；v15 起才带 owner/模板/留样列"
            )
        tables = _source_table_names(source)
        source_rows: dict[str, list[dict[str, object]]] = {
            table: _read_source_rows(source, table) for table in tables
        }
        has_sessions = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
        ).fetchone()
        sessions = (
            source.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
            if has_sessions is not None
            else None
        )
        skipped_sessions = 0 if sessions is None else int(sessions["n"])
    finally:
        source.close()

    materials = source_rows.get("materials", [])
    seen_materials: dict[tuple[str, str], str] = {}
    duplicates: list[tuple[str, str, str]] = []
    for row in materials:
        key = (str(row.get("project_id")), str(row.get("sha256")))
        material_id = str(row.get("material_id"))
        if key in seen_materials:
            duplicates.append((key[0], key[1], seen_materials[key]))
        else:
            seen_materials[key] = material_id
    if duplicates:
        sample = "、".join(
            f"{project}/{sha}（{material_id} 与 {other}）"
            for project, sha, other in duplicates[:3]
        )
        raise EtlError(
            f"源库有重复 (project_id, sha256)：{len(duplicates)} 组，例如 {sample}；"
            "拒绝静默丢行"
        )

    material_titles = {
        str(row.get("material_id")): str(row.get("title") or "")
        for row in materials
    }

    exploration_rows = source_rows.get("explorations", [])
    by_project: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in exploration_rows:
        by_project[str(row.get("project_id"))].append(row)
    deduped = _dedupe_running(by_project)

    vector_meta = _vector_meta_from_source(source_rows.get("vector_index_meta", []))
    report = EtlReport(
        source=path,
        user_version=version,
        skipped_sessions=skipped_sessions,
        deduped_running=deduped,
        vector_profile=vector_meta,
    )

    with connect(database_url) as connection:
        _ensure_target(connection)
        current_counts = _target_counts(connection)
        if any(current_counts.values()):
            if not truncate:
                detail = "、".join(
                    f"{table} {count}"
                    for table, count in current_counts.items()
                    if count
                )
                raise EtlError(
                    f"目标表非空（{detail}）；确认这是重跑后加 --truncate 清空目标表"
                )
            _truncate_target(connection)

        specs = {table: _target_columns(connection, table) for table in tables}
        prepared: dict[str, list[PreparedRow]] = {}
        for table in tables:
            if table == "vector_index_meta":
                if vector_meta is None:
                    prepared[table] = []
                else:
                    values = dict(vector_meta)
                    prepared[table] = [
                        PreparedRow(values=values, compare=dict(values), pk=(1,))
                    ]
                continue
            prepared[table] = _prepare_table(
                table,
                source_rows[table],
                specs[table],
                material_titles=material_titles,
            )

        with connection.transaction():
            for table in INSERT_ORDER:
                _insert_prepared(connection, table, prepared.get(table, []))
                report.counts[table] = len(prepared.get(table, []))
            _restart_identities(connection, prepared)
            _verify(connection, report, prepared, specs)

        compatible = (
            vector_meta is None
            or (
                vector_meta.get("profile") == EMBED_PROFILE
                and vector_meta.get("dimensions") == DIMENSIONS
                and vector_meta.get("model") == MODEL_NAME
            )
        )
        report.embedded_chunks, report.embedding_skipped_reason = _embed_chunks(
            connection,
            embedder,
            allow=compatible,
            reason=(
                None
                if compatible or vector_meta is None
                else "旧向量 profile 与当前不一致；finalize 会清空并由维护任务重建"
            ),
        )
    return report


def _print_report(report: EtlReport) -> None:
    print(f"ETL 完成：{report.source}（user_version={report.user_version}）")
    for table in TARGET_TABLES:
        if table == "sessions":
            print(f"  sessions：跳过（源 {report.skipped_sessions} / 目标 0）")
        else:
            print(f"  {table}: {report.counts.get(table, 0)}")
    if report.deduped_running:
        print(
            "  running 去重（保留最新，全部行照插）："
            + "、".join(f"{project}/{item}" for project, item in report.deduped_running)
        )
    if report.vector_profile is not None:
        print(
            "  vector_index_meta："
            f"{report.vector_profile['profile']} / "
            f"{report.vector_profile['dimensions']} / {report.vector_profile['model']}"
        )
    print(f"  向量重嵌入：{report.embedded_chunks}")
    if report.embedding_skipped_reason:
        print(f"  向量跳过：{report.embedding_skipped_reason}")
    checks = "、".join(f"{name}={'OK' if ok else 'FAIL'}" for name, ok in report.checks.items())
    print(f"校验：{checks}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="旧 SQLite → PostgreSQL 一次性 ETL")
    parser.add_argument("--sqlite", required=True, help="旧 SQLite 库路径（只读打开）")
    parser.add_argument(
        "--database-url",
        default="",
        help="直连 PostgreSQL（owner）；缺省读 PMSTUDIO_DIRECT_DATABASE_URL",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="先清空目标 19 张表再导入（失败重跑用；不会碰 schema_migrations）",
    )
    args = parser.parse_args(argv)

    url = args.database_url
    if not url:
        try:
            url = OpsSettings.from_env().direct_database_url
        except ConfigError as error:
            print(error, file=sys.stderr)
            return 2
    try:
        report = migrate(Path(args.sqlite), url, truncate=args.truncate)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    except EtlError as error:
        print(f"ETL 失败：{error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 —— 命令行给出非零退出与原因
        print(f"ETL 失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
