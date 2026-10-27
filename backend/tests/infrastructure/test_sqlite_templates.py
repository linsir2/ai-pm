"""模板库表：builtin seed + 自定义存取 + 删（`02` §11.0.70）。"""

import json

import pytest
from psycopg.types.json import Jsonb

from pmstudio.application.errors import UnknownTemplate
from pmstudio.domain.shared.template import standard_template, template_from_dict
from pmstudio.infrastructure.sqlite.repositories import SqliteTemplateRepository
from tests.support.postgres import open_direct


def _custom_template() -> object:
    """一份最小的自建模板（三锚点齐全，`validate` 才放行）。"""

    return template_from_dict(
        {
            "template_id": "tpl-custom",
            "title": "自建",
            "sections": [
                {"id": "s1", "title": "背景", "purpose": "摆设", "role": "plain"},
                {"id": "s5", "title": "功能", "purpose": "摆设", "role": "feature"},
                {"id": "s11", "title": "未决", "purpose": "摆设", "role": "open_questions"},
                {
                    "id": "s13",
                    "title": "版本",
                    "purpose": "摆设",
                    "role": "version_history",
                },
            ],
        }
    )


def test_templates_table_created_and_round_trips():
    from pmstudio.infrastructure.templates.loader import TEMPLATES_DIR, load_template_file

    connection = open_direct()
    repo = SqliteTemplateRepository(connection)

    repo.save(standard_template(), builtin=True)
    repo.save(standard_template(), builtin=True)  # seed 幂等：重复种不炸
    repo.save(load_template_file(TEMPLATES_DIR / "light.toml"), builtin=True)
    # 内置按标题排（标准版在前）：画廊默认选 standard，它得是第一张卡
    # `list()` 改报 `TemplateEntry`（带 builtin 标记，`02` §11.0.70）：需求变更
    assert [item.template.template_id for item in repo.list()] == ["standard", "light"]

    got = repo.get("standard")
    assert got.template == standard_template()
    assert got.builtin is True
    connection.close()


def test_unknown_template_id_is_a_404():
    connection = open_direct()
    repo = SqliteTemplateRepository(connection)

    try:
        repo.get("nope")
        raise AssertionError("该抛 UnknownTemplate")
    except UnknownTemplate as error:
        assert error.code == "unknown_template"
    finally:
        connection.close()


def test_list_and_get_report_which_templates_are_builtin():
    """库里要能分辨出厂的和自建的（`06` §2.3：内置那份不���编辑/删除按钮）。"""

    connection = open_direct()
    repo = SqliteTemplateRepository(connection)
    repo.save(standard_template(), builtin=True)
    repo.save(_custom_template(), builtin=False)

    assert {item.template.template_id: item.builtin for item in repo.list()} == {
        "standard": True,
        "tpl-custom": False,
    }
    assert repo.get("standard").builtin is True
    assert repo.get("tpl-custom").builtin is False
    connection.close()


def test_delete_removes_that_template_and_leaves_the_others():
    connection = open_direct()
    repo = SqliteTemplateRepository(connection)
    repo.save(standard_template(), builtin=True)
    repo.save(_custom_template(), builtin=False)

    repo.delete("tpl-custom")

    assert [item.template.template_id for item in repo.list()] == ["standard"]
    connection.close()


def test_deleting_a_template_that_is_not_there_is_an_error():
    connection = open_direct()
    repo = SqliteTemplateRepository(connection)
    repo.save(standard_template(), builtin=True)

    with pytest.raises(UnknownTemplate):
        repo.delete("tpl-nope")

    assert len(repo.list()) == 1
    connection.close()


def test_old_document_row_without_template_reads_back_as_standard():
    """无模板的老行读回即 STANDARD：真存一份，再把列手写回 '{}' 模拟老行。"""

    from pmstudio.domain.shared.template import STANDARD_TEMPLATE_ID
    from pmstudio.infrastructure.sqlite.repositories import SqliteDocumentRepository
    from tests.support.builders import PROJECT, make_document

    connection = open_direct()
    docs = SqliteDocumentRepository(connection)
    docs.save(make_document())
    connection.execute(
        "UPDATE documents SET template = %s WHERE project_id = 'p1'", (Jsonb({}),)
    )

    document = docs.get(PROJECT)

    assert document.template == standard_template()
    assert document.template.template_id == STANDARD_TEMPLATE_ID
    connection.close()


