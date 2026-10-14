"""调研用例（M8）：发起 / 跑 / 重试 / 放弃 / 收下 / 删掉 / 列表，以及种子进轮的校验。"""

import json
from dataclasses import dataclass, field

import pytest

from pmstudio.application.deliverable import iter_document_entries
from pmstudio.application.errors import UnknownEntryId, UnknownExploration
from pmstudio.domain.exploration.exploration import ExplorationScope, ScopeKind
from pmstudio.domain.exploration.item import CandidateState
from pmstudio.domain.shared.errors import CandidateNotReviewable, ShapeError
from pmstudio.domain.shared.ids import ExplorationId, ProjectId
from tests.support.builders import PROJECT, document_with_feature
from tests.support.harness import Harness


def _good_reply() -> str:
    return json.dumps(
        {
            "basis": "读了 s5 的功能描述",
            "hypotheses": [
                {
                    "local_id": "H1",
                    "statement": "运营做日报时按客户名找单",
                    "grounding": "invented",
                    "based_on": [],
                    "falsification": "访谈后若没人这么找单即被推翻",
                    "evidence_needed": "两个真实运营的访谈记录",
                },
                {
                    "local_id": "H2",
                    "statement": "月末对账时才需要全量导出",
                    "grounding": "invented",
                    "based_on": [],
                    "falsification": "若月中也有批量导出需求即被推翻",
                    "evidence_needed": "看一周的导出日志",
                },
            ],
            "blind_spots": [
                {
                    "local_id": "B1",
                    "statement": "客户合并后的订单归属未知",
                    "next_step": "去问数据组：合并客户挂哪个 ID",
                }
            ],
        },
        ensure_ascii=False,
    )


def _bad_ref_reply() -> str:
    return json.dumps(
        {
            "basis": "读了文档",
            "hypotheses": [
                {
                    "local_id": "H1",
                    "statement": "运营按客户名找单",
                    "grounding": "document",
                    "based_on": ["E99"],
                    "falsification": "访谈后没人这么找",
                    "evidence_needed": "两个运营的访谈",
                }
            ],
            "blind_spots": [],
        },
        ensure_ascii=False,
    )


def _numeric_reply() -> str:
    return json.dumps(
        {
            "basis": "读了文档",
            "hypotheses": [
                {
                    "local_id": "H1",
                    "statement": "约 62% 的用户会按客户名找单",
                    "grounding": "invented",
                    "based_on": [],
                    "falsification": "访谈后若比例明显不同",
                    "evidence_needed": "一份问卷",
                }
            ],
            "blind_spots": [],
        },
        ensure_ascii=False,
    )


def _empty_reply() -> str:
    return json.dumps(
        {"basis": "看了但没有想法", "hypotheses": [], "blind_spots": []}, ensure_ascii=False
    )


def _filled(harness: Harness) -> ProjectId:
    harness.seed_project()
    harness.documents.seed(document_with_feature())
    return PROJECT


async def test_create_rejects_empty_document() -> None:
    harness = Harness(replies={})
    harness.seed_project()  # 空文档
    with pytest.raises(ShapeError):
        await harness.run_inquiry().create(
            project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT)
        )


async def test_create_with_unknown_entry_is_a_resource_error() -> None:
    harness = Harness(replies={})
    _filled(harness)
    with pytest.raises(UnknownEntryId):
        await harness.run_inquiry().create(
            project_id=PROJECT,
            scope=ExplorationScope(kind=ScopeKind.ENTRY, ref="entry-nope"),
        )


async def test_create_persists_running_and_publishes_one_event() -> None:
    harness = Harness(replies={})
    _filled(harness)
    xid = await harness.run_inquiry().create(
        project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT)
    )
    stored = harness.explorations.get(xid)
    assert stored.status.value == "running"
    assert harness.events.names == ["exploration.updated"]
    assert harness.events.published[0].payload["exploration_id"] == str(xid)


