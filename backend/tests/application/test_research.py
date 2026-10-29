"""项目级研究用例（P3b-1）：跑工具循环 → 综合 → 一个 UoW 落库。

这一层只验"用例把什么写进了哪里"：来源与材料一一对应、E 一条不重复、
失败不留孤儿、空文档也能研究。工具边界在 `tests/infrastructure/test_research_tools.py`。
"""

import json

import pytest

from pmstudio.application.errors import UnknownProject
from pmstudio.application.project_research import (
    research_material_key,
    resolve_source_refs,
    source_card_text,
    sources_for_prompt,
)
from pmstudio.domain.exploration.exploration import ExplorationKind, ExplorationStatus
from pmstudio.domain.exploration.research import MAX_RESEARCH_SOURCES
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import ProjectId
from tests.support.builders import PROJECT, document_with_feature, make_document
from tests.support.doubles import LoopScriptedGateway, RecordingWebSearch, ToolStep
from tests.support.harness import Harness


def _page(url: str = "https://example.com/a", text: str = "同类产品默认 60 秒上限。"):
    from pmstudio.application.dto import MaterialChunk, ParsedMaterial

    return ParsedMaterial(
        origin="upload",
        title="导出上限白皮书",
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


def _synthesis(*refs: str, open_questions: tuple[str, ...] = ()) -> str:
    return json.dumps(
        {
            "basis": "依据 S1 的白皮书",
            "findings": [
                {"text": "主流同类产品的导出上限是 60 秒", "refs": list(refs)}
            ],
            "open_questions": list(open_questions),
        },
        ensure_ascii=False,
    )


def _gateway(
    *, steps: tuple[ToolStep, ...] | None = None, synthesis: str = ""
) -> LoopScriptedGateway:
    return LoopScriptedGateway(
        loop=steps
        or (
            ToolStep(calls=(("web_search", {"query": "导出上限"}),)),
            ToolStep(text="够了"),
        ),
        structured={"research": synthesis or _synthesis("S1")},
    )


def _harness(
    *,
    gateway=None,
    filled: bool = True,
    returns=None,
):  # type: ignore[no-untyped-def]
    harness = Harness(
        replies={},
        seat_replies={},
        gateway=gateway or _gateway(),
        web_search=RecordingWebSearch(
            returns=returns if returns is not None else (_page(),)
        ),
    )
    harness.seed_project()
    harness.documents.seed(document_with_feature() if filled else make_document())
    return harness


async def test_a_successful_run_records_one_eid_and_one_material_per_source() -> None:
    harness = _harness()

    view = await harness.exploration_runner().start_research(
        project_id=ProjectId(PROJECT), topic="批量导出的性能上限"
    )

    assert view.kind == ExplorationKind.WEB_RESEARCH.value
    assert view.status == ExplorationStatus.DONE.value
    assert view.research is not None
    assert len(view.research.sources) == 1
    source = view.research.sources[0]
    assert source.url == "https://example.com/a"
    assert source.eid == "E1"
    assert [finding.refs for finding in view.research.findings] == [("E1",)]

    # 账本里那条 E 的正文 == 来源卡（逐字）
    ledger = harness.evidences.get(ProjectId(PROJECT))
    entry = next(item for item in ledger.evidences() if str(item.eid) == "E1")
    assert entry.kind.value == "external"
    assert entry.text == source_card_text(
        title="导出上限白皮书", url=source.url, excerpt=source.excerpt
    )
    assert entry.source.external_ref == source.url

    # 材料：研究来源的 origin + 去重键，且块的正文进去了。
    # 视图不再投影 `material_id`（编号经账本能查到那条 external），所以按材料库反查。
    stored = harness.materials.list_for_project(PROJECT, current=frozenset())
    assert [item.origin for item in stored] == ["web_research"]
    assert stored[0].source_ref == source.url


async def test_an_empty_document_can_be_researched() -> None:
    """研究是"写之前"的事：M8 的接地检查不能继承过来。"""

    harness = _harness(filled=False)

    view = await harness.exploration_runner().start_research(
        project_id=ProjectId(PROJECT), topic="这个品类的常见定价"
    )

    assert view.status == ExplorationStatus.DONE.value


async def test_zero_sources_fail_and_touch_nothing() -> None:
    harness = _harness(returns=())
    harness.web_search.returns = ()

    view = await harness.exploration_runner().start_research(
        project_id=ProjectId(PROJECT), topic="查不到的主题"
    )

    assert view.status == ExplorationStatus.FAILED.value
    assert "没有取到任何来源" in (view.failure_reason or "")
    assert harness.evidences.get(ProjectId(PROJECT)).evidences() == ()
    assert harness.materials.list_for_project(PROJECT, current=frozenset()) == ()


async def test_unknown_project_is_rejected_before_any_network_call() -> None:
    harness = _harness()

    with pytest.raises(UnknownProject):
        await harness.exploration_runner().start_research(
            project_id=ProjectId("p-nope"), topic="随便"
        )

    assert harness.web_search.calls == []


async def test_synthesis_rejects_unknown_source_refs_then_repairs() -> None:
    gateway = LoopScriptedGateway(
        loop=(
            ToolStep(calls=(("web_search", {"query": "导出上限"}),)),
            ToolStep(text="够了"),
        ),
        structured={"research": [_synthesis("S9"), _synthesis("S1")]},
    )
    harness = _harness(gateway=gateway)

    view = await harness.exploration_runner().start_research(
        project_id=ProjectId(PROJECT), topic="批量导出的性能上限"
    )

    assert view.status == ExplorationStatus.DONE.value
    assert [finding.refs for finding in view.research.findings] == [("E1",)]  # type: ignore[union-attr]
    assert "research" in gateway.purposes
    assert sum(1 for purpose in gateway.purposes if purpose == "research") == 2


async def test_two_bad_syntheses_fail_without_writing_materials() -> None:
    gateway = LoopScriptedGateway(
        loop=(
            ToolStep(calls=(("web_search", {"query": "导出上限"}),)),
            ToolStep(text="够了"),
        ),
        structured={
            "research": json.dumps(
                {"findings": [{"text": "结论", "refs": ["S9"]}], "open_questions": []},
                ensure_ascii=False,
            )
        },
    )
    harness = _harness(gateway=gateway)

    view = await harness.exploration_runner().start_research(
        project_id=ProjectId(PROJECT), topic="批量导出的性能上限"
    )

    assert view.status == ExplorationStatus.FAILED.value
    assert harness.materials.list_for_project(PROJECT, current=frozenset()) == ()
    assert harness.evidences.get(ProjectId(PROJECT)).evidences() == ()


async def test_empty_findings_are_a_legitimate_result() -> None:
    gateway = LoopScriptedGateway(
        loop=(
            ToolStep(calls=(("web_search", {"query": "导出上限"}),)),
            ToolStep(text="够了"),
        ),
        structured={
            "research": json.dumps(
                {"basis": "只找到来源，没有可下的判断", "findings": [], "open_questions": []},
                ensure_ascii=False,
            )
        },
    )
    harness = _harness(gateway=gateway)

    view = await harness.exploration_runner().start_research(
        project_id=ProjectId(PROJECT), topic="一个还没人能回答的问题"
    )

    assert view.status == ExplorationStatus.DONE.value
    assert view.research is not None
    assert view.research.findings == ()
    assert len(view.research.sources) == 1


async def test_notes_record_tool_attempts_and_overflow() -> None:
    many = tuple(
        _page(f"https://example.com/{index}", text=f"第 {index} 份结果")
        for index in range(MAX_RESEARCH_SOURCES + 2)
    )
    harness = _harness(returns=many)

    view = await harness.exploration_runner().start_research(
        project_id=ProjectId(PROJECT), topic="导出上限"
    )

    assert view.status == ExplorationStatus.DONE.value
    assert view.research is not None
    assert len(view.research.sources) == MAX_RESEARCH_SOURCES
    notes = "；".join(view.research.notes)
    assert "工具调用" in notes
    assert "未收" in notes


# —— 纯函数 ——


def test_source_refs_are_normalized_and_lenient_mode_drops_strangers() -> None:
    assert resolve_source_refs(["S1", "s1", "Ｓ2"], count=2, lenient=False) == (1, 2)
    with pytest.raises(ShapeError):
        resolve_source_refs(["S3"], count=2, lenient=False)
    with pytest.raises(ShapeError):
        resolve_source_refs(["E7"], count=2, lenient=False)
    assert resolve_source_refs(["S3", "S1"], count=2, lenient=True) == (1,)


def test_material_key_is_namespaced_away_from_p3a() -> None:
    key = research_material_key("https://example.com/a", b"same bytes")
    assert key != research_material_key("https://example.com/a", b"other bytes")
    assert len(key) == 64


def test_sources_for_prompt_numbers_the_cards() -> None:
    from pmstudio.application.dto import ResearchHit

    text = sources_for_prompt(
        [
            ResearchHit(
                url="https://example.com/a",
                title="A",
                excerpt="甲",
                parsed=_page(),
                content="甲".encode(),
            )
        ]
    )
    assert text.startswith("[S1] A")
    assert "https://example.com/a" in text


async def test_missing_tools_are_refused_before_any_row_or_model_call() -> None:
    """装配错必须在建行之前说清（C-2）：不许跑满 6 轮模型再说"没搜到"。"""

    from pmstudio.application.project_research import StartWebResearch
    from pmstudio.infrastructure.tools.extract import LocalMaterialParser
    from pmstudio.infrastructure.tools.research_tools import ResearchToolbox

    harness = Harness(replies={}, seat_replies={}, gateway=_gateway())
    harness.seed_project()
    harness.documents.seed(document_with_feature())
    research = StartWebResearch(
        documents=harness.documents,
        explorations=harness.explorations,
        exploration_writer=harness.explorations,
        evidence=harness.evidences,
        evidence_writer=harness.evidences,
        materials=harness.materials,
        uow=harness.uow,
        clock=harness.clock,
        ids=harness.ids,
        events=harness.events,
        gateway=harness.gateway,
        prompts=harness.prompts,
        synthesizer=harness.research_synthesizer(),
        toolbox=lambda: ResearchToolbox(
            search=None, fetcher=None, parser=LocalMaterialParser()
        ),
    )

    with pytest.raises(ShapeError) as caught:
        await research.create(project_id=ProjectId(PROJECT), topic="导出上限")

    assert "没有配置研究工具" in caught.value.message
    assert harness.gateway.purposes == []
    assert harness.explorations.list_for_project(PROJECT) == ()


async def test_material_quota_stops_the_run_instead_of_creeping(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """研究来源也吃项目材料配额（C-3）：绕过配额 + 永久冻结 = 只增不减。"""

    from pmstudio.application import project_research as module

    monkeypatch.setattr(module, "MAX_MATERIALS_PER_PROJECT", 0)
    harness = _harness()

    view = await harness.exploration_runner().start_research(
        project_id=ProjectId(PROJECT), topic="导出上限"
    )

    assert view.status == "failed"
    assert "材料已经到上限" in (view.failure_reason or "")
    assert harness.materials.list_for_project(PROJECT, current=frozenset()) == ()
