"""带进本轮（P1.3）：pin + `manual_chunks`，**不发 E 号**。

E 号在投影时发（P2）；这里只证明"用户点的块进了本轮的证据池",
并且状态/归属/上限都拦得住。
"""

from dataclasses import replace

import pytest

from pmstudio.application.errors import (
    RoundNotAttachable,
    UnknownMaterial,
    WebMaterialNotAttachable,
)
from pmstudio.application.use_cases import AttachMaterials
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import (
    DraftId,
    EntryId,
    EvidenceId,
    ProposalId,
    UserId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.template import standard_template
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.sqlite.repositories import SqliteRoundRepository
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.application.test_materials import make_material
from tests.support.builders import NOW, PROJECT, ROUND_ID, make_round
from tests.support.harness import Harness

OWNER = UserId("u-attach")


def use_case(harness: Harness) -> AttachMaterials:
    return AttachMaterials(
        rounds=harness.rounds,
        round_writer=harness.rounds,
        materials=harness.materials,
        uow=harness.uow,
    )


def harness_with(packet) -> Harness:
    harness = Harness(replies={})
    harness.seed_project()
    harness.materials.seed(packet)
    return harness


async def a_round(harness: Harness):
    return await harness.start_round().create(
        project_id=PROJECT, user_input="导出权限怎么定", material_ids=()
    )


def draft_for(round_id=ROUND_ID) -> Draft:
    return Draft(
        draft_id=DraftId("d1"),
        round_id=round_id,
        proposals=(
            Proposal(
                proposal_id=ProposalId("p1"),
                entry_id=EntryId("r1#01"),
                op=WriteOp.ADD,
                target=Target.body("s5"),
                text="导出要有权限",
                origin=Origin(OriginType.USER, (EvidenceId("E1"),)),
                kind=BlockKind.TEXT,
            ),
        ),
    )


async def test_attach_pins_and_records_manual_chunks_without_eid():
    packet = make_material("权限：管理员才能导出", "审计：导出要留痕")
    harness = harness_with(packet)
    round_id = await a_round(harness)

    await use_case(harness)(
        round_id=round_id, material_id=packet.material_id, chunk_ids=("m1#c01",)
    )

    round_ = harness.rounds.get(round_id)
    assert round_.pinned_materials == ("m1",)
    assert [(item.material_id, item.chunk_id) for item in round_.manual_chunks] == [
        ("m1", "m1#c01")
    ]
    assert round_.materials == (), "还没投影，不许提前发 E 号"


async def test_attach_is_idempotent():
    packet = make_material("权限：管理员才能导出")
    harness = harness_with(packet)
    round_id = await a_round(harness)

    for _ in range(2):
        await use_case(harness)(
            round_id=round_id, material_id="m1", chunk_ids=("m1#c00",)
        )

    assert len(harness.rounds.get(round_id).manual_chunks) == 1


async def test_attach_rejects_an_unknown_material():
    harness = harness_with(make_material("权限：管理员才能导出"))
    round_id = await a_round(harness)

    with pytest.raises(UnknownMaterial):
        await use_case(harness)(
            round_id=round_id, material_id="m9", chunk_ids=("m9#c00",)
        )


async def test_attach_rejects_a_web_material():
    packet = make_material("联网来的")
    packet = replace(packet, parsed=replace(packet.parsed, origin="web_search"))
    harness = harness_with(packet)
    round_id = await a_round(harness)

    with pytest.raises(WebMaterialNotAttachable):
        await use_case(harness)(
            round_id=round_id, material_id="m1", chunk_ids=("m1#c00",)
        )


async def test_attach_rejects_a_chunk_outside_the_material():
    harness = harness_with(make_material("权限：管理员才能导出"))
    round_id = await a_round(harness)

    with pytest.raises(ShapeError):
        await use_case(harness)(
            round_id=round_id, material_id="m1", chunk_ids=("m1#c99",)
        )


async def test_attach_rejects_after_the_draft_card():
    packet = make_material("权限：管理员才能导出")
    harness = harness_with(packet)
    round_ = make_round()
    round_.integrate(draft=draft_for())
    harness.rounds.seed(round_)

    with pytest.raises(RoundNotAttachable):
        await use_case(harness)(
            round_id=round_.round_id, material_id="m1", chunk_ids=("m1#c00",)
        )


async def test_attach_rejects_more_than_three_chunks_in_one_call():
    packet = make_material("a", "b", "c", "d")
    harness = harness_with(packet)
    round_id = await a_round(harness)

    with pytest.raises(ShapeError):
        await use_case(harness)(
            round_id=round_id,
            material_id="m1",
            chunk_ids=("m1#c00", "m1#c01", "m1#c02", "m1#c03"),
        )


async def test_attach_rejects_more_than_ten_manual_chunks_per_round():
    packet = make_material(*[f"第 {index} 段" for index in range(12)])
    harness = harness_with(packet)
    round_id = await a_round(harness)

    for start in range(0, 9, 3):
        await use_case(harness)(
            round_id=round_id,
            material_id="m1",
            chunk_ids=tuple(f"m1#c{index:02d}" for index in range(start, start + 3)),
        )
    with pytest.raises(ShapeError):
        await use_case(harness)(
            round_id=round_id,
            material_id="m1",
            chunk_ids=("m1#c09", "m1#c10", "m1#c11"),
        )


async def test_attach_round_trips_through_real_sqlite(tmp_path):
    """P：真 SQLite + 真材料仓 + 真轮次仓。"""

    connection = connect(tmp_path / "attach.db")
    ids = UuidIdGenerator()
    store = SqliteMaterialStore(connection, ids=ids)
    rounds = SqliteRoundRepository(connection)
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT, title="带料核验", now=NOW, template=standard_template()
    )
    parsed = make_material("权限：管理员才能导出", "审计：导出要留痕").parsed
    packet = store.put(
        project_id=PROJECT,
        parsed=parsed,
        sha256="a" * 64,
        content=b"raw",
        now=NOW,
    )
    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="导出", now=NOW
    )
    rounds.save(round_)

    await AttachMaterials(
        rounds=rounds,
        round_writer=rounds,
        materials=store,
        uow=SqliteUnitOfWork(connection),
    )(
        round_id=ROUND_ID,
        material_id=packet.material_id,
        chunk_ids=(packet.parsed.chunks[0].chunk_id,),
    )

    again = rounds.get(ROUND_ID)
    assert again.pinned_materials == (packet.material_id,)
    assert [item.chunk_id for item in again.manual_chunks] == [
        packet.parsed.chunks[0].chunk_id
    ]
    assert again.materials == ()
    connection.close()
