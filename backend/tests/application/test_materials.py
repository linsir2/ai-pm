"""材料进这一轮：选块 → 只给带进去的块发号 → 三个智能体的提示词里都看得到。

口径（`02` §11.0.28）：
1. **编号 == 带进提示词的块**（没带进去的块没有编号，但仍然在材料表里、检索得到）；
2. 材料是**外部材料**，不是用户说的话（提示词里明写；来源分组靠既有不变量兜住）；
3. 没有材料时**不留空节**，写"（这一轮没有外部材料）"。
"""

import json

import pytest

from pmstudio.application.dto import MaterialChunk, MaterialPacket, ParsedMaterial
from pmstudio.application.errors import UnsupportedMaterial  # noqa: F401  (接口存在性)
from pmstudio.application.use_cases import _unreferenced_materials_for
from pmstudio.domain.authoring.instruction import WriteInstruction  # noqa: F401
from pmstudio.domain.provenance.evidence import EvidenceKind
from pmstudio.domain.round.draft import Origin, OriginType, Proposal
from pmstudio.domain.round.round import MaterialRef, Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId, RoundId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from tests.support.builders import NOW, PROJECT, SEAT
from tests.support.harness import Harness


def claims_reply() -> str:
    """最小可用的 ClaimsPacket（与 `test_start_round.py` 同一个形状）。"""

    return json.dumps(
        {
            "basis": "读了 s1–s13 全文（空文档）与本轮输入",
            "claims": [
                {
                    "text": "按这份材料写",
                    "raw_span": "按这份材料写",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": "seat.data_scale", "why": "你说到了数据量"}],
        },
        ensure_ascii=False,
    )


def make_material(*texts: str, material_id: str = "m1", title: str = "需求.md") -> MaterialPacket:
    return MaterialPacket(
        material_id=material_id,
        project_id=str(PROJECT),
        parsed=ParsedMaterial(
            origin="upload",
            title=title,
            source_ref=f"attachment:{title}",
            media_type="text/markdown",
            bytes_len=100,
            chunks=tuple(
                MaterialChunk(
                    chunk_id=f"{material_id}#c{index:02d}",
                    ordinal=index,
                    text=text,
                    heading_path=(),
                    anchor=f"第 {index + 1} 行",
                )
                for index, text in enumerate(texts)
            ),
            parser_id="local-text",
            parser_version="v1",
        ),
        created_at=NOW,
    )


def harness_with_material(*texts: str) -> Harness:
    harness = Harness(replies={"claims": claims_reply()})
    harness.seed_project()
    harness.materials.seed(make_material(*texts))
    return harness


async def started_round(
    harness: Harness, *, user_input: str = "按这份材料写", material_ids=("m1",)
):
    """建轮 + 落主张（用真正的 StartRound，不手搓状态）。"""

    use_case = harness.start_round()
    round_id = await use_case.create(
        project_id=PROJECT, user_input=user_input, material_ids=list(material_ids)
    )
    await use_case.analyze(round_id=round_id)
    return round_id


async def test_starting_a_round_records_one_external_evidence_per_selected_chunk():
    harness = harness_with_material("目标：导出要有权限", "边界：只给管理员")

    round_id = await started_round(harness)

    ledger = harness.evidences.get(PROJECT)
    externals = [item for item in ledger.evidences() if item.kind is EvidenceKind.EXTERNAL]
    assert [item.text for item in externals] == ["目标：导出要有权限", "边界：只给管理员"]
    assert externals[0].source.external_ref == "attachment:需求.md#c00"

    round_ = harness.rounds.get(RoundId(round_id))
    assert [ref.chunk_id for ref in round_.materials] == ["m1#c00", "m1#c01"]
    assert [str(ref.eid) for ref in round_.materials] == [str(item.eid) for item in externals]


async def test_only_the_chunks_that_fit_the_budget_get_numbers():
    """预算挤不下的块**不发号**（否则账本里全是模型引不到的编号）。"""

    harness = harness_with_material(*["字" * 500 for _ in range(20)])

    round_id = await started_round(harness)

    round_ = harness.rounds.get(RoundId(round_id))
    assert 0 < len(round_.materials) < 20


async def test_the_analyzer_prompt_carries_the_material_with_its_numbers():
    harness = harness_with_material("目标：导出要有权限")

    await started_round(harness)

    slots = [item for item in harness.prompts.calls if item[0] == "analyzer"][-1][1]
    assert "需求.md" in slots["external_material"]
    assert "第 1 行" in slots["external_material"]
    assert "不是你" in slots["external_material"] or "不是用户说的话" in slots["external_material"]


async def test_a_round_without_materials_says_so_instead_of_leaving_it_blank():
    harness = Harness(replies={"claims": claims_reply()})
    harness.seed_project()

    await started_round(harness, material_ids=())

    slots = [item for item in harness.prompts.calls if item[0] == "analyzer"][-1][1]
    assert slots["external_material"] == "（这一轮没有外部材料）"


async def test_unknown_material_id_is_a_shape_error():
    harness = Harness(replies={"claims": claims_reply()})
    harness.seed_project()

    with pytest.raises(ShapeError):
        await started_round(harness, material_ids=["m-不存在"])


def test_a_proposal_backed_only_by_material_is_downgraded_to_proposed():
    """材料**不算权威**（`02` §11.0.3）：说成 `derived` 也没用——回溯不到用户输入，
    就落进"它提议的"那一组（默认不勾）。这条不变量靠既有规则兜住，这里只把它钉死。"""

    from pmstudio.application import assembly
    from pmstudio.application.agents import schemas
    from pmstudio.domain.provenance.evidence import EvidenceSource
    from pmstudio.domain.provenance.ledger import EvidenceLedger
    from pmstudio.domain.round.draft import OriginType
    from tests.support.builders import document_with_feature, make_round

    ledger = EvidenceLedger.empty(PROJECT)
    found = ledger.record(
        kind=EvidenceKind.EXTERNAL,
        text="行业通行做法是 RBAC",
        source=EvidenceSource(external_ref="https://example.com/rbac"),
        now=NOW,
    )

    result = assembly.assemble(
        schemas.IntegrationPacket.model_validate(
            {
                "questions": [],
                "list_items": [],
                "proposals": [
                    {
                        "op": "add",
                        "target": {
                            "zone": "body",
                            "section_id": "s5",
                            "feature_slug": "order-export",
                            "field": "one_liner",
                        },
                        "text": "权限模型采用行业通行的 RBAC",
                        "origin_type": "derived",
                        "evidence_refs": [str(found)],
                    }
                ],
            }
        ),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger,
    )

    assert result.proposals[0].origin.type is OriginType.PROPOSED


async def test_the_seat_prompt_also_carries_the_material(tmp_path):
    """席位也要看得到材料（三个智能体共用同一个渲染结果）。"""

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: json.dumps({"basis": "读了材料", "opinions": []})},
    )
    harness.seed_project()
    harness.materials.seed(make_material("目标：导出要有权限"))

    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=RoundId(round_id), seats=(SEAT,))
    await harness.run_seat_round()(round_id=RoundId(round_id))

    seat_calls = [item for item in harness.prompts.calls if item[0] == "seat_review"]
    assert seat_calls, "席位没被调用"
    assert "需求.md" in seat_calls[-1][1]["external_material"]


