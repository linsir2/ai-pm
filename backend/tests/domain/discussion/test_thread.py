"""讨论线程 Thread＋Card 的不变量 T1–T4 与领域行为。

T1 open 门；T2 turns 原子成对；T3 close 终态幂等；T4 turns≤12、cards≤6。
"""

from datetime import UTC, datetime

import pytest

from pmstudio.domain.discussion.card import MAX_CARDS_PER_THREAD, Card, Grounding
from pmstudio.domain.discussion.thread import (
    MAX_DISCUSSION_TURNS,
    Discussion,
    QuestionSnapshot,
    ThreadStatus,
)
from pmstudio.domain.shared.errors import ShapeError, ThreadClosed, ThreadFull
from pmstudio.domain.shared.ids import DiscussionId, EvidenceId, ProjectId

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def start_thread(**over: object) -> Discussion:
    fields: dict[str, object] = {
        "discussion_id": DiscussionId("d-1"),
        "project_id": ProjectId("p-1"),
        "topic": "怎么让日报更好写",
        "role_id": "dlg.challenger",
        "role_title": "反方",
        "question": None,
        "now": NOW,
    }
    fields.update(over)
    return Discussion.start(**fields)  # type: ignore[arg-type]


def doc_card(card_id: str = "C1", **over: object) -> Card:
    fields: dict[str, object] = {
        "card_id": card_id,
        "statement": "日报按客户名找单，比按订单号快",
        "grounding": Grounding.DOCUMENT,
        "based_on": (EvidenceId("E1"),),
    }
    fields.update(over)
    return Card(**fields)  # type: ignore[arg-type]


def say(thread: Discussion, n: int = 1) -> None:
    for i in range(n):
        thread.say_pair(
            user_text=f"用户第{i}问",
            assistant_text=f"助手第{i}答",
            based_on=(),
        )


# —— T1：say/组装只认 open 线程 ——


def test_T1_ok_say_appends_a_user_assistant_pair() -> None:
    thread = start_thread()
    thread.say_pair(user_text="日报到底给谁看？", assistant_text="先给直属组长看", based_on=())

    turns = thread.turns
    assert len(turns) == 2
    assert (turns[0].role, turns[0].index) == ("user", 0)
    assert (turns[1].role, turns[1].index) == ("assistant", 1)
    assert turns[0].text == "日报到底给谁看？"
    assert turns[1].text == "先给直属组长看"


def test_T1_bad_say_on_closed_thread_raises() -> None:
    thread = start_thread()
    thread.close()

    with pytest.raises(ThreadClosed) as exc_info:
        thread.say_pair(user_text="还想聊", assistant_text="不聊了", based_on=())
    assert exc_info.value.invariant == "T1"


# —— T2：turns 原子成对、user 开头交替；失败不记轮 ——


def test_T2_ok_failed_model_call_leaves_no_lone_user_turn() -> None:
    thread = start_thread()
    with pytest.raises(ShapeError):
        thread.say_pair(user_text="用户先说了", assistant_text="   ", based_on=())

    assert thread.turns == ()


def test_T2_bad_single_turn_is_rejected() -> None:
    thread = start_thread()
    with pytest.raises(ShapeError):
        thread.say_pair(user_text="只有半句", assistant_text="", based_on=())


# —— T3：close 终态幂等 ——


def test_T3_ok_close_is_idempotent_and_terminal() -> None:
    thread = start_thread()
    assert thread.status is ThreadStatus.OPEN

    thread.close()
    thread.close()

    assert thread.status is ThreadStatus.CLOSED


def test_T3_bad_say_after_close_raises() -> None:
    thread = start_thread()
    thread.close()

    with pytest.raises(ThreadClosed):
        thread.say_pair(user_text="关了还说", assistant_text="不回", based_on=())


# —— T4：turns≤12、cards≤6 ——


def test_T4_ok_threads_and_cards_have_caps() -> None:
    assert MAX_DISCUSSION_TURNS == 12
    assert MAX_CARDS_PER_THREAD == 6

    thread = start_thread()
    say(thread, n=MAX_DISCUSSION_TURNS // 2)
    thread.append_cards(tuple(doc_card(f"C{i}") for i in range(1, MAX_CARDS_PER_THREAD + 1)))

    assert len(thread.turns) == MAX_DISCUSSION_TURNS
    assert len(thread.cards) == MAX_CARDS_PER_THREAD


def test_T4_bad_13th_turn_raises_thread_full() -> None:
    thread = start_thread()
    say(thread, n=MAX_DISCUSSION_TURNS // 2)

    with pytest.raises(ThreadFull) as exc_info:
        thread.say_pair(user_text="第13轮", assistant_text="满了", based_on=())
    assert exc_info.value.invariant == "T4"
    assert len(thread.turns) == MAX_DISCUSSION_TURNS


def test_T4_bad_7th_card_raises() -> None:
    thread = start_thread()
    thread.append_cards(tuple(doc_card(f"C{i}") for i in range(1, MAX_CARDS_PER_THREAD + 1)))

    with pytest.raises(ThreadFull) as exc_info:
        thread.append_cards((doc_card("C7"),))
    assert exc_info.value.invariant == "T4"


# —— 卡片接地规则（抄 item.py:48-56） ——


def test_cards_ok_invented_must_carry_no_refs() -> None:
    card = Card(card_id="C1", statement="也许大家根本不想写日报", grounding=Grounding.INVENTED)
    assert card.based_on == ()


def test_cards_bad_invented_with_refs_raises() -> None:
    with pytest.raises(ShapeError):
        Card(
            card_id="C1",
            statement="凭空的还敢带编号",
            grounding=Grounding.INVENTED,
            based_on=(EvidenceId("E1"),),
        )


# —— 收卡回流 ——


def test_keep_ok_marks_card_and_is_idempotent() -> None:
    thread = start_thread()
    thread.append_cards((doc_card("C1"),))
    thread.close()  # 收下是回流不是聊天：关掉的线程还能收卡

    thread.keep_card("C1", "m-1")
    thread.keep_card("C1", "m-1")

    assert thread.cards[0].kept_ref == "m-1"


def test_keep_bad_unknown_card_raises() -> None:
    thread = start_thread()
    with pytest.raises(ShapeError):
        thread.keep_card("CX", "m-1")


# —— 开场 ——


def test_start_ok_snapshot_carries_question_title_and_seat() -> None:
    thread = start_thread(
        topic="问卷第2题：日报模板太死",
        role_id="seat.devil",
        role_title="魔鬼代言人",
        question=QuestionSnapshot(title="日报模板太死怎么办？", why="三个人都这么说"),
    )

    assert thread.topic == "问卷第2题：日报模板太死"
    assert thread.role_id == "seat.devil"
    assert thread.question is not None
    assert thread.question.title == "日报模板太死怎么办？"


def test_start_rejects_empty_topic() -> None:
    with pytest.raises(ShapeError):
        start_thread(topic="   ")
