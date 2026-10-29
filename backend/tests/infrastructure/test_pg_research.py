"""真 PostgreSQL：研究行的 payload 往返与"同项目只允许一趟在跑"。

工具/综合那一段的编排在 `tests/application/test_research.py`（内存端口）；
这一层只验两件只有真库才成立的事：

1. `research` 键能完整落库读回（JSONB + 新领域对象）；
2. 同项目第二条 RUNNING 行被部分唯一索引挡住，并翻成 `ExplorationBusy`（409 的根）。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from psycopg.types.json import Jsonb

from pmstudio.application.errors import ExplorationBusy
from pmstudio.application.project_research import StartWebResearch
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationKind,
    ExplorationScope,
    ExplorationStatus,
    ScopeKind,
)
from pmstudio.domain.exploration.research import (
    ResearchFinding,
    ResearchReport,
    ResearchSource,
)
from pmstudio.domain.shared.ids import EvidenceId, ExplorationId
from pmstudio.infrastructure.postgres.database import connect
from pmstudio.infrastructure.postgres.leases import PgLeaseStore
from pmstudio.infrastructure.postgres.owner_scope import clear_owner_scope, set_owner_scope
from pmstudio.infrastructure.postgres.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteExplorationRepository,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.conftest import TEST_OWNER
from tests.support.builders import NOW, PROJECT, document_with_feature
from tests.support.doubles import FixedClock, RecordingPublisher
from tests.support.postgres import PgTestDatabase, truncate_data_tables

OWNER = TEST_OWNER


@dataclass(frozen=True, slots=True)
class _Database:
    pg: PgTestDatabase


@pytest.fixture(scope="module")
def database(pg_test_database: PgTestDatabase) -> _Database:
    return _Database(pg_test_database)


@pytest.fixture()
def conn(database: _Database) -> Iterator[object]:
    truncate_data_tables(database.pg)
    with connect(database.pg.direct_url) as connection:
        yield connection
    truncate_data_tables(database.pg)


def _research(exploration_id: str = "x-1") -> Exploration:
    return Exploration.start(
        exploration_id=ExplorationId(exploration_id),
        project_id=PROJECT,
        scope=ExplorationScope(kind=ScopeKind.TOPIC, text="批量导出的性能上限"),
        now=NOW,
        kind=ExplorationKind.WEB_RESEARCH,
    )


def _report() -> ResearchReport:
    return ResearchReport(
        findings=(
            ResearchFinding(
                finding_id="F1",
                statement="主流同类产品 60 秒上限",
                refs=(EvidenceId("E7"),),
            ),
        ),
        sources=(
            ResearchSource(
                eid=EvidenceId("E7"),
                material_id="m-1",
                title="白皮书",
                url="https://example.com/a",
                excerpt="同类产品默认 60 秒上限。",
            ),
        ),
        open_questions=("要不要断点续传？",),
        notes=("工具调用 2 次",),
    )


def _toolbox():  # type: ignore[no-untyped-def]
    """`create` 只用到 `ready`（装配错在建行之前就要说清）。"""

    from pmstudio.infrastructure.tools.extract import LocalMaterialParser
    from pmstudio.infrastructure.tools.research_tools import ResearchToolbox

    class _Search:
        async def search(self, *, query: str, limit: int):
            return ()

    class _Fetch:
        async def fetch(self, *, url: str):
            raise AssertionError("这个用例不出网")

    return ResearchToolbox(search=_Search(), fetcher=_Fetch(), parser=LocalMaterialParser())


def _research_use_case(connection) -> StartWebResearch:  # type: ignore[no-untyped-def]
    explorations = SqliteExplorationRepository(connection)
    return StartWebResearch(
        documents=SqliteDocumentRepository(connection),
        explorations=explorations,
        exploration_writer=explorations,
        evidence=SqliteEvidenceRepository(connection),
        evidence_writer=SqliteEvidenceRepository(connection),
        materials=None,  # create 不碰材料；execute 才用
        uow=PgUnitOfWork(connection),
        clock=FixedClock(),
        ids=UuidIdGenerator(),
        events=RecordingPublisher(),
        gateway=None,
        prompts=None,
        synthesizer=None,
        toolbox=_toolbox,
        leases=PgLeaseStore(connection),
    )


def test_research_payload_round_trips_through_postgres(conn) -> None:  # type: ignore[no-untyped-def]
    set_owner_scope(OWNER)
    try:
        SqliteDocumentRepository(conn).save(document_with_feature())
        repository = SqliteExplorationRepository(conn)
        exploration = _research()
        exploration.complete_research(_report())
        repository.save(exploration)

        loaded = repository.get(ExplorationId("x-1"))

        assert loaded.kind is ExplorationKind.WEB_RESEARCH
        assert loaded.status is ExplorationStatus.DONE
        assert loaded.scope.kind is ScopeKind.TOPIC
        assert loaded.scope.text == "批量导出的性能上限"
        assert loaded.research is not None
        source = loaded.research.sources[0]
        assert str(source.eid) == "E7"
        assert source.url == "https://example.com/a"
        assert loaded.research.findings[0].refs == (EvidenceId("E7"),)
        assert loaded.research.notes == ("工具调用 2 次",)
    finally:
        clear_owner_scope()


async def test_a_second_running_research_in_the_same_project_is_busy(conn) -> None:  # type: ignore[no-untyped-def]
    set_owner_scope(OWNER)
    try:
        SqliteDocumentRepository(conn).save(document_with_feature())
        research = _research_use_case(conn)

        await research.create(project_id=PROJECT, topic="第一趟", owner=OWNER)

        with pytest.raises(ExplorationBusy):
            await research.create(project_id=PROJECT, topic="第二趟", owner=OWNER)

        rows = conn.execute(
            "SELECT count(*) AS n FROM explorations WHERE kind = %s AND status = %s",
            (ExplorationKind.WEB_RESEARCH.value, ExplorationStatus.RUNNING.value),
        ).fetchone()
        assert rows is not None
        assert int(rows["n"]) == 1
    finally:
        clear_owner_scope()


def test_a_corrupt_research_row_is_skipped_instead_of_breaking_the_list(conn) -> None:  # type: ignore[no-untyped-def]
    """坏行跳过不抛（仓储口径）：研究 payload 坏了只丢那一行，不把列表打成 500。"""

    set_owner_scope(OWNER)
    try:
        SqliteDocumentRepository(conn).save(document_with_feature())
        repository = SqliteExplorationRepository(conn)
        good = _research("x-good")
        good.complete_research(_report())
        repository.save(good)

        # 直接写一行"done 但没有 research"的坏 payload（模拟被外部改坏的库）
        payload = {
            "scope": {"kind": "topic", "ref": None, "text": "坏行"},
            "hypotheses": [],
            "blind_spots": [],
            "states": {},
            "kept": {},
            "kept_ref": {},
            "failure": None,
            "abandon_reason": None,
            "overflow_count": 0,
            "research": {"sources": [{"eid": "E9"}]},
        }
        conn.execute(
            """
            INSERT INTO explorations (exploration_id, project_id, owner_id, kind,
                                      status, payload, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, now(), now())
            """,
            ("x-bad", str(PROJECT), str(OWNER), "web_research", "running", Jsonb(payload)),
        )
        conn.commit()

        listed = repository.list_for_project(PROJECT)

        assert [str(item.exploration_id) for item in listed] == ["x-good"]
    finally:
        clear_owner_scope()


def test_a_done_research_without_a_report_is_skipped_not_500(conn) -> None:  # type: ignore[no-untyped-def]
    """聚合守卫在**读盘**这一侧也必须走"坏行跳过"（评审判据）。

    `done` 却没有报告是一份被读丢的数据：它不能安静出现在列表里，更不能让
    `list_for_project` / `clear_stale` 整页 422。序列化层把 `DomainError` 翻成
    `ValueError`，仓储的坏行网才接得住。
    """

    set_owner_scope(OWNER)
    try:
        SqliteDocumentRepository(conn).save(document_with_feature())
        repository = SqliteExplorationRepository(conn)
        good = _research("x-good")
        good.complete_research(_report())
        repository.save(good)

        payload = {
            "scope": {"kind": "topic", "ref": None, "text": "坏行"},
            "hypotheses": [],
            "blind_spots": [],
            "states": {},
            "kept": {},
            "kept_ref": {},
            "failure": None,
            "abandon_reason": None,
            "overflow_count": 0,
            "research": None,
        }
        conn.execute(
            """
            INSERT INTO explorations (exploration_id, project_id, owner_id, kind,
                                      status, payload, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, now(), now())
            """,
            ("x-done-no-report", str(PROJECT), str(OWNER), "web_research", "done",
             Jsonb(payload)),
        )
        conn.commit()

        listed = repository.list_for_project(PROJECT)

        assert [str(item.exploration_id) for item in listed] == ["x-good"]
    finally:
        clear_owner_scope()
