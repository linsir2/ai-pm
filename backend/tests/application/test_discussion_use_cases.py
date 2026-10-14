"""讨论用例层（M9-T3）：5 用例＋ThreadView 与材料回流。"""

import json

import pytest

from pmstudio.application.errors import UnknownRole
from pmstudio.application.use_cases import (
    AssembleCards,  # noqa: F401  (红点：Step 2 确认它未定义)
    CloseDiscussion,  # noqa: F401
    KeepDiscussionCard,  # noqa: F401
    ListDiscussions,  # noqa: F401
    SayToThread,  # noqa: F401
    StartDiscussion,  # noqa: F401
    _material_kind,
)
from pmstudio.domain.discussion.thread import QuestionSnapshot
from pmstudio.domain.exploration.exploration import ExplorationScope, ScopeKind
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.shared.errors import CardNotReviewable, ShapeError
from pmstudio.domain.shared.ids import DiscussionId
from pmstudio.harness.errors import PacketParseError
from tests.support.builders import PROJECT, ROUND_ID, document_with_feature, make_material_packet
from tests.support.harness import Harness

SAY_TEXT = "先把导出范围收窄到本店本月，再谈全量。"
BAD_REF_TEXT = "这个做法有依据。"


def _say_reply(*, text: str = SAY_TEXT, based_on: list[str] | None = None) -> str:
    return json.dumps(
        {"text": text, "based_on": based_on or []}, ensure_ascii=False
    )


def _bad_ref_reply() -> str:
    return json.dumps(
        {"text": BAD_REF_TEXT, "based_on": ["E99"]}, ensure_ascii=False
    )


def _empty_say_reply() -> str:
    """结构错：空回答（不是编号问题）——降级修不了它，两试皆败仍判死。"""
    return json.dumps({"text": "", "based_on": []}, ensure_ascii=False)


def _cards_reply_2() -> str:
    return json.dumps(
        {
            "cards": [
                {
                    "statement": "给导出加一个按客户名找单的快捷入口",
                    "grounding": "invented",
                    "based_on": [],
                },
                {
                    "statement": "月末对账前先收窄范围再全量导出",
                    "grounding": "document",
                    "based_on": ["E1"],
                },
            ]
        },
        ensure_ascii=False,
    )


def _empty_cards_reply() -> str:
    return json.dumps({"cards": []}, ensure_ascii=False)


def _seed_e1(harness: Harness) -> None:
    """让 E1 **同时进了账本和文档**（2026-10-10 §11.0.61 后必须有）。

    旧版只往账本里记 E1，于是它不出现在提示词任何槽位里——而罐头回复引它，
    靠的是"known = 全账本"这个漏洞（真跑 955 个引用里 9 个真幻觉就这么静默通过）。
    `seat_review.md:14` 的标题是「本轮主张（带编号）」、`inquiry.md:40` 明写
    「不许凭空造 E 编号」——**都是要求可见**，代码却放行全账本。

    现在 E1 是一条真实文档条目的出处（`document_with_feature` 的 `refs=(E1,)`），
    它就在 `$document_state` 里，模型看得见，引它合法。"""
    ledger = harness.evidences.get(PROJECT)
    ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=ROUND_ID, raw_span="想做个订单导出的功能"),
        now=harness.clock.now(),
    )
    harness.evidences.seed(ledger)
    harness.documents.seed(document_with_feature())


async def _start(
    harness: Harness, *, topic: str = "导出优化", role_id: str = "dlg.challenger",
    question: QuestionSnapshot | None = None,
):  # type: ignore[no-untyped-def]
    return await harness.start_discussion()(
        project_id=PROJECT, topic=topic, role_id=role_id, question=question
    )


