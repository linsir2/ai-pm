"""问卷：一批最多 5 题；超出的先降级成清单项。问卷不是必然环节。

**最多两批**（`04-未决.md` C6）：第二批答完再整合时，剩下的题目一律降级成 s11 开放问题，
不再追问——用户的体感是"别没完没了地问"，代价是有些题要到文档里再补。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pmstudio.domain.shared.errors import (
    DuplicateQuestionId,
    ShapeError,
    TooManyQuestions,
)
from pmstudio.domain.shared.ids import EvidenceId, FormId, QuestionId, RoundId
from pmstudio.domain.shared.target import Target

MAX_QUESTIONS_PER_BATCH = 5
MAX_QUESTIONNAIRES = 2


class QuestionType(StrEnum):
    SINGLE = "single"
    MULTI = "multi"
    TEXT = "text"


class QuestionKind(StrEnum):
    GAP = "gap"  # 缺一个只有用户知道的信息
    CONFLICT = "conflict"  # 与本轮输入或文档里的既有结论冲突
    DECISION = "decision"  # 需要用户拍一个口径（含"这是不是独立功能"）


@dataclass(frozen=True, slots=True)
class Option:
    option_id: str
    text: str

    def __post_init__(self) -> None:
        if not self.option_id.strip() or not self.text.strip():
            raise ShapeError("选项必须有 id 与文本")


@dataclass(frozen=True, slots=True)
class Question:
    question_id: QuestionId
    target: Target
    type: QuestionType
    title: str
    why: str
    consequence_if_skipped: str
    options: tuple[Option, ...] = ()
    evidence_refs: tuple[EvidenceId, ...] = ()
    asked_by: tuple[str, ...] = ()
    kind: QuestionKind = QuestionKind.GAP

    def __post_init__(self) -> None:
        if not isinstance(self.kind, QuestionKind):
            raise ShapeError(
                "题目类型必须是 gap / conflict / decision",
                question_id=str(self.question_id),
            )
        for value, message in (
            (self.title, "题目必须写清问什么"),
            (self.why, "题目必须写为什么问"),
            (self.consequence_if_skipped, "题目必须写跳过的后果"),
        ):
            if not value.strip():
                raise ShapeError(message, question_id=str(self.question_id))
        if self.type is QuestionType.TEXT:
            if self.options:
                raise ShapeError("开放题不该有选项", question_id=str(self.question_id))
        elif len(self.options) < 2:
            raise ShapeError("选择题至少两个选项", question_id=str(self.question_id))


@dataclass(frozen=True, slots=True)
class ListItem:
    """超出本批上限的题降级成的清单项：记下来，但不挡这一轮。"""

    text: str
    target: Target
    reason: str


@dataclass(frozen=True, slots=True)
class Questionnaire:
    form_id: FormId
    round_id: RoundId
    questions: tuple[Question, ...]
    list_items: tuple[ListItem, ...] = ()
    dropped: tuple[str, ...] = ()
    """归因解析不上的席位条目地址（形如 `seat.x:O1`）。

    留痕用：模型写了"这道题来自哪条席位意见"，装配层对不上号时把原文记在这儿，
    界面上能看见"这几条没算数"，也就是 `04-未决.md` C5 要的"丢弃要留痕"的一半。
    """

    def __post_init__(self) -> None:
        if not self.questions:
            raise ShapeError("问卷至少一题；没有必须问的就不要发问卷")
        if len(self.questions) > MAX_QUESTIONS_PER_BATCH:
            raise TooManyQuestions(count=len(self.questions), limit=MAX_QUESTIONS_PER_BATCH)
        ids = [item.question_id for item in self.questions]
        if len(set(ids)) != len(ids):
            repeated = next(item for item in ids if ids.count(item) > 1)
            raise DuplicateQuestionId(question_id=repeated)


@dataclass(frozen=True, slots=True)
class Answer:
    question_id: QuestionId
    text: str
    selected: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnswerSet:
    form_id: FormId
    answers: tuple[Answer, ...]
    answered_at: datetime
    skipped: tuple[QuestionId, ...] = ()

    def __post_init__(self) -> None:
        ids = [answer.question_id for answer in self.answers] + list(self.skipped)
        if len(set(ids)) != len(ids):
            raise ShapeError("同一题不能既答又跳，也不能答两次")
        if not ids:
            raise ShapeError("交卷至少要有答案或显式跳过")
