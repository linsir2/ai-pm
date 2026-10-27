"""P2.0：材料冻结判据在**真 SQLite** 上走一遍（不是内存替身）。

三种引用形状都要挡住"删"与"就地改"：
- 已投影的块（带 E 号）——编号指向它，删了/改了编号就改指；
- 只被手勾、还没投影（没有 E 号）——用户已经明确要它，冻结判据看 `pinned_materials`；
- 手动带进的块——同上，看 `manual_chunks`。

`ReparseMaterial` 不走 409：被引用时**另出一份变体**，旧 id 原地冻结（parser_version 与
E 号一个字节都不动），二次重解析靠变体键幂等，不会生出第三份。
"""

from datetime import UTC, datetime

import pytest

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.errors import MaterialFrozen
from pmstudio.application.use_cases import DeleteMaterial, ReparseMaterial, list_material_views
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.round.round import MaterialRef, Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, RoundId
from pmstudio.domain.shared.template import standard_template
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteEvidenceRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.tools.extract import LocalMaterialParser
from tests.e2e.conftest import E2E_USERNAME, build_app
from tests.support.api_client import async_client
from tests.support.builders import NOW, PROJECT
from tests.support.doubles import FixedClock
from tests.support.postgres import open_direct

RAW = "# 目标\n导出要有权限\n# 边界\n只给管理员\n"
OLD_VERSION = "v0-旧解析器"


def _old_parse() -> ParsedMaterial:
    return ParsedMaterial(
        origin="upload",
        title="报告.md",
        source_ref="attachment:报告.md",
        media_type="text/markdown",
        bytes_len=len(RAW.encode("utf-8")),
        chunks=(
            MaterialChunk(
                chunk_id="local-text#c00",
                ordinal=0,
                text=RAW,
                heading_path=(),
                anchor="旧锚点",
            ),
        ),
        parser_id="local-text",
        parser_version=OLD_VERSION,
    )


def _world():  # type: ignore[no-untyped-def]
    connection = open_direct()
    ids = UuidIdGenerator()
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT,
        title="材料冻结 e2e",
        now=NOW,
        template=standard_template(),
    )
    store = SqliteMaterialStore(connection, ids=ids)
    return (
        connection,
        store,
        SqliteRoundRepository(connection),
        SqliteEvidenceRepository(connection),
        SqliteWorkspaceIndex(connection),
        PgUnitOfWork(connection),
        LocalMaterialParser(),
    )


def _put_old_material(store: SqliteMaterialStore) -> str:  # type: ignore[no-untyped-def]
    return store.put(
        project_id=PROJECT,
        parsed=_old_parse(),
        sha256="a" * 64,
        content=RAW.encode("utf-8"),
        now=NOW,
    ).material_id


def _pinned_round(rounds: SqliteRoundRepository, material_id: str) -> None:  # type: ignore[no-untyped-def]
    round_ = Round(
        round_id=RoundId("r-pinned"),
        project_id=PROJECT,
        user_input="手勾这份",
        created_at=NOW,
        step=Step.DONE,
        waiting_on=WaitingOn.NONE,
    )
    round_.pin_materials((material_id,))
    rounds.save(round_)


def _projected_round(rounds: SqliteRoundRepository, *, material_id: str, chunk_id: str) -> None:  # type: ignore[no-untyped-def]
    round_ = Round(
        round_id=RoundId("r-projected"),
        project_id=PROJECT,
        user_input="按这份写",
        created_at=NOW,
        step=Step.DONE,
        waiting_on=WaitingOn.NONE,
    )
    round_.add_materials(
        (MaterialRef(material_id=material_id, chunk_id=chunk_id, eid=EvidenceId("E4")),)
    )
    rounds.save(round_)


def _reparse(store, parser, index, uow):  # type: ignore[no-untyped-def]
    return ReparseMaterial(
        materials=store,
        parser=parser,
        index=index,
        uow=uow,
        clock=FixedClock(),
    )