async def test_start_resolves_explicit_role_and_snapshots_topic() -> None:
    harness = Harness(replies={})
    harness.seed_project()
    question = QuestionSnapshot(title="题面", why="为啥问")
    view = await _start(
        harness, topic="  导出   优化  ", role_id="dlg.challenger", question=question
    )

    assert view.topic == "导出 优化"
    assert view.role_id == "dlg.challenger"
    assert view.role_title == "反方"
    assert view.status == "open"
    assert view.turns == ()
    assert view.cards == ()
    assert view.question is not None and view.question.title == "题面"
    stored = harness.discussions.get(DiscussionId(view.thread_id))
    assert stored.topic == "导出 优化"


async def test_start_returns_existing_open_thread_for_same_topic_role() -> None:
    harness = Harness(replies={})
    harness.seed_project()
    first = await _start(harness, topic="导出 优化", role_id="dlg.challenger")
    second = await _start(harness, topic="  导出   优化 ", role_id="dlg.challenger")
    assert second.thread_id == first.thread_id

    third = await _start(harness, topic="导出 优化", role_id="dlg.user_voice")
    assert third.thread_id != first.thread_id


async def test_start_rejects_unknown_role_with_404_code() -> None:
    harness = Harness(replies={})
    harness.seed_project()
    with pytest.raises(UnknownRole) as caught:
        await _start(harness, topic="导出优化", role_id="dlg.nope")
    assert caught.value.code == "unknown_role"


async def test_start_rejects_empty_topic() -> None:
    harness = Harness(replies={})
    harness.seed_project()
    with pytest.raises(ShapeError):
        await _start(harness, topic="   ", role_id="dlg.challenger")


async def test_say_appends_pair_and_counts_turn_slot() -> None:
    harness = Harness(replies={"discussion": _say_reply()})
    harness.seed_project()
    started = await _start(harness)
    first = await harness.say_to_thread()(
        discussion_id=DiscussionId(started.thread_id), text="我想做导出"
    )

    assert len(first.turns) == 2
    assert first.turns[0].role == "user" and first.turns[0].text == "我想做导出"
    assert first.turns[0].index == 0
    assert first.turns[1].role == "assistant" and first.turns[1].text == SAY_TEXT
    assert first.turns[1].index == 1

    second = await harness.say_to_thread()(
        discussion_id=DiscussionId(started.thread_id), text="再补一句"
    )
    assert [turn.index for turn in second.turns] == [0, 1, 2, 3]
    assert len(harness.gateway.requests) == 2


async def test_say_puts_the_current_utterance_into_the_prompt() -> None:
    """**本轮用户说的话必须出现在这一趟的提示词里**（2026-10-10 真跑抓到的 P0）。

    事务顺序是「模型先跑、校验通过才原子记一对」——顺序本身是对的（先落库会在
    模型失败时留下孤儿 user 轮），但补上事务时漏了一件事：还没落库的那一轮也得
    出现在提示词里。漏掉的后果是角色永远在答**上一轮**：第 1 次追问只看得到开场
    问卷快照，第 2 次追问看到的是第 1 次追问。

    取证方式照 `test_say_renders_findings_into_the_prompt`——留样记的是槽位值。
    """

    harness = Harness(replies={"discussion": _say_reply()})
    harness.seed_project()
    started = await _start(harness)

    await harness.say_to_thread()(
        discussion_id=DiscussionId(started.thread_id), text="我想做导出"
    )

    prompt = "\n".join(message.content for message in harness.gateway.requests[-1].messages)
    assert "我想做导出" in prompt


