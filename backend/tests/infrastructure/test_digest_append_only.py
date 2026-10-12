"""L4 轮次摘要只追加、不改写（`07-记忆层.md` §7.14）。

**为什么**：回头改一个已写下的摘要，等于让"前几轮发生了什么"在读过它的那些轮次之后
变样。而摘要会进后面每一轮的提示词（`$digests` 最多带最近 3 轮）——改它就是改历史。
供应商侧还有一层代价：前缀匹配的缓存命中的是"上一次写下的字节"，改写即作废
（Manus：`even a single-token difference can invalidates the cache from that token onward`）。

**现状**：`SqliteDigestRepository.save` 用的是 `ON CONFLICT(id) DO UPDATE`——契约上
允许改写。真跑目前不可达（`ProposalCommitter` 有 `waiting_on` 门禁，同一轮 confirm 第二次
会被拒），所以这是**潜在隐患**不是活 bug：哪天加一条"重跑摘要"的路，历史就悄悄变了。
"""

import pytest

from pmstudio.application.errors import DigestAlreadyWritten
from pmstudio.domain.memory.digest import RoundDigest
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.digest_repo import SqliteDigestRepository


def _digest(*, round_id: str = "r1", summary: str = "第一版摘要") -> RoundDigest:
    return RoundDigest(
        round_id=round_id,
        project_id="p1",
        summary=summary,
        sections_touched=("s1",),
        features_addressed=("order-export",),
        key_outcomes=("定了按角色过滤",),
    )


def _repo(tmp_path):
    connection = connect(tmp_path / "pmstudio.db")
    return SqliteDigestRepository(connection)


def test_the_first_write_lands(tmp_path):
    repo = _repo(tmp_path)

    repo.save(_digest())

    assert repo.get("r1").summary == "第一版摘要"


def test_a_second_write_for_the_same_round_is_refused_not_silently_overwritten(tmp_path):
    """同一个 round 的摘要只能写一次——第二次明确报错，不静默覆盖。"""

    repo = _repo(tmp_path)
    repo.save(_digest())

    with pytest.raises(DigestAlreadyWritten):
        repo.save(_digest(summary="改过的摘要"))

    assert repo.get("r1").summary == "第一版摘要"      # 原文一个字节都没动


def test_a_different_round_still_gets_its_own(tmp_path):
    """只追加不妨碍新轮次——拦的是"同一轮写第二次"，不是"写第二条"。"""

    repo = _repo(tmp_path)
    repo.save(_digest())

    repo.save(_digest(round_id="r2", summary="另一轮的"))

    assert repo.get("r1").summary == "第一版摘要"
