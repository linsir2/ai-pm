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
    LeaseLost,
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
from pmstudio.domain.memory.digest import RoundDigest
from pmstudio.domain.memory.inspiration import InspirationItem, InspirationOrigin
from pmstudio.domain.memory.observations import ObservationType, RoundObservation
from pmstudio.domain.shared.ids import (
    DiscussionId,
    ExplorationId,
    RoundId,
    UserId,
)
from pmstudio.domain.shared.template import template_from_dict
from pmstudio.harness.errors import ApplicationError
from pmstudio.infrastructure.postgres import owner_scope
from pmstudio.infrastructure.postgres.digest_repo import SqliteDigestRepository
from pmstudio.infrastructure.postgres.inspiration_repo import SqliteInspirationRepository
from pmstudio.infrastructure.postgres.materials import SqliteMaterialStore
from pmstudio.infrastructure.postgres.observations_repo import SqliteObservationRepository
from pmstudio.infrastructure.postgres.repositories import (
    SqliteDiscussionRepository,
    SqliteDocumentRepository,
    SqliteExplorationRepository,
    SqliteRoundRepository,
    SqliteTemplateRepository,
)
from tests.support.builders import NOW, PROJECT, make_document, make_round
from tests.support.doubles import SeqIds
from tests.support.postgres import open_direct

ALICE = UserId("u-alice")
BOB = UserId("u-bob")


@pytest.fixture()
def connection():  # type: ignore[no-untyped-def]
    conn = open_direct()
    yield conn
    conn.close()


def _material(text: str = "一条需求") -> ParsedMaterial:
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
                text=text,
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
        "SELECT owner_id FROM documents WHERE project_id = %s", (str(PROJECT),)
    ).fetchone()
    assert stamped["owner_id"] == str(ALICE)


def test_write_stamp_does_not_change_on_later_owner(connection):
    """越权 `save` 是**空操作**：owner 与内容都动不了（fix round Important-1）。

    owner 写入后不可改（§0.7）；再补一层物理边界——Bob 用同一个 `project_id` 覆盖
    Alice 的行时，`ON CONFLICT ... DO UPDATE ... WHERE owner_id = ?` 让整条 UPDATE 不生效。
    """

    docs = SqliteDocumentRepository(connection)
    docs.save(make_document(title="第一版"), owner=ALICE)
    docs.save(make_document(title="第二版"), owner=BOB)

    row = connection.execute(
        "SELECT owner_id, title FROM documents WHERE project_id = %s", (str(PROJECT),)
    ).fetchone()
    assert row["owner_id"] == str(ALICE)
    assert row["title"] == "第一版"


def test_rounds_save_cannot_overwrite_another_owners_row(connection):
    rounds = SqliteRoundRepository(connection)
    rounds.save(make_round(), owner=ALICE)
    with pytest.raises(LeaseLost):  # 越权 save：P3b 起是 fencing 失败，不再静默 no-op
        rounds.save(make_round(), owner=BOB)

    row = connection.execute(
        "SELECT owner_id FROM rounds WHERE round_id = %s", (str(RoundId("r1")),)
    ).fetchone()
    assert row["owner_id"] == str(ALICE)
    with pytest.raises(UnknownRound):
        rounds.get(RoundId("r1"), owner=BOB)


def test_materials_delete_is_scoped_to_the_owner(connection):
    store = SqliteMaterialStore(connection, ids=SeqIds())
    saved = store.put(
        project_id=PROJECT, parsed=_material(), sha256="d" * 64,
        content=b"bytes", now=NOW, owner=ALICE,
    )

    store.delete(saved.material_id, owner=BOB)  # 别人删不掉
    assert store.get(saved.material_id, owner=ALICE) is not None

    store.delete(saved.material_id, owner=ALICE)  # 自己能删
    assert store.get(saved.material_id, owner=ALICE) is None


