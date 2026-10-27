"""扫真 PostgreSQL schema：**内容表必须含 `owner_id`**，豁免表写死。

为什么要扫表形状而不是只靠运行时：`owner_id` 列是隔离的**物理前提**——仓储的读过滤
`AND owner_id = %s` 只有在列存在时才有意义。漏给一张新表加列，仓储一时不读它，
隔离就悄悄缺一个面，而没有任何用例会红。所以这里把"哪些表必须有列"变成可执行的契约。

以 `information_schema` + `current_schema()` 为准：测试 schema 由 0001 迁移建出，
不再解析 `schema.sql`（SQLite 源在 T10 删除）。
"""

from pmstudio.infrastructure.postgres.database import connect
from tests.support.postgres import direct_url

#: 必须含 `owner_id` 的表：项目域表 + `templates`。
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
        "round_skips",
    }
)

#: 明确**不带**归属的表：身份本身（users/sessions）、留样（model_calls / tool_calls，
#: join 查）、迁移账本与向量口径。
EXEMPT_TABLES = frozenset(
    {
        "users",
        "sessions",
        "model_calls",
        "tool_calls",
        "schema_migrations",
        "vector_index_meta",
    }
)


def _tables_and_columns() -> dict[str, set[str]]:
    """从当前测试 schema 的 `information_schema` 读真实表与列集。"""

    with connect(direct_url()) as connection:
        rows = connection.execute(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_schema = current_schema()
               AND table_type = 'BASE TABLE'
            """
        ).fetchall()
        names = [str(row["table_name"]) for row in rows]
        columns = connection.execute(
            """
            SELECT table_name, column_name
              FROM information_schema.columns
             WHERE table_schema = current_schema()
            """
        ).fetchall()
    found: dict[str, set[str]] = {name: set() for name in names}
    for row in columns:
        found.setdefault(str(row["table_name"]), set()).add(str(row["column_name"]))
    return found


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