async def test_a_pin_only_material_is_frozen_on_real_pg():
    connection, store, rounds, _, index, uow, parser = _world()
    try:
        material_id = _put_old_material(store)
        _pinned_round(rounds, material_id)

        with pytest.raises(MaterialFrozen) as error:
            await DeleteMaterial(materials=store, parser=parser, index=index, uow=uow)(
                project_id=PROJECT, material_id=material_id
            )

        assert "手勾" in error.value.message
        assert store.get(material_id) is not None, "冻结的材料必须原样还在"
        assert index.material_references(PROJECT)[material_id] == ("手勾",), (
            "引用列要如实带上手勾哨兵（它还没有 E 号）"
        )
        views = list_material_views(
            project_id=PROJECT, materials=store, parser=parser, index=index
        )
        assert views[0].frozen is True
        assert views[0].eids == (), "引用列里的哨兵不是编号，不能当 E 号显示"
    finally:
        connection.close()


async def test_reparsing_a_pin_only_material_makes_exactly_one_variant(tmp_path):
    connection, store, rounds, _, index, uow, parser = _world()
    try:
        material_id = _put_old_material(store)
        _pinned_round(rounds, material_id)
        reparse = _reparse(store, parser, index, uow)

        first = await reparse(project_id=PROJECT, material_id=material_id)

        assert len(first) == 2
        old = store.get(material_id)
        assert old is not None
        assert old.parsed.parser_version == OLD_VERSION
        assert old.parsed.chunks[0].anchor == "旧锚点"
        fresh_id = next(item.material_id for item in first if item.material_id != material_id)
        fresh = store.get(fresh_id)
        assert fresh is not None
        assert fresh.parsed.parser_version != OLD_VERSION
        assert index.material_references(PROJECT)[material_id] == ("手勾",)
        assert index.material_references(PROJECT).get(fresh_id) is None

        second = await reparse(project_id=PROJECT, material_id=material_id)

        assert len(second) == len(first) == 2, "变体键幂等：二次重解析不生成第三份"
        assert {item.material_id for item in second} == {material_id, fresh_id}
    finally:
        connection.close()


async def test_reparsing_a_projected_material_keeps_the_old_eid_and_parser_version(tmp_path):
    connection, store, rounds, _, index, uow, parser = _world()
    try:
        material_id = _put_old_material(store)
        chunk_id = store.get(material_id).parsed.chunks[0].chunk_id
        _projected_round(rounds, material_id=material_id, chunk_id=chunk_id)
        reparse = _reparse(store, parser, index, uow)

        first = await reparse(project_id=PROJECT, material_id=material_id)

        assert len(first) == 2
        old = store.get(material_id)
        assert old.parsed.parser_version == OLD_VERSION
        assert index.material_references(PROJECT)[material_id] == ("E4",), (
            "旧 id 上的 E 号必须原地不动"
        )
        old_view = next(item for item in first if item.material_id == material_id)
        assert old_view.eids == ("E4",)
        assert old_view.frozen is True

        fresh_id = next(item.material_id for item in first if item.material_id != material_id)
        assert store.get(fresh_id).parsed.parser_version != OLD_VERSION
        assert index.material_references(PROJECT).get(fresh_id) is None

        second = await reparse(project_id=PROJECT, material_id=material_id)

        assert len(second) == 2
        assert {item.material_id for item in second} == {material_id, fresh_id}
    finally:
        connection.close()


async def test_the_api_returns_409_for_a_pin_only_material(tmp_path):
    app, _ = build_app(tmp_path, replies={})
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        saved = (
            await client.post(
                f"/api/projects/{project_id}/attachments",
                files={"file": ("需求.md", "# 目标\n导出".encode(), "text/markdown")},
            )
        ).json()

        owner = app.state.auth.users.get_by_name(E2E_USERNAME).user_id
        connection = app.state.auth.users._connection
        round_ = Round(
            round_id=RoundId("r-pinned-api"),
            project_id=ProjectId(project_id),
            user_input="手勾这份",
            created_at=datetime.now(UTC),
            step=Step.DONE,
            waiting_on=WaitingOn.NONE,
        )
        round_.pin_materials((saved["material_id"],))
        SqliteRoundRepository(connection).save(round_, owner=owner)
        references = SqliteWorkspaceIndex(connection).material_references(
            ProjectId(project_id), owner=owner
        )
        assert references[saved["material_id"]] == ("手勾",)

        refused = await client.delete(
            f"/api/projects/{project_id}/attachments/{saved['material_id']}"
        )
        listed = await client.get(f"/api/projects/{project_id}/attachments")

    assert refused.status_code == 409
    body = refused.json()
    assert body["code"] == "material_frozen"
    assert "手勾" in body["message"]
    assert [item["material_id"] for item in listed.json()] == [saved["material_id"]]
