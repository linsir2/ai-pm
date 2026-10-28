"""T10 守卫：运行时、脚本、测试里都不许再出现 SQLite 的运行时痕迹。

判据是 **AST import + 非 docstring 字符串字面量**（注释不进 AST，天然不扫）。
ETL 只允许在 `scripts/migrate_sqlite_to_pg.py` 与 `tests/test_etl_cutover.py`
里碰 SQLite；那是迁移窗口的一次性工具，不是运行时。

守卫自己也在这棵被扫的树里，所以模式都拆成两段拼——写成完整字面量就会自己抓自己。
"""

from pathlib import Path

from tests.architecture.guards import (
    SQLITE_WHITELIST,
    SRC,
    sqlite_imports,
    sqlite_offenders,
    sqlite_sql_literals,
)

BACKEND = SRC.parent


def test_no_sqlite_runtime_left_in_the_tree() -> None:
    assert sqlite_offenders(BACKEND) == {}


def test_the_sqlite_package_is_gone() -> None:
    assert not (SRC / "pmstudio" / "infrastructure" / "sqlite").exists()


def test_guard_catches_imports() -> None:
    assert sqlite_imports("import sqlite" + "3\n") == {"sqlite" + "3"}
    assert sqlite_imports("from sqlite" + "_vec import vec" + "0\n") == {"sqlite" + "_vec"}
    assert sqlite_imports("from pmstudio.infrastructure.postgres import database\n") == set()


def test_guard_catches_sql_literals_but_not_docstrings_or_comments() -> None:
    banned = (
        "'PRA" + "GMA journal_mode'",
        "'INSERT" + " OR REPLACE INTO t VALUES (1)'",
        "'created_at DESC, row" + "id DESC'",
        "'FTS" + "5'",
        "'sqlite" + "3'",
        "'PMSTUDIO_" + "DB'",
    )
    for literal in banned:
        source = f"SQL = {literal}\n"
        assert sqlite_sql_literals(source), literal

    clean = (
        '"""这里解释 PRA' + 'GMA 为什么不在这里用。"""\n'
        "# 注释里出现 sqlite" + "3 与 row" + "id 不算\n"
        "SQL = 'SELECT 1'\n"
    )
    assert sqlite_sql_literals(clean) == {}


def test_only_the_etl_script_and_test_are_whitelisted() -> None:
    assert SQLITE_WHITELIST == {
        "scripts/" + "migrate_sqlite_to_pg.py",
        "tests/" + "test_etl_cutover.py",
    }
    for relative in SQLITE_WHITELIST:
        assert (BACKEND / Path(relative)).is_file(), relative