async def test_execute_success_persists_candidates_and_scope_focus() -> None:
    harness = Harness(replies={"inquiry": _good_reply()})
    _filled(harness)
    entry = next(iter_document_entries(harness.documents.get(PROJECT).view()))
    use = harness.run_inquiry()
    xid = await use.create(
        project_id=PROJECT,
        scope=ExplorationScope(kind=ScopeKind.ENTRY, ref=str(entry.entry_id)),
    )
    view = await use.execute(xid)

    assert view.status == "done"
    assert [item.candidate_id for item in view.candidates] == ["H1", "H2", "B1"]
    assert view.candidates[0].falsification
    assert view.candidates[2].next_step
    assert harness.prompts.last("inquiry")["scope"].find(entry.text) >= 0
    assert harness.events.names == ["exploration.updated", "exploration.updated"]


async def test_unknown_exploration_is_a_resource_error() -> None:
    harness = Harness(replies={})
    with pytest.raises(UnknownExploration):
        harness.explorations.get(ExplorationId("x-9"))


async def test_invalid_ref_retries_once_then_salvages_instead_of_failing() -> None:
    """引用了不存在的编号：重装一次仍犯 → 最后一次摘掉该编号、降级为 invented，不判死整趟。

    2026-10-14 修复：以前这类错一次就把整趟调研打死（`02` §11.0.2 口径）。
    """
    harness = Harness(replies={"inquiry": [_bad_ref_reply(), _bad_ref_reply()]})
    _filled(harness)
    use = harness.run_inquiry()
    xid = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    view = await use.execute(xid)

    assert view.status == "done"  # 不再 failed
    assert len(harness.gateway.requests) == 2
    assert "E99" in harness.prompts.last("inquiry")["validation_error"]  # 重装那次说了原因
    # 文档接地摘空 → 降级成"凭空推演"（领域不放行无据的 document）
    assert view.candidates[0].grounding == "invented"
    assert view.candidates[0].based_on == ()


async def test_numeric_claim_triggers_one_repair_then_succeeds() -> None:
    harness = Harness(replies={"inquiry": [_numeric_reply(), _good_reply()]})
    _filled(harness)
    use = harness.run_inquiry()
    xid = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    view = await use.execute(xid)

    assert view.status == "done"
    assert len(harness.gateway.requests) == 2
    assert "数量断言" in harness.prompts.last("inquiry")["validation_error"]


async def test_empty_produce_is_not_success() -> None:
    harness = Harness(replies={"inquiry": [_empty_reply(), _empty_reply()]})
    _filled(harness)
    use = harness.run_inquiry()
    xid = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    view = await use.execute(xid)
    assert view.status == "failed"
    assert "空结果" in (view.failure_reason or "")


async def test_retry_resumes_failed_and_hands_the_reason_to_the_next_attempt() -> None:
    # 引火索用**结构性失败**（空结果）——引用错已改成降级，不再能把一趟调研逼到 failed。
    harness = Harness(replies={"inquiry": [_empty_reply(), _empty_reply(), _good_reply()]})
    _filled(harness)
    use = harness.run_inquiry()
    xid = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    first = await use.execute(xid)
    assert first.status == "failed"

    hint = await harness.retry_exploration().resume(xid)
    assert hint is not None and "空结果" in hint
    second = await use.execute(xid, retry_hint=hint)
    assert second.status == "done"
    assert "空结果" in harness.prompts.last("inquiry")["validation_error"]


async def test_keep_as_material_creates_one_material_and_is_idempotent() -> None:
    harness = Harness(replies={"inquiry": _good_reply()})
    _filled(harness)
    use = harness.run_inquiry()
    xid = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    await use.execute(xid)

    keep = harness.keep_candidate()
    view = await keep(exploration_id=xid, candidate_id="H1", as_="material")
    candidate = next(item for item in view.candidates if item.candidate_id == "H1")
    assert candidate.state == "kept"
    assert candidate.kept_as == "material"
    assert candidate.kept_ref

    packet = harness.materials.get(candidate.kept_ref or "")
    assert packet is not None
    assert packet.parsed.origin == "exploration"
    assert packet.parsed.source_ref == f"exploration:{xid}#H1"

    again = await keep(exploration_id=xid, candidate_id="H1", as_="material")
    assert next(item for item in again.candidates if item.candidate_id == "H1").kept_ref == (
        candidate.kept_ref
    )
    assert len(harness.materials.list_for_project(PROJECT, current=frozenset())) == 1


