"""项目级研究（P3b-1）的领域形状：来源 / 结论 / 报告，以及探索聚合上的分派。

这一层只判机器判得了的事：形状、上限、eid 归属。**零来源不是领域错误**
（由用例判，因为"搜不到东西"是运行结果，不是数据不自洽）。
"""

from datetime import UTC, datetime

import pytest

from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationKind,
    ExplorationScope,
    ExplorationStatus,
    ScopeKind,
)
from pmstudio.domain.exploration.item import Grounding, Hypothesis
from pmstudio.domain.exploration.research import (
    MAX_RESEARCH_ANSWER_CHARS,
    MAX_RESEARCH_FINDINGS,
    MAX_RESEARCH_OPEN_QUESTIONS,
    MAX_RESEARCH_SOURCES,
    MAX_RESEARCH_TOPIC_CHARS,
    RESEARCH_SOURCE_CARD_CHARS,
    ResearchFinding,
    ResearchReport,
    ResearchSource,
)
from pmstudio.domain.shared.errors import (
    IllegalExplorationTransition,
    ShapeError,
    TooManyInquiryItems,
)
from pmstudio.domain.shared.ids import EvidenceId, ExplorationId, ProjectId
from pmstudio.infrastructure.postgres import serialization as ser

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
PID = ProjectId("p-1")
XID = ExplorationId("x-1")


def source(**over: object) -> ResearchSource:
    fields: dict[str, object] = {
        "eid": EvidenceId("E7"),
        "material_id": "m-1",
        "title": "导出上限白皮书",
        "url": "https://example.com/export",
        "excerpt": "同类产品默认 60 秒上限。",
    }
    fields.update(over)
    return ResearchSource(**fields)  # type: ignore[arg-type]


def finding(**over: object) -> ResearchFinding:
    fields: dict[str, object] = {
        "finding_id": "F1",
        "statement": "主流同类产品的导出上限是 60 秒",
        "refs": (EvidenceId("E7"),),
    }
    fields.update(over)
    return ResearchFinding(**fields)  # type: ignore[arg-type]


def report(**over: object) -> ResearchReport:
    fields: dict[str, object] = {
        "answer": "同类产品多把批量导出上限设在 60 秒；超大文件需要分片。",
        "answer_refs": (EvidenceId("E7"),),
        "findings": (finding(),),
        "sources": (source(),),
        "open_questions": ("是否支持断点续传？",),
        "notes": (),
    }
    fields.update(over)
    return ResearchReport(**fields)  # type: ignore[arg-type]


def research_exploration() -> Exploration:
    return Exploration.start(
        exploration_id=XID,
        project_id=PID,
        scope=ExplorationScope(kind=ScopeKind.TOPIC, text="批量导出的性能上限"),
        now=NOW,
        kind=ExplorationKind.WEB_RESEARCH,
    )


# —— 来源 ——


def test_source_rejects_empty_fields_including_the_eid() -> None:
    for field in ("material_id", "title", "url", "excerpt"):
        with pytest.raises(ShapeError):
            source(**{field: "   "})
    with pytest.raises(ShapeError):
        source(eid=EvidenceId("  "))


def test_the_whole_card_is_capped_not_just_the_excerpt() -> None:
    """上限落在**整串**（标题 + 网址 + 摘要）上：只卡摘要的话，长标题照样突破预算。"""

    assert RESEARCH_SOURCE_CARD_CHARS == 800
    title, url = "标" * 10, "https://example.com/a"
    head = len(title) + len(url) + 2  # 两个换行
    source(title=title, url=url, excerpt="字" * (RESEARCH_SOURCE_CARD_CHARS - head))
    with pytest.raises(ShapeError):
        source(
            title=title,
            url=url,
            excerpt="字" * (RESEARCH_SOURCE_CARD_CHARS - head + 1),
        )
    with pytest.raises(ShapeError):
        source(title="标" * 900)


