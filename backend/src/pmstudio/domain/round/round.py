"""Round 聚合：一轮怎么推进，全在这里。LangGraph 的 state 只放 round_id 与 step。"""

from dataclasses import dataclass
from datetime import datetime

from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.draft import Draft
from pmstudio.domain.round.opinion import Opinion, SeatReview
from pmstudio.domain.round.questionnaire import AnswerSet, Questionnaire
from pmstudio.domain.round.step import LEGAL_ADVANCES, MAX_SEAT_ROUNDS, Step, WaitingOn
from pmstudio.domain.shared.errors import (
    AlreadyAnswered,
    ContentWithoutEvidence,
    EmptyUserInput,
    FeatureAlreadyAuthorized,
    IllegalStepTransition,
    NotAwaitingAnswers,
    SeatRoundLimitReached,
    ShapeError,
    UnknownQuestion,
    UnknownSeat,
    WaitingOnHuman,
)
from pmstudio.domain.shared.ids import EvidenceId, FeatureSlug, ProjectId, RoundId
from pmstudio.domain.shared.target import Target


@dataclass(frozen=True, slots=True)
class Failure:
    at_step: Step
    waiting_on: WaitingOn
    reason: str


class Round:
    def __init__(
        self,
        *,
        round_id: RoundId,
        project_id: ProjectId,
        user_input: str,
        created_at: datetime,
        step: Step = Step.STARTED,
        waiting_on: WaitingOn = WaitingOn.NONE,
        claims: tuple[Claim, ...] = (),
        seats: tuple[str, ...] = (),
        seat_reviews: tuple[SeatReview, ...] = (),
        questionnaire: Questionnaire | None = None,
        answer_sets: tuple[AnswerSet, ...] = (),
        draft: Draft | None = None,
        seat_round_count: int = 0,
        authorized_new_features: dict[FeatureSlug, EvidenceId] | None = None,
        failure: Failure | None = None,
        confirmed_version_no: int | None = None,
    ) -> None:
        self._round_id = round_id
        self._project_id = project_id
        self._user_input = user_input
        self._created_at = created_at
        self._step = step
        self._waiting_on = waiting_on
        self._claims = tuple(claims)
        self._seats = tuple(seats)
        self._seat_reviews = tuple(seat_reviews)
        self._questionnaire = questionnaire
        self._answer_sets = tuple(answer_sets)
        self._draft = draft
        self._seat_round_count = seat_round_count
        self._authorized = dict(authorized_new_features or {})
        self._failure = failure
        self._confirmed_version_no = confirmed_version_no

    @classmethod
    def start(
        cls, *, round_id: RoundId, project_id: ProjectId, user_input: str, now: datetime
    ) -> "Round":
        if not user_input.strip():
            raise EmptyUserInput()
        return cls(
            round_id=round_id, project_id=project_id, user_input=user_input, created_at=now
        )

    # —— 只读 ——
    @property
    def round_id(self) -> RoundId:
        return self._round_id

    @property
    def project_id(self) -> ProjectId:
        return self._project_id

    @property
    def user_input(self) -> str:
        return self._user_input

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def step(self) -> Step:
        return self._step

    @property
    def waiting_on(self) -> WaitingOn:
        return self._waiting_on

    @property
    def claims(self) -> tuple[Claim, ...]:
        return self._claims

    @property
    def seats(self) -> tuple[str, ...]:
        return self._seats

    @property
    def seat_reviews(self) -> tuple[SeatReview, ...]:
        return self._seat_reviews

    @property
    def opinions(self) -> tuple[Opinion, ...]:
        return tuple(opinion for review in self._seat_reviews for opinion in review.opinions)

    @property
    def questionnaire(self) -> Questionnaire | None:
        return self._questionnaire

    @property
    def answer_sets(self) -> tuple[AnswerSet, ...]:
        return self._answer_sets

    @property
    def draft(self) -> Draft | None:
        return self._draft

    @property
    def seat_round_count(self) -> int:
        return self._seat_round_count

    @property
    def can_begin_seat_round(self) -> bool:
        return self._seat_round_count < MAX_SEAT_ROUNDS

    @property
    def authorized_new_features(self) -> frozenset[FeatureSlug]:
        return frozenset(self._authorized)

    @property
    def failure(self) -> Failure | None:
        return self._failure

    @property
    def confirmed_version_no(self) -> int | None:
        return self._confirmed_version_no

    @property
    def write_scope(self) -> frozenset[Target]:
        """本轮允许写的去处：主张的 target ∪ 已作答问题的 target。"""

        scope = {claim.target for claim in self._claims}
        if self._questionnaire is not None:
            answered = {
                answer.question_id
                for answer_set in self._answer_sets
                for answer in answer_set.answers
            }
            scope |= {
                question.target
                for question in self._questionnaire.questions
                if question.question_id in answered
            }
        return frozenset(scope)

    # —— 推进 ——
    def advance(self, target: Step) -> None:
        if self._waiting_on is not WaitingOn.NONE:
            raise WaitingOnHuman(waiting_on=self._waiting_on)
        if target not in LEGAL_ADVANCES[self._step]:
            raise IllegalStepTransition(current=self._step, target=target)
        self._step = target

    def attach_claims(self, claims: tuple[Claim, ...] | list[Claim]) -> None:
        claims = tuple(claims)
        if not claims:
            raise ShapeError("一轮至少要有一条主张")
        for item in claims:
            if item.eid is None:
                raise ContentWithoutEvidence(what=f"主张 {item.claim_id}")
        self._claims = claims
        self._advance_to(Step.CLAIMS_READY, waiting_on=WaitingOn.OPENING)

    def submit_opening(self, *, seats: tuple[str, ...] | list[str]) -> None:
        self._require_step(Step.CLAIMS_READY)
        self._require_waiting(WaitingOn.OPENING)
        unique_seats = tuple(dict.fromkeys(seats))
        if not unique_seats:
            raise ShapeError("席位编制不能为空")
        self._seats = unique_seats
        self._begin_seat_round()
        self._advance_to(Step.SEATS_RUNNING)

    def attest(self, review: SeatReview) -> None:
        self._require_step(Step.SEATS_RUNNING)
        if review.seat_id not in self._seats:
            raise UnknownSeat(seat_id=review.seat_id)
        self._seat_reviews = self._seat_reviews + (review,)

    def integrate(
        self, *, questionnaire: Questionnaire | None = None, draft: Draft | None = None
    ) -> None:
        self._require_step(Step.SEATS_RUNNING)
        if (questionnaire is None) == (draft is None):
            raise ShapeError("整合必须给出问卷或草案其中之一（问卷不是必然环节）")
        if questionnaire is not None:
            if questionnaire.round_id != self._round_id:
                raise ShapeError("问卷不属于本轮", form_id=str(questionnaire.form_id))
            self._questionnaire = questionnaire
            self._advance_to(Step.AWAITING_ANSWERS, waiting_on=WaitingOn.QUESTIONNAIRE)
            return
        assert draft is not None
        if draft.round_id != self._round_id:
            raise ShapeError("草案不属于本轮", draft_id=str(draft.draft_id))
        self._draft = draft
        self._advance_to(Step.AWAITING_CONFIRM, waiting_on=WaitingOn.DRAFT)

    def submit_answers(self, answer_set: AnswerSet) -> None:
        questionnaire = self._questionnaire
        if (
            self._step is not Step.AWAITING_ANSWERS
            or self._waiting_on is not WaitingOn.QUESTIONNAIRE
            or questionnaire is None
        ):
            raise NotAwaitingAnswers(step=self._step)
        if answer_set.form_id != questionnaire.form_id:
            raise UnknownQuestion(question_id=answer_set.form_id)
        if any(item.form_id == answer_set.form_id for item in self._answer_sets):
            raise AlreadyAnswered(form_id=answer_set.form_id)
        known = {question.question_id for question in questionnaire.questions}
        touched = [answer.question_id for answer in answer_set.answers] + list(answer_set.skipped)
        for question_id in touched:
            if question_id not in known:
                raise UnknownQuestion(question_id=question_id)
        self._begin_seat_round()
        self._answer_sets = self._answer_sets + (answer_set,)
        self._advance_to(Step.SEATS_RUNNING)

    def confirm(self, version_no: int) -> None:
        self._require_step(Step.AWAITING_CONFIRM)
        self._require_waiting(WaitingOn.DRAFT)
        self._confirmed_version_no = version_no
        self._advance_to(Step.DONE)

    def authorize_new_feature(self, slug: FeatureSlug, *, basis: EvidenceId) -> None:
        if slug in self._authorized:
            raise FeatureAlreadyAuthorized(feature_slug=slug)
        self._authorized[slug] = basis

    def fail(self, reason: str) -> None:
        if self._step is Step.DONE:
            raise IllegalStepTransition(current=self._step, target=Step.FAILED)
        self._failure = Failure(at_step=self._step, waiting_on=self._waiting_on, reason=reason)
        self._waiting_on = WaitingOn.NONE
        self._step = Step.FAILED

    def resume(self) -> None:
        if self._step is not Step.FAILED or self._failure is None:
            raise ShapeError("没有可恢复的失败")
        self._step = self._failure.at_step
        self._waiting_on = self._failure.waiting_on
        self._failure = None

    # —— 内部 ——
    def _advance_to(self, target: Step, *, waiting_on: WaitingOn = WaitingOn.NONE) -> None:
        self._waiting_on = WaitingOn.NONE
        self.advance(target)
        self._waiting_on = waiting_on

    def _begin_seat_round(self) -> None:
        if not self.can_begin_seat_round:
            raise SeatRoundLimitReached(limit=MAX_SEAT_ROUNDS)
        self._seat_round_count += 1

    def _require_step(self, target: Step) -> None:
        if self._step is not target:
            raise IllegalStepTransition(current=self._step, target=target)

    def _require_waiting(self, target: WaitingOn) -> None:
        if self._waiting_on is not target:
            raise WaitingOnHuman(waiting_on=self._waiting_on)
