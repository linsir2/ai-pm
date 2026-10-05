"""探索运行器：一次请求内跑完、同项目串行、取消、僵壳自愈、失败可见。"""

import asyncio
import json

import pytest

from pmstudio.application.errors import ExplorationBusy
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationScope,
    ExplorationStatus,
    ScopeKind,
)
from pmstudio.domain.shared.ids import ExplorationId
from tests.support.builders import PROJECT, document_with_feature
from tests.support.harness import Harness


def _good_reply() -> str:
    return json.dumps(
        {
            "basis": "读了 s5",
            "hypotheses": [
                {
                    "local_id": "H1",
                    "statement": "运营按客户名找单",
                    "grounding": "invented",
                    "based_on": [],
                    "falsification": "访谈没人这么找",
                    "evidence_needed": "两个访谈",
                }
            ],
            "blind_spots": [],
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
                    "falsification": "访谈没人这么找",
                    "evidence_needed": "两个访谈",
                }
            ],
            "blind_spots": [],
        },
        ensure_ascii=False,
    )


def _filled(harness: Harness) -> None:
    harness.seed_project()
    harness.documents.seed(document_with_feature())


class _BlockingGateway:
    """第一次调用就卡住：把探索钉在"正在跑"，好测串行与取消。"""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, request):  # type: ignore[no-untyped-def]
        self.entered.set()
        await self.release.wait()
        from pmstudio.harness.errors import ModelCallFailed

        raise ModelCallFailed("测试用：卡住之后不打算成功", purpose=str(request.purpose))


async def test_start_runs_to_done() -> None:
    harness = Harness(replies={"inquiry": _good_reply()})
    _filled(harness)
    view = await harness.exploration_runner().start(
        project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT)
    )
    assert view.status == "done"
    assert view.candidates[0].candidate_id == "H1"


async def test_second_start_in_the_same_project_is_rejected_while_running() -> None:
    gateway = _BlockingGateway()
    harness = Harness(gateway=gateway)
    _filled(harness)
    runner = harness.exploration_runner()

    first = asyncio.create_task(
        runner.start(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    )
    await asyncio.wait_for(gateway.entered.wait(), timeout=5)

    with pytest.raises(ExplorationBusy):
        await runner.start(
            project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT)
        )

    gateway.release.set()
    view = await first  # 网关失败 → 这一趟落 failed（失败可见，可重试）
    assert view.status == "failed"


async def test_gateway_failure_marks_the_run_failed() -> None:
    harness = Harness(replies={})  # 脚本里没有 inquiry 回复 → 网关直接失败
    _filled(harness)
    view = await harness.exploration_runner().start(
        project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT)
    )
    assert view.status == "failed"
    assert "模型调用失败" in (view.failure_reason or "")
    assert harness.events.names[-1] == "exploration.updated"


async def test_cancel_stops_the_inflight_task_without_writing_status() -> None:
    gateway = _BlockingGateway()
    harness = Harness(gateway=gateway)
    _filled(harness)
    runner = harness.exploration_runner()

    task = asyncio.create_task(
        runner.start(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    )
    await asyncio.wait_for(gateway.entered.wait(), timeout=5)
    xid = harness.explorations.list_for_project(PROJECT)[0].exploration_id

    await runner.cancel(xid)
    assert task.done()

    # 取消只让调用停下来，**不写状态**——写状态是 AbandonExploration 的活
    assert harness.explorations.get(xid).status is ExplorationStatus.RUNNING


async def test_stale_running_row_is_abandoned_on_the_next_start() -> None:
    harness = Harness(replies={"inquiry": _good_reply()})
    _filled(harness)
    stale = Exploration.start(
        exploration_id=ExplorationId("x-stale"),
        project_id=PROJECT,
        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
        now=harness.clock.now(),
    )
    harness.explorations.seed(stale)

    view = await harness.exploration_runner().start(
        project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT)
    )

    assert view.status == "done"
    assert (
        harness.explorations.get(ExplorationId("x-stale")).status
        is ExplorationStatus.ABANDONED
    )


async def test_runner_retry_resumes_a_failed_run() -> None:
    harness = Harness(
        replies={"inquiry": [_bad_ref_reply(), _bad_ref_reply(), _good_reply()]}
    )
    _filled(harness)
    runner = harness.exploration_runner()

    started = await runner.start(
        project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT)
    )
    assert started.status == "failed"

    view = await runner.retry(exploration_id=str(started.exploration_id))
    assert view.status == "done"
