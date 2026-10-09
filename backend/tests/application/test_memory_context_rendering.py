"""L1 + L2 在应用层的渲染：context.py 的新函数。"""


from pmstudio.application.context import (
    render_observations_index,
    render_project_context,
)
from pmstudio.domain.memory.context import ProjectContext
from pmstudio.domain.memory.observations import (
    ObservationType,
    RoundObservation,
)


class TestRenderProjectContext:
    def test_empty(self):
        text = render_project_context(ProjectContext())
        assert "<project-context>" in text

    def test_with_domain(self):
        text = render_project_context(ProjectContext(domain="团购平台"))
        assert "团购平台" in text


class TestRenderObservationsIndex:
    """L2 索引渲染——只给标题，≤5 条。"""

    def test_empty(self):
        text = render_observations_index(())
        assert text == ""

    def test_limit_5(self):
        obs = tuple(
            RoundObservation(
                obs_id=f"obs-{i}",
                round_id="r1",
                project_id="p1",
                obs_type=ObservationType.SEAT_INSIGHT,
                title=f"洞察{i}",
                detail="",
                source_ref="seat:biz:O1",
            )
            for i in range(10)
        )
        text = render_observations_index(obs)
        # 只渲染前 5 条
        assert "洞察0" in text
        assert "洞察4" in text
        # 第 6 条不渲染
        assert "洞察5" not in text

    def test_format_includes_source(self):
        obs = (
            RoundObservation(
                obs_id="obs-1",
                round_id="r1",
                project_id="p1",
                obs_type=ObservationType.GAP_IDENTIFIED,
                title="SKU数量？",
                detail="",
                source_ref="seat.data_scale:O2",
            ),
        )
        text = render_observations_index(obs)
        assert "SKU数量？" in text
        assert "seat.data_scale:O2" in text

    def test_question_mark_for_gap(self):
        obs = (
            RoundObservation(
                obs_id="obs-1",
                round_id="r1",
                project_id="p1",
                obs_type=ObservationType.GAP_IDENTIFIED,
                title="问题",
                detail="",
                source_ref="seat:biz:O1",
            ),
        )
        text = render_observations_index(obs)
        # GAP_IDENTIFIED 用问号标记
        assert "?" in text or "？" in text

    def test_insight_mark_for_seat_insight(self):
        obs = (
            RoundObservation(
                obs_id="obs-1",
                round_id="r1",
                project_id="p1",
                obs_type=ObservationType.SEAT_INSIGHT,
                title="判断",
                detail="",
                source_ref="seat:biz:O1",
            ),
        )
        text = render_observations_index(obs)
        # SEAT_INSIGHT 用洞察标记
        assert "洞察" in text or "●" in text
