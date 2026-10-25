"""Round 聚合：一轮怎么推进，全在这里。LangGraph 的 state 只放 round_id 与 step。"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from pmstudio.domain.round.claim import Claim, Contradiction
from pmstudio.domain.round.draft import Draft
from pmstudio.domain.round.opinion import Opinion, SeatReview
from pmstudio.domain.round.questionnaire import AnswerSet, ListItem, Question, Questionnaire
from pmstudio.domain.round.retrieval import ManualChunk, RetrievalStage, RetrievalTrace
from pmstudio.domain.round.step import LEGAL_ADVANCES, MAX_SEAT_ROUNDS, Step, WaitingOn
from pmstudio.domain.round.web import (
    LEGACY_REQUEST_WHY,
    MAX_WEB_QUERIES,
    WebRequest,
    WebRequestState,
    normalized_query,
)
from pmstudio.domain.round.web_query import WebQuery
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
from pmstudio.domain.shared.memory import MemoryChange
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


@dataclass(frozen=True, slots=True)
class MaterialRef:
    """这一轮**带进提示词**的材料块：来源 + 块 + 账本里那条 `external` 出处的编号。

    只放这三个字段。锚点、正文都在材料块里（`MaterialChunk`）——放这儿就是第二份真相源。
    """

    material_id: str
    chunk_id: str
    eid: EvidenceId

    def __post_init__(self) -> None:
        if not self.material_id.strip():
            raise ShapeError("材料引用必须带 material_id")
        if not self.chunk_id.strip():
            raise ShapeError("材料引用必须带 chunk_id")


@dataclass(frozen=True, slots=True)
class SeedRef:
    """这一轮带进来的过目产物（种子）：**只引用，不复制内容**。

    条目本地 id（`H1`/`B1`/`C1`）只在趟内/线程内唯一，所以必须连来源 id 一起记——
    单靠本地 id 会跨来源撞名（与留痕必须带 `pass_index` 是同一条理由）。

    它只是回链：主张的出处仍然是 `user_input`（用户编辑后提交的那段话），
    种子回答的是"这段输入是从哪条过目产物来的"。
    """

    origin: str  # "exploration"（调研趟）| "discussion"（讨论线程）
    ref_id: str  # exploration_id / discussion_id
    item_id: str  # candidate_id / card_id

    def __post_init__(self) -> None:
        if self.origin not in ("exploration", "discussion"):
            raise ShapeError(f"种子来源只能是 exploration 或 discussion：{self.origin}")
        if not self.ref_id.strip():
            raise ShapeError("种子必须带来源 id")
        if not self.item_id.strip():
            raise ShapeError("种子必须带条目 id")


class DropSource(StrEnum):
    """留痕是谁发现的。"""

    INTEGRATOR = "integrator"  # 模型在报文里申报的（原因它自己写）
    ASSEMBLY = "assembly"  # 装配层算出来的：归因对不上号
    UNACCOUNTED = "unaccounted"  # 装配层核出来的：既没说丢、也没交代落到哪儿
    #: 工具这一趟的：检索失败 / 没搜到结果 / 搜到了但预算带不进去（C-2b）。
    #: 与席位意见无关，所以 `seat_id` 恒为 None——闭环区会把它单列一句。
    TOOL = "tool"
    #: **材料这一趟**的：用户勾了材料，但预算装不下（整份材料一块都没进来）。
    #: 与工具那一趟是同一类事（"本来要带、预算带不下"），但**不是工具**——
    #: 混进 `TOOL` 会让界面上"工具 N 条"里混进一条不是工具的事。
    MATERIAL = "material"
    #: **库级检索进来的**（`02 §11.0.91`）：用户**没勾**，但本轮按输入检索并收敛进来了。
    #: 与 `MATERIAL` 分开是**必须的**：`MATERIAL` 的含义是"勾了但预算装不下"，
    #: 提示词会据此写"另有 N 份材料因预算没带进来"——自动材料恰恰**带进来了**，
    #: 混用会让提示词说反话（`external_material_text` 只数 `MATERIAL`）。
    MATERIAL_AUTO = "material_auto"
    #: 编排层跳过的整个席位：两次都交不出可用报文，本轮不再请它（`02` §11.0.67）。
    #: 与上面四个"条目级"不同，这是席位级的——`handle` 直接写 `seat_id`，
    #: `packet_id` 为 None（不来自模型回包，TOOL 同例）。
    SKIPPED = "skipped"
    #: **本轮输入（主张）没被整合者引用**的那一条：系统替它**机械落地**成了提案
    #: （`assembly.landed_claim_proposals`），但"整合者没引用它"这件事要**留痕**——
    #: 否则用户看不出哪条输入是系统替它落地的（`04-未决.md` C1）。
    #:
    #: 与 `UNACCOUNTED` 分开：那一条审的是**席位意见**（整合者本该申报丢弃却没申报），
    #: 这一条审的是**本轮输入**（整合者没引用它，系统替它落地了）——两类事故不能混成一行
    #: （与 `SKIPPED` / `USER_SKIPPED` 分开是同一个道理）。
    CLAIM = "claim"
    #: 用户手动跳过的席位：整包不进整合（含跳过后迟交的那一份）。
    #: 与 SKIPPED 分开——"谁跳的"排障时很重要（编排层自动 vs 用户手动），
    #: 混成一个来源等于把两类事故写进同一行留痕。缺口行点名读 `skipped_seats`
    #: 不分来源，所以那一行无感；留痕折叠行按 source 显示，这里分得开。
    USER_SKIPPED = "user_skipped"


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
    #: 这一条来自哪一包（§4.4 信封的 `packet_id`）。
    #:
    #: **可选，默认 `None`**：留痕有四个来源（integrator / assembly / unaccounted / tool），
    #: 其中 tool 来源本来就没有 agent 包（是联网检索失败，不是模型回包），
    #: assembly 的"归因对不上号"也挂不到席位包下。所以 `None` 的含义是
    #: **"不来自模型回包"**，不是"没填"——这个区别必须写清，否则半年后没人分得清。
    #:
    #: `__post_init__` **不校验**它：那三条（handle / reason / pass_index）是
    #: "这条留痕有意义"的必要条件，而 packet_id 是**可选溯源**，None 是合法值。
    packet_id: str | None = None

    def __post_init__(self) -> None:
        if not self.handle.strip():
            raise ShapeError("留痕必须写清是哪一条席位意见（handle）")
        if not self.reason.strip():
            raise ShapeError("留痕必须写清原因", handle=self.handle)
        if self.pass_index < 1:
            raise ShapeError("留痕必须说明属于哪一趟席位轮", handle=self.handle)


@dataclass(frozen=True, slots=True)
class MemoryProposal:
    """草案卡上那一档"这一轮攒下的记忆"的一行（M6）。

    它是**提议**：`change` 是给记忆聚合的指令（词汇表在 `domain/shared/memory.py`），
    只有用户在草案卡上勾了、并在同一次确认里带上 `proposal_id`，它才会生效。
    为什么带 `proposal_id`：界面与 `POST /confirm` 需要一个稳定的句柄
    （`mem#1`），而不是靠数组下标——下标会随渲染顺序漂移。
    """

    proposal_id: str
    change: MemoryChange

    def __post_init__(self) -> None:
        if not self.proposal_id.strip():
            raise ShapeError("记忆提议必须有 id")


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
        #: 本轮跳过的席位（`02` §11.0.67）：`pending_seats` 不再含它们，
        #: 答后第二趟也不再请；下一轮编制照旧。
        skipped_seats: tuple[str, ...] = (),
        deferred: tuple[Question, ...] = (),
        carried_sources: dict[str, str] | None = None,
        questionnaire_history: tuple[Questionnaire, ...] = (),
        list_items: tuple[ListItem, ...] = (),
        contradictions: tuple[Contradiction, ...] = (),
        retry_hint: str | None = None,
        analysis_basis: str = "",
        proposed_offered: int | None = None,
        proposed_accepted: int | None = None,
        proposal_ignore_streak: int = 0,
        extra_batches: int = 0,
        extra_seat_rounds: int = 0,
        integration_seq: int = 0,
        materials: tuple[MaterialRef, ...] = (),
        #: 每一步检索的轨迹（P0）：命中/选中，不含 eid（编号真相源是 materials+账本）。
        retrievals: tuple[RetrievalTrace, ...] = (),
        #: 用户手勾的材料 id：投影时这些材料的块合计最多占 60% 预算。
        pinned_materials: tuple[str, ...] = (),
        #: 用户在搜索框「带进本轮」的块：投影时先装（装不下要留痕）。
        manual_chunks: tuple[ManualChunk, ...] = (),
        unreferenced_materials: tuple[str, ...] = (),
        seeds: tuple[SeedRef, ...] = (),
        web_requests: tuple[WebRequest, ...] = (),
        allow_web: bool = False,
        #: 最近一趟联网执行**选中**的块（(material_id, chunk_id)，步级快照）。
        #: `None` = 旧 payload：渲染层回退到全部旧 web refs（B3）。
        web_selection: tuple[tuple[str, str], ...] | None = None,
        memory_proposals: tuple[MemoryProposal, ...] = (),
        accepted_memory_ids: tuple[str, ...] = (),
        sim_slices: tuple[dict, ...] = (),
        sim_hypotheses: tuple[dict, ...] = (),
        sim_unknowns: tuple[dict, ...] = (),
        sim_inquiry_runs: int = 0,
        sim_inquiry_chosen: tuple[str, ...] = (),
        sim_review: dict | None = None,
        sim_review_runs: int = 0,
    ) -> None:
        self._round_id = round_id
        self._project_id = project_id
        self._user_input = user_input
        self._analysis_basis = analysis_basis
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
        self._skipped_seats = tuple(skipped_seats)
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
        self._extra_seat_rounds = extra_seat_rounds
        self._integration_seq = integration_seq
        self._materials = tuple(materials)
        self._retrievals = tuple(retrievals)
        self._pinned_materials = tuple(pinned_materials)
        self._manual_chunks = tuple(manual_chunks)
        self._unreferenced_materials = tuple(unreferenced_materials)
        #: 这一轮带进来的过目产物（M8 调研种子 / M12 灵感箱）；空 = 不是从过目产物来的
        self._seeds = tuple(seeds)
        #: 三方结构化联网申请（P3a）：提名、批准、执行状态都在这里
        self._web_requests = tuple(web_requests)
        #: 用户是否允许后续申请自动批准（只在开场卡上设置）
        self._allow_web = bool(allow_web)
        #: 最近一趟执行选中的 web 块；None = 旧 payload（渲染层回退全部旧 web refs）
        self._web_selection = (
            None
            if web_selection is None
            else tuple((str(material_id), str(chunk_id)) for material_id, chunk_id in web_selection)
        )
        #: 这一轮攒下的记忆提议（草案卡上那一档）；确认时才可能生效（M6）
        self._memory_proposals = tuple(memory_proposals)
        #: 确认时用户勾了哪几条记忆提议（身份，不只是数量）
        self._accepted_memory_ids = tuple(accepted_memory_ids)
        #: 模拟调研产物（M10）：三件套全是 plain dict——`round` 不许 import
        #: `exploration`（上下文隔离守卫），校验形状住在那边，装配层转成 dict 才落这里。
        #: 假设 dict 与 `exploration_to_payload` 的 6 键同形。
        self._sim_slices = tuple(sim_slices)
        self._sim_hypotheses = tuple(sim_hypotheses)
        self._sim_unknowns = tuple(sim_unknowns)
        self._sim_inquiry_runs = sim_inquiry_runs
        self._sim_inquiry_chosen = tuple(sim_inquiry_chosen)
        #: 模拟评测产物（M11）：一趟 = 一个 dict（evaluated_version / personas / advisories）。
        #: 同上，plain dict：校验住在应用层。`dig` 出新草案时随草案一起作废（每份草案 1 次）。
        self._sim_review = dict(sim_review) if sim_review is not None else None
        self._sim_review_runs = sim_review_runs
        #: 本轮**每一批**问卷（追加不覆盖）。`_questionnaire` 仍是"当前批"，
        #: 历史那一份给渲染与回查用——题号每批重排会让裸题号指向别的批（计划 §0.1）
        self._questionnaire_history = tuple(questionnaire_history)
        if not self._questionnaire_history and self._questionnaire is not None:
            # 老库/老 payload 没有历史键：退化成"只有当前这一批"，不编历史
            self._questionnaire_history = (self._questionnaire,)

    @classmethod
    def start(
        cls,
        *,
        round_id: RoundId,
        project_id: ProjectId,
        user_input: str,
        now: datetime,
        seeds: tuple[SeedRef, ...] = (),
    ) -> "Round":
        if not user_input.strip():
            raise EmptyUserInput()
        return cls(
            round_id=round_id,
            project_id=project_id,
            user_input=user_input,
            created_at=now,
            seeds=seeds,
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
    def touched_question_keys(self) -> frozenset[tuple[str, str]]:
        """用户**已经表过态**的题的身份集合：答了的 + 显式跳过的。

        为什么要有它：这些题是"模型看得见的"——`$answered` 与 `$skipped` 两段渲染的就是它们。
        反过来，问过但没答也没跳过的题**模型看不见**（提示词里一个字都没有）。这个区别决定了
        模型重问一道题时该不该算它的错，装配层与渲染层都要用同一个判据，
        所以它是领域事实而不是工具函数。

        身份是 `(form_id, question_id)`（不是裸题号）：题号跨批会重复（D4 之前的老库），
        用裸题号会把上一批的题面挂到这一批的题号上。
        """

        return frozenset(
            (str(answer_set.form_id), str(item))
            for answer_set in self._answer_sets
            for item in (
                [answer.question_id for answer in answer_set.answers]
                + list(answer_set.skipped)
            )
        )

    @property
    def draft(self) -> Draft | None:
        return self._draft

    @property
    def seat_round_count(self) -> int:
        return self._seat_round_count

    @property
    def can_begin_seat_round(self) -> bool:
        """还能不能再开一趟席位轮。

        上限不是死的：用户每要求一次"继续深挖"（`dig`），就多一批问卷**和**一趟席位轮——
        因为每一批答案后面都要跟一趟席位轮（缩编 + 重新表态），只放宽问卷会让用户在
        "答完这一批"那一步直接走不动（`SeatRoundLimitReached`）。
        """

        return self._seat_round_count < self.seat_round_limit

    @property
    def seat_round_limit(self) -> int:
        """这一轮的席位轮上限（`MAX_SEAT_ROUNDS` + dig 放行的趟数）。

        **闸门与 `can_begin_seat_round` 必须看同一个数**：整合用例的"该不该停止追问"
        也读它——否则用户 dig 之后，`submit_answers` 放行、整合这边的闸门却先响，
        于是"继续深挖"照样被降级成 s11（真跑里踩到过）。
        """

        return MAX_SEAT_ROUNDS + self._extra_seat_rounds

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

    @property
    def skipped_seats(self) -> tuple[str, ...]:
        """本轮跳过的席位（`02` §11.0.67）：两次都交不出可用报文，本轮不再请。"""

        return self._skipped_seats

    def skip_seat(self, seat_id: str) -> None:
        """跳过一个席位：只在席位轮里调（`RunSeatRound` 失败分支）。

        幂等：崩溃重试/重复进入时第二次调用直接返回——跳过是事实记录，
        不是计数（`note_dropped` 的留痕追加语义不受影响，那是另一条）。
        """

        self._require_step(Step.SEATS_RUNNING)
        if seat_id not in self.active_seats:
            raise UnknownSeat(seat_id=seat_id)
        if seat_id not in self._skipped_seats:
            self._skipped_seats = self._skipped_seats + (seat_id,)

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

    @property
    def web_requests(self) -> tuple[WebRequest, ...]:
        """三方结构化联网申请（提名 / 批准 / 执行结果都在这里）。"""

        return self._web_requests

    @property
    def allow_web(self) -> bool:
        """用户在开场卡上是否允许后续申请自动批准（默认关）。"""

        return self._allow_web

    @property
    def web_selection(self) -> tuple[tuple[str, str], ...] | None:
        """最近一趟联网执行选中的块（步级快照）；`None` = 旧 payload。"""

        return self._web_selection

    @property
    def web_queries(self) -> tuple[WebQuery, ...]:
        """旧口径派生：分析者提名的检索词（带"为什么"），只读。

        顺序是申请挂载顺序（不是用户勾选顺序）；文本是申请里的原话，不做归一化。
        """

        return tuple(
            WebQuery(query=item.query, why=item.why)
            for item in self._web_requests
            if item.origin == "analyzer"
        )

    @property
    def web_queries_chosen(self) -> tuple[str, ...]:
        """旧口径派生：用户批准过（含已执行）的分析者检索词，只读。

        `proposed` 还没批、`rejected` 是被拒的，都不算"勾中"。
        **顺序是申请挂载顺序，不是用户勾选顺序**；文本是申请里的原话。
        当前消费方（DTO 的 `chosen` 布尔、执行/迁移的成员判断）都按集合用，
        没有消费者依赖这个顺序——别把它当"用户点选的先后"。
        """

        return tuple(
            item.query
            for item in self._web_requests
            if item.origin == "analyzer"
            and item.state in (
                WebRequestState.APPROVED,
                WebRequestState.DONE,
                WebRequestState.FAILED,
            )
        )

    def next_web_request_id(self) -> str:
        """三方共用的下一个编号：扫描已有 `wN`，返回 `w{max+1}`。"""

        return f"w{self._next_request_number(self._web_requests)}"

    @staticmethod
    def _next_request_number(requests: Sequence[WebRequest]) -> int:
        used = [
            int(item.request_id[1:])
            for item in requests
            if item.request_id.startswith("w") and item.request_id[1:].isdigit()
        ]
        return max(used, default=0) + 1

    def attach_web_requests(
        self, items: tuple[WebRequest, ...] | list[WebRequest]
    ) -> None:
        """挂上一批申请：按 `(origin, seat_id, 归一化 query)` 去重、保序、每方最多 3 条。

        超过每方名额是**编程错误**：用例层已经按方取前 3 条并把余下的 TOOL 留痕
        （解析层也丢过一轮），这里拦住手搓的调用方，不静默截断。
        """

        incoming = tuple(items)
        ids = [item.request_id for item in incoming]
        if len(set(ids)) != len(ids):
            raise ShapeError("联网申请 id 不能重复", request_ids=ids)
        known = {item.request_id for item in self._web_requests}
        collisions = [request_id for request_id in ids if request_id in known]
        if collisions:
            raise ShapeError("联网申请 id 已经存在", request_ids=collisions)

        keys = {
            (item.origin, item.seat_id, normalized_query(item.query))
            for item in self._web_requests
        }
        fresh: list[WebRequest] = []
        for item in incoming:
            key = (item.origin, item.seat_id, normalized_query(item.query))
            if key in keys:
                continue
            keys.add(key)
            fresh.append(item)

        merged = self._web_requests + tuple(fresh)
        for origin in ("analyzer", "seat", "integrator"):
            count = sum(1 for item in merged if item.origin == origin)
            if count > MAX_WEB_QUERIES:
                raise ShapeError(
                    f"每方最多 {MAX_WEB_QUERIES} 条联网申请", origin=origin, count=count
                )
        self._web_requests = merged

    def attach_web_queries(self, items: tuple[WebQuery, ...] | list[WebQuery]) -> None:
        """旧入口兼容包装：分析者提名的检索词 → analyzer 申请（proposed）。"""

        unique: dict[str, WebQuery] = {}
        for item in items:
            unique.setdefault(normalized_query(item.query), item)
        start = self._next_request_number(self._web_requests)
        self.attach_web_requests(
            [
                WebRequest(
                    request_id=f"w{start + index}",
                    query=item.query,
                    why=item.why,
                    origin="analyzer",
                )
                for index, item in enumerate(unique.values())
            ]
        )

    def decide_web_request(self, request_id: str, *, approve: bool) -> None:
        """决策只动 `proposed`：批准 → approved，拒绝 → rejected。"""

        position = self._web_request_position(request_id)
        current = self._web_requests[position]
        if current.state is not WebRequestState.PROPOSED:
            raise ShapeError(
                "只有待批准的联网申请能被决策", request_id=request_id, state=current.state
            )
        target = WebRequestState.APPROVED if approve else WebRequestState.REJECTED
        updated = list(self._web_requests)
        updated[position] = replace(current, state=target)
        self._web_requests = tuple(updated)

    def mark_web_request(
        self,
        request_id: str,
        state: WebRequestState,
        *,
        result_note: str | None = None,
    ) -> None:
        """执行结果只动 `approved`：成功 → done，失败 → failed（可带一句说明）。"""

        if state not in (WebRequestState.DONE, WebRequestState.FAILED):
            raise ShapeError("联网申请的结果只能是 done 或 failed", state=state)
        position = self._web_request_position(request_id)
        current = self._web_requests[position]
        if current.state is not WebRequestState.APPROVED:
            raise ShapeError(
                "只有已批准的联网申请能记结果",
                request_id=request_id,
                state=current.state,
            )
        updated = list(self._web_requests)
        updated[position] = replace(current, state=state, result_note=result_note)
        self._web_requests = tuple(updated)

    def _web_request_position(self, request_id: str) -> int:
        for position, item in enumerate(self._web_requests):
            if item.request_id == request_id:
                return position
        raise ShapeError("没有这条联网申请", request_id=request_id)

    def note_web_selection(
        self, items: tuple[tuple[str, str], ...] | list[tuple[str, str]]
    ) -> None:
        """记下这一趟执行选中的块（去重、保序）；`()` 表示"这一趟一块都没选"。"""

        unique: dict[tuple[str, str], None] = {}
        for material_id, chunk_id in items:
            material_id, chunk_id = str(material_id), str(chunk_id)
            if not material_id.strip() or not chunk_id.strip():
                raise ShapeError("web 选块必须带 material_id 与 chunk_id")
            unique.setdefault((material_id, chunk_id))
        self._web_selection = tuple(unique)

    def clear_web_selection(self) -> None:
        """回到"没有本步快照"（旧 payload 语义：渲染层回退全部旧 web refs）。"""

        self._web_selection = None

    def submit_opening(
        self,
        *,
        seats: tuple[str, ...] | list[str],
        allow_web: bool = False,
        approved_request_ids: tuple[str, ...] | list[str] = (),
        web_queries_chosen: tuple[str, ...] | list[str] = (),
    ) -> None:
        self._require_step(Step.CLAIMS_READY)
        self._require_waiting(WaitingOn.OPENING)
        unique_seats = tuple(dict.fromkeys(seats))
        if not unique_seats:
            raise ShapeError("席位编制不能为空")
        # 先把新/旧两套授权一次性核完再落任何字段：请求不对就不该留下半推半就的状态。
        web_requests = self._approve_at_opening(
            list(self._web_requests),
            approved_request_ids=approved_request_ids,
            web_queries_chosen=web_queries_chosen,
        )
        self._seats = unique_seats
        self._active_seats = unique_seats  # 开场卡确认：这一趟就是用户编制
        self._allow_web = bool(allow_web)
        self._web_requests = tuple(web_requests)
        self._begin_seat_round()
        self._advance_to(Step.SEATS_RUNNING)

    def _approve_at_opening(
        self,
        requests: list[WebRequest],
        *,
        approved_request_ids: tuple[str, ...] | list[str],
        web_queries_chosen: tuple[str, ...] | list[str],
    ) -> list[WebRequest]:
        positions = {item.request_id: index for index, item in enumerate(requests)}
        for raw in approved_request_ids:
            request_id = str(raw).strip()
            if not request_id:
                continue
            if request_id not in positions:
                raise ShapeError("没有这条联网申请", request_id=request_id)
            position = positions[request_id]
            current = requests[position]
            if current.state is WebRequestState.PROPOSED:
                requests[position] = replace(current, state=WebRequestState.APPROVED)
            elif current.state is not WebRequestState.APPROVED:
                raise ShapeError(
                    "只有待批准或已批准的联网申请能在开场卡上批准",
                    request_id=request_id,
                    state=current.state,
                )

        seen: set[str] = set()
        for raw in web_queries_chosen:
            query = str(raw).strip()
            if not query:
                continue
            key = normalized_query(query)
            if key in seen:
                continue
            seen.add(key)
            matched = next(
                (
                    position
                    for position, item in enumerate(requests)
                    if item.origin == "analyzer" and normalized_query(item.query) == key
                ),
                None,
            )
            if matched is None:
                # 旧版复述卡允许勾一条分析者没提名的词：补成一条 approved analyzer 申请，
                # 否则"用户勾了"这件事在新载体里没有地方可存。
                # 但 analyzer 名额已经满了就不能再补：这是用户可改的请求（422），
                # 不静默丢用户的选择，也不把一组超过 3 条的申请写进聚合。
                analyzer_count = sum(1 for item in requests if item.origin == "analyzer")
                if analyzer_count >= MAX_WEB_QUERIES:
                    raise ShapeError(
                        f"分析者最多 {MAX_WEB_QUERIES} 条联网申请，"
                        f"勾选的「{query}」没有名额",
                        query=query,
                        count=analyzer_count,
                    )
                request_id = f"w{self._next_request_number(requests)}"
                requests.append(
                    WebRequest(
                        request_id=request_id,
                        query=query,
                        why=LEGACY_REQUEST_WHY,
                        origin="analyzer",
                        state=WebRequestState.APPROVED,
                    )
                )
                positions[request_id] = len(requests) - 1
                continue
            current = requests[matched]
            if current.state is WebRequestState.PROPOSED:
                requests[matched] = replace(current, state=WebRequestState.APPROVED)
            elif current.state is WebRequestState.REJECTED:
                raise ShapeError("这条检索词已被拒绝，不能再勾选", query=query)
        return requests

    def attest(self, review: SeatReview, *, packet_id: str | None = None) -> None:
        self._require_step(Step.SEATS_RUNNING)
        if review.seat_id not in self.active_seats:
            raise UnknownSeat(seat_id=review.seat_id)
        stamped = review if packet_id is None else replace(review, packet_id=packet_id)
        self._seat_reviews = self._seat_reviews + (
            replace(stamped, pass_index=self._seat_round_count),
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
        """这一轮还没交回意见的席位。崩了重进只补这些，不重跑已交回的（计划 §3.11）。
        跳过的也不补：`skip_seat` 是本轮内的永久决定（`02` §11.0.67）。"""

        done = {review.seat_id for review in self.current_reviews}
        skipped = set(self._skipped_seats)
        return tuple(
            seat for seat in self.active_seats if seat not in done and seat not in skipped
        )

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
        self._integration_seq += 1
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
        accepted_memory_ids: Sequence[str] = (),
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
        self._accepted_memory_ids = tuple(accepted_memory_ids)
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
    def memory_proposals(self) -> tuple[MemoryProposal, ...]:
        """这一轮攒下的记忆提议（草案卡上那一档）。**提议就是提议**：没确认就不生效。"""

        return self._memory_proposals

    @property
    def accepted_memory_ids(self) -> tuple[str, ...]:
        """用户勾了哪几条记忆提议（没确认过就是空——**不编**）。"""

        return self._accepted_memory_ids

    def remember_memory_proposals(self, items: Sequence[MemoryProposal]) -> None:
        """出草案那一刻记下这一档（与 `remember_list_items` 同一个写法）。"""

        self._memory_proposals = tuple(items)

    @property
    def proposal_ignore_streak(self) -> int:
        """出草案那一刻算出来的"最近连续几轮没采用席位提议"（算数的是整合用例，这里只存）。"""

        return self._proposal_ignore_streak

    def remember_proposal_ignore_streak(self, count: int) -> None:
        self._proposal_ignore_streak = max(0, count)

    @property
    def integration_seq(self) -> int:
        """本轮已经**整合成功**过几次。

        它是信封 `packet_id` 的槽位来源（`i{seq}`）：一轮里整合者会跑多次——问卷一批、
        补交代后再整合、答案回来后第二趟席位再整合（`02` §13 第 6/7/8 步）。
        那个次数**必须落在聚合上**：用例层的重试计数是局部变量，每次进入整合都重置，
        用它当槽位会让第二次整合拿到与第一次一样的 id（实测撞过）。
        """

        return self._integration_seq

    @property
    def extra_batches(self) -> int:
        """用户主动要求"继续深挖"多出来的问卷批次（问卷闸门的额度，C6）。"""

        return self._extra_batches

    @property
    def extra_seat_rounds(self) -> int:
        """随之放宽的席位轮额度（每一批答案后面都要跟一趟席位轮）。"""

        return self._extra_seat_rounds

    @property
    def materials(self) -> tuple[MaterialRef, ...]:
        """这一轮**带进提示词**的外部材料块（附件与检索结果同一条路）。"""

        return self._materials

    @property
    def retrievals(self) -> tuple[RetrievalTrace, ...]:
        """每一步检索的轨迹（只追加；`selected` 是真正进过提示词的块）。"""

        return self._retrievals

    @property
    def pinned_materials(self) -> tuple[str, ...]:
        """用户手勾的材料 id（投影时合计 ≤60% 预算）。"""

        return self._pinned_materials

    @property
    def manual_chunks(self) -> tuple[ManualChunk, ...]:
        """用户在搜索框「带进本轮」的块（投影时先装）。"""

        return self._manual_chunks

    def note_retrieval(self, trace: RetrievalTrace) -> None:
        """追加一条检索轨迹。**只追加**：同一 (stage, pass) 的幂等由调用方先查。"""

        self._retrievals = self._retrievals + (trace,)

    def has_retrieval(self, stage: RetrievalStage, pass_index: int) -> bool:
        """这一 (stage, pass) 是否已经查过。恢复/重入时据此跳过检索。"""

        return any(
            item.stage is stage and item.pass_index == pass_index
            for item in self._retrievals
        )

    def pin_materials(self, material_ids: tuple[str, ...] | list[str]) -> None:
        """把材料标成"手勾"：去重、保序。空串不是材料 id。"""

        self._pinned_materials = tuple(
            dict.fromkeys(item.strip() for item in material_ids if item.strip())
        )

    def note_manual_chunks(
        self, items: tuple[ManualChunk, ...] | list[ManualChunk]
    ) -> None:
        """记下用户手动带进的块：按 (material_id, chunk_id) 去重、保序。"""

        unique: dict[tuple[str, str], ManualChunk] = {}
        for item in items:
            unique.setdefault((item.material_id, item.chunk_id), item)
        self._manual_chunks = tuple(unique.values())

    @property
    def unreferenced_materials(self) -> tuple[str, ...]:
        """本轮带进提示词但零引用的材料块出处编号（F35）。只读——由整合那一刻写一次。"""

        return self._unreferenced_materials

    @property
    def seeds(self) -> tuple[SeedRef, ...]:
        """这一轮带进来的调研产物（M8）：只是回链，不是第二份内容。"""

        return self._seeds

    def note_unreferenced_materials(self, eids: tuple[str, ...]) -> None:
        """整合那一刻记一笔：哪些材料块零引用（F35）。只写一次，重复调用追加。"""
        self._unreferenced_materials = self._unreferenced_materials + tuple(eids)

    def add_materials(self, refs: tuple[MaterialRef, ...] | list[MaterialRef]) -> None:
        """记下这一轮带进来的材料块。不能为空（空就不是"带进来了"）；同一块只留一次。"""

        refs = tuple(refs)
        if not refs:
            raise ShapeError("材料引用不能为空")
        known = {ref.chunk_id for ref in self._materials}
        fresh = [ref for ref in refs if ref.chunk_id not in known]
        self._materials = self._materials + tuple(fresh)

    @property
    def sim_slices(self) -> tuple[dict, ...]:
        return self._sim_slices

    @property
    def sim_hypotheses(self) -> tuple[dict, ...]:
        return self._sim_hypotheses

    @property
    def sim_unknowns(self) -> tuple[dict, ...]:
        return self._sim_unknowns

    @property
    def sim_inquiry_runs(self) -> int:
        return self._sim_inquiry_runs

    @property
    def sim_inquiry_chosen(self) -> tuple[str, ...]:
        return self._sim_inquiry_chosen

    def record_sim_inquiry(
        self,
        *,
        slices: tuple[dict, ...] | list[dict],
        hypotheses: tuple[dict, ...] | list[dict],
        unknowns: tuple[dict, ...] | list[dict],
    ) -> None:
        """记下一趟模拟调研的产物（覆盖写，不是追加——抽屉里永远只看最新一份）。

        不校验内容：装配层落进来之前已经验过（账本锚点/去重/窄词表），
        这里只记数（`sim_inquiry_runs` 是每轮 ≤2 次上限的分子）。
        """

        self._sim_slices = tuple(slices)
        self._sim_hypotheses = tuple(hypotheses)
        self._sim_unknowns = tuple(unknowns)
        self._sim_inquiry_chosen = ()
        self._sim_inquiry_runs += 1

    def choose_sim_inquiry(self, chosen: tuple[str, ...] | list[str]) -> None:
        """记下用户勾中的假设/盲区 id（keep 时读它合成材料）。"""

        self._sim_inquiry_chosen = tuple(chosen)

    @property
    def sim_review(self) -> dict | None:
        return self._sim_review

    @property
    def sim_review_runs(self) -> int:
        return self._sim_review_runs

    def record_sim_review(self, review: dict) -> None:
        """记下一趟模拟评测的产物（覆盖写；`sim_review_runs` 是每份草案 ≤1 的分子）。

        不校验内容：装配层落进来之前已经验过（按 persona 结构/引用/窄表三重筛），
        这里只记数——同 `record_sim_inquiry`。
        """

        self._sim_review = dict(review)
        self._sim_review_runs += 1

    def augment_draft(
        self,
        *,
        proposals: Sequence = (),
        gaps: Sequence[str] = (),
    ) -> None:
        """把模拟评测的"减"提案/留痕**追加**进现有草案（不改草案身份，不覆盖整合那批）。

        查重交给 `Draft.__post_init__`：撞 id 直接炸（不静默合并——两条同 id 提案
        在确认端只会被当成一条，静默合并会把哪条被丢藏起来）。
        没有草案直接拒：评测只在草案卡上跑，这是防"在错误的时刻调进用例"。
        """

        if self._draft is None:
            raise ShapeError("还没有草案，模拟评测只能在草案卡上追加")
        self._draft = replace(
            self._draft,
            proposals=self._draft.proposals + tuple(proposals),
            gaps=self._draft.gaps + tuple(gaps),
        )

    def dig(self) -> None:
        """从草案卡回到提问：**把这一轮没问上的那些题重新问一遍**（P14 C6）。

        四件事，缺一不可：
        1. 清掉未确认的草案——文档**从没被写过**（写入只在确认那一刻发生），所以不用回滚；
        2. 问卷闸门 +1 批；
        3. 席位轮额度也 +1——**每一批答案后面都要跟一趟席位轮**，只放宽问卷会让用户
           "答完这一批"时撞上 `SeatRoundLimitReached`（常规两批流程已经用满 3 趟）；
        4. 回到 `seats_running`，但**不新开**席位轮：`pending_seats` 已经是空的，
           席位一个都不重跑，dig 只花一次整合调用；紧接着的整合会因为闸门放宽而把
           轮内队列里的题（carried）真的端出来问——这才是"继续深挖"。
        """

        self._require_step(Step.AWAITING_CONFIRM)
        self._require_waiting(WaitingOn.DRAFT)
        self._draft = None
        # 评测属于"那一份草案"：新草案出来，旧 artifact 与次数一起作废
        # （额度语义因此是"每份草案 ≤1 次"，不是"每轮"——dig 后还能评测新草案）。
        self._sim_review = None
        self._sim_review_runs = 0
        self._extra_batches += 1
        self._extra_seat_rounds += 1
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
    def analysis_basis(self) -> str:
        """分析者交回的「我读了什么才切出这些主张」。

        模型每轮必填、且写得很好（真跑样例：「…以及现有文档中 s5 (daily-report-generation),
        s8 (…), s11 (open issues) 的内容进行切分。」），但这一路原本**零消费方**——
        不落库、不进渲染、不进事件。落在这里，复述卡才能在用户批准主张之前
        让它看见「分析者读没读对章节」。
        """

        return self._analysis_basis

    def note_analysis_basis(self, text: str) -> None:
        """记下分析者读了什么（只在 StartRound.analyze 调一次）。"""

        self._analysis_basis = text

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
            raise SeatRoundLimitReached(limit=self.seat_round_limit)
        self._seat_round_count += 1

    def _require_step(self, target: Step) -> None:
        if self._step is not target:
            raise IllegalStepTransition(current=self._step, target=target)

    def _require_waiting(self, target: WaitingOn) -> None:
        if self._waiting_on is not target:
            raise WaitingOnHuman(waiting_on=self._waiting_on)
