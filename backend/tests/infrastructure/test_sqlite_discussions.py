"""讨论线程落库（M9-T4）：往返、坏行跳过、列表排序、版本断言。

口径照 `test_sqlite_explorations.py`：列只放"要按它查"的
（`discussion_id/project_id/status/payload/created_at/updated_at`），
其余进 payload；坏行跳过不抛。
"""

from datetime import UTC, datetime

import pytest

from pmstudio.application.errors import UnknownDiscussion
from pmstudio.domain.discussion.card import Card, CardState, Grounding
from pmstudio.domain.discussion.thread import (
    Discussion,
    FindingRef,
    Participant,
    QuestionSnapshot,
    RoundSummary,
    ThreadStatus,
)
from pmstudio.domain.shared.ids import DiscussionId, EvidenceId, ProjectId
from pmstudio.infrastructure.sqlite import database, owner_scope
from pmstudio.infrastructure.sqlite.repositories import SqliteDiscussionRepository
from pmstudio.infrastructure.sqlite.serialization import (
    discussion_from_payload,
    discussion_to_payload,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture()
def store(tmp_path):  # type: ignore[no-untyped-def]
    connection = database.connect(tmp_path / "pmstudio.db")
    return SqliteDiscussionRepository(connection), connection


def _thread() -> Discussion:
    thread = Discussion.start(
        discussion_id=DiscussionId("d-1"),
        project_id=ProjectId("p1"),
        topic="怎么让灵感不丢",
        participants=(
            Participant(role_id="dlg.challenger", role_title="反方", joined_round=1),
        ),
        question=QuestionSnapshot(title="题面", why="为啥问"),
        now=NOW,
    )
    thread.say_pair(
        user_text="我的想法是随手记",
        assistant_text="我来挑刺：随手记的东西从不回看",
        based_on=(EvidenceId("E1"),),
    )
    thread.append_cards(
        (
            Card(
                card_id="C1",
                statement="先记下来再说",
                grounding=Grounding.DOCUMENT,
                based_on=(EvidenceId("E1"),),
            ),
            Card(
                card_id="C2",
                statement="凭空一个点子",
                grounding=Grounding.INVENTED,
            ),
            Card(
                card_id="C3",
                statement="第三条：收成下一轮输入",
                grounding=Grounding.DOCUMENT,
                based_on=(EvidenceId("E1"),),
            ),
        )
    )
    thread.keep_card("C1", as_="material", material_id="m-1")
    thread.dismiss_card("C2")
    thread.keep_card("C3", as_="seed")
    return thread


def test_thread_round_trips_with_turns_cards_and_state(store) -> None:  # type: ignore[no-untyped-def]
    repo, _ = store
    repo.save(_thread())
    restored = repo.get(DiscussionId("d-1"))

    assert restored.topic == "怎么让灵感不丢"
    assert restored.role_id == "dlg.challenger"
    assert restored.role_title == "反方"
    assert restored.status.value == "open"
    assert restored.created_at == NOW
    assert restored.question is not None
    assert restored.question.title == "题面"
    assert restored.question.why == "为啥问"
    assert [(turn.index, turn.role) for turn in restored.turns] == [(0, "user"), (1, "assistant")]
    assert restored.turns[0].text == "我的想法是随手记"
    assert restored.turns[0].based_on == ()
    assert restored.turns[1].based_on == (EvidenceId("E1"),)
    assert [(card.card_id, card.grounding.value) for card in restored.cards] == [
        ("C1", "document"),
        ("C2", "invented"),
        ("C3", "document"),
    ]
    assert restored.cards[0].statement == "先记下来再说"
    assert restored.cards[0].based_on == (EvidenceId("E1"),)
    # 三种过目状态各回各的形状（T5）：材料带 ref、忽略、种子不带 ref
    assert (restored.cards[0].state, restored.cards[0].kept_as, restored.cards[0].kept_ref) == (
        CardState.KEPT,
        "material",
        "m-1",
    )
    assert (restored.cards[1].state, restored.cards[1].kept_as) == (
        CardState.DISMISSED,
        None,
    )
    assert (restored.cards[2].state, restored.cards[2].kept_as, restored.cards[2].kept_ref) == (
        CardState.KEPT,
        "seed",
        None,
    )


def test_payload_keeps_sparse_state_maps_and_drops_legacy_kept_key() -> None:
    """状态只存非默认的（照探索的 `states` 口径）；老 `kept` 键不再写。"""

    payload = discussion_to_payload(_thread())

    assert payload["states"] == {"C1": "kept", "C2": "dismissed", "C3": "kept"}
    assert payload["kept_as"] == {"C1": "material", "C3": "seed"}
    assert payload["kept_ref"] == {"C1": "m-1"}
    assert "kept" not in payload


def test_legacy_kept_map_reads_back_as_material(store) -> None:  # type: ignore[no-untyped-def]
    """老行（M9 的 `kept` map：card_id → material_id）读回来就是"已收下为材料"。"""

    thread = Discussion.start(
        discussion_id=DiscussionId("d-1"),
        project_id=ProjectId("p1"),
        topic="旧行",
        participants=(
            Participant(role_id="dlg.challenger", role_title="反方", joined_round=1),
        ),
        question=None,
        now=NOW,
    )
    thread.append_cards(
        (
            Card(
                card_id="C1",
                statement="旧行里收过的卡",
                grounding=Grounding.DOCUMENT,
                based_on=(EvidenceId("E1"),),
            ),
            Card(
                card_id="C2",
                statement="旧行里没收的卡",
                grounding=Grounding.INVENTED,
            ),
        )
    )
    old = discussion_to_payload(thread)
    old.pop("states", None)
    old.pop("kept_as", None)
    old.pop("kept_ref", None)
    old["kept"] = {"C1": "m-old"}

    restored = discussion_from_payload(
        old, discussion_id=DiscussionId("d-1"), project_id=ProjectId("p1"),
        status=thread.status, created_at=NOW,
    )

    assert (restored.cards[0].state, restored.cards[0].kept_as, restored.cards[0].kept_ref) == (
        CardState.KEPT,
        "material",
        "m-old",
    )
    assert (restored.cards[1].state, restored.cards[1].kept_as, restored.cards[1].kept_ref) == (
        CardState.NEW,
        None,
        None,
    )


def test_findings_round_trip_and_old_rows_fall_back(store) -> None:  # type: ignore[no-untyped-def]
    """开题调研底稿（M13）：只引用；老行缺 `findings` 键 → 空。"""

    repo, _ = store
    thread = Discussion.start(
        discussion_id=DiscussionId("d-f"),
        project_id=ProjectId("p1"),
        topic="带底稿的线程",
        participants=(
            Participant(role_id="dlg.challenger", role_title="反方", joined_round=1),
        ),
        question=None,
        now=NOW,
        findings=(
            FindingRef(exploration_id="x-1", local_id="H1"),
            FindingRef(exploration_id="x-1", local_id="B1"),
        ),
    )
    repo.save(thread)

    restored = repo.get(DiscussionId("d-f"))
    assert [(ref.exploration_id, ref.local_id) for ref in restored.findings] == [
        ("x-1", "H1"),
        ("x-1", "B1"),
    ]

    payload = discussion_to_payload(thread)
    payload.pop("findings")  # 老行：没有这把键
    restored_old = discussion_from_payload(
        payload, discussion_id=DiscussionId("d-f"), project_id=ProjectId("p1"),
        status=thread.status, created_at=NOW,
    )
    assert restored_old.findings == ()


def test_list_returns_newest_first_and_skips_bad_rows(store) -> None:  # type: ignore[no-untyped-def]
    repo, connection = store
    first = Discussion.start(
        discussion_id=DiscussionId("d-1"),
        project_id=ProjectId("p1"),
        topic="旧主题",
        participants=(
            Participant(role_id="dlg.challenger", role_title="反方", joined_round=1),
        ),
        question=None,
        now=NOW,
    )
    second = Discussion.start(
        discussion_id=DiscussionId("d-2"),
        project_id=ProjectId("p1"),
        topic="新主题",
        participants=(
            Participant(role_id="dlg.challenger", role_title="反方", joined_round=1),
        ),
        question=None,
        now=NOW.replace(minute=30),
    )
    repo.save(first)
    repo.save(second)
    connection.execute(
        "INSERT INTO discussions (discussion_id, project_id, owner_id, status, payload,"
        " created_at, updated_at) VALUES ('d-bad','p1',?,'open','{oops',"
        "'2026-10-06T12:00:00+00:00','2026-10-06T12:00:00+00:00')",
        (owner_scope.current_owner_scope(),),
    )

    assert [str(item.discussion_id) for item in repo.list_for_project(ProjectId("p1"))] == [
        "d-2",
        "d-1",
    ]


def test_unknown_thread_raises_unknown_discussion(store) -> None:  # type: ignore[no-untyped-def]
    repo, _ = store
    with pytest.raises(UnknownDiscussion):
        repo.get(DiscussionId("d-9"))


def test_user_version_is_current_and_discussions_table_exists(store) -> None:  # type: ignore[no-untyped-def]
    _, connection = store
    assert database.USER_VERSION == 14
    assert connection.execute("PRAGMA user_version").fetchone()[0] == database.USER_VERSION
    tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "discussions" in tables
    assert "observations" in tables
    assert "round_digests" in tables
    assert "inspirations" in tables
    # 死字段已销账
    obs_cols = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(observations)")
    }
    assert "relevance_count" not in obs_cols
    assert "last_accessed" not in obs_cols
    assert "sections" not in obs_cols
    assert "features" not in obs_cols
    # 原先这里还断言 `created_at` 不在——v11 当死字段删掉，v14 把它加回来当
    # `recent()` 的排序判据（修 obs-9 排在 obs-10 前的字典序 bug），故不再断言缺席。
    insp_cols = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(inspirations)")
    }
    assert "grounding" not in insp_cols
    assert "context" not in insp_cols