# —— 结论 ——


def test_finding_requires_id_statement_and_at_least_one_ref() -> None:
    with pytest.raises(ShapeError):
        finding(finding_id="")
    with pytest.raises(ShapeError):
        finding(statement="")
    with pytest.raises(ShapeError):
        finding(refs=())


# —— 报告 ——


def test_empty_findings_are_legal() -> None:
    """搜到来源但没结论，是合法结果（不是失败）；零来源才由用例判失败。"""

    assert report(findings=()).findings == ()


def test_report_requires_a_nonempty_capped_answer() -> None:
    with pytest.raises(ShapeError):
        report(answer="   ")
    with pytest.raises(ShapeError):
        report(answer="字" * (MAX_RESEARCH_ANSWER_CHARS + 1))
    assert report(answer="字" * MAX_RESEARCH_ANSWER_CHARS).answer


def test_report_requires_answer_refs_that_exist_in_sources() -> None:
    with pytest.raises(ShapeError):
        report(answer_refs=())
    with pytest.raises(ShapeError):
        report(answer_refs=(EvidenceId("E99"),))
    assert report(answer_refs=(EvidenceId("E7"),)).answer_refs == (EvidenceId("E7"),)


def test_report_rejects_unknown_refs_and_duplicates() -> None:
    with pytest.raises(ShapeError):
        report(findings=(finding(refs=(EvidenceId("E99"),)),))
    with pytest.raises(ShapeError):
        report(sources=(source(), source(material_id="m-2")))
    with pytest.raises(ShapeError):
        report(sources=(source(), source(eid=EvidenceId("E8"))))
    with pytest.raises(ShapeError):
        report(findings=(finding(), finding()))


def test_report_caps_are_enforced_in_the_domain() -> None:
    sources = tuple(
        source(eid=EvidenceId(f"E{index + 1}"), material_id=f"m-{index}")
        for index in range(MAX_RESEARCH_SOURCES + 1)
    )
    with pytest.raises(TooManyInquiryItems) as sources_error:
        report(sources=sources)
    assert "研究" in str(sources_error.value)

    findings = tuple(
        finding(finding_id=f"F{index}") for index in range(MAX_RESEARCH_FINDINGS + 1)
    )
    with pytest.raises(TooManyInquiryItems):
        report(findings=findings)

    questions = tuple(f"q{index}" for index in range(MAX_RESEARCH_OPEN_QUESTIONS + 1))
    with pytest.raises(TooManyInquiryItems):
        report(open_questions=questions)


def test_report_allows_multiple_refs_and_keeps_order() -> None:
    two = (
        source(),
        source(eid=EvidenceId("E8"), material_id="m-2", url="https://example.com/b"),
    )
    built = report(
        sources=two, findings=(finding(refs=(EvidenceId("E8"), EvidenceId("E7"))),)
    )
    assert [str(ref) for ref in built.findings[0].refs] == ["E8", "E7"]


# —— 范围 ——


def test_topic_scope_requires_text_within_the_cap() -> None:
    assert MAX_RESEARCH_TOPIC_CHARS == 500
    ExplorationScope(kind=ScopeKind.TOPIC, text="字" * MAX_RESEARCH_TOPIC_CHARS)
    with pytest.raises(ShapeError):
        ExplorationScope(kind=ScopeKind.TOPIC, text="   ")
    with pytest.raises(ShapeError):
        ExplorationScope(kind=ScopeKind.TOPIC, text="字" * (MAX_RESEARCH_TOPIC_CHARS + 1))
    with pytest.raises(ShapeError):
        ExplorationScope(kind=ScopeKind.TOPIC, ref="entry-1", text="有 ref 就不叫 topic")


# —— 聚合 ——


