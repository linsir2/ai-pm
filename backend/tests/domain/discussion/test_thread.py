"""讨论线程 Thread＋Card 的不变量 T1–T5 与领域行为。

T1 open 门；T2 turns 原子成对（M18 起 agent turns 按 round/author 有序追加）；
T3 close 终态幂等；T4 rounds≤5、panel≤4、turns≤30、cards≤6；
T5 卡只能被过目一次：new → kept|dismissed，单向、幂等（收种子/收材料/忽略）。
"""

from datetime import UTC, datetime

import pytest

from pmstudio.domain.discussion.card import (
    MAX_CARDS_PER_THREAD,
    Card,
    CardState,
    Grounding,
)
from pmstudio.domain.discussion.thread import (
    MAX_DISCUSSION_TURNS,
    MAX_PANEL_ROUNDS,
    MAX_PANEL_SIZE,
    Discussion,
    FindingRef,
    Participant,
    QuestionSnapshot,
    RoundSummary,
    ThreadStatus,
)
from pmstudio.domain.shared.errors import (
    CardNotReviewable,
    ShapeError,
    ThreadClosed,
    ThreadFull,
)
from pmstudio.domain.shared.ids import DiscussionId, EvidenceId, ProjectId

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def start_thread(**over: object) -> Discussion:
    fields: dict[str, object] = {
        "discussion_id": DiscussionId("d-1"),
        "project_id": ProjectId("p-1"),
        "topic": "怎么让日报更好写",
        "participants": (
            Participant(role_id="dlg.challenger", role_title="反方", joined_round=1),
        ),
        "question": None,
        "now": NOW,
    }
    fields.update(over)
    return Discussion.start(**fields)  # type: ignore[arg-type]


def panel_of(*pairs: tuple[str, str]) -> tuple[Participant, ...]:
    return tuple(
        Participant(role_id=role_id, role_title=title, joined_round=1)
        for role_id, title in pairs
    )


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


# —— T4：rounds≤5、panel≤4、turns≤30、cards≤6 ——


