"""扫 `schema.sql`：**13 张内容表必须含 `owner_id`**，豁免三张写死（多用户改造 Task 5）。

为什么要扫表形状而不是只靠运行时：`owner_id` 列是隔离的**物理前提**——仓储的读过滤
`AND owner_id = ?` 只有在列存在时才有意义。漏给一张新表加列，仓储一时不读它，
隔离就悄悄缺一个面，而没有任何用例会红。所以这里把"哪些表必须有列"变成可执行的契约。

`templates` 也算内容表（自建模板归 owner；builtin 行 owner 留 NULL 人人可见）。
豁免名单 `{users, sessions, model_calls}` **写死在测试里**：新增一张表而不同时回答
"它归不归 owner"，这里先红——不许静默溜过去。
"""

import sqlite3
from pathlib import Path

SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "pmstudio"
    / "infrastructure"
    / "sqlite"
    / "schema.sql"
)

#: 必须含 `owner_id` 的表（裁决 A）：12 张项目域表 + `templates`。
OWNER_TABLES = frozenset(
    {
        "documents",
        "document_versions",
        "rounds",
        "evidence_ledgers",
        "materials",
        "material_chunks",
        "explorations",
        "discussions",
        "project_memory",
        "observations",
        "round_digests",
        "inspirations",
        "templates",
    }
)

#: 明确**不带**归属的表：身份本身（users/sessions）与留样（model_calls / tool_calls，join 查）；
#: 外加 FTS5 影子表 `material_chunks_fts`——它是**派生的索引**，归属由 JOIN `materials`
#: 判定（`material_chunks_fts.chunk_id → material_chunks → materials.owner_id`），
#: 自己不带也没有 owner 列。它的 5 张内部影表（`_data/_idx/...`）由下面的取表口径跳过。
EXEMPT_TABLES = frozenset(
    {"users", "sessions", "model_calls", "tool_calls", "material_chunks_fts"}
)


def _tables_and_columns() -> dict[str, set[str]]:
    """把 `schema.sql` 灌进内存库，读真实列集——比正则解析 DDL 可靠。"""

    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        # 只取**真表**与**虚拟表**：FTS5 的 5 张内部影表（`type='shadow'`）是引擎的
        # 实现细节，不是应用表——把它们当"新表"来审是误报。虚拟表（fts5 本体）留下，
        # 它需要显式回答"归不归 owner"。
        names = [
            row[1]
            for row in connection.execute("PRAGMA table_list")
            if not row[1].startswith("sqlite_") and row[2] in ("table", "virtual")
        ]
        return {
            name: {row[1] for row in connection.execute(f"PRAGMA table_info({name})")}
            for name in names
        }
    finally:
        connection.close()


def test_every_content_table_has_an_owner_column():
    columns = _tables_and_columns()
    missing = sorted(table for table in OWNER_TABLES if "owner_id" not in columns.get(table, set()))
    assert missing == [], (
        "这些内容表没有 owner_id 列，隔离缺一个物理面：" + "、".join(missing)
    )


def test_exempt_tables_have_no_owner_column():
    columns = _tables_and_columns()
    offenders = sorted(
        table for table in EXEMPT_TABLES if "owner_id" in columns.get(table, set())
    )
    assert offenders == [], f"这些表不该有 owner_id：{offenders}"


def test_schema_has_no_unregistered_table():
    """每张表要么"必须带 owner"，要么在豁免名单里——增表不增豁免即红。"""

    columns = _tables_and_columns()
    unknown = sorted(set(columns) - OWNER_TABLES - EXEMPT_TABLES)
    assert unknown == [], (
        "这些表既不在 OWNER_TABLES 也不在 EXEMPT_TABLES："
        f"{unknown}。新增表要显式回答「归不归 owner」"
    )
