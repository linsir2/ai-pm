"""Round 聚合：一轮怎么推进，全在这里。LangGraph 的 state 只放 round_id 与 step。"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from pmstudio.domain.round.claim import Claim, Contradiction
from pmstudio.domain.round.draft import Draft
from pmstudio.domain.round.opinion import Opinion, SeatReview
from pmstudio.domain.round.questionnaire import AnswerSet, ListItem, Question, Questionnaire
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
from pmstudio.domain.shared.ids import EvidenceId, FeatureSlug, FormId, ProjectId, RoundId
from pmstudio.domain.shared.target import Target


@dataclass(frozen=True, slots=True)
class Failure:
    at_step: Step
    waiting_on: WaitingOn
    reason: str


@dataclass(frozen=True, slots=True)
class SeatNomination:
    """Analyzer 读这一轮输入之后挑的席位，外加一句"为什么这一轮需要它"。

    这张名单是**判断的产物**，不是关键词匹配的结果：目录只提供"有哪些席位"，
    要不要请、请谁，由读得懂这段话的那一次模型调用说了算。用户拿到的默认勾选就是它，
    可以做减法（也可以从目录里补，写入端只要求席位在注册表里）。
    """

    seat_id: str
    why: str

    def __post_init__(self) -> None:
        if not self.seat_id.strip():
            raise ShapeError("席位提名必须有 seat_id")
        if not self.why.strip():
            raise ShapeError("席位提名必须写清为什么请它", seat_id=self.seat_id)


class DropSource(StrEnum):
    """留痕是谁发现的。"""

    INTEGRATOR = "integrator"  # 模型在报文里申报的（原因它自己写）
    ASSEMBLY = "assembly"  # 装配层算出来的：归因对不上号
    UNACCOUNTED = "unaccounted"  # 装配层核出来的：既没说丢、也没交代落到哪儿


def widened(target: Target) -> Target:
    """把功能字段的去处放宽到**整棵功能子树**（2026-09-29 作者拍板 D1）。

    `Target.is_within` 的语义是"scope 不给 field 就覆盖这个功能的全部字段"，
    所以"点了 `s5/order-export.one_liner`"放宽成"点了 `s5/order-export`"即可。

    为什么要放宽：P8 的提示词要求"主张点名过的功能已授权，内容要落到功能字段，
    拿不准也别降级成章节级"（`02` §11.0.9 口径三），而写范围原来只认主张点名的**那一个字段**——
    两条规则自己跟自己打架，真跑里一轮刷出 20 条 advisory，把"零功能候选"那条置顶告警挤得看不见。

    **只对功能字段放宽**：章节槽（`s8.compatibility`）说一个不等于授权另一个，
    章节级（只给 section_id）本来就覆盖整章，原样返回。
    """

    if target.feature_slug is None:
        return target
    return Target.body(target.section_id, feature_slug=target.feature_slug)


@dataclass(frozen=True, slots=True)
class DroppedItem:
    """一条"没进卡片"的留痕（`04-未决.md` C5 后半）。

    `handle` 是**席位条目地址**（`seat.data_scale:O2`），不是出处编号——疑问根本没有编号。
    形状**故意不校验**：模型写歪的地址正是要留痕的东西，写歪就抛错等于"写歪了就不留痕"。

    为什么带 `pass_index`：同一轮里席位会跑好几趟（答案回来再跑一次），而 `local_id`
    每趟都从 O1 重新开始；只有加上趟数，界面才知道这行该挂在哪个席位包下面。
    """

    handle: str
    reason: str
    source: DropSource
    seat_id: str | None
    pass_index: int

    def __post_init__(self) -> None:
        if not self.handle.strip():
            raise ShapeError("留痕必须写清是哪一条席位意见（handle）")
        if not self.reason.strip():
            raise ShapeError("留痕必须写清原因", handle=self.handle)
        if self.pass_index < 1:
            raise ShapeError("留痕必须说明属于哪一趟席位轮", handle=self.handle)


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
        nominated_seats: tuple[SeatNomination, ...] = (),
        seat_reviews: tuple[SeatReview, ...] = (),
        questionnaire: Questionnaire | None = None,
        answer_sets: tuple[AnswerSet, ...] = (),
        draft: Draft | None = None,
        seat_round_count: int = 0,
        authorized_new_features: dict[FeatureSlug, EvidenceId] | None = None,
        failure: Failure | None = None,
        confirmed_version_no: int | None = None,
        dropped: tuple[DroppedItem, ...] = (),
        abandon_reason: str | None = None,
        active_seats: tuple[str, ...] = (),
        deferred: tuple[Question, ...] = (),
        carried_sources: dict[str, str] | None = None,
        questionnaire_history: tuple[Questionnaire, ...] = (),
        list_items: tuple[ListItem, ...] = (),
        contradictions: tuple[Contradiction, ...] = (),
        retry_hint: str | None = None,
        proposed_offered: int | None = None,
        proposed_accepted: int | None = None,
        proposal_ignore_streak: int = 0,
        extra_batches: int = 0,
    ) -> None:
        self._round_id = round_id
        self._project_id = project_id
        self._user_input = user_input
        self._created_at = created_at
        self._step = step
        self._waiting_on = waiting_on
        self._claims = tuple(claims)
        self._seats = tuple(seats)
        self._nominated_seats = tuple(nominated_seats)
        self._seat_reviews = tuple(seat_reviews)
        self._questionnaire = questionnaire
        self._answer_sets = tuple(answer_sets)
        self._draft = draft
        self._seat_round_count = seat_round_count
        self._authorized = dict(authorized_new_features or {})
        self._failure = failure
        self._confirmed_version_no = confirmed_version_no
        self._dropped = tuple(dropped)
        self._abandon_reason = abandon_reason
        self._active_seats = tuple(active_seats)
        #: 轮内未决队列：本批被 5 题上限挤掉的题（下一批优先问；出草案时落 s11）
        self._deferred = tuple(deferred)
        #: 这一轮把哪些 s11 未决条目问回来了（question_id → entry_id）：答完要机械撤下它们
        self._carried_sources = dict(carried_sources or {})
        self._list_items = tuple(list_items)
        self._contradictions = tuple(contradictions)
        self._retry_hint = retry_hint
        self._proposed_offered = proposed_offered
        self._proposed_accepted = proposed_accepted
        self._proposal_ignore_streak = proposal_ignore_streak
        self._extra_batches = extra_batches
        #: 本轮**每一批**问卷（追加不覆盖）。`_questionnaire` 仍是"当前批"，
        #: 历史那一份给渲染与回查用——题号每批重排会让裸题号指向别的批（计划 §0.1）
        self._questionnaire_history = tuple(questionnaire_history)
        if not self._questionnaire_history and self._questionnaire is not None:
            # 老库/老 payload 没有历史键：退化成"只有当前这一批"，不编历史
            self._questionnaire_history = (self._questionnaire,)

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
    def nominated_seats(self) -> tuple[SeatNomination, ...]:
        return self._nominated_seats

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
    def questionnaire_history(self) -> tuple[Questionnaire, ...]:
        """本轮**问过的每一批**问卷（追加不覆盖）。

        要它是因为"按题号回查"的六处地方（计划 §0.1）：题面、跳过后果、选项文案都得回到
        **出题那一批**去找，拿当前批反查会张冠李戴。当前批仍是 `questionnaire`。
        """

        return self._questionnaire_history

    @property
    def asked_question_count(self) -> int:
        """本轮**已经问出去**多少题——下一批的题号从这里接着发（D4：题号轮内唯一）。

        只数"问出去的"（进了问卷的）；轮内队列里没问上的题不算，它们下一批会重新编号。
        """

        return sum(len(form.questions) for form in self._questionnaire_history)

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
    def authorized_feature_basis(self) -> dict[FeatureSlug, EvidenceId]:
        """功能授权的依据编号（持久化与追溯要用；`authorized_new_features` 只给 slug）。"""

        return dict(self._authorized)

    @property
    def failure(self) -> Failure | None:
        return self._failure

    @property
    def confirmed_version_no(self) -> int | None:
        return self._confirmed_version_no

    @property
    def dropped(self) -> tuple[DroppedItem, ...]:
        """留痕通道：`integrator` 申报 / `assembly` 归因对不上 / `unaccounted` 没交代去处。

        跨趟累加：一趟席位轮一条命，历史留痕不覆盖。
        """

        return self._dropped

    @property
    def abandon_reason(self) -> str | None:
        return self._abandon_reason

    @property
    def active_seats(self) -> tuple[str, ...]:
        """这一趟**实际要跑的**名单（开场卡确认时 = 用户编制；答案轮 = 缩编结果）。

        `seats`（用户编制）永远不动：R7 与复述卡要的是"这一轮请了谁"，
        不是"这一趟跑了谁"（`04` C8）。
        """

        return self._active_seats or self._seats

    def abandon(self, reason: str) -> None:
        """放弃这一轮：幂等、终点，**只记第一次**的原因。

        已经 done / failed / abandoned 的一轮什么都不做（终态不可改）。
        空原因要报错——"为什么放弃"是这条记录唯一的用处。
        """

        if self._step in (Step.DONE, Step.FAILED, Step.ABANDONED):
            return
        if not reason.strip():
            raise ShapeError("放弃要写清原因")
        self._abandon_reason = reason.strip()
        self._waiting_on = WaitingOn.NONE
        self._step = Step.ABANDONED

    @property
    def write_scope(self) -> frozenset[Target]:
        """本轮允许写的去处：主张的 target ∪ 已作答问题的 target（**功能字段放宽到功能级**）。

        放宽的口径与理由见 `widened()`。它只影响"跑出本轮去处"这条**提示**（`ScopeGuard`），
        写入的硬约束（不得新增未授权功能子树、不得离开 13 章）在 `Document.apply` 与 `Target`
        构造里。
        """

        scope = {widened(claim.target) for claim in self._claims}
        # 已答题的去处回**出题那一批**找（`(表单, 题号)`）：拿当前批按裸题号反查，会把
        # 上一批答过的去处丢掉、把这一批只是问过的去处混进来（计划 §0.2 探针 2）。
        answered = {
            (str(answer_set.form_id), str(answer.question_id))
            for answer_set in self._answer_sets
            for answer in answer_set.answers
        }
        scope |= {
            widened(question.target)
            for form in self._questionnaire_history
            for question in form.questions
            if (str(form.form_id), str(question.question_id)) in answered
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

    @property
    def contradictions(self) -> tuple[Contradiction, ...]:
        """这一轮被判成**立场互斥**的主张对子（analyzer 判的，只判一次）。

        装配层拿它给"同时覆盖两侧"的题目盖章 `kind=conflict`；整合者也看得到它（要出题问用户）。
        """

        return self._contradictions

    def attach_contradictions(
        self, items: tuple[Contradiction, ...] | list[Contradiction]
    ) -> None:
        """挂上互斥对子。**要在 `attach_claims` 之后调**：它得核对主张确实在这一轮里。

        指向不存在的主张 = 解析那一层已经丢过一遍（丢条留痕）；这里再拦一道，
        是为了不让"领域里不存在的对子"悄悄进聚合。
        """

        known = {claim.claim_id for claim in self._claims}
        for item in items:
            for claim_id in (item.claim_id_a, item.claim_id_b):
                if claim_id not in known:
                    raise ShapeError("互斥对子指向了这一轮不存在的主张", claim_id=claim_id)
        self._contradictions = tuple(items)

    def nominate(self, seats: tuple[SeatNomination, ...] | list[SeatNomination]) -> None:
        """记下这一轮的席位提名（Analyzer 挑的）。同一席位只留第一次出现的那条。"""

        unique: dict[str, SeatNomination] = {}
        for item in seats:
            unique.setdefault(item.seat_id, item)
        if not unique:
            raise ShapeError("席位提名不能为空")
        self._nominated_seats = tuple(unique.values())

    def submit_opening(self, *, seats: tuple[str, ...] | list[str]) -> None:
        self._require_step(Step.CLAIMS_READY)
        self._require_waiting(WaitingOn.OPENING)
        unique_seats = tuple(dict.fromkeys(seats))
        if not unique_seats:
            raise ShapeError("席位编制不能为空")
        self._seats = unique_seats
        self._active_seats = unique_seats  # 开场卡确认：这一趟就是用户编制
        self._begin_seat_round()
        self._advance_to(Step.SEATS_RUNNING)

    def attest(self, review: SeatReview) -> None:
        self._require_step(Step.SEATS_RUNNING)
        if review.seat_id not in self.active_seats:
            raise UnknownSeat(seat_id=review.seat_id)
        self._seat_reviews = self._seat_reviews + (
            replace(review, pass_index=self._seat_round_count),
        )

    def note_dropped(self, items: Iterable[DroppedItem]) -> None:
        """记一条留痕。与 `integrate(dropped=…)` 同一个口子，只是**不必在整合那一步**。

        谁用：席位轮的超限截断、claims 里被丢掉的目录外席位提名。
        为什么是追加而不是集合：留痕是历史记录，同一件事发生两次就该有两条。
        """

        self._dropped = self._dropped + tuple(items)

    # —— 轮内未决队列（P9）——

    @property
    def deferred(self) -> tuple[Question, ...]:
        """本批没问上的题（先进先出）。它**不跨轮**：出草案或问卷到顶时一律落 s11。"""

        return self._deferred

    def defer(self, questions: Sequence[Question]) -> None:
        """把没问上的题存进队列。去重按"同一个去处 + 同一个问法"，不是按 question_id。"""

        seen = {
            (item.target.label(), " ".join(item.title.split())) for item in self._deferred
        }
        fresh: list[Question] = []
        for item in questions:
            key = (item.target.label(), " ".join(item.title.split()))
            if key in seen:
                continue
            seen.add(key)
            fresh.append(item)
        self._deferred = self._deferred + tuple(fresh)

    def peek_deferred(self, limit: int) -> tuple[Question, ...]:
        """只看不取（模型调用前用它算 carried）。"""

        return self._deferred[:limit]

    def take_deferred(self, limit: int) -> tuple[Question, ...]:
        taken, self._deferred = self._deferred[:limit], self._deferred[limit:]
        return taken

    def clear_deferred(self) -> tuple[Question, ...]:
        taken, self._deferred = self._deferred, ()
        return taken

    def remember_carried(self, form_id: FormId, questions: Sequence[Question]) -> None:
        """记住"这条题来自哪条 s11 未决条目"。

    为什么不能只看当前问卷：问卷一批换一批，下一批就看不到上一批的 `from_entry` 了；
    而"答完要撤下哪条未决条目"必须留到出草案那一刻才算得出来。

    **键必须带 form_id**：`question_id` 在同一轮的每一批里都从 `q1` 重排，
    只按 question_id 记会把上一批的来源覆盖掉（真跑第 2 轮暴露过）。
    """

        for item in questions:
            if item.from_entry is not None:
                self._carried_sources[f"{form_id}#{item.question_id}"] = item.from_entry

    def carried_sources(self) -> dict[str, str]:
        return dict(self._carried_sources)

    @property
    def list_items(self) -> tuple[ListItem, ...]:
        """本轮**历次**记下的清单项（追加不覆盖）。

        原来是"当轮问卷的 `list_items`"：问卷一批换一批，第二批一到，第一批记下的东西就没了
        （真跑 F8：第一轮 0 条、第二轮 19 条、第三轮 2 条，谁也追不回来）。真相源放轮次上，
        与 `questionnaire_history` 同一个范式；老行没有这个 payload 键 → 读侧回落到当轮问卷的那批。
        """

        return self._list_items

    def remember_list_items(self, items: Sequence[ListItem]) -> None:
        """把这一批记下的清单项存进轮次。追加：一批一批攒，不覆盖。

        只有整合用例调它（问卷与草案两条路都要调，不然"这一批没出题"的那些清单项会丢）。
        """

        if items:
            self._list_items = self._list_items + tuple(items)

    @property
    def current_reviews(self) -> tuple[SeatReview, ...]:
        """当前这次席位轮交回的意见。整合者与闭环区只看这个，历史意见留在 seat_reviews。"""

        return tuple(
            review for review in self._seat_reviews if review.pass_index == self._seat_round_count
        )

    @property
    def pending_seats(self) -> tuple[str, ...]:
        """这一轮还没交回意见的席位。崩了重进只补这些，不重跑已交回的（计划 §3.11）。"""

        done = {review.seat_id for review in self.current_reviews}
        return tuple(seat for seat in self.active_seats if seat not in done)

    def revise_claims(self, claims: tuple[Claim, ...] | list[Claim]) -> None:
        """开场卡纠正：只改标签 / 去处 / 假设 / 决策者，不改文本、原话片段与编号。"""

        self._require_step(Step.CLAIMS_READY)
        self._require_waiting(WaitingOn.OPENING)
        revised = tuple(claims)
        ids = [item.claim_id for item in revised]
        if len(set(ids)) != len(ids):
            raise ShapeError("同一主张不能在复述卡里出现两次", claim_ids=ids)
        before = {item.claim_id: item for item in self._claims}
        if set(ids) != set(before):
            raise ShapeError(
                "纠正必须覆盖且只覆盖原有主张",
                before=sorted(before),
                after=sorted(ids),
            )
        for item in revised:
            old = before[item.claim_id]
            if item.eid != old.eid:
                raise ShapeError("纠正标签与去处不能改出处编号", claim_id=item.claim_id)
            if item.text != old.text or item.raw_span != old.raw_span:
                raise ShapeError(
                    "卡片里不能改内容（改文案请重新输入一轮）", claim_id=item.claim_id
                )
        self._claims = revised

    def integrate(
        self,
        *,
        questionnaire: Questionnaire | None = None,
        draft: Draft | None = None,
        dropped: tuple[DroppedItem, ...] | list[DroppedItem] = (),
    ) -> None:
        self._require_step(Step.SEATS_RUNNING)
        if (questionnaire is None) == (draft is None):
            raise ShapeError("整合必须给出问卷或草案其中之一（问卷不是必然环节）")
        self._dropped = self._dropped + tuple(dropped)
        if questionnaire is not None:
            if questionnaire.round_id != self._round_id:
                raise ShapeError("问卷不属于本轮", form_id=str(questionnaire.form_id))
            self._questionnaire = questionnaire
            # 历史**追加不覆盖**：下一批一到，上一批的题面/选项就没别的地方可查了（D4）
            self._questionnaire_history = self._questionnaire_history + (questionnaire,)
            self._advance_to(Step.AWAITING_ANSWERS, waiting_on=WaitingOn.QUESTIONNAIRE)
            return
        assert draft is not None
        if draft.round_id != self._round_id:
            raise ShapeError("草案不属于本轮", draft_id=str(draft.draft_id))
        self._draft = draft
        self._advance_to(Step.AWAITING_CONFIRM, waiting_on=WaitingOn.DRAFT)

    def submit_answers(
        self,
        answer_set: AnswerSet,
        *,
        active_seats: tuple[str, ...] | list[str] | None = None,
    ) -> None:
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
        if active_seats is not None:
            # C8 缩编：这一趟只跑名单里的席位。名单必须在**用户编制**之内（R7 不许破），
            # 也不能是空的（宁多勿缺：空了就整趟没人跑，等于静默漏面）。
            narrowed = tuple(dict.fromkeys(active_seats))
            if not narrowed:
                raise ShapeError("缩编后的席位名单不能为空")
            outside = [seat for seat in narrowed if seat not in self._seats]
            if outside:
                raise UnknownSeat(seat_id=outside[0])
            self._active_seats = narrowed
        self._begin_seat_round()
        self._answer_sets = self._answer_sets + (answer_set,)
        self._advance_to(Step.SEATS_RUNNING)

    def confirm(
        self,
        version_no: int,
        *,
        proposed_offered: int = 0,
        proposed_accepted: int = 0,
    ) -> None:
        """确认写入：记版本号，也记下"它提议的"**给了几条、用户收了几条**（C4 的判据）。

        给这两个数是 `ProposalCommitter` 的活（它手里才有草案与勾选）；
        默认 0 只服务于"这一轮压根没有提议组"的老调用方式。
        """

        self._require_step(Step.AWAITING_CONFIRM)
        self._require_waiting(WaitingOn.DRAFT)
        self._confirmed_version_no = version_no
        self._proposed_offered = proposed_offered
        self._proposed_accepted = proposed_accepted
        self._advance_to(Step.DONE)

    @property
    def proposed_offered(self) -> int | None:
        """"它提议的"这一组给了几条（没确认过就是 `None`——**不编 0**）。"""

        return self._proposed_offered

    @property
    def proposed_accepted(self) -> int | None:
        """"它提议的"这一组用户收了几条。"""

        return self._proposed_accepted

    @property
    def proposal_ignore_streak(self) -> int:
        """出草案那一刻算出来的"最近连续几轮没采用席位提议"（算数的是整合用例，这里只存）。"""

        return self._proposal_ignore_streak

    def remember_proposal_ignore_streak(self, count: int) -> None:
        self._proposal_ignore_streak = max(0, count)

    @property
    def extra_batches(self) -> int:
        """用户主动要求"继续深挖"多出来的问卷批次（问卷闸门的额度，C6）。"""

        return self._extra_batches

    def dig(self) -> None:
        """从草案卡回到提问：**把这一轮没问上的那些题重新问一遍**（P14 C6）。

        四件事，缺一不可：
        1. 清掉未确认的草案——文档**从没被写过**（写入只在确认那一刻发生），所以不用回滚；
        2. 问卷闸门 +1 批；
        3. **新开一次席位轮**（席位要针对"用户还想再深挖"重新表态；到上限就明确报错）；
        4. 回到 `seats_running`——轮内队列**保留**（出草案时已经不清空了），
           于是下一趟整合会把这些题当 carried 真的问出来。
        """

        self._require_step(Step.AWAITING_CONFIRM)
        self._require_waiting(WaitingOn.DRAFT)
        self._draft = None
        self._extra_batches += 1
        self._begin_seat_round()
        self._advance_to(Step.SEATS_RUNNING)

    def authorize_new_feature(self, slug: FeatureSlug, *, basis: EvidenceId) -> None:
        if slug in self._authorized:
            raise FeatureAlreadyAuthorized(feature_slug=slug)
        self._authorized[slug] = basis

    def fail(self, reason: str, *, hint: str | None = None) -> None:
        """记失败：**只记第一次**。

        第二次调用（并发重试、重复提交）会把 `at_step` 记成当时那一步——那时它已经是
        `failed` 了，于是"从哪一步重跑"这个信息就没了，任谁都救不回来（`04-未决.md` C9）。
        失败点只有一个，重复的失败不改写它。

        `hint` 是**报文类**失败的摘要（给重试时的第一次尝试用）：网关那类（超时 / 5xx）
        不传——那种错对模型没有信息量，递进去只是把提示词喂脏。
        """

        if self._step is Step.DONE:
            raise IllegalStepTransition(current=self._step, target=Step.FAILED)
        if self._step is Step.FAILED:
            return
        self._failure = Failure(at_step=self._step, waiting_on=self._waiting_on, reason=reason)
        if hint:
            self._retry_hint = hint
        self._waiting_on = WaitingOn.NONE
        self._step = Step.FAILED

    @property
    def retry_hint(self) -> str | None:
        """上次失败的**报文类**原因：重试时递给第一次尝试（"别盲调"）。"""

        return self._retry_hint

    def take_retry_hint(self) -> str | None:
        """取走重试提示（**一次性**）。

        不取走的话，陈年错误会被后面每个动作当成 `validation_error` 递进提示词——
        那比不递更糟（模型会去修一个已经不存在的问题）。
        """

        hint, self._retry_hint = self._retry_hint, None
        return hint

    def resume(self) -> None:
        if self._step is not Step.FAILED or self._failure is None:
            raise ShapeError("没有可恢复的失败")
        if self._failure.at_step is Step.FAILED:
            # 老库里真出现过这种行（并发写的）：恢复成 failed 等于没有恢复，
            # 路由表也没有这个出口。明确报错，别让它变成 500。
            raise ShapeError(
                "这一轮的失败点被写坏了（at_step=failed），没法自动续跑；请重新开一轮",
                at_step=Step.FAILED.value,
            )
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
