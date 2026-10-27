"""探索仓储：全字段往返、坏行跳过、列表排序与截断。"""

from datetime import UTC, datetime

import pytest
from psycopg.types.json import Jsonb

from pmstudio.application.errors import UnknownExploration
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationScope,
    ExplorationStatus,
    ScopeKind,
)
from pmstudio.domain.exploration.item import BlindSpot, Grounding, Hypothesis
from pmstudio.domain.shared.ids import EvidenceId, ExplorationId, ProjectId
from pmstudio.infrastructure.postgres import owner_scope
from pmstudio.infrastructure.sqlite.repositories import SqliteExplorationRepository
from tests.support.postgres import open_direct

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture()
def store():  # type: ignore[no-untyped-def]
    connection = open_direct()
    return SqliteExplorationRepository(connection), connection


def _done() -> Exploration:
    run = Exploration.start(
        exploration_id=ExplorationId("x-1"),
        project_id=ProjectId("p1"),
        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
        now=NOW,
    )
    run.complete(
        hypotheses=(
            Hypothesis(
                local_id="H1",
                statement="运营按客户名找单",
                grounding=Grounding.INVENTED,
                based_on=(),
                falsification="访谈没人这么找",
                evidence_needed="两个访谈",
            ),
            Hypothesis(
                local_id="H2",
                statement="月末才全量导出",
                grounding=Grounding.DOCUMENT,
                based_on=(EvidenceId("E7"),),
                falsification="月中也有批量需求",
                evidence_needed="一周的导出日志",
            ),
        ),
        blind_spots=(BlindSpot(local_id="B1", statement="归属未知", next_step="问数据组"),),
        overflow_count=2,
    )
    run.keep("H1", as_="material", material_id="m-1")
    run.dismiss("B1")
    return run


def test_roundtrip_keeps_everything(store) -> None:  # type: ignore[no-untyped-def]
    repo, _ = store
    repo.save(_done())
    restored = repo.get(ExplorationId("x-1"))

    assert restored.status is ExplorationStatus.DONE
    assert restored.scope.kind is ScopeKind.DOCUMENT
    assert restored.overflow_count == 2
    assert [(h.local_id, h.grounding.value) for h in restored.hypotheses] == [
        ("H1", "invented"),
        ("H2", "document"),
    ]
    assert restored.hypotheses[1].based_on == (EvidenceId("E7"),)
    assert restored.blind_spots[0].next_step == "问数据组"
    candidates = {item.candidate_id: item for item in restored.candidates()}
    assert candidates["H1"].kept_as == "material"
    assert candidates["H1"].kept_ref == "m-1"
    assert candidates["B1"].state.value == "dismissed"


def test_unknown_exploration_raises(store) -> None:  # type: ignore[no-untyped-def]
    repo, _ = store
    with pytest.raises(UnknownExploration):
        repo.get(ExplorationId("x-9"))


def test_list_is_newest_first_and_limited(store) -> None:  # type: ignore[no-untyped-def]
    repo, _ = store
    first = Exploration.start(
        exploration_id=ExplorationId("x-1"),
        project_id=ProjectId("p1"),
        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
        now=NOW,
    )
    second = Exploration.start(
        exploration_id=ExplorationId("x-2"),
        project_id=ProjectId("p1"),
        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
        now=NOW.replace(minute=30),
    )
    # v17 起同一项目只许一趟 running（部分唯一索引）；这条用例只验列表排序，
    # 两趟都先跑完再落库。
    first.complete(hypotheses=(), blind_spots=())
    second.complete(hypotheses=(), blind_spots=())
    repo.save(first)
    repo.save(second)

    assert [str(item.exploration_id) for item in repo.list_for_project(ProjectId("p1"))] == [
        "x-2",
        "x-1",
    ]
    assert len(repo.list_for_project(ProjectId("p1"), limit=1)) == 1


def test_a_corrupted_row_is_skipped_not_raised(store) -> None:  # type: ignore[no-untyped-def]
    repo, connection = store
    connection.execute(
        "INSERT INTO explorations (exploration_id, project_id, owner_id, kind, status, payload,"
        " created_at, updated_at) VALUES ('x-bad','p1',%s,'inquiry','done',%s,"
        "'2026-10-06T12:00:00+00:00','2026-10-06T12:00:00+00:00')",
        (owner_scope.current_owner_scope(), Jsonb("oops")),
    )
    assert repo.list_for_project(ProjectId("p1")) == ()