def material_backed_claims_reply() -> str:
    """主张说读过 E1（本轮带进来的那块材料）——E43 那一幕的复现形状。"""

    return json.dumps(
        {
            "basis": "读了本轮输入与带进来的材料块 E1",
            "claims": [
                {
                    "text": "按这份材料写",
                    "raw_span": "按这份材料写",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                    "material_eids": ["E1"],
                }
            ],
            "seats": [{"seat_id": "seat.data_scale", "why": "材料里有量级"}],
        },
        ensure_ascii=False,
    )


async def test_start_round_links_picked_material_into_claim_derived_from():
    from pmstudio.domain.shared.ids import EvidenceId

    harness = Harness(replies={"claims": material_backed_claims_reply()})
    harness.seed_project()
    harness.materials.seed(make_material("目标：导出要有权限"))
    starter = harness.start_round()
    round_id = await starter.create(
        project_id=PROJECT, user_input="按这份材料写", material_ids=["m1"]
    )
    await starter.analyze(round_id=round_id)

    ledger = harness.evidences.get(PROJECT)
    claims = [item for item in ledger.evidences() if item.kind is EvidenceKind.USER_INPUT]
    assert [item.source.derived_from for item in claims] == [(EvidenceId("E1"),)]


async def test_evidence_lookup_shows_material_in_claim_chain():
    harness = Harness(replies={"claims": material_backed_claims_reply()})
    harness.seed_project()
    harness.materials.seed(make_material("目标：导出要有权限"))
    starter = harness.start_round()
    round_id = await starter.create(
        project_id=PROJECT, user_input="按这份材料写", material_ids=["m1"]
    )
    await starter.analyze(round_id=round_id)

    ledger = harness.evidences.get(PROJECT)
    claim_eid = next(
        item.eid for item in ledger.evidences() if item.kind is EvidenceKind.USER_INPUT
    )
    view = await harness.get_evidence()(project_id=PROJECT, eid=str(claim_eid))
    assert [item.eid for item in view.chain] == [str(claim_eid), "E1"]


