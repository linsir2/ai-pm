"""探索接进主闭环（M8）：调研 → 收下 → 带进某一轮 → 账本里的 `simulated` 编号 / 种子回链。

只脚本化模型；其余全真：Harness 的真领域 + 真用例 + 真图（`RoundRunner`）。
"""

import json

from pmstudio.application.commands import RoundCommand
from pmstudio.domain.exploration.exploration import ExplorationScope, ScopeKind
from pmstudio.domain.provenance.evidence import EvidenceKind
from pmstudio.domain.shared.ids import ExplorationId, RoundId
from tests.support.builders import PROJECT, document_with_feature
from tests.support.harness import Harness

INQUIRY_REPLY = json.dumps(
    {
        "basis": "读了 s5 的功能描述",
        "hypotheses": [
            {
                "local_id": "H1",
                "statement": "运营做日报时按客户名找单",
                "grounding": "invented",
                "based_on": [],
                "falsification": "访谈没人这么找",
                "evidence_needed": "两个运营的访谈",
            }
        ],
        "blind_spots": [
            {"local_id": "B1", "statement": "归属未知", "next_step": "去问数据组"}
        ],
    },
    ensure_ascii=False,
)

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了文档与本轮输入",
        "claims": [
            {
                "text": "按调研的假设去验证",
                "raw_span": "调研说运营按客户名找单",
                "label": "need",
                "zone": "body",
                "section_id": "s10",
            }
        ],
        "seats": [{"seat_id": "seat.data_scale", "why": "与量级有关"}],
    },
    ensure_ascii=False,
)


def _harness() -> Harness:
    harness = Harness(replies={"inquiry": INQUIRY_REPLY, "claims": CLAIMS_REPLY})
    harness.seed_project()
    harness.documents.seed(document_with_feature())
    return harness


async def test_kept_material_flows_into_a_round_as_simulated_evidence() -> None:
    harness = _harness()
    started = await harness.exploration_runner().start(
        project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT)
    )
    kept = await harness.keep_candidate()(
        exploration_id=ExplorationId(started.exploration_id),
        candidate_id="H1",
        as_="material",
    )
    material_id = next(
        item for item in kept.candidates if item.candidate_id == "H1"
    ).kept_ref
    assert material_id

    view = await harness.runner().run(
        RoundCommand(
            kind="start",
            project_id=str(PROJECT),
            user_input="调研说运营按客户名找单，先记下来",
            material_ids=(material_id,),
        )
    )

    assert [item.material_id for item in view.materials] == [material_id]
    ledger = harness.evidences.get(PROJECT)
    simulated = [item for item in ledger.evidences() if item.kind is EvidenceKind.SIMULATED]
    assert len(simulated) == 1
    # 账本里的外部指称沿用 `_external_ref` 的约定：非 URL 的 source_ref 追加块号
    assert (
        simulated[0].source.external_ref
        == f"exploration:{started.exploration_id}#H1#c00"
    )


async def test_kept_seed_is_recorded_on_the_next_round() -> None:
    harness = _harness()
    started = await harness.exploration_runner().start(
        project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT)
    )
    await harness.keep_candidate()(
        exploration_id=ExplorationId(started.exploration_id),
        candidate_id="B1",
        as_="seed",
    )

    view = await harness.runner().run(
        RoundCommand(
            kind="start",
            project_id=str(PROJECT),
            user_input="调研说运营按客户名找单；顺便去问数据组客户合并的归属",
            seed_ids=(f"exploration:{started.exploration_id}#B1",),
        )
    )

    round_ = harness.rounds.get(RoundId(view.round_id))
    assert [
        (ref.origin, ref.ref_id, ref.item_id) for ref in round_.seeds
    ] == [("exploration", str(started.exploration_id), "B1")]
