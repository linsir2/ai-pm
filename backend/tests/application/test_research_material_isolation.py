"""研究来源材料的隔离（P3b-1）：不进材料池、不被手勾、不参与检索、不能删/重解析。

用户拍板的口径（2026-10-29）：研究来源**不入 RAG 池，只能显式挂载**；
生命周期用 **origin 即冻结**，不做引用扫描。这一层把这三条钉住。
"""

import json

import pytest

from pmstudio.application.errors import (
    MaterialFrozen,
    UnsupportedMaterial,
    WebMaterialNotAttachable,
)
from pmstudio.application.ports import FetchedPage
from pmstudio.application.use_cases import AttachMaterials, DeleteMaterial, ReparseMaterial
from pmstudio.domain.shared.ids import ProjectId
from tests.support.builders import PROJECT, document_with_feature
from tests.support.doubles import (
    LoopScriptedGateway,
    RecordingWebFetch,
    RecordingWebSearch,
    ToolStep,
)
from tests.support.harness import Harness


def _page(url: str, text: str):
    from pmstudio.application.dto import MaterialChunk, ParsedMaterial

    return ParsedMaterial(
        origin="upload",
        title="网页",
        source_ref=url,
        media_type="text/html",
        bytes_len=len(text.encode()),
        chunks=(
            MaterialChunk(
                chunk_id=f"{url}#c00",
                ordinal=0,
                text=text,
                heading_path=(),
                anchor="正文",
            ),
        ),
        parser_id="fake",
        parser_version="1",
    )


def _synthesis() -> str:
    return json.dumps(
        {
            "answer": "内网线走背板槽位，这是这一趟来源给出的答案。",
            "answer_refs": ["S1"],
            "findings": [{"text": "结论", "refs": ["S1"]}],
            "open_questions": [],
        },
        ensure_ascii=False,
    )


async def _researched() -> tuple[Harness, str]:
    gateway = LoopScriptedGateway(
        loop=(
            ToolStep(calls=(("web_search", {"query": "背板槽位"}),)),
            ToolStep(text="够了"),
        ),
        structured={"research": _synthesis()},
    )
    harness = Harness(
        replies={},
        seat_replies={},
        gateway=gateway,
        web_search=RecordingWebSearch(
            returns=(_page("https://example.com/a", "内网线走背板槽位"),)
        ),
        fetcher=RecordingWebFetch(
            returns=(
                FetchedPage(
                    content="<html><body><p>内网线走背板槽位</p></body></html>".encode(),
                    media_type="text/html",
                    final_url="https://example.com/a",
                    title="网页",
                ),
            )
        ),
    )
    harness.seed_project()
    harness.documents.seed(document_with_feature())

    view = await harness.exploration_runner().start_research(
        project_id=ProjectId(PROJECT), topic="线该接哪儿"
    )
    assert view.status == "done", view.failure_reason
    # 视图不再投影 material_id（编号经账本能查到那条 external），按材料库反查
    stored = harness.materials.list_for_project(ProjectId(PROJECT), current=frozenset())
    assert len(stored) == 1
    return harness, stored[0].material_id


async def test_research_material_cannot_be_attached_by_hand() -> None:
    harness, material_id = await _researched()
    round_id = await harness.start_round().create(
        project_id=PROJECT, user_input="想做个东西", material_ids=()
    )

    with pytest.raises(WebMaterialNotAttachable):
        await AttachMaterials(
            rounds=harness.rounds,
            round_writer=harness.rounds,
            materials=harness.materials,
            uow=harness.uow,
        )(round_id=round_id, material_id=material_id, chunk_ids=(f"{material_id}#c00",))


async def test_research_material_cannot_be_handpicked_when_starting_a_round() -> None:
    harness, material_id = await _researched()

    with pytest.raises(WebMaterialNotAttachable):
        await harness.start_round().create(
            project_id=PROJECT,
            user_input="想做个东西",
            material_ids=(material_id,),
        )


async def test_research_material_is_frozen_by_origin_not_by_scanning_references() -> None:
    harness, material_id = await _researched()
    packet = harness.materials.get(material_id)
    assert packet is not None
    assert packet.parsed.origin == "web_research"

    with pytest.raises(MaterialFrozen) as caught:
        await DeleteMaterial(
            materials=harness.materials,
            parser=harness.parser,
            index=harness.index,
            uow=harness.uow,
        )(project_id=ProjectId(PROJECT), material_id=material_id)
    assert "研究来源" in caught.value.message


async def test_research_material_cannot_be_reparsed() -> None:
    harness, material_id = await _researched()

    with pytest.raises(UnsupportedMaterial):
        await ReparseMaterial(
            materials=harness.materials,
            parser=harness.parser,
            index=harness.index,
            uow=harness.uow,
            clock=harness.clock,
        )(project_id=ProjectId(PROJECT), material_id=material_id)