def test_template_json_round_trip():
    from pmstudio.infrastructure.sqlite import serialization

    template = standard_template()
    assert serialization.template_from_json(serialization.template_to_json(template)) == template
    assert json.loads(json.dumps(serialization.template_to_json(template)))


def test_project_summary_carries_template_ref():
    """列表/摘要带模板 id 与名（首页密行渲染用；读行快照，零新查询）。"""

    from pmstudio.domain.authoring.document import Document
    from pmstudio.domain.shared.ids import DocumentId, ProjectId
    from pmstudio.infrastructure.sqlite.repositories import (
        SqliteDocumentRepository,
        SqliteWorkspaceIndex,
    )
    from pmstudio.infrastructure.templates.loader import TEMPLATES_DIR, load_template_file
    from tests.support.builders import NOW, PROJECT, make_document

    connection = open_direct()
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


def test_template_json_roundtrip_keeps_criteria_declarations():
    """判据声明要跟着文档行走：快照丢字段 = 老项目换模板后判据静默变空。"""

    from pmstudio.infrastructure.sqlite import serialization
    from pmstudio.infrastructure.templates import loader

    template = loader.load_template_file(loader.TEMPLATES_DIR / "light.toml")

    assert serialization.template_from_json(serialization.template_to_json(template)) == template


def test_template_json_from_legacy_snapshot_falls_back_to_no_declaration():
    """切片 1 存的老行没有这两个键：读回来是"没声明"，不是炸（老项目照样打开）。"""

    from pmstudio.infrastructure.sqlite import serialization

    legacy = serialization.template_to_json(
        serialization.template_from_json(
            {
                "template_id": "t",
                "title": "老行",
                "sections": [
                    {
                        "section_id": "s5",
                        "title": "功能",
                        "purpose": "p",
                        "role": "feature",
                    },
                    {
                        "section_id": "s11",
                        "title": "未决",
                        "purpose": "p",
                        "role": "open_questions",
                    },
                    {
                        "section_id": "s13",
                        "title": "版本",
                        "purpose": "p",
                        "role": "version_history",
                    },
                ],
            }
        )
    )
    assert "min_entries" not in legacy["sections"][0]
    assert "coverage" not in legacy["sections"][0]


#: 回归：启动路径没有身份上下文（`build_container` 在组装期读模板），
#: 那条读必须仍然可用——builtin 行（owner_id IS NULL）本来就人人可见。
#: 这条用例必须显式清掉 conftest 的 autouse 默认 scope，否则测不到"启动时"这个条件
#: （2327 条全绿却启动崩溃就是这么漏的：D2 的全局 fixture 把它遮住了）。
def test_list_works_without_an_owner_scope_returning_only_builtin():
    from pmstudio.domain.shared.ids import UserId
    from pmstudio.infrastructure.postgres import owner_scope
    from pmstudio.infrastructure.templates.loader import TEMPLATES_DIR, load_template_file

    connection = open_direct()
    repo = SqliteTemplateRepository(connection)
    repo.save(standard_template(), builtin=True)
    repo.save(load_template_file(TEMPLATES_DIR / "light.toml"), builtin=True)
    repo.save(template_from_dict(
        {
            "template_id": "tpl-someone-else",
            "title": "别人的",
            "sections": [
                {"id": "s1", "title": "背景", "purpose": "摆设", "role": "plain"},
                {"id": "s5", "title": "功能", "purpose": "摆设", "role": "feature"},
                {"id": "s11", "title": "未决", "purpose": "摆设", "role": "open_questions"},
                {"id": "s13", "title": "版本", "purpose": "摆设", "role": "version_history"},
            ],
        }
    ), builtin=False, owner=UserId("u-someone-else"))

    owner_scope.clear_owner_scope()
    assert owner_scope.current_owner_scope() is None
    # 没有身份 → 只回出厂模板；不抛 `missing_owner_scope`（启动路径要的就是这个）
    assert [item.template.template_id for item in repo.list()] == ["standard", "light"]
    connection.close()