# ── 装配层与材料块的**摆放顺序**无关（`02 §11.0.54` 的前提）────────────────
#
# 材料节的呈现改成 U 型之后，"哪些块被引用了"的核算必须不受影响。
# 装配层只按集合消费编号（`cited_eids` 返回 frozenset），这里钉死它。


def test_the_material_accounting_ignores_the_order_blocks_are_stored_in():
    def round_with(*refs):
        round_ = Round(
            round_id=RoundId("r1"),
            project_id=PROJECT,
            user_input="按这份写",
            created_at=NOW,
            step=Step.DONE,
            waiting_on=WaitingOn.NONE,
        )
        round_.add_materials(refs)
        return round_

    refs = [
        MaterialRef(
            material_id="m1", chunk_id=f"m1#c{index:02d}", eid=EvidenceId(f"E{10 + index}")
        )
        for index in range(6)
    ]
    proposal = Proposal(
        proposal_id="p1",
        entry_id="en1",
        op=WriteOp.ADD,
        target=Target.body("s5"),
        text="测试提案",
        origin=Origin(type=OriginType.USER, refs=(EvidenceId("E10"),)),
    )

    forward = _unreferenced_materials_for(round_with(*refs), proposals=[proposal], questions=())
    backward = _unreferenced_materials_for(
        round_with(*reversed(refs)), proposals=[proposal], questions=()
    )

    assert set(forward) == set(backward)
    assert "E10" not in forward        # 被引用的那份不算零引用，两种摆法下都不算
    assert len(forward) == 5


async def test_the_same_chunk_gets_the_same_number_no_matter_how_relevant_it_is():
    """编号按**文档顺序**发，不按相关度发。

    相关度是本轮输入算出来的：让它决定编号，等于同一份材料换个问法就换一套号——
    记忆条目、F35 核算、用户对着材料卡找 `[E15]`，全都要跟问法联动。
    所以：相关度只决定**挑哪些**与**怎么摆**，编号始终按"第几份材料 × 第几块"。
    """

    harness = harness_with_material("甲甲甲甲", "导出权限说明", "乙乙乙乙")
    await started_round(harness, user_input="按这份材料写，导出权限该怎么定")

    ledger = harness.evidences.get(PROJECT)
    externals = [item for item in ledger.evidences() if item.kind is EvidenceKind.EXTERNAL]
    assert [item.text for item in externals] == ["甲甲甲甲", "导出权限说明", "乙乙乙乙"]
    assert externals[1].eid != externals[0].eid

    # 换个问法（"导出"仍命中第二块，但分数更高）：同一份材料的编号形状不变
    other = harness_with_material("甲甲甲甲", "导出权限说明", "乙乙乙乙")
    await started_round(other, user_input="按这份材料写，只想要导出的部分")
    externals2 = [
        item for item in other.evidences.get(PROJECT).evidences()
        if item.kind is EvidenceKind.EXTERNAL
    ]
    assert [item.text for item in externals2] == ["甲甲甲甲", "导出权限说明", "乙乙乙乙"]


async def test_the_material_card_reads_in_document_order_not_relevance_order():
    """材料卡按**文档顺序**排（用户看的是原文，不是相关度榜单）。

    提示词那一节是 U 型（`context.ushape`，给模型躲中段低谷用的），卡片是给人读的——
    两个顺序，各自服务各自的人。编号也按文档序发，所以卡上号码顺序走。
    """

    harness = harness_with_material("甲甲甲甲", "导出权限说明在这里", "乙乙乙乙", "丙丙丙丙")
    round_id = await started_round(harness, user_input="按这份材料写，导出权限该怎么定")

    from pmstudio.application.dto import round_material_views

    views = round_material_views(
        harness.rounds.get(RoundId(round_id)), materials=[harness.materials.get("m1")]
    )
    assert [chunk.chunk_id for chunk in views[0].chunks] == [
        "m1#c00",
        "m1#c01",
        "m1#c02",
        "m1#c03",
    ]
    eids = [chunk.eid for chunk in views[0].chunks]
    assert eids == sorted(eids)      # 编号也按文档序，所以卡上顺序走
