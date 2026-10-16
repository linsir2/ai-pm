"""跨 owner 读写矩阵 + 缺 owner 上下文即抛（多用户改造 Task 5 / D2）。

落笔即定（写进测试名里）：
- 读不到 / 跨 owner = 与"不存在"**同形**——`get` 类抛对应 `unknown_*`（404），
  `list`/`search` 类回空。绝不返回"别人的东西"。
- 缺 owner 上下文（显式没传、环境也没设）= `ApplicationError(code="missing_owner_scope")`
  （500 级程序员错误，不许退化成 404）。

矩阵取六类代表表：documents / rounds / materials / templates / explorations / discussions。
全 13 张逐张写是复制粘贴——代表表 + `test_owner_columns.py` 的 schema 扫表即等价覆盖。
"""

import pytest

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.errors import (
    UnknownDiscussion,
    UnknownExploration,
    UnknownProject,
    UnknownRound,
    UnknownTemplate,
)
from pmstudio.domain.discussion.thread import (
    Discussion,
    Participant,
    QuestionSnapshot,
)
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationScope,
    ScopeKind,
)
from pmstudio.domain.shared.ids import (
    DiscussionId,
    ExplorationId,
    RoundId,
    UserId,
)
from pmstudio.domain.shared.template import template_from_dict
from pmstudio.harness.errors import ApplicationError
from pmstudio.infrastructure.sqlite import database, owner_scope
from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteDiscussionRepository,
    SqliteDocumentRepository,
    SqliteExplorationRepository,
    SqliteRoundRepository,
    SqliteTemplateRepository,
)
from tests.support.builders import NOW, PROJECT, make_document, make_round
from tests.support.doubles import SeqIds

ALICE = UserId("u-alice")
BOB = UserId("u-bob")


@pytest.fixture()
def connection(tmp_path):  # type: ignore[no-untyped-def]
    conn = database.connect(tmp_path / "pm.db")
    yield conn
    conn.close()


def _material() -> ParsedMaterial:
    return ParsedMaterial(
        origin="upload",
        title="需求.md",
        source_ref="attachment:需求.md",
        media_type="text/markdown",
        bytes_len=6,
        chunks=(
            MaterialChunk(
                chunk_id="local-text#c00",
                ordinal=0,
                text="一条需求",
                heading_path=(),
                anchor="第 1 行",
            ),
        ),
        parser_id="local-text",
        parser_version="v1",
    )


def _custom_template():  # type: ignore[no-untyped-def]
    return template_from_dict(
        {
            "template_id": "tpl-alice",
            "title": "爱丽丝的模板",
            "sections": [
                {"id": "s1", "title": "背景", "purpose": "摆设", "role": "plain"},
                {"id": "s5", "title": "功能", "purpose": "摆设", "role": "feature"},
                {"id": "s11", "title": "未决", "purpose": "摆设", "role": "open_questions"},
                {"id": "s13", "title": "版本", "purpose": "摆设", "role": "version_history"},
            ],
        }
    )


def _exploration() -> Exploration:
    return Exploration.start(
        exploration_id=ExplorationId("x-alice"),
        project_id=PROJECT,
        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
        now=NOW,
    )


def _discussion() -> Discussion:
    return Discussion.start(
        discussion_id=DiscussionId("d-alice"),
        project_id=PROJECT,
        topic="怎么让灵感不丢",
        participants=(
            Participant(role_id="dlg.challenger", role_title="反方", joined_round=1),
        ),
        question=QuestionSnapshot(title="题面", why="为啥问"),
        now=NOW,
    )


# ── 缺上下文即抛 ─────────────────────────────────────────────────────────────


def test_missing_owner_scope_raises_loudly(connection):
    """显式不传、环境也没设 → 响亮地抛，绝不静默当一个无主操作。"""

    owner_scope.clear_owner_scope()
    repo = SqliteDocumentRepository(connection)
    with pytest.raises(ApplicationError) as caught:
        repo.get(PROJECT)  # 不带 owner
    assert caught.value.code == "missing_owner_scope"


def test_missing_owner_scope_also_guards_writes(connection):
    owner_scope.clear_owner_scope()
    repo = SqliteDocumentRepository(connection)
    with pytest.raises(ApplicationError) as caught:
        repo.save(make_document())  # 写入也要归属，否则新行 owner 为 NULL
    assert caught.value.code == "missing_owner_scope"