def test_panel_thread_round_trips_with_participants_authors_and_summaries(store) -> None:  # type: ignore[no-untyped-def]
    """M18：panel 成员/author/综合落库再读回来，一个不少（TDD 先红）。"""
    repo, _ = store
    thread = Discussion.start(
        discussion_id=DiscussionId("d-9"),
        project_id=ProjectId("p1"),
        topic="日报要不要日报",
        participants=(
            Participant(role_id="dlg.challenger", role_title="反方", joined_round=1),
            Participant(role_id="dlg.user_voice", role_title="用户之声", joined_round=1),
        ),
        question=None,
        now=NOW,
    )
    thread.append_agent_turn(role_id="dlg.challenger", text="先泼冷水", based_on=(), round=1)
    thread.append_summary(
        RoundSummary(index=1, agreed=("都认要写",), disagreed=("怎么写",), open=("给谁看",))
    )
    repo.save(thread)

    restored = repo.get(DiscussionId("d-9"))
    assert [(p.role_id, p.joined_round) for p in restored.participants] == [
        ("dlg.challenger", 1),
        ("dlg.user_voice", 1),
    ]
    assert [(t.author, t.round) for t in restored.turns] == [("dlg.challenger", 1)]
    assert [(s.index, s.agreed, s.disagreed, s.open) for s in restored.summaries] == [
        (1, ("都认要写",), ("怎么写",), ("给谁看",))
    ]


def test_old_rows_without_panel_keys_read_back_as_single_member(store) -> None:  # type: ignore[no-untyped-def]
    """M18 兼容：M9 老行没有 participants/summaries/author/round 键，读回即单成员 panel。"""
    from pmstudio.infrastructure.sqlite.serialization import discussion_from_payload

    restored = discussion_from_payload(
        {
            "topic": "老话题",
            "role_id": "dlg.challenger",
            "role_title": "反方",
            "turns": [{"index": 0, "role": "user", "text": "老话", "based_on": []}],
            "cards": [],
        },
        discussion_id=DiscussionId("d-old"),
        project_id=ProjectId("p1"),
        status=ThreadStatus.OPEN,
        created_at=NOW,
    )
    assert [(p.role_id, p.joined_round) for p in restored.participants] == [
        ("dlg.challenger", 1)
    ]
    assert restored.turns[0].author == "assistant"
    assert restored.summaries == ()
