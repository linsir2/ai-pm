"""模板库表：builtin seed + 自定义存取 + 老库迁移。"""

import json
import sqlite3

from pmstudio.application.errors import UnknownTemplate
from pmstudio.domain.shared.template import standard_template
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.repositories import SqliteTemplateRepository


def test_templates_table_created_and_round_trips(tmp_path):
    from pmstudio.infrastructure.templates.loader import TEMPLATES_DIR, load_template_file

    connection = database.connect(tmp_path / "pm.db")
    repo = SqliteTemplateRepository(connection)

    repo.save(standard_template(), builtin=True)
    repo.save(standard_template(), builtin=True)  # seed 幂等：重复种不炸
    repo.save(load_template_file(TEMPLATES_DIR / "light.toml"), builtin=True)
    # 内置按标题排（标准版在前）：画廊默认选 standard，它得是第一张卡
    assert [template.template_id for template in repo.list()] == ["standard", "light"]

    got = repo.get("standard")
    assert got == standard_template()
    connection.close()


def test_unknown_template_id_is_a_404(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    repo = SqliteTemplateRepository(connection)

    try:
        repo.get("nope")
        raise AssertionError("该抛 UnknownTemplate")
    except UnknownTemplate as error:
        assert error.code == "unknown_template"
    finally:
        connection.close()


OLD_DOCUMENTS_DDL = """
CREATE TABLE documents (
  project_id         TEXT PRIMARY KEY,
  document_id        TEXT NOT NULL,
  title              TEXT NOT NULL,
  blocks             TEXT NOT NULL,
  preference_entries TEXT NOT NULL,
  tombstones         TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);
"""


def test_an_old_db_gets_templates_table_and_documents_template_column(tmp_path):
    """老库：补 `templates` 表 + `documents.template` 列，旧行不丢。"""

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(OLD_DOCUMENTS_DDL)
    old.execute(
        "INSERT INTO documents (project_id, document_id, title, blocks,"
        " preference_entries, tombstones, created_at, updated_at) VALUES"
        " ('p1', 'doc-1', '旧项目', '[]', '[]', '[]',"
        "  '2026-09-01T00:00:00+00:00', '2026-09-01T00:00:00+00:00')"
    )
    old.commit()
    old.close()

    connection = database.connect(path)
    names = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "templates" in names
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(documents)")
    }
    assert "template" in columns
    row = connection.execute("SELECT title FROM documents WHERE project_id='p1'").fetchone()
    assert row["title"] == "旧项目"
    assert connection.execute("PRAGMA user_version").fetchone()[0] == database.USER_VERSION
    connection.close()


def test_old_document_row_without_template_reads_back_as_standard(tmp_path):
    """无模板的老行读回即 STANDARD：真存一份，再把列手写回 '{}' 模拟老行。"""

    from pmstudio.domain.shared.template import STANDARD_TEMPLATE_ID
    from pmstudio.infrastructure.sqlite.repositories import SqliteDocumentRepository
    from tests.support.builders import PROJECT, make_document

    connection = database.connect(tmp_path / "pm.db")
    docs = SqliteDocumentRepository(connection)
    docs.save(make_document())
    connection.execute("UPDATE documents SET template = '{}' WHERE project_id = 'p1'")

    document = docs.get(PROJECT)

    assert document.template == standard_template()
    assert document.template.template_id == STANDARD_TEMPLATE_ID
    connection.close()


def test_template_json_round_trip():
    from pmstudio.infrastructure.sqlite import serialization

    template = standard_template()
    assert serialization.template_from_json(serialization.template_to_json(template)) == template
    assert json.loads(json.dumps(serialization.template_to_json(template)))


def test_project_summary_carries_template_ref(tmp_path):
    """列表/摘要带模板 id 与名（首页密行渲染用；读行快照，零新查询）。"""

    from pmstudio.domain.authoring.document import Document
    from pmstudio.domain.shared.ids import DocumentId, ProjectId
    from pmstudio.infrastructure.sqlite.repositories import (
        SqliteDocumentRepository,
        SqliteWorkspaceIndex,
    )
    from pmstudio.infrastructure.templates.loader import TEMPLATES_DIR, load_template_file
    from tests.support.builders import NOW, PROJECT, make_document

    connection = database.connect(tmp_path / "pm.db")
    docs = SqliteDocumentRepository(connection)
    docs.save(make_document())
    docs.save(
        Document.new(
            document_id=DocumentId("doc-light"),
            project_id=ProjectId("p-light"),
            title="轻项目",
            now=NOW,
            template=load_template_file(TEMPLATES_DIR / "light.toml"),
        )
    )
    index = SqliteWorkspaceIndex(connection)

    standard = index.summary(PROJECT)
    assert (standard.template_id, standard.template_title) == ("standard", "标准版")
    light = index.summary(ProjectId("p-light"))
    assert (light.template_id, light.template_title) == ("light", "轻量版")
    by_id = {item.project_id: item for item in index.list_summaries()}
    assert by_id["p-light"].template_title == "轻量版"
    connection.close()