def test_T4_ok_threads_and_cards_have_caps() -> None:
    assert MAX_DISCUSSION_TURNS == 30
    assert MAX_PANEL_ROUNDS == 5
    assert MAX_PANEL_SIZE == 4
    assert MAX_CARDS_PER_THREAD == 6

    thread = start_thread()
    say(thread, n=MAX_DISCUSSION_TURNS // 2)
    thread.append_cards(tuple(doc_card(f"C{i}") for i in range(1, MAX_CARDS_PER_THREAD + 1)))

    assert len(thread.turns) == MAX_DISCUSSION_TURNS
    assert len(thread.cards) == MAX_CARDS_PER_THREAD


def test_T4_bad_31st_turn_raises_thread_full() -> None:
    thread = start_thread()
    say(thread, n=MAX_DISCUSSION_TURNS // 2)

    with pytest.raises(ThreadFull) as exc_info:
        thread.say_pair(user_text="第31条", assistant_text="满了", based_on=())
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


# —— T5：卡只能被过目一次（收种子 / 收材料 / 忽略） ——


def test_T5_ok_keep_seed_then_idempotent() -> None:
    thread = start_thread()
    thread.append_cards((doc_card("C1"),))
    thread.close()  # 收下是回流不是聊天：关掉的线程还能收卡

    thread.keep_card("C1", as_="seed")
    thread.keep_card("C1", as_="seed")  # 同去向重复收下幂等静默

    card = thread.cards[0]
    assert card.state is CardState.KEPT
    assert card.kept_as == "seed"
    assert card.kept_ref is None


def test_T5_ok_keep_material_records_ref_and_dismiss_is_idempotent() -> None:
    thread = start_thread()
    thread.append_cards((doc_card("C1"), doc_card("C2")))

    thread.keep_card("C1", as_="material", material_id="m-1")
    thread.keep_card("C1", as_="material", material_id="m-1")  # 幂等
    thread.dismiss_card("C2")
    thread.dismiss_card("C2")  # 幂等

    kept, dismissed = thread.cards
    assert (kept.state, kept.kept_as, kept.kept_ref) == (
        CardState.KEPT,
        "material",
        "m-1",
    )
    assert dismissed.state is CardState.DISMISSED


def test_T5_bad_keep_after_dismiss_raises() -> None:
    thread = start_thread()
    thread.append_cards((doc_card("C1"),))
    thread.dismiss_card("C1")

    with pytest.raises(CardNotReviewable) as exc_info:
        thread.keep_card("C1", as_="seed")
    assert exc_info.value.invariant == "T5"


def test_T5_bad_keep_other_destination_raises() -> None:
    thread = start_thread()
    thread.append_cards((doc_card("C1"),))
    thread.keep_card("C1", as_="seed")

    with pytest.raises(CardNotReviewable) as exc_info:
        thread.keep_card("C1", as_="material", material_id="m-1")
    assert exc_info.value.invariant == "T5"
    assert thread.cards[0].kept_as == "seed"  # 拒了就原样，不改去向


def test_T5_bad_dismiss_kept_card_raises() -> None:
    thread = start_thread()
    thread.append_cards((doc_card("C1"),))
    thread.keep_card("C1", as_="material", material_id="m-1")

    with pytest.raises(CardNotReviewable) as exc_info:
        thread.dismiss_card("C1")
    assert exc_info.value.invariant == "T5"
    assert thread.cards[0].state is CardState.KEPT


# —— 收卡：形状错误（不是"过目一次"的问题） ——


def test_keep_bad_unknown_card_raises() -> None:
    thread = start_thread()
    with pytest.raises(ShapeError):
        thread.keep_card("CX", as_="seed")


def test_keep_bad_unknown_destination_raises() -> None:
    thread = start_thread()
    thread.append_cards((doc_card("C1"),))

    with pytest.raises(ShapeError):
        thread.keep_card("C1", as_="gift")


def test_keep_bad_material_without_id_raises() -> None:
    thread = start_thread()
    thread.append_cards((doc_card("C1"),))

    with pytest.raises(ShapeError):
        thread.keep_card("C1", as_="material")


# —— 开题调研（M13）：findings 只引用，不复制 ——


def test_start_ok_records_finding_refs() -> None:
    thread = start_thread(
        findings=(
            FindingRef(exploration_id="x-1", local_id="H1"),
            FindingRef(exploration_id="x-1", local_id="B1"),
        )
    )

    assert [(ref.exploration_id, ref.local_id) for ref in thread.findings] == [
        ("x-1", "H1"),
        ("x-1", "B1"),
    ]


def test_start_without_findings_has_none() -> None:
    assert start_thread().findings == ()


def test_finding_ref_needs_both_ids() -> None:
    with pytest.raises(ShapeError):
        FindingRef(exploration_id="  ", local_id="H1")
    with pytest.raises(ShapeError):
        FindingRef(exploration_id="x-1", local_id=" ")


# —— 开场 ——


def test_start_ok_snapshot_carries_question_title_and_seat() -> None:
    thread = start_thread(
        topic="问卷第2题：日报模板太死",
        participants=panel_of(("seat.devil", "魔鬼代言人")),
        question=QuestionSnapshot(title="日报模板太死怎么办？", why="三个人都这么说"),
    )

    assert thread.topic == "问卷第2题：日报模板太死"
    assert thread.role_id == "seat.devil"
    assert thread.question is not None
    assert thread.question.title == "日报模板太死怎么办？"



def test_start_rejects_empty_topic() -> None:
    with pytest.raises(ShapeError):
        start_thread(topic="   ")


# —— Panel：多角色同场（M18） ——


def test_panel_start_records_participants_joined_round_1() -> None:
    thread = start_thread(
        participants=panel_of(("dlg.challenger", "反方"), ("dlg.user_voice", "用户之声"))
    )

    assert [(p.role_id, p.role_title, p.joined_round) for p in thread.participants] == [
        ("dlg.challenger", "反方", 1),
        ("dlg.user_voice", "用户之声", 1),
    ]
    # 首位即旧 role_id/role_title：单成员 panel 就是今天的行为，无迁移
    assert thread.role_id == "dlg.challenger"
    assert thread.role_title == "反方"


def test_panel_start_rejects_empty_duplicate_and_oversize() -> None:
    with pytest.raises(ShapeError):
        start_thread(participants=())
    with pytest.raises(ShapeError):
        start_thread(
            participants=panel_of(("dlg.challenger", "反方"), ("dlg.challenger", "反方"))
        )
    with pytest.raises(ShapeError):
        start_thread(
            participants=tuple(
                Participant(role_id=f"dlg.x{i}", role_title="X", joined_round=1)
                for i in range(MAX_PANEL_SIZE + 1)
            )
        )


def test_append_agent_turn_records_author_and_round() -> None:
    thread = start_thread(
        participants=panel_of(("dlg.challenger", "反方"), ("dlg.user_voice", "用户之声"))
    )
    thread.append_agent_turn(role_id="dlg.user_voice", text="用户会骂", based_on=(), round=1)
    thread.append_agent_turn(role_id="dlg.challenger", text="先泼冷水", based_on=(), round=1)

    assert [(t.author, t.round, t.role) for t in thread.turns] == [
        ("dlg.user_voice", 1, "assistant"),
        ("dlg.challenger", 1, "assistant"),
    ]


def test_append_agent_turn_rejects_unknown_closed_and_full() -> None:
    thread = start_thread()
    with pytest.raises(ShapeError):
        thread.append_agent_turn(role_id="dlg.nobody", text="混入", based_on=(), round=1)
    thread.close()
    with pytest.raises(ThreadClosed):
        thread.append_agent_turn(
            role_id="dlg.challenger", text="关了还说", based_on=(), round=1
        )


def test_append_summary_follows_round_order_and_caps_rounds() -> None:
    thread = start_thread()
    with pytest.raises(ShapeError):
        thread.append_summary(RoundSummary(index=2, agreed=(), disagreed=(), open=()))
    for index in range(1, MAX_PANEL_ROUNDS + 1):
        thread.append_summary(
            RoundSummary(index=index, agreed=(f"共识{index}",), disagreed=(), open=())
        )
    assert len(thread.summaries) == MAX_PANEL_ROUNDS
    with pytest.raises(ThreadFull):
        thread.append_summary(
            RoundSummary(index=MAX_PANEL_ROUNDS + 1, agreed=(), disagreed=(), open=())
        )


def test_join_adds_for_next_round_only() -> None:
    thread = start_thread()
    thread.append_summary(RoundSummary(index=1, agreed=(), disagreed=(), open=("未决",)))
    thread.join(role_id="dlg.user_voice", role_title="用户之声")

    assert [(p.role_id, p.joined_round) for p in thread.participants] == [
        ("dlg.challenger", 1),
        ("dlg.user_voice", 2),
    ]
    with pytest.raises(ShapeError):
        thread.join(role_id="dlg.user_voice", role_title="用户之声")  # 重复加入
    thread.close()
    with pytest.raises(ThreadClosed):
        thread.join(role_id="dlg.analogist", role_title="类比者")


def test_say_pair_marks_current_round() -> None:
    thread = start_thread()
    thread.say_pair(user_text="第一问", assistant_text="第一答", based_on=())
    assert [t.round for t in thread.turns] == [1, 1]
    thread.append_summary(RoundSummary(index=1, agreed=(), disagreed=(), open=()))
    thread.say_pair(user_text="第二问", assistant_text="第二答", based_on=())
    assert [t.round for t in thread.turns] == [1, 1, 2, 2]
