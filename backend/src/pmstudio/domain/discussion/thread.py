"""讨论线程：挂 project 的自由主题多轮讨论（M9 灵感阶段）。

round_id/question 存裸字符串——领域层只许碰 shared，不许 import round。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pmstudio.domain.discussion.card import MAX_CARDS_PER_THREAD, Card, CardState
from pmstudio.domain.shared.errors import (
    CardNotReviewable,
    ShapeError,
    ThreadClosed,
    ThreadFull,
)
from pmstudio.domain.shared.ids import DiscussionId, EvidenceId, ProjectId

MAX_DISCUSSION_TURNS = 30
#: 自主 panel 一次开跑最多几轮（M18）：机器可判的终止条件，不用"觉得够深了"。
MAX_PANEL_ROUNDS = 5
#: 一条线程最多几个角色同场（M18）：广播给所有席位只适合搜料，深想靠点名 panel。
MAX_PANEL_SIZE = 4


class ThreadStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class Turn:
    index: int
    role: str  # "user" | "assistant"
    text: str
    based_on: tuple[EvidenceId, ...] = ()
    #: 谁说的：用户 turn 写 "user"，席位 turn 写 role_id（M18；旧行缺省 "assistant"）。
    author: str = "assistant"
    #: 第几轮 panel 轮次（M18；say_pair 记当前轮，旧行缺省 1）。
    round: int = 1

    def __post_init__(self) -> None:
        if self.role not in ("user", "assistant"):
            raise ShapeError(f"turn 角色只能是 user 或 assistant：{self.role}")
        if not self.text.strip():
            raise ShapeError("turn 必须写清内容", index=self.index)


@dataclass(frozen=True, slots=True)
class Participant:
    """panel 成员（M18）：哪个角色、从第几轮开始在场。

    中途加入只对后续轮次生效（不追补、不重跑）；单成员 panel 就是今天的 1:1 线程。
    """

    role_id: str
    role_title: str
    joined_round: int

    def __post_init__(self) -> None:
        if not self.role_id.strip():
            raise ShapeError("panel 成员必须带 role_id")
        if self.joined_round < 1:
            raise ShapeError("joined_round 从 1 起", role_id=self.role_id)


@dataclass(frozen=True, slots=True)
class RoundSummary:
    """一轮的综合（M18）：共识 / 分歧 / 未决三组 bullets，只归纳不新开观点。

    它是给人看的副栏，不是第二真相源：回流只走卡片。
    """

    index: int
    agreed: tuple[str, ...] = ()
    disagreed: tuple[str, ...] = ()
    open: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QuestionSnapshot:
    title: str
    why: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ShapeError("问卷开场必须带题面")


@dataclass(frozen=True, slots=True)
class FindingRef:
    """开题调研底稿的一条引用（M13）：**只引用，不复制**。

    条目本地 id（`H1`/`B1`）只在趟内唯一，所以必须连 `exploration_id` 一起记——
    与轮次种子（`SeedRef`）同一条理由。文本在探索表里，说的时候读回来。
    """

    exploration_id: str
    local_id: str

    def __post_init__(self) -> None:
        if not self.exploration_id.strip():
            raise ShapeError("底稿引用必须带 exploration_id")
        if not self.local_id.strip():
            raise ShapeError("底稿引用必须带 local_id")


class Discussion:
    def __init__(
        self,
        *,
        discussion_id: DiscussionId,
        project_id: ProjectId,
        topic: str,
        participants: tuple[Participant, ...] | list[Participant],
        created_at: datetime,
        question: QuestionSnapshot | None = None,
        status: ThreadStatus = ThreadStatus.OPEN,
        turns: tuple[Turn, ...] = (),
        cards: tuple[Card, ...] = (),
        findings: tuple[FindingRef, ...] = (),
        summaries: tuple[RoundSummary, ...] = (),
    ) -> None:
        self._discussion_id = discussion_id
        self._project_id = project_id
        self._topic = topic
        self._participants = tuple(participants)
        self._created_at = created_at
        self._question = question
        self._status = status
        self._turns = tuple(turns)
        self._cards = tuple(cards)
        self._findings = tuple(findings)
        self._summaries = tuple(summaries)

    @classmethod
    def start(
        cls,
        *,
        discussion_id: DiscussionId,
        project_id: ProjectId,
        topic: str,
        participants: tuple[Participant, ...] | list[Participant],
        question: QuestionSnapshot | None,
        now: datetime,
        findings: tuple[FindingRef, ...] = (),
    ) -> "Discussion":
        if not topic.strip():
            raise ShapeError("开聊必须带主题")
        members = tuple(participants)
        if not 1 <= len(members) <= MAX_PANEL_SIZE:
            raise ShapeError(
                f"panel 一次 1–{MAX_PANEL_SIZE} 个角色", count=len(members)
            )
        if len({member.role_id for member in members}) != len(members):
            raise ShapeError("同一个角色不能请两次")
        if any(member.joined_round != 1 for member in members):
            raise ShapeError("开聊时所有成员都从第 1 轮在场")
        return cls(
            discussion_id=discussion_id,
            project_id=project_id,
            topic=topic,
            participants=members,
            created_at=now,
            question=question,
            findings=findings,
        )

    # —— 只读 ——
    @property
    def discussion_id(self) -> DiscussionId:
        return self._discussion_id

    @property
    def project_id(self) -> ProjectId:
        return self._project_id

    @property
    def topic(self) -> str:
        return self._topic

    @property
    def role_id(self) -> str:
        """首位成员（旧单角色线程的 role_id；多成员 panel 取首位，仅作兼容）。"""
        return self._participants[0].role_id

    @property
    def role_title(self) -> str:
        return self._participants[0].role_title

    @property
    def participants(self) -> tuple[Participant, ...]:
        return self._participants

    @property
    def current_round(self) -> int:
        """下一轮是第几轮（已出综合数 + 1；开跑/拉人都按它算）。"""
        return len(self._summaries) + 1

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def question(self) -> QuestionSnapshot | None:
        return self._question

    @property
    def status(self) -> ThreadStatus:
        return self._status

    @property
    def turns(self) -> tuple[Turn, ...]:
        return self._turns

    @property
    def cards(self) -> tuple[Card, ...]:
        return self._cards

    @property
    def findings(self) -> tuple[FindingRef, ...]:
        """开题调研底稿的引用（M13）：文本在探索表，这里只记回链。"""
        return self._findings

    @property
    def summaries(self) -> tuple[RoundSummary, ...]:
        """每轮的综合（M18）：只增；综合是给人看的副栏，回流只走卡片。"""
        return self._summaries

    # —— 推进 ——
    def say_pair(
        self,
        *,
        user_text: str,
        assistant_text: str,
        based_on: tuple[EvidenceId, ...],
    ) -> None:
        if self._status is not ThreadStatus.OPEN:
            raise ThreadClosed(discussion_id=self._discussion_id)
        if not user_text.strip() or not assistant_text.strip():
            raise ShapeError("一轮必须 user 与 assistant 成对给：单条写不进来")
        if len(self._turns) + 2 > MAX_DISCUSSION_TURNS:
            raise ThreadFull(limit=MAX_DISCUSSION_TURNS)
        base = len(self._turns)
        round_no = self.current_round
        self._turns = self._turns + (
            Turn(
                index=base, role="user", text=user_text, based_on=(),
                author="user", round=round_no,
            ),
            Turn(
                index=base + 1, role="assistant", text=assistant_text,
                based_on=tuple(based_on), author="assistant", round=round_no,
            ),
        )

    def append_agent_turn(
        self,
        *,
        role_id: str,
        text: str,
        based_on: tuple[EvidenceId, ...],
        round: int,
    ) -> None:
        """自主轮次里某成员的一条发言（M18）：单条可记，不要求成对。

        与 `say_pair` 的分工：用户插话走成对（T2 的原子性不动），panel 内 agent 发言
        自带 author，按（round, 到达序）追加。失败的 turn 不记——调用方保证只把
        校验通过的交进来。
        """
        if self._status is not ThreadStatus.OPEN:
            raise ThreadClosed(discussion_id=self._discussion_id)
        if role_id not in {member.role_id for member in self._participants}:
            raise ShapeError("不在场的角色不能发言", role_id=role_id)
        if not text.strip():
            raise ShapeError("发言必须写清内容", role_id=role_id)
        if round != self.current_round:
            raise ShapeError("只能往当前轮追加发言", round=round, current=self.current_round)
        if len(self._turns) + 1 > MAX_DISCUSSION_TURNS:
            raise ThreadFull(limit=MAX_DISCUSSION_TURNS)
        base = len(self._turns)
        self._turns = self._turns + (
            Turn(
                index=base, role="assistant", text=text,
                based_on=tuple(based_on), author=role_id, round=round,
            ),
        )

    def append_summary(self, summary: RoundSummary) -> None:
        """记一轮的综合（M18）：必须按轮次顺序， rounds 上限一到就停。"""
        if self._status is not ThreadStatus.OPEN:
            raise ThreadClosed(discussion_id=self._discussion_id)
        if summary.index != self.current_round:
            raise ShapeError(
                "综合必须按轮次顺序记", index=summary.index, current=self.current_round
            )
        if len(self._summaries) + 1 > MAX_PANEL_ROUNDS:
            raise ThreadFull(limit=MAX_PANEL_ROUNDS)
        self._summaries = self._summaries + (summary,)

    def join(self, *, role_id: str, role_title: str) -> None:
        """中途拉人进 panel（M18）：下一轮生效，不追补、不重跑。"""
        if self._status is not ThreadStatus.OPEN:
            raise ThreadClosed(discussion_id=self._discussion_id)
        if role_id in {member.role_id for member in self._participants}:
            raise ShapeError("这个角色已经在场了", role_id=role_id)
        if len(self._participants) + 1 > MAX_PANEL_SIZE:
            raise ShapeError(f"panel 一次最多 {MAX_PANEL_SIZE} 个角色")
        self._participants = self._participants + (
            Participant(role_id=role_id, role_title=role_title, joined_round=self.current_round),
        )

    def append_cards(self, cards: tuple[Card, ...]) -> None:
        if self._status is not ThreadStatus.OPEN:
            raise ThreadClosed(discussion_id=self._discussion_id)
        if not cards:
            raise ShapeError("组装至少要出一张卡：空包不算成功")
        if len(self._cards) + len(cards) > MAX_CARDS_PER_THREAD:
            raise ThreadFull(limit=MAX_CARDS_PER_THREAD)
        self._cards = self._cards + tuple(cards)

    def close(self) -> None:
        if self._status is ThreadStatus.CLOSED:
            return
        self._status = ThreadStatus.CLOSED

    def keep_card(
        self, card_id: str, *, as_: str, material_id: str | None = None
    ) -> None:
        """收下一张卡：幂等（同去向重复收下什么都不做），但**不许改去向**（T5）。

        `as_` 决定回流路径：`seed` = 预填下一轮输入；`material` = 落成材料（必须带 id，
        因为材料已经先落库了才盖这个章）。与候选的 `Exploration.keep` 同一套法条。
        """
        # 收下是回流不是聊天，只读的是 turns——所以不判 open（关掉的线程还能收卡）。
        card = self._card_of(card_id)
        if as_ not in ("seed", "material"):
            raise ShapeError("收下的去向只能是 seed 或 material", as_=as_)
        if card.state is CardState.DISMISSED:
            raise CardNotReviewable(
                card_id=card_id, state="已忽略", action="收下"
            )
        if card.state is CardState.KEPT:
            if card.kept_as == as_:
                return
            raise CardNotReviewable(
                card_id=card_id,
                state=f"已收下为{'种子' if card.kept_as == 'seed' else '材料'}",
                action=f"改成{'种子' if as_ == 'seed' else '材料'}",
            )
        if as_ == "material" and not (material_id or "").strip():
            raise ShapeError("收下为材料必须带 material_id", card_id=card_id)
        self._replace(
            card_id,
            state=CardState.KEPT,
            kept_as=as_,
            kept_ref=material_id if as_ == "material" else None,
        )

    def dismiss_card(self, card_id: str) -> None:
        """忽略一张卡：已收下的不许忽略；重复忽略幂等（T5）。"""

        card = self._card_of(card_id)
        if card.state is CardState.KEPT:
            raise CardNotReviewable(card_id=card_id, state="已收下", action="忽略")
        self._replace(card_id, state=CardState.DISMISSED)

    # —— 内部 ——
    def _card_of(self, card_id: str) -> Card:
        for card in self._cards:
            if card.card_id == card_id:
                return card
        raise ShapeError(f"这个线程没有卡片 {card_id}", card_id=card_id)

    def _replace(
        self,
        card_id: str,
        *,
        state: CardState,
        kept_as: str | None = None,
        kept_ref: str | None = None,
    ) -> None:
        self._cards = tuple(
            Card(
                card_id=card.card_id,
                statement=card.statement,
                grounding=card.grounding,
                based_on=card.based_on,
                kept_ref=kept_ref,
                state=state,
                kept_as=kept_as,
            )
            if card.card_id == card_id
            else card
            for card in self._cards
        )
