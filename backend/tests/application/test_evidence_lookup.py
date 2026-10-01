"""按编号查处方（`04` F5）：界面查不到的编号，后端要能给出原话、链条与"现在被谁用着"。"""

import pytest

from pmstudio.application.errors import UnknownEvidenceId
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.shared.ids import EntryId, RoundId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import NOW, PROJECT, make_document
from tests.support.harness import Harness


@pytest.fixture()
def world():
    """一个带 E1（用户原话）的项目，文档里有一条内容引用了它。"""

    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    ledger = harness.evidences.get(PROJECT)
    eid = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=RoundId("r1"), raw_span="想做个订单导出的功能"),
        now=NOW,
    )
    harness.evidences.save(ledger)

    document = make_document()
    document.apply(
        [
            WriteInstruction(
                instruction_id="i1",
                entry_id=EntryId("en-one-liner"),
                op=WriteOp.ADD,
                target=Target.feature_field("order-export", "one_liner"),
                text="把订单导出成表格",
                refs=(eid,),
                kind=BlockKind.TEXT,
            )
        ],
        now=NOW,
        summary="落一条内容",
        authorized_new_features=frozenset({"order-export"}),
    )
    harness.documents.seed(document)
    return harness, eid


async def test_lookup_gives_text_original_words_and_round(world):
    harness, eid = world

    view = await harness.get_evidence()(project_id=PROJECT, eid=str(eid))

    assert (view.eid, view.kind, view.kind_label) == (str(eid), "user_input", "用户输入")
    assert view.text == "要做一个订单导出功能"
    assert view.raw_span == "想做个订单导出的功能"  # 只在界面给，不进导出（04 D5）
    assert view.round_id == "r1"
    assert view.summary.startswith("要做一个订单导出功能")


async def test_lookup_reports_who_uses_this_evidence(world):
    harness, eid = world

    view = await harness.get_evidence()(project_id=PROJECT, eid=str(eid))

    assert view.used_by == ("en-one-liner",)
    assert [item.eid for item in view.chain] == [str(eid)]


async def test_lookup_of_an_unknown_eid_is_not_found(world):
    harness, _ = world

    with pytest.raises(UnknownEvidenceId):
        await harness.get_evidence()(project_id=PROJECT, eid="E404")


async def test_lookup_keeps_the_chain_of_a_derived_judgment(world):
    """席位判断的链条要能回溯到用户输入（可追溯不变式 GB1 的可视化）。"""

    harness, e1 = world
    ledger = harness.evidences.get(PROJECT)
    judged = ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="单次导出应在 60 秒内完成",
        source=EvidenceSource(
            seat_id="seat.data_scale",
            derived_from=(e1,),
            rationale="量级在 1 万级",
        ),
        now=NOW,
    )
    harness.evidences.save(ledger)

    view = await harness.get_evidence()(project_id=PROJECT, eid=str(judged))

    assert view.kind == "agent_derived"
    assert view.seat_id == "seat.data_scale"
    assert view.derived_from == (str(e1),)
    assert [item.eid for item in view.chain] == [str(judged), str(e1)]


async def test_a_web_result_points_back_at_its_url(world):
    """联网检索结果和上传材料走同一条出处通道：点编号要能看到它是**哪个网址**（C-2b）。"""

    harness, _ = world
    ledger = harness.evidences.get(PROJECT)
    found = ledger.record(
        kind=EvidenceKind.EXTERNAL,
        text="行业通行做法是 RBAC：角色绑权限，用户绑角色。",
        source=EvidenceSource(external_ref="https://example.com/rbac"),
        now=NOW,
    )
    harness.evidences.save(ledger)

    view = await harness.get_evidence()(project_id=PROJECT, eid=str(found))

    assert view.kind == "external"
    assert view.kind_label == "外部材料"
    assert view.external_ref == "https://example.com/rbac"