def test_research_completion_is_separate_from_inquiry_completion() -> None:
    exploration = research_exploration()
    assert exploration.kind is ExplorationKind.WEB_RESEARCH
    assert exploration.research is None

    exploration.complete_research(report())
    assert exploration.status is ExplorationStatus.DONE
    assert exploration.research is not None
    assert len(exploration.research.sources) == 1

    with pytest.raises(IllegalExplorationTransition):
        exploration.complete_research(report())


def test_inquiry_cannot_complete_research_and_vice_versa() -> None:
    inquiry = Exploration.start(
        exploration_id=XID,
        project_id=PID,
        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
        now=NOW,
    )
    assert inquiry.kind is ExplorationKind.INQUIRY
    with pytest.raises(ShapeError):
        inquiry.complete_research(report())

    research = research_exploration()
    with pytest.raises(ShapeError):
        research.complete(
            hypotheses=(
                Hypothesis(
                    local_id="H1",
                    statement="s",
                    grounding=Grounding.INVENTED,
                    based_on=(),
                    falsification="f",
                    evidence_needed="e",
                ),
            ),
            blind_spots=(),
        )


def test_research_has_no_reviewable_candidates() -> None:
    exploration = research_exploration()
    exploration.complete_research(report())

    assert exploration.candidates() == ()
    with pytest.raises(ShapeError):
        exploration.keep("F1", as_="seed")
    with pytest.raises(ShapeError):
        exploration.dismiss("F1")


def test_kind_and_scope_are_bound_together() -> None:
    with pytest.raises(ShapeError):
        Exploration.start(
            exploration_id=XID,
            project_id=PID,
            scope=ExplorationScope(kind=ScopeKind.ENTRY, ref="entry-1"),
            now=NOW,
            kind=ExplorationKind.WEB_RESEARCH,
        )
    with pytest.raises(ShapeError):
        Exploration.start(
            exploration_id=XID,
            project_id=PID,
            scope=ExplorationScope(kind=ScopeKind.TOPIC, text="主题"),
            now=NOW,
        )


def test_a_done_research_without_a_report_is_refused() -> None:
    with pytest.raises(ShapeError):
        Exploration(
            exploration_id=XID,
            project_id=PID,
            kind=ExplorationKind.WEB_RESEARCH,
            scope=ExplorationScope(kind=ScopeKind.TOPIC, text="主题"),
            created_at=NOW,
            status=ExplorationStatus.DONE,
            research=None,
        )


def test_research_can_abandon_and_fail_like_any_exploration() -> None:
    failed = research_exploration()
    failed.fail("没有取到任何来源")
    assert failed.status is ExplorationStatus.FAILED

    abandoned = research_exploration()
    abandoned.abandon("不查了")
    assert abandoned.status is ExplorationStatus.ABANDONED


# —— payload 往返（仓储用的就是这两个纯函数）——


def test_payload_round_trips_research() -> None:
    exploration = research_exploration()
    exploration.complete_research(report())

    payload = ser.exploration_to_payload(exploration)
    assert payload["research"]["sources"][0]["url"] == "https://example.com/export"
    assert payload["research"]["answer"].startswith("同类产品多把批量导出上限")
    assert payload["research"]["answer_refs"] == ["E7"]

    back = ser.exploration_from_payload(
        payload,
        exploration_id=XID,
        project_id=PID,
        kind=ExplorationKind.WEB_RESEARCH,
        status=ExplorationStatus.DONE,
        created_at=NOW,
    )
    assert back.scope.kind is ScopeKind.TOPIC
    assert back.research is not None
    assert back.research.answer.startswith("同类产品多把批量导出上限")
    assert str(back.research.answer_refs[0]) == "E7"
    assert str(back.research.findings[0].refs[0]) == "E7"
    assert back.research.sources[0].excerpt == "同类产品默认 60 秒上限。"