def test_materials_in_place_edits_ignore_other_owners(connection):
    """`replace_chunks` / `refresh` 都按 owner 收窄 WHERE：别人的行改不动。"""

    store = SqliteMaterialStore(connection, ids=SeqIds())
    saved = store.put(
        project_id=PROJECT, parsed=_material(), sha256="e" * 64,
        content=b"orig", now=NOW, owner=ALICE,
    )

    owner_scope.set_owner_scope(BOB)
    with pytest.raises(KeyError):  # 改不到 = 找不到该 owner 的行
        store.replace_chunks(saved.material_id, _material("换过的正文"))
    with pytest.raises(KeyError):
        store.refresh(saved.material_id, parsed=_material("刷新正文"), content=b"new")

    owner_scope.set_owner_scope(ALICE)
    after = store.get(saved.material_id, owner=ALICE)
    assert after is not None
    assert after.parsed.chunks[0].text == "一条需求"
    assert store.raw_bytes(saved.material_id, owner=ALICE) == b"orig"


def test_templates_delete_is_scoped_and_never_removes_builtin(connection):
    from pmstudio.domain.shared.template import standard_template

    templates = SqliteTemplateRepository(connection)
    templates.save(standard_template(), builtin=True)
    templates.save(_custom_template(), builtin=False, owner=ALICE)

    with pytest.raises(UnknownTemplate):
        templates.delete("tpl-alice", owner=BOB)  # 别人的删不掉
    assert templates.get("tpl-alice", owner=ALICE) is not None

    with pytest.raises(UnknownTemplate):
        templates.delete("standard", owner=ALICE)  # builtin 不由此路删
    assert templates.get("standard", owner=ALICE).builtin is True

    templates.delete("tpl-alice", owner=ALICE)  # 自己的能删
    with pytest.raises(UnknownTemplate):
        templates.get("tpl-alice", owner=ALICE)


# ── 记忆三仓储跨 owner 读（fix round Important-2）────────────────────────────


def test_digest_reads_are_scoped_to_the_owner(connection):
    digests = SqliteDigestRepository(connection)
    digests.save(
        RoundDigest(
            round_id="r-a",
            project_id=str(PROJECT),
            summary="A摘要",
            sections_touched=(),
            features_addressed=(),
            key_outcomes=(),
        ),
        owner=ALICE,
    )

    # `get` 已删（详情内联定案，0 生产者）——owner 隔离只剩 `recent` 这一条读路。
    assert [d.summary for d in digests.recent(str(PROJECT), owner=ALICE)] == ["A摘要"]
    assert digests.recent(str(PROJECT), owner=BOB) == ()


def test_observations_reads_are_scoped_to_the_owner(connection):
    observations = SqliteObservationRepository(connection)
    observations.save(
        RoundObservation(
            obs_id="obs-a",
            round_id="r1",
            project_id=str(PROJECT),
            obs_type=ObservationType.SEAT_INSIGHT,
            title="保证金",
            detail="",
            source_ref="seat.x:O1",
            created_at=NOW.isoformat(),
        ),
        owner=ALICE,
    )

    # `get`/`search` 已删（详情内联定案，0 生产者）——只剩 `recent` 这一条读路。
    assert observations.recent(str(PROJECT), owner=BOB) == ()


def test_inspirations_reads_are_scoped_to_the_owner(connection):
    inspirations = SqliteInspirationRepository(connection)
    inspirations.save(
        InspirationItem(
            item_id="ins-a",
            project_id=str(PROJECT),
            origin=InspirationOrigin.DISCUSSION,
            statement="灵感",
        ),
        owner=ALICE,
    )

    # `get`/`search` 已删（详情内联定案，0 生产者）——owner 隔离只剩 `recent` 这一条读路。
    assert [i.item_id for i in inspirations.recent(str(PROJECT), owner=ALICE)] == ["ins-a"]
    assert inspirations.recent(str(PROJECT), owner=BOB) == ()
