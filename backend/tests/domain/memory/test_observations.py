"""L2 轮次观察：从席位意见中提炼可跨轮追踪的洞察。

口径：`.codex/docs/设计/05-DDD架构.md` §记忆层设计。
"""

from datetime import datetime

from pmstudio.application.context import extract_observations_from_reviews
from pmstudio.domain.memory.observations import (
    ObservationType,
)
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview

NOW = datetime(2026, 10, 8, 10, 0, 0)

#: 提取函数不自己取时间（调用方给轮次的时刻），所以纯函数测试里自己指定。
AT = "2026-10-08T10:00:00+00:00"


class SeqIds:
    def __init__(self) -> None:
        self._seq: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        self._seq[prefix] = self._seq.get(prefix, 0) + 1
        return f"{prefix}-{self._seq[prefix]}"


def _opinion(
    local_id: str,
    form: OpinionForm,
    about: str,
    claim: str,
    because: str = "依据",
) -> Opinion:
    return Opinion(
        local_id=local_id,
        form=form,
        about=about,
        claim=claim,
        because=because,
        evidence_refs=(),
        basis_note="无已有编号",
    )


def _review(seat_id: str, *opinions: Opinion) -> SeatReview:
    return SeatReview(
        seat_id=seat_id,
        basis="basis",
        opinions=opinions,
        pass_index=1,
    )


class TestExtractObservationsFromReviews:
    """extract_observations_from_reviews: 纯函数，从 SeatReview 提炼观察。"""

    def test_question_becomes_gap_identified(self):
        review = _review(
            "seat.data_scale",
            _opinion("O1", OpinionForm.QUESTION, "SKU", "SKU数量？", "影响损耗"),
        )
        obs = extract_observations_from_reviews((review,), "r1", "p1", SeqIds(), created_at=AT)
        assert len(obs) == 1
        assert obs[0].obs_type == ObservationType.GAP_IDENTIFIED
        assert "SKU" in obs[0].title
        assert obs[0].source_ref == "seat.data_scale:O1"

    def test_judgment_becomes_seat_insight(self):
        review = _review(
            "seat.business_rules",
            _opinion("O1", OpinionForm.JUDGMENT, "准入", "保证金", "降低流失"),
        )
        obs = extract_observations_from_reviews((review,), "r1", "p1", SeqIds(), created_at=AT)
        assert len(obs) == 1
        assert obs[0].obs_type == ObservationType.SEAT_INSIGHT
        assert "准入" in obs[0].title
        assert "保证金" in obs[0].detail

    def test_multiple_opinions_all_extracted(self):
        review = _review(
            "seat.business_rules",
            _opinion("O1", OpinionForm.JUDGMENT, "准入", "保证金"),
            _opinion("O2", OpinionForm.QUESTION, "退出", "退还周期？"),
            _opinion("O3", OpinionForm.JUDGMENT, "品控", "标准化流程"),
        )
        obs = extract_observations_from_reviews((review,), "r1", "p1", SeqIds(), created_at=AT)
        assert len(obs) == 3
        types = [o.obs_type for o in obs]
        assert types.count(ObservationType.SEAT_INSIGHT) == 2
        assert types.count(ObservationType.GAP_IDENTIFIED) == 1

    def test_multiple_reviews_all_extracted(self):
        r1 = _review("seat.business_rules", _opinion("O1", OpinionForm.JUDGMENT, "准入", "保证金"))
        r2 = _review("seat.data_scale", _opinion("O1", OpinionForm.QUESTION, "SKU", "SKU数量？"))
        obs = extract_observations_from_reviews((r1, r2), "r1", "p1", SeqIds(), created_at=AT)
        assert len(obs) == 2

    def test_empty_reviews_returns_empty(self):
        obs = extract_observations_from_reviews((), "r1", "p1", SeqIds(), created_at=AT)
        assert obs == ()

    def test_empty_opinions_returns_empty(self):
        review = _review("seat.business_rules")
        obs = extract_observations_from_reviews((review,), "r1", "p1", SeqIds(), created_at=AT)
        assert obs == ()

    def test_observation_has_correct_round_and_project(self):
        review = _review(
            "seat.business_rules",
            _opinion("O1", OpinionForm.JUDGMENT, "准入", "保证金"),
        )
        obs = extract_observations_from_reviews((review,), "r42", "p-abc", SeqIds(), created_at=AT)
        assert obs[0].round_id == "r42"
        assert obs[0].project_id == "p-abc"

    def test_observation_id_generated(self):
        review = _review(
            "seat.business_rules",
            _opinion("O1", OpinionForm.JUDGMENT, "准入", "保证金"),
        )
        ids = SeqIds()
        obs = extract_observations_from_reviews((review,), "r1", "p1", ids, created_at=AT)
        assert obs[0].obs_id.startswith("obs-")

    def test_observation_preserves_detail(self):
        review = _review(
            "seat.business_rules",
            _opinion("O1", OpinionForm.JUDGMENT, "准入", "团长需缴纳保证金", "降低流失率"),
        )
        obs = extract_observations_from_reviews((review,), "r1", "p1", SeqIds(), created_at=AT)
        assert "保证金" in obs[0].detail
        assert "降低流失" in obs[0].detail

    def test_every_observation_carries_the_given_time(self):
        """R：时间由调用方盖章且**逐条**盖上——仓储的排序判据就是它。

        漏盖一条就是一条"永远排最后"的观察，而测试看不出来（空时间不报错）。
        """

        review = _review(
            "seat.business_rules",
            _opinion("O1", OpinionForm.JUDGMENT, "准入", "保证金"),
            _opinion("O2", OpinionForm.QUESTION, "SKU", "SKU数量？"),
        )
        obs = extract_observations_from_reviews((review,), "r1", "p1", SeqIds(), created_at=AT)
        assert [o.created_at for o in obs] == [AT, AT]