async def test_say_history_window_stays_capped_with_the_current_utterance_in_it() -> None:
    """本轮发言占窗口**一格**，窗口仍是 6 条 + 一行照实说明（不许变成 7 行）。

    为什么不是 7 行：`render_thread_history` 的说明写「共 N 轮，只带了最近 6 轮」
    ——它存在的意义就是不让模型以为前面没了（`application/discussion.py:68`）。
    窗口涨到 7 行，这句说明就成了假话。

    「共 N 轮」里的 N 算本轮：模型跑完就真的会落这一轮，说 7 是诚实的。
    """

    harness = Harness(replies={"discussion": _say_reply()})
    harness.seed_project()
    started = await _start(harness)
    thread_id = DiscussionId(started.thread_id)
    for i in range(3):  # 喂满 6 轮（每轮 user+assistant 成对）
        await harness.say_to_thread()(discussion_id=thread_id, text=f"第{i + 1}次追问")

    await harness.say_to_thread()(discussion_id=thread_id, text="第4次追问：本轮原文")

    prompt = "\n".join(message.content for message in harness.gateway.requests[-1].messages)
    assert "第4次追问：本轮原文" in prompt
    # 窗口没涨：6 条历史 + 1 行说明
    assert "共 7 轮" in prompt and "只带了最近 6 轮" in prompt
    # 被挤出去的是最早那条，不是本轮
    assert "第1次追问" not in prompt
    assert "第3次追问" in prompt


async def test_say_repairs_once_then_succeeds() -> None:
    harness = Harness(replies={"discussion": [_bad_ref_reply(), _say_reply()]})
    harness.seed_project()
    started = await _start(harness)
    view = await harness.say_to_thread()(
        discussion_id=DiscussionId(started.thread_id), text="我想做导出"
    )

    assert len(view.turns) == 2
    assert len(harness.gateway.requests) == 2


async def test_say_salvages_a_hallucinated_ref_on_the_last_try() -> None:
    """引用了不存在的编号：重装一次仍犯 → 最后一次摘掉该编号、正文照收（2026-10-14）。"""
    harness = Harness(replies={"discussion": [_bad_ref_reply(), _bad_ref_reply()]})
    harness.seed_project()
    started = await _start(harness)

    view = await harness.say_to_thread()(
        discussion_id=DiscussionId(started.thread_id), text="我想做导出"
    )

    assert len(view.turns) == 2  # 不再判死；这一对记下来了
    assert len(harness.gateway.requests) == 2
    assert view.turns[1].based_on == ()  # E99 摘掉


async def test_say_fails_twice_on_a_structural_error_leaves_thread_untouched() -> None:
    """结构错（空回答）两试皆败 → 照旧判死、线程不动（降级只对"编号不认识"这一类）。"""
    harness = Harness(replies={"discussion": [_empty_say_reply(), _empty_say_reply()]})
    harness.seed_project()
    started = await _start(harness)
    with pytest.raises(PacketParseError) as caught:
        await harness.say_to_thread()(
            discussion_id=DiscussionId(started.thread_id), text="我想做导出"
        )
    assert caught.value.code == "model_packet_unparsable"
    assert harness.discussions.get(DiscussionId(started.thread_id)).turns == ()


async def test_say_on_closed_thread_is_409() -> None:
    harness = Harness(replies={"discussion": _say_reply()})
    harness.seed_project()
    started = await _start(harness)
    await harness.close_discussion()(discussion_id=DiscussionId(started.thread_id))
    with pytest.raises(Exception) as caught:
        await harness.say_to_thread()(
            discussion_id=DiscussionId(started.thread_id), text="还能聊吗"
        )
    assert str(getattr(caught.value, "code", "")).startswith("invariant:")


async def test_say_refuses_13th_turn() -> None:
    harness = Harness(replies={"discussion": _say_reply()})
    harness.seed_project()
    started = await _start(harness)
    did = DiscussionId(started.thread_id)
    for _ in range(6):
        stored = harness.discussions.get(did)
        stored.say_pair(user_text="u", assistant_text="a", based_on=())
        harness.discussions.save(stored)
    assert len(harness.discussions.get(did).turns) == 12
    with pytest.raises(Exception) as caught:
        await harness.say_to_thread()(discussion_id=did, text="第 13 轮")
    assert str(getattr(caught.value, "code", "")).startswith("invariant:")


