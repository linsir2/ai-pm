"""讨论线程落库（M9-T4）：往返、坏行跳过、列表排序、版本断言。

口径照 `test_sqlite_explorations.py`：列只放"要按它查"的
（`discussion_id/project_id/status/payload/created_at/updated_at`），
其余进 payload；坏行跳过不抛。
"""

from datetime import UTC, datetime

import pytest

from pmstudio.application.errors import UnknownDiscussion
from pmstudio.domain.discussion.card import Card, Grounding
from pmstudio.domain.discussion.thread import Discussion, QuestionSnapshot
from pmstudio.domain.shared.ids import DiscussionId, EvidenceId, ProjectId
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.repositories import SqliteDiscussionRepository

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
        role_id="dlg.challenger",
        role_title="反方",
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
        )
    )
    thread.keep_card("C1", "m-1")
    return thread


def test_thread_round_trips_with_turns_cards_and_kept_ref(store) -> None:  # type: ignore[no-untyped-def]
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
    ]
    assert restored.cards[0].statement == "先记下来再说"
    assert restored.cards[0].based_on == (EvidenceId("E1"),)
    assert restored.cards[0].kept_ref == "m-1"
    assert restored.cards[1].kept_ref is None


def test_list_returns_newest_first_and_skips_bad_rows(store) -> None:  # type: ignore[no-untyped-def]
    repo, connection = store
    first = Discussion.start(
        discussion_id=DiscussionId("d-1"),
        project_id=ProjectId("p1"),
        topic="旧主题",
        role_id="dlg.challenger",
        role_title="反方",
        question=None,
        now=NOW,
    )
    second = Discussion.start(
        discussion_id=DiscussionId("d-2"),
        project_id=ProjectId("p1"),
        topic="新主题",
        role_id="dlg.challenger",
        role_title="反方",
        question=None,
        now=NOW.replace(minute=30),
    )
    repo.save(first)
    repo.save(second)
    connection.execute(
        "INSERT INTO discussions (discussion_id, project_id, status, payload,"
        " created_at, updated_at) VALUES ('d-bad','p1','open','{oops',"
        "'2026-10-06T12:00:00+00:00','2026-10-06T12:00:00+00:00')"
    )

    assert [str(item.discussion_id) for item in repo.list_for_project(ProjectId("p1"))] == [
        "d-2",
        "d-1",
    ]


def test_unknown_thread_raises_unknown_discussion(store) -> None:  # type: ignore[no-untyped-def]
    repo, _ = store
    with pytest.raises(UnknownDiscussion):
        repo.get(DiscussionId("d-9"))


def test_user_version_is_5_and_discussions_table_exists(store) -> None:  # type: ignore[no-untyped-def]
    _, connection = store
    assert database.USER_VERSION == 5
    assert connection.execute("PRAGMA user_version").fetchone()[0] == database.USER_VERSION
    tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "discussions" in tables