async def test_keep_as_seed_marks_without_material() -> None:
    harness = Harness(replies={"inquiry": _good_reply()})
    _filled(harness)
    use = harness.run_inquiry()
    xid = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    await use.execute(xid)

    view = await harness.keep_candidate()(exploration_id=xid, candidate_id="B1", as_="seed")
    candidate = next(item for item in view.candidates if item.candidate_id == "B1")
    assert candidate.kept_as == "seed"
    assert candidate.kept_ref is None
    assert harness.materials.list_for_project(PROJECT, current=frozenset()) == ()


async def test_dismiss_is_terminal_and_keep_after_is_rejected() -> None:
    harness = Harness(replies={"inquiry": _good_reply()})
    _filled(harness)
    use = harness.run_inquiry()
    xid = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    await use.execute(xid)

    view = await harness.dismiss_candidate()(exploration_id=xid, candidate_id="B1")
    assert (
        next(item for item in view.candidates if item.candidate_id == "B1").state
        == CandidateState.DISMISSED
    )
    with pytest.raises(CandidateNotReviewable):
        await harness.keep_candidate()(exploration_id=xid, candidate_id="B1", as_="seed")


async def test_start_round_records_kept_seeds_and_rejects_bad_ones() -> None:
    harness = Harness(replies={"inquiry": _good_reply()})
    _filled(harness)
    use = harness.run_inquiry()
    xid = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    await use.execute(xid)
    await harness.keep_candidate()(exploration_id=xid, candidate_id="H1", as_="seed")

    starter = harness.start_round()
    round_id = await starter.create(
        project_id=PROJECT,
        user_input="按调研说的去问数据组",
        seed_ids=[f"exploration:{xid}#H1"],
    )
    seeds = [
        (ref.origin, ref.ref_id, ref.item_id)
        for ref in harness.rounds.get(round_id).seeds
    ]
    assert seeds == [("exploration", str(xid), "H1")]

    with pytest.raises(ShapeError):  # 没被收下的候选不能当种子
        await starter.create(
            project_id=PROJECT,
            user_input="再试一次",
            seed_ids=[f"exploration:{xid}#H2"],
        )
    with pytest.raises(ShapeError):  # 形状不对（老格式没有来源前缀）
        await starter.create(project_id=PROJECT, user_input="再试一次", seed_ids=["nope"])
    with pytest.raises(ShapeError):  # 不认识的来源
        await starter.create(
            project_id=PROJECT, user_input="再试一次", seed_ids=[f"gap:{xid}#H1"]
        )


@dataclass
class _RecordingCanceller:
    called: list[str] = field(default_factory=list)

    async def cancel(self, exploration_id: ExplorationId) -> None:
        self.called.append(str(exploration_id))


async def test_abandon_writes_status_then_cancels() -> None:
    harness = Harness(replies={})
    _filled(harness)
    xid = await harness.run_inquiry().create(
        project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT)
    )
    canceller = _RecordingCanceller()
    view = await harness.abandon_exploration(canceller=canceller)(exploration_id=xid)

    assert view.status == "abandoned"
    assert harness.explorations.get(xid).status.value == "abandoned"
    assert canceller.called == [str(xid)]


async def test_list_returns_views_and_honours_the_limit() -> None:
    harness = Harness(replies={})
    _filled(harness)
    use = harness.run_inquiry()
    first = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    second = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))

    views = await harness.list_explorations()(project_id=PROJECT)
    assert [view.exploration_id for view in views] == [str(second), str(first)]

    limited = await harness.list_explorations(limit=1)(project_id=PROJECT)
    assert len(limited) == 1