async def test_assemble_stamps_c_ids_in_order_and_caps_6() -> None:
    harness = Harness(replies={"discussion_cards": _cards_reply_2()})
    harness.seed_project()
    _seed_e1(harness)
    started = await _start(harness)
    did = DiscussionId(started.thread_id)

    first = await harness.assemble_cards()(discussion_id=did)
    assert [card.card_id for card in first.cards] == ["C1", "C2"]
    second = await harness.assemble_cards()(discussion_id=did)
    assert [card.card_id for card in second.cards] == ["C1", "C2", "C3", "C4"]
    third = await harness.assemble_cards()(discussion_id=did)
    assert [card.card_id for card in third.cards] == ["C1", "C2", "C3", "C4", "C5", "C6"]

    # 同一线程第 7 张必须拒（ThreadFull → invariant:）
    with pytest.raises(Exception) as caught:
        await harness.assemble_cards()(discussion_id=did)
    assert str(getattr(caught.value, "code", "")).startswith("invariant:")
    assert len(harness.discussions.get(did).cards) == 6


async def test_assemble_empty_packet_fails_without_touching_thread() -> None:
    harness = Harness(
        replies={"discussion_cards": [_empty_cards_reply(), _empty_cards_reply()]}
    )
    harness.seed_project()
    started = await _start(harness)
    did = DiscussionId(started.thread_id)
    with pytest.raises(PacketParseError):
        await harness.assemble_cards()(discussion_id=did)
    assert harness.discussions.get(did).cards == ()


async def test_keep_card_puts_material_and_records_ref() -> None:
    harness = Harness(replies={"discussion_cards": _cards_reply_2()})
    harness.seed_project()
    _seed_e1(harness)
    started = await _start(harness)
    did = DiscussionId(started.thread_id)
    await harness.assemble_cards()(discussion_id=did)

    view = await harness.keep_discussion_card()(
        discussion_id=did, card_id="C1", as_="material"
    )
    kept = next(card for card in view.cards if card.card_id == "C1")
    assert kept.kept_ref
    assert (kept.state, kept.kept_as) == ("kept", "material")

    packet = harness.materials.get(kept.kept_ref or "")
    assert packet is not None
    assert packet.parsed.origin == "discussion"
    assert packet.parsed.source_ref == f"discussion:{did}#C1"


async def test_keep_card_as_seed_writes_no_material() -> None:
    harness = Harness(replies={"discussion_cards": _cards_reply_2()})
    harness.seed_project()
    _seed_e1(harness)
    started = await _start(harness)
    did = DiscussionId(started.thread_id)
    await harness.assemble_cards()(discussion_id=did)

    view = await harness.keep_discussion_card()(
        discussion_id=did, card_id="C1", as_="seed"
    )
    kept = next(card for card in view.cards if card.card_id == "C1")
    assert (kept.state, kept.kept_as, kept.kept_ref) == ("kept", "seed", None)
    # 收种子不回流材料：材料库一份都没多
    assert harness.materials.list_for_project(PROJECT, current=frozenset()) == ()


async def test_keep_same_card_twice_returns_same_material() -> None:
    harness = Harness(replies={"discussion_cards": _cards_reply_2()})
    harness.seed_project()
    _seed_e1(harness)
    started = await _start(harness)
    did = DiscussionId(started.thread_id)
    await harness.assemble_cards()(discussion_id=did)

    first = await harness.keep_discussion_card()(
        discussion_id=did, card_id="C1", as_="material"
    )
    ref = next(card for card in first.cards if card.card_id == "C1").kept_ref
    second = await harness.keep_discussion_card()(
        discussion_id=did, card_id="C1", as_="material"
    )
    again = next(card for card in second.cards if card.card_id == "C1").kept_ref
    assert again == ref
    assert len(harness.materials.list_for_project(PROJECT, current=frozenset())) == 1


async def test_keep_card_bad_same_card_other_destination_raises() -> None:
    harness = Harness(replies={"discussion_cards": _cards_reply_2()})
    harness.seed_project()
    _seed_e1(harness)
    started = await _start(harness)
    did = DiscussionId(started.thread_id)
    await harness.assemble_cards()(discussion_id=did)
    await harness.keep_discussion_card()(discussion_id=did, card_id="C1", as_="seed")

    with pytest.raises(CardNotReviewable) as caught:
        await harness.keep_discussion_card()(
            discussion_id=did, card_id="C1", as_="material"
        )
    assert caught.value.code == "invariant:T5"


