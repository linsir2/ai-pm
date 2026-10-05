"""讨论线程：挂 project 的自由主题多轮讨论（M9 灵感阶段）。

round_id/question 存裸字符串——领域层只许碰 shared，不许 import round。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pmstudio.domain.discussion.card import MAX_CARDS_PER_THREAD, Card
from pmstudio.domain.shared.errors import ShapeError, ThreadClosed, ThreadFull
from pmstudio.domain.shared.ids import DiscussionId, EvidenceId, ProjectId

MAX_DISCUSSION_TURNS = 12


class ThreadStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class Turn:
    index: int
    role: str  # "user" | "assistant"
    text: str
    based_on: tuple[EvidenceId, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in ("user", "assistant"):
            raise ShapeError(f"turn 角色只能是 user 或 assistant：{self.role}")
        if not self.text.strip():
            raise ShapeError("turn 必须写清内容", index=self.index)


@dataclass(frozen=True, slots=True)
class QuestionSnapshot:
    title: str
    why: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ShapeError("问卷开场必须带题面")


class Discussion:
    def __init__(
        self,
        *,
        discussion_id: DiscussionId,
        project_id: ProjectId,
        topic: str,
        role_id: str,
        role_title: str,
        created_at: datetime,
        question: QuestionSnapshot | None = None,
        status: ThreadStatus = ThreadStatus.OPEN,
        turns: tuple[Turn, ...] = (),
        cards: tuple[Card, ...] = (),
    ) -> None:
        self._discussion_id = discussion_id
        self._project_id = project_id
        self._topic = topic
        self._role_id = role_id
        self._role_title = role_title
        self._created_at = created_at
        self._question = question
        self._status = status
        self._turns = tuple(turns)
        self._cards = tuple(cards)

    @classmethod
    def start(
        cls,
        *,
        discussion_id: DiscussionId,
        project_id: ProjectId,
        topic: str,
        role_id: str,
        role_title: str,
        question: QuestionSnapshot | None,
        now: datetime,
    ) -> "Discussion":
        if not topic.strip():
            raise ShapeError("开聊必须带主题")
        return cls(
            discussion_id=discussion_id,
            project_id=project_id,
            topic=topic,
            role_id=role_id,
            role_title=role_title,
            created_at=now,
            question=question,
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
        return self._role_id

    @property
    def role_title(self) -> str:
        return self._role_title

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
        self._turns = self._turns + (
            Turn(index=base, role="user", text=user_text, based_on=()),
            Turn(index=base + 1, role="assistant", text=assistant_text, based_on=tuple(based_on)),
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

    def keep_card(self, card_id: str, material_id: str) -> None:
        # 收下是回流不是聊天，只读的是 turns——所以不判 open（关掉的线程还能收卡）。
        known = {card.card_id for card in self._cards}
        if card_id not in known:
            raise ShapeError(f"这个线程没有卡片 {card_id}", card_id=card_id)
        self._cards = tuple(
            Card(
                card_id=card.card_id,
                statement=card.statement,
                grounding=card.grounding,
                based_on=card.based_on,
                kept_ref=material_id,
            )
            if card.card_id == card_id
            else card
            for card in self._cards
        )
