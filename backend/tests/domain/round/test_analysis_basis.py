"""分析者的「我读了什么才切出这些主张」要有地方放、并且能被看见。

背景（2026-10-10 Static 高信号审计）：`ClaimsPacket.basis` 是**必填**字段，
模型每轮都认真写（真跑样例：「基于用户输入…以及现有文档中 s5 (daily-report-generation),
s8 (…), s11 (open issues) 的内容进行切分。」），但这一路**零消费方**——
不落库、不进任何渲染、不进任何事件。等于每轮白付一次 mandatory tax。
"""

from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import RoundId
from tests.support.builders import NOW, PROJECT


def _round(**kwargs) -> Round:
    return Round(
        round_id=RoundId("r1"),
        project_id=kwargs.get("project_id", PROJECT),
        user_input="按这份写",
        created_at=NOW,
        step=Step.CLAIMS_READY,
        waiting_on=WaitingOn.OPENING,
        analysis_basis=kwargs.get("analysis_basis", ""),
    )


def test_a_round_remembers_what_the_analyzer_read():
    round_ = _round(analysis_basis="读了 s5 与 s11")

    assert round_.analysis_basis == "读了 s5 与 s11"


def test_an_old_round_without_it_reads_as_empty():
    """老行没有这个键——不许炸，回落空串。"""

    round_ = _round()

    assert round_.analysis_basis == ""