async def test_dismiss_card_marks_it_and_blocks_keep() -> None:
    harness = Harness(replies={"discussion_cards": _cards_reply_2()})
    harness.seed_project()
    _seed_e1(harness)
    started = await _start(harness)
    did = DiscussionId(started.thread_id)
    await harness.assemble_cards()(discussion_id=did)

    view = await harness.dismiss_discussion_card()(discussion_id=did, card_id="C1")
    dismissed = next(card for card in view.cards if card.card_id == "C1")
    assert (dismissed.state, dismissed.kept_as, dismissed.kept_ref) == (
        "dismissed",
        None,
        None,
    )
    # 重复忽略幂等
    again = await harness.dismiss_discussion_card()(discussion_id=did, card_id="C1")
    assert next(card for card in again.cards if card.card_id == "C1").state == "dismissed"

    with pytest.raises(CardNotReviewable) as caught:
        await harness.keep_discussion_card()(
            discussion_id=did, card_id="C1", as_="seed"
        )
    assert caught.value.code == "invariant:T5"


async def test_start_round_resolves_discussion_card_seed_and_rejects_the_rest() -> None:
    """起轮认讨论卡种子（M12）：收下为 seed 的能带进轮，其余路径全拒。"""

    harness = Harness(replies={"discussion_cards": _cards_reply_2()})
    harness.seed_project()
    _seed_e1(harness)
    started = await _start(harness)
    did = DiscussionId(started.thread_id)
    await harness.assemble_cards()(discussion_id=did)
    await harness.keep_discussion_card()(discussion_id=did, card_id="C1", as_="seed")

    starter = harness.start_round()
    round_id = await starter.create(
        project_id=PROJECT,
        user_input="把 C1 记下来",
        seed_ids=[f"discussion:{did}#C1"],
    )
    seeds = [
        (ref.origin, ref.ref_id, ref.item_id)
        for ref in harness.rounds.get(round_id).seeds
    ]
    assert seeds == [("discussion", str(did), "C1")]

    with pytest.raises(ShapeError):  # C2 还没收下
        await starter.create(
            project_id=PROJECT, user_input="再试一次", seed_ids=[f"discussion:{did}#C2"]
        )
    with pytest.raises(ShapeError):  # 收成材料的也不能当「下一轮输入」
        await harness.keep_discussion_card()(
            discussion_id=did, card_id="C2", as_="material"
        )
        await starter.create(
            project_id=PROJECT, user_input="再试一次", seed_ids=[f"discussion:{did}#C2"]
        )


_INQUIRY_REPLY = json.dumps(
    {
        "basis": "读了文档",
        "hypotheses": [
            {
                "local_id": "H1",
                "statement": "运营做日报时按客户名找单",
                "grounding": "invented",
                "based_on": [],
                "falsification": "访谈后若没人这么找单即被推翻",
                "evidence_needed": "两个真实运营的访谈记录",
            }
        ],
        "blind_spots": [
            {
                "local_id": "B1",
                "statement": "客户合并后的订单归属未知",
                "next_step": "去问数据组",
            }
        ],
    },
    ensure_ascii=False,
)


async def test_start_with_finding_exploration_snapshots_candidates() -> None:
    """先跑一轮调研（M13）：把该趟全部候选快照成 findings 引用；线程上带得到计数。"""

    harness = Harness(replies={"inquiry": _INQUIRY_REPLY})
    harness.seed_project()
    harness.documents.seed(document_with_feature())
    use = harness.run_inquiry()
    xid = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    await use.execute(xid)

    view = await harness.start_discussion()(
        project_id=PROJECT,
        topic="带底稿聊导出",
        role_id="dlg.challenger",
        question=None,
        finding_exploration_id=str(xid),
    )

    assert view.findings_count == 2
    stored = harness.discussions.get(DiscussionId(view.thread_id))
    assert [(ref.exploration_id, ref.local_id) for ref in stored.findings] == [
        (str(xid), "H1"),
        (str(xid), "B1"),
    ]


