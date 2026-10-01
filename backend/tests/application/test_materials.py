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
from pmstudio.domain.provenance.evidence import EvidenceKind
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import RoundId
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
