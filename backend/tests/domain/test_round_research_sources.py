"""轮次上的来源卡：`Round.attach_research_sources` 的形状与上限。

这一层不碰存储：只验"挂什么、按什么幂等、最多几张"。
可挂载窗口（step）与跨聚合校验在用例层（`tests/application/test_attach_research.py`）。
"""

import pytest

from pmstudio.domain.round.research import RoundResearchSource
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, RoundId
from pmstudio.domain.shared.research import MAX_RESEARCH_SOURCES
from pmstudio.infrastructure.postgres import serialization as ser
from tests.support.builders import NOW


def source(index: int = 1, **over: object) -> RoundResearchSource:
    fields: dict[str, object] = {
        "eid": EvidenceId(f"E{index}"),
        "title": f"白皮书 {index}",
        "url": f"https://example.com/{index}",
        "excerpt": "同类产品默认 60 秒上限。",
    }
    fields.update(over)
    return RoundResearchSource(**fields)  # type: ignore[arg-type]


def a_round() -> Round:
    return Round.start(
        round_id=RoundId("r-1"),
        project_id=ProjectId("p-1"),
        user_input="想做一个批量导出",
        now=NOW,
    )


def test_every_field_is_required() -> None:
    for field in ("title", "url", "excerpt"):
        with pytest.raises(ShapeError):
            source(**{field: "  "})
    with pytest.raises(ShapeError):
        source(eid=EvidenceId(""))


def test_attach_is_idempotent_by_eid() -> None:
    round_ = a_round()
    assert round_.research_sources == ()

    round_.attach_research_sources([source(1), source(2)])
    round_.attach_research_sources([source(1)])

    assert [str(item.eid) for item in round_.research_sources] == ["E1", "E2"]


def test_attach_caps_at_the_same_number_as_a_research_run() -> None:
    round_ = a_round()
    cards = [source(index) for index in range(1, MAX_RESEARCH_SOURCES + 1)]
    round_.attach_research_sources(cards)

    with pytest.raises(ShapeError):
        round_.attach_research_sources([source(MAX_RESEARCH_SOURCES + 1)])


def test_attach_rejects_an_empty_list() -> None:
    with pytest.raises(ShapeError):
        a_round().attach_research_sources([])


def test_round_payload_round_trips_research_sources() -> None:
    round_ = a_round()
    round_.attach_research_sources([source(1)])
    payload = ser.round_to_payload(round_)

    assert payload["research_sources"][0]["url"] == "https://example.com/1"

    back = ser.round_from_payload(
        payload,
        round_id=RoundId("r-1"),
        project_id=ProjectId("p-1"),
        created_at=NOW,
        step=Step.STARTED,
        waiting_on=WaitingOn.NONE,
    )
    assert [str(item.eid) for item in back.research_sources] == ["E1"]
    assert back.research_sources[0].excerpt == "同类产品默认 60 秒上限。"


def test_old_payload_without_the_key_reads_as_empty() -> None:
    payload = ser.round_to_payload(a_round())
    payload.pop("research_sources")

    back = ser.round_from_payload(
        payload,
        round_id=RoundId("r-1"),
        project_id=ProjectId("p-1"),
        created_at=NOW,
        step=Step.STARTED,
        waiting_on=WaitingOn.NONE,
    )
    assert back.research_sources == ()