async def test_start_rejects_bad_finding_exploration() -> None:
    from pmstudio.application.errors import UnknownExploration

    harness = Harness(replies={"inquiry": _INQUIRY_REPLY})
    harness.seed_project()
    harness.documents.seed(document_with_feature())

    with pytest.raises(UnknownExploration):  # 不存在 → 404
        await harness.start_discussion()(
            project_id=PROJECT,
            topic="x",
            role_id="dlg.challenger",
            question=None,
            finding_exploration_id="x-nope",
        )

    use = harness.run_inquiry()
    xid = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    with pytest.raises(ShapeError):  # 还没跑完，不能当底稿
        await harness.start_discussion()(
            project_id=PROJECT,
            topic="y",
            role_id="dlg.challenger",
            question=None,
            finding_exploration_id=str(xid),
        )


async def test_say_renders_findings_into_the_prompt() -> None:
    """底稿要真的进说这一轮的提示词（不是只存了没人读）。"""

    harness = Harness(replies={"inquiry": _INQUIRY_REPLY, "discussion": _say_reply()})
    harness.seed_project()
    harness.documents.seed(document_with_feature())
    use = harness.run_inquiry()
    xid = await use.create(project_id=PROJECT, scope=ExplorationScope(kind=ScopeKind.DOCUMENT))
    await use.execute(xid)
    started = await harness.start_discussion()(
        project_id=PROJECT,
        topic="带底稿聊导出",
        role_id="dlg.challenger",
        question=None,
        finding_exploration_id=str(xid),
    )

    await harness.say_to_thread()(
        discussion_id=DiscussionId(started.thread_id), text="先只做全量？"
    )

    prompt = "\n".join(message.content for message in harness.gateway.requests[-1].messages)
    # 留样记的是槽位值（模板头不在 messages 里）：断言语义——假设与盲区的原文都进了提示词
    assert "运营做日报时按客户名找单" in prompt
    assert "客户合并后的订单归属未知" in prompt


async def test_discussion_material_sorts_to_simulated() -> None:
    from pmstudio.application.discussion import discussion_material
    from pmstudio.domain.discussion.card import Card, Grounding

    harness = Harness(replies={})
    harness.seed_project()
    card = Card(card_id="C1", statement="快捷入口", grounding=Grounding.INVENTED)
    parsed, content = discussion_material(card, thread_id="d-1")
    packet = harness.materials.put(
        project_id=PROJECT,
        parsed=parsed,
        sha256="discussion-material:discussion:d-1#C1",
        content=content,
        now=harness.clock.now(),
    )
    assert _material_kind(packet).value == "simulated"
    assert _material_kind(make_material_packet()).value == "external"


def _cards_bad_ref_reply() -> str:
    """document 卡引了不存在的编号：降级应把编号摘掉、把 grounding 降成 invented。"""
    return json.dumps(
        {
            "cards": [
                {
                    "statement": "有据但还是摘了",
                    "grounding": "document",
                    "based_on": ["E99"],
                }
            ]
        },
        ensure_ascii=False,
    )


async def test_assemble_salvages_a_hallucinated_card_ref_as_invented() -> None:
    """卡引用不存在的编号：重装一次仍犯 → 最后一次摘编号、document 降成 invented（不判死）。"""
    harness = Harness(
        replies={"discussion_cards": [_cards_bad_ref_reply(), _cards_bad_ref_reply()]}
    )
    harness.seed_project()
    started = await _start(harness)

    view = await harness.assemble_cards()(discussion_id=DiscussionId(started.thread_id))

    assert len(view.cards) == 1  # 不再判死
    card = view.cards[0]
    assert card.based_on == ()  # E99 摘掉
    assert card.grounding == "invented"  # 文档接地摘空 → 降级凭空推演
