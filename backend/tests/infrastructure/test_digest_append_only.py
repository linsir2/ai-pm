"""L4 轮次摘要只追加、不改写（`07-记忆层.md` §7.14）。

**为什么**：回头改一个已写下的摘要，等于让"前几轮发生了什么"在读过它的那些轮次之后
变样。而摘要会进后面每一轮的提示词（`$digests` 最多带最近 3 轮）——改它就是改历史。
"""

import pytest

from pmstudio.application.errors import DigestAlreadyWritten
from pmstudio.domain.memory.digest import RoundDigest
from pmstudio.infrastructure.postgres.digest_repo import SqliteDigestRepository
from tests.support.postgres import open_direct


def _digest(*, round_id: str = "r1", summary: str = "第一版摘要") -> RoundDigest:
    return RoundDigest(
        round_id=round_id,
        project_id="p1",
        summary=summary,
        sections_touched=("s1",),
        features_addressed=("order-export",),
        key_outcomes=("定了按角色过滤",),
    )


def _repo():
    return SqliteDigestRepository(open_direct())


def test_the_first_write_lands():
    repo = _repo()

    repo.save(_digest())

    loaded = repo.recent("p1")
    assert len(loaded) == 1
    assert loaded[0].summary == "第一版摘要"


def test_a_second_write_for_the_same_round_is_refused_not_silently_overwritten():
    """同一个 round 的摘要只能写一次——第二次明确报错，不静默覆盖。"""

    repo = _repo()
    repo.save(_digest())

    with pytest.raises(DigestAlreadyWritten):
        repo.save(_digest(summary="改过的摘要"))

    assert repo.recent("p1")[0].summary == "第一版摘要"      # 原文一个字节都没动


def test_a_different_round_still_gets_its_own():
    """只追加不妨碍新轮次——拦的是"同一轮写第二次"，不是"写第二条"。"""

    repo = _repo()
    repo.save(_digest())

    repo.save(_digest(round_id="r2", summary="另一轮的"))

    assert {d.round_id: d for d in repo.recent("p1")}["r1"].summary == "第一版摘要"
