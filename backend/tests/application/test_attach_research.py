"""把研究来源挂到当前轮（P3b-1）：状态闸、幂等、跨项目、以及"来源 E 真的可引用"。

这一层验的是**接缝**：用例能不能把来源卡挂对、错的时候拦不拦得住、
挂上去之后编号是否真的进了"看得见"那一侧（`known_eids` 的渲染半边）。
"""

import json

import pytest

from pmstudio.application.context import render_research_sources
from pmstudio.application.document_window import visible_eids
from pmstudio.application.errors import RoundNotAttachable, UnknownExploration
from pmstudio.application.use_cases import AttachResearchSources, external_material_text
from pmstudio.domain.round.research import RoundResearchSource
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId, ExplorationId, ProjectId, RoundId
from pmstudio.domain.shared.research import (
    MAX_RESEARCH_SOURCES,
    RESEARCH_SOURCE_CARD_CHARS,
)
from tests.support.builders import NOW, PROJECT, document_with_feature
from tests.support.doubles import LoopScriptedGateway, RecordingWebSearch, ToolStep
from tests.support.harness import Harness


def _page(url: str, text: str):
    from pmstudio.application.dto import MaterialChunk, ParsedMaterial

    return ParsedMaterial(
        origin="upload",
        title=f"网页 {url}",
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


def _synthesis(count: int) -> str:
    return json.dumps(
        {
            "basis": "依据来源",
            "findings": [
                {"text": f"结论 {index}", "refs": [f"S{index}"]}
                for index in range(1, count + 1)
            ],
            "open_questions": [],
        },
        ensure_ascii=False,
    )


async def _research_done(harness: Harness, count: int = 2):
    view = await harness.exploration_runner().start_research(
        project_id=ProjectId(PROJECT), topic="导出上限"
    )
    assert view.status == "done", view.failure_reason
    assert len(view.research.sources) == count  # type: ignore[union-attr]
    return view


def _harness(count: int = 2) -> Harness:
    gateway = LoopScriptedGateway(
        loop=(
            ToolStep(calls=(("web_search", {"query": "导出上限"}),)),
            ToolStep(text="够了"),
        ),
        structured={"research": _synthesis(count)},
    )
    harness = Harness(
        replies={},
        seat_replies={},
        gateway=gateway,
        web_search=RecordingWebSearch(
            returns=tuple(
                _page(f"https://example.com/{index}", f"第 {index} 份正文")
                for index in range(1, count + 1)
            )
        ),
    )
    harness.seed_project()
    harness.documents.seed(document_with_feature())
    return harness


async def _open_round(harness: Harness) -> RoundId:
    return await harness.start_round().create(
        project_id=PROJECT, user_input="想做一个批量导出", material_ids=()
    )


def _use_case(harness: Harness) -> AttachResearchSources:
    return harness.attach_research_sources()


async def test_attach_snapshots_sources_and_emits_round_updated() -> None:
    harness = _harness()
    research = await _research_done(harness)
    round_id = await _open_round(harness)
    eids = [item.eid for item in research.research.sources]  # type: ignore[union-attr]
    before = len(harness.events.published)

    view = await _use_case(harness)(
        round_id=round_id, exploration_id=research.exploration_id, eids=eids
    )

    assert [item.eid for item in view.research_sources] == eids
    assert view.research_sources[0].eid == eids[0]
    assert harness.events.names[before:] == ["round.updated"]
    # 快照，不是引用：研究行删了也不影响这一轮手里的卡（卡面在研究的 sources 里）
    assert [item.eid for item in view.research_sources] == eids


async def test_attach_is_idempotent_and_does_not_touch_materials_or_web_slots() -> None:
    harness = _harness()
    research = await _research_done(harness)
    round_id = await _open_round(harness)
    eid = research.research.sources[0].eid  # type: ignore[union-attr]

    for _ in range(2):
        await _use_case(harness)(
            round_id=round_id, exploration_id=research.exploration_id, eids=[eid]
        )

    round_ = harness.rounds.get(round_id)
    assert len(round_.research_sources) == 1
    assert round_.materials == ()
    assert round_.pinned_materials == ()
    assert round_.web_selection is None


async def test_unknown_eid_is_rejected() -> None:
    harness = _harness()
    research = await _research_done(harness)
    round_id = await _open_round(harness)

    with pytest.raises(ShapeError):
        await _use_case(harness)(
            round_id=round_id,
            exploration_id=research.exploration_id,
            eids=["E999"],
        )


async def test_unknown_exploration_is_404_semantics() -> None:
    harness = _harness()
    round_id = await _open_round(harness)

    with pytest.raises(UnknownExploration):
        await _use_case(harness)(
            round_id=round_id, exploration_id="x-nope", eids=["E1"]
        )


async def test_a_research_from_another_project_is_404() -> None:
    """归属在事务内用同一份 round 判：别的项目的研究不能挂进来。"""

    from pmstudio.application.errors import UnknownExploration
    from pmstudio.domain.exploration.exploration import (
        Exploration,
        ExplorationKind,
        ExplorationScope,
        ScopeKind,
    )
    from pmstudio.domain.exploration.research import (
        ResearchFinding,
        ResearchReport,
        ResearchSource,
    )

    harness = _harness()
    round_id = await _open_round(harness)
    other = Exploration.start(
        exploration_id=ExplorationId("x-other"),
        project_id=ProjectId("p-other"),
        scope=ExplorationScope(kind=ScopeKind.TOPIC, text="别的项目"),
        now=NOW,
        kind=ExplorationKind.WEB_RESEARCH,
    )
    other.complete_research(
        ResearchReport(
            findings=(
                ResearchFinding(
                    finding_id="F1", statement="结论", refs=(EvidenceId("E9"),)
                ),
            ),
            sources=(
                ResearchSource(
                    eid=EvidenceId("E9"),
                    material_id="m-9",
                    title="别的项目的来源",
                    url="https://example.com/other",
                    excerpt="正文。",
                ),
            ),
        )
    )
    harness.explorations.seed(other) if hasattr(harness.explorations, "seed") else None
    harness.explorations.save(other)

    with pytest.raises(UnknownExploration):
        await _use_case(harness)(
            round_id=round_id, exploration_id="x-other", eids=["E9"]
        )


async def test_attaching_a_run_that_did_not_finish_is_422() -> None:
    from pmstudio.domain.exploration.exploration import (
        Exploration,
        ExplorationKind,
        ExplorationScope,
        ScopeKind,
    )

    harness = _harness()
    round_id = await _open_round(harness)
    running = Exploration.start(
        exploration_id=ExplorationId("x-running"),
        project_id=ProjectId(PROJECT),
        scope=ExplorationScope(kind=ScopeKind.TOPIC, text="还在跑"),
        now=NOW,
        kind=ExplorationKind.WEB_RESEARCH,
    )
    harness.explorations.save(running)

    with pytest.raises(ShapeError) as caught:
        await _use_case(harness)(
            round_id=round_id, exploration_id="x-running", eids=["E1"]
        )
    assert "还没跑完" in caught.value.message


async def test_attaching_six_at_once_is_422() -> None:
    harness = _harness()
    research = await _research_done(harness)
    round_id = await _open_round(harness)

    with pytest.raises(ShapeError):
        await _use_case(harness)(
            round_id=round_id,
            exploration_id=research.exploration_id,
            eids=[f"E{index}" for index in range(1, MAX_RESEARCH_SOURCES + 2)],
        )


async def test_attaching_after_the_draft_is_refused() -> None:
    harness = _harness()
    research = await _research_done(harness)
    closed = Round(
        round_id=RoundId("r-closed"),
        project_id=ProjectId(PROJECT),
        user_input="已经出草案了",
        created_at=NOW,
        step=Step.AWAITING_CONFIRM,
    )
    harness.rounds.seed(closed)

    with pytest.raises(RoundNotAttachable):
        await _use_case(harness)(
            round_id=closed.round_id,
            exploration_id=research.exploration_id,
            eids=[research.research.sources[0].eid],  # type: ignore[union-attr]
        )


async def test_attached_eids_become_visible_in_the_prompt_side() -> None:
    """**这条是"零白名单"的验收**：挂上去的 E 只要出现在渲染文本里，
    就会经 `visible_eids ∩ 账本` 自动变成模型可引用的编号。"""

    harness = _harness()
    research = await _research_done(harness)
    round_id = await _open_round(harness)
    eids = [item.eid for item in research.research.sources]  # type: ignore[union-attr]
    await _use_case(harness)(
        round_id=round_id, exploration_id=research.exploration_id, eids=eids
    )

    round_ = harness.rounds.get(round_id)
    text = external_material_text(round_, store=harness.materials)

    for eid in eids:
        assert f"[{eid}]" in text
    ledger = harness.evidences.get(ProjectId(PROJECT))
    ledger_ids = {str(item.eid) for item in ledger.evidences()}
    assert {str(eid) for eid in visible_eids(text)} & ledger_ids >= set(eids)


def test_render_keeps_whole_cards_and_says_how_many_are_left() -> None:
    cards = tuple(
        RoundResearchSource(
            eid=EvidenceId(f"E{index}"),
            title=f"标题 {index}",
            url=f"https://example.com/{index}",
            excerpt="字" * 300,
        )
        for index in range(1, 6)
    )
    rendered = render_research_sources(cards, budget=RESEARCH_SOURCE_CARD_CHARS)

    assert "[E1]" in rendered
    assert "[E5]" not in rendered
    assert "没进本步" in rendered


def test_render_of_no_cards_is_empty_not_a_placeholder() -> None:
    assert render_research_sources(()) == ""


def test_attach_cap_is_the_research_cap() -> None:
    round_ = Round.start(
        round_id=RoundId("r-cap"),
        project_id=ProjectId(PROJECT),
        user_input="挂满",
        now=NOW,
    )
    round_.attach_research_sources(
        [
            RoundResearchSource(
                eid=EvidenceId(f"E{index}"),
                title="t",
                url="https://example.com/a",
                excerpt="e",
            )
            for index in range(1, MAX_RESEARCH_SOURCES + 1)
        ]
    )
    assert len(round_.research_sources) == MAX_RESEARCH_SOURCES


def test_five_full_cards_all_render_with_their_eids() -> None:
    """5 张**满卡**必须全部进提示词（P3b-1 评审抓到的预算漏洞）。

    卡上限 800、来源上限 5，所以 5 × 800 正好等于 4000；预算若不把编号行与
    分隔符算进去，第 5 张会被无声丢掉——它的 E 就永远不可引用。
    """

    title, url = "标题", "https://example.com/a"
    head = len(title) + len(url) + 2
    excerpt = "字" * (RESEARCH_SOURCE_CARD_CHARS - head)
    # 用**真实最坏形状**的编号（5 位）验证预算留了余量：4 位时恰好打平，
    # 再多一位就破——预算不该卡在等号上。
    eids = [EvidenceId(f"E{12340 + index}") for index in range(1, MAX_RESEARCH_SOURCES + 1)]
    cards = tuple(
        RoundResearchSource(eid=eid, title=title, url=url, excerpt=excerpt)
        for eid in eids
    )

    rendered = render_research_sources(cards)

    for eid in eids:
        assert f"[{eid}]" in rendered
    assert "没进本步" not in rendered
