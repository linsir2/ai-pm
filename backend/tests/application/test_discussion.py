"""讨论纯函数（M9-T2）：账本引用校验、历史渲染、卡片材料合成、去重命名空间。"""

import pytest

from pmstudio.application.discussion import (
    ORIGIN_DISCUSSION,
    discussion_material,
    render_thread_history,
    validate_ledger_refs,
)
from pmstudio.application.exploration import material_sha as exploration_sha
from pmstudio.application.material_synthesis import material_sha
from pmstudio.domain.discussion.card import Card, Grounding
from pmstudio.domain.discussion.thread import Turn
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId


def _known() -> frozenset[EvidenceId]:
    return frozenset({EvidenceId("E1"), EvidenceId("E2")})


def test_validate_ledger_refs_passes_known_and_rejects_unknown_with_candidates() -> None:
    known = _known()
    validate_ledger_refs((EvidenceId("E1"),), known_eids=known)

    with pytest.raises(ShapeError) as caught:
        validate_ledger_refs((EvidenceId("E99"),), known_eids=known)
    assert "E99" in caught.value.message


def test_render_thread_history_keeps_only_last_6_and_numbers_them() -> None:
    turns = tuple(
        Turn(
            index=index,
            role="user" if index % 2 == 0 else "assistant",
            text=f"第{index}轮",
            based_on=(EvidenceId("E1"),) if index == 6 else (),
        )
        for index in range(8)
    )
    text = render_thread_history(turns)

    lines = text.splitlines()
    assert len(lines) == 7  # 6 条历史 + 1 行"只带了最近 6 轮"的照实说明
    assert "[t0 用户]" not in text
    assert "[t1 助手]" not in text
    assert "[t2 用户]" in text
    assert "[t7 助手]" in text
    assert "依据 E1" in text


def test_render_thread_history_notes_the_omitted_turns() -> None:
    """只带最近 6 轮，就说清一共几轮（不然模型以为前面没了）。"""

    turns = tuple(
        Turn(
            index=index,
            role="user" if index % 2 == 0 else "assistant",
            text=f"第{index}轮",
        )
        for index in range(8)
    )
    text = render_thread_history(turns)

    assert "共 8 轮" in text
    assert "只带了最近 6 轮" in text
    # 没超时不说
    assert "共 6 轮" not in render_thread_history(turns[:6])


def test_discussion_material_builds_source_ref_and_single_chunk() -> None:
    card = Card(
        card_id="C1",
        statement="给导出加一个按客户名找单的快捷入口",
        grounding=Grounding.INVENTED,
    )
    parsed, content = discussion_material(card, thread_id="d-1")

    assert parsed.source_ref == "discussion:d-1#C1"
    assert parsed.origin == "discussion"
    assert parsed.origin == ORIGIN_DISCUSSION
    assert len(parsed.chunks) == 1
    assert parsed.chunks[0].ordinal == 0
    assert parsed.title.startswith("讨论追问：")
    assert content == parsed.chunks[0].text.encode("utf-8")


def test_material_sha_namespaces_do_not_collide() -> None:
    assert material_sha("discussion-material", "discussion:d-1#C1") != material_sha(
        "exploration-material", "exploration:x-1#H1"
    )
    # 探索侧旧值逐字同（单参 wrapper 只是换了个调用点，输出不动）：
    assert exploration_sha("exploration:x-1#H1") == material_sha(
        "exploration-material", "exploration:x-1#H1"
    )