def test_legacy_payload_without_answer_is_rebuilt_from_the_first_finding() -> None:
    """S4 之前落库的报告没有 answer：读回时按第一条 finding 重建，不丢旧报告。"""

    exploration = research_exploration()
    exploration.complete_research(report())
    payload = ser.exploration_to_payload(exploration)
    payload["research"].pop("answer")
    payload["research"].pop("answer_refs")

    back = ser.exploration_from_payload(
        payload,
        exploration_id=XID,
        project_id=PID,
        kind=ExplorationKind.WEB_RESEARCH,
        status=ExplorationStatus.DONE,
        created_at=NOW,
    )

    assert back.research is not None
    assert back.research.answer == "主流同类产品的导出上限是 60 秒"
    assert back.research.answer_refs == (EvidenceId("E7"),)


def test_legacy_payload_without_findings_is_rebuilt_from_the_first_source() -> None:
    exploration = research_exploration()
    exploration.complete_research(report())
    payload = ser.exploration_to_payload(exploration)
    payload["research"].pop("answer")
    payload["research"].pop("answer_refs")
    payload["research"]["findings"] = []

    back = ser.exploration_from_payload(
        payload,
        exploration_id=XID,
        project_id=PID,
        kind=ExplorationKind.WEB_RESEARCH,
        status=ExplorationStatus.DONE,
        created_at=NOW,
    )

    assert back.research is not None
    assert back.research.answer == "同类产品默认 60 秒上限。"
    assert back.research.answer_refs == (EvidenceId("E7"),)


def test_legacy_payload_without_answer_findings_or_sources_reads_as_no_report() -> None:
    """三样都没有时无法重建答案，只能按"没有报告"读回（不是编一段）。"""

    exploration = research_exploration()
    payload = ser.exploration_to_payload(exploration)
    payload["research"] = {
        "findings": [],
        "sources": [],
        "open_questions": [],
        "notes": [],
    }

    back = ser.exploration_from_payload(
        payload,
        exploration_id=XID,
        project_id=PID,
        kind=ExplorationKind.WEB_RESEARCH,
        status=ExplorationStatus.RUNNING,
        created_at=NOW,
    )

    assert back.research is None


def test_explicit_empty_answer_is_still_a_bad_row() -> None:
    """回读兼容只在**键缺失**时生效：新写入的空答案仍然算坏数据。"""

    exploration = research_exploration()
    exploration.complete_research(report())
    payload = ser.exploration_to_payload(exploration)
    payload["research"]["answer"] = ""

    with pytest.raises(ValueError):
        ser.exploration_from_payload(
            payload,
            exploration_id=XID,
            project_id=PID,
            kind=ExplorationKind.WEB_RESEARCH,
            status=ExplorationStatus.DONE,
            created_at=NOW,
        )


def test_corrupt_research_payload_raises_value_error_for_the_bad_row_net() -> None:
    """坏数据必须翻成 `ValueError`：仓储只按那四个内置异常跳过坏行。"""

    payload = ser.exploration_to_payload(research_exploration())
    payload["research"] = "oops"
    with pytest.raises(ValueError):
        ser.exploration_from_payload(
            payload,
            exploration_id=XID,
            project_id=PID,
            kind=ExplorationKind.WEB_RESEARCH,
            status=ExplorationStatus.RUNNING,
            created_at=NOW,
        )

    payload["research"] = {"sources": [{"eid": "E7"}]}
    with pytest.raises(ValueError):
        ser.exploration_from_payload(
            payload,
            exploration_id=XID,
            project_id=PID,
            kind=ExplorationKind.WEB_RESEARCH,
            status=ExplorationStatus.RUNNING,
            created_at=NOW,
        )


def test_legacy_payload_without_research_key_reads_as_none() -> None:
    payload = ser.exploration_to_payload(research_exploration())
    payload.pop("research")

    back = ser.exploration_from_payload(
        payload,
        exploration_id=XID,
        project_id=PID,
        kind=ExplorationKind.WEB_RESEARCH,
        status=ExplorationStatus.RUNNING,
        created_at=NOW,
    )
    assert back.research is None
