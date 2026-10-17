"""L1 + L2 在应用层的渲染：context.py 的新函数。"""


from pmstudio.application.context import (
    OBSERVATION_DETAIL_CHARS,
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

    def test_notes_omitted_decisions(self):
        """截断要照实说：只带前 10 条，就得说清一共几条。"""

        text = render_project_context(
            ProjectContext(
                top_decisions=tuple(f"决策{i}" for i in range(10)),
                decisions_total=12,
            )
        )
        assert "共 12 条" in text
        assert "只带了前 10 条" in text

    def test_notes_omitted_open_questions(self):
        text = render_project_context(
            ProjectContext(
                open_questions=tuple(f"问题{i}" for i in range(10)),
                open_questions_total=15,
            )
        )
        assert "共 15 条" in text
        assert "只带了前 10 条" in text

    def test_no_note_when_nothing_omitted(self):
        text = render_project_context(
            ProjectContext(top_decisions=("d1", "d2"), decisions_total=2)
        )
        assert "只带了前" not in text


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

    def test_limit_5_notes_omitted(self):
        """截断要照实说：只带前 5 条，就说清一共几条。"""
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
        assert "共 10 条" in text
        assert "只带了前 5 条" in text

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


    def test_the_detail_rides_along_with_the_title(self):
        """R：观察的实质内容（`detail`）要进索引。

        改前只给 `title`——模型看得到"有这条观察"，看不到它的结论；
        真库里 46 条观察的 detail 全是非空真内容（87–150 字），全部白存。
        """

        obs = (
            RoundObservation(
                obs_id="obs-1",
                round_id="r1",
                project_id="p1",
                obs_type=ObservationType.SEAT_INSIGHT,
                title="讨论区验收标准缺失",
                detail="必须在 s12 补验收条目（依据：当前 s12 只覆盖调研）",
                source_ref="seat.business_rules:O4",
            ),
        )
        text = render_observations_index(obs)

        assert "讨论区验收标准缺失" in text
        assert "必须在 s12 补验收条目" in text

    def test_a_long_detail_is_clipped(self):
        """R：`opinion.claim/because` 在 schema 里**无长度上限**，防 detail 撑爆预算。"""

        obs = (
            RoundObservation(
                obs_id="obs-1",
                round_id="r1",
                project_id="p1",
                obs_type=ObservationType.SEAT_INSIGHT,
                title="标题",
                detail="长" * 500,
                source_ref="seat:biz:O1",
            ),
        )
        text = render_observations_index(obs)

        assert "长" * OBSERVATION_DETAIL_CHARS in text
        assert "长" * (OBSERVATION_DETAIL_CHARS + 1) not in text
        assert "…" in text

    def test_an_empty_detail_does_not_leave_a_dangling_colon(self):
        """R：老行 `detail=''`（schema default）——不许渲染出一个空冒号尾巴。"""

        obs = (
            RoundObservation(
                obs_id="obs-1",
                round_id="r1",
                project_id="p1",
                obs_type=ObservationType.SEAT_INSIGHT,
                title="只有标题",
                detail="",
                source_ref="seat:biz:O1",
            ),
        )
        text = render_observations_index(obs)

        line = next(item for item in text.splitlines() if "只有标题" in item)
        assert not line.rstrip().endswith("：")
        assert not line.rstrip().endswith(":")

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