# ── 环境上下文兜底 ───────────────────────────────────────────────────────────


def test_ambient_scope_is_used_when_owner_not_passed(connection):
    owner_scope.set_owner_scope(ALICE)
    repo = SqliteDocumentRepository(connection)
    repo.save(make_document())  # 不传 owner → 取环境
    assert repo.get(PROJECT).title == "订单中台"


# ── 跨 owner 读写矩阵（六类代表表）────────────────────────────────────────────


def test_documents_reads_are_scoped_to_the_owner(connection):
    docs = SqliteDocumentRepository(connection)
    docs.save(make_document(), owner=ALICE)

    assert docs.get(PROJECT, owner=ALICE).title == "订单中台"
    with pytest.raises(UnknownProject):
        docs.get(PROJECT, owner=BOB)


def test_rounds_reads_are_scoped_to_the_owner(connection):
    rounds = SqliteRoundRepository(connection)
    rounds.save(make_round(), owner=ALICE)

    assert rounds.get(RoundId("r1"), owner=ALICE).project_id == PROJECT
    with pytest.raises(UnknownRound):
        rounds.get(RoundId("r1"), owner=BOB)


def test_materials_reads_are_scoped_to_the_owner(connection):
    store = SqliteMaterialStore(connection, ids=SeqIds())
    saved = store.put(
        project_id=PROJECT,
        parsed=_material(),
        sha256="a" * 64,
        content=b"bytes",
        now=NOW,
        owner=ALICE,
    )

    assert store.get(saved.material_id, owner=ALICE) is not None
    assert store.get(saved.material_id, owner=BOB) is None
    assert store.list_for_project(PROJECT, owner=ALICE, current=frozenset()) != ()
    assert store.list_for_project(PROJECT, owner=BOB, current=frozenset()) == ()


def test_templates_reads_are_scoped_except_builtin(connection):
    templates = SqliteTemplateRepository(connection)
    from pmstudio.domain.shared.template import standard_template

    templates.save(standard_template(), builtin=True)  # builtin：owner NULL，人人可见
    templates.save(_custom_template(), builtin=False, owner=ALICE)

    # builtin 两个人都不抛；自建的只有 Alice 看得到
    assert templates.get("standard", owner=BOB).builtin is True
    assert templates.get("tpl-alice", owner=ALICE).template.template_id == "tpl-alice"
    with pytest.raises(UnknownTemplate):
        templates.get("tpl-alice", owner=BOB)


def test_explorations_reads_are_scoped_to_the_owner(connection):
    explorations = SqliteExplorationRepository(connection)
    explorations.save(_exploration(), owner=ALICE)

    assert explorations.get(ExplorationId("x-alice"), owner=ALICE).project_id == PROJECT
    with pytest.raises(UnknownExploration):
        explorations.get(ExplorationId("x-alice"), owner=BOB)


def test_discussions_reads_are_scoped_to_the_owner(connection):
    discussions = SqliteDiscussionRepository(connection)
    discussions.save(_discussion(), owner=ALICE)

    assert discussions.get(DiscussionId("d-alice"), owner=ALICE).topic == "怎么让灵感不丢"
    with pytest.raises(UnknownDiscussion):
        discussions.get(DiscussionId("d-alice"), owner=BOB)


# ── 写入盖章 ────────────────────────────────────────────────────────────────


def test_writes_stamp_the_owner(connection):
    docs = SqliteDocumentRepository(connection)
    docs.save(make_document(), owner=ALICE)

    stamped = connection.execute(
        "SELECT owner_id FROM documents WHERE project_id = ?", (str(PROJECT),)
    ).fetchone()
    assert stamped["owner_id"] == str(ALICE)


def test_write_stamp_does_not_change_on_later_owner(connection):
    """owner 写入后不可改（§0.7）：同一行再存一次换个 owner，归属不动。"""

    docs = SqliteDocumentRepository(connection)
    docs.save(make_document(title="第一版"), owner=ALICE)
    docs.save(make_document(title="第二版"), owner=BOB)

    row = connection.execute(
        "SELECT owner_id, title FROM documents WHERE project_id = ?", (str(PROJECT),)
    ).fetchone()
    assert row["owner_id"] == str(ALICE)
    assert row["title"] == "第二版"
