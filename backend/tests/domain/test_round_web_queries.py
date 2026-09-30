"""轮次记住"这一轮提名了哪些检索词、用户勾了哪几条"（工具层 C-2b，P17b §5）。

两条口径：
1. 提名是**分析者的判断**（带一句为什么），勾选是**用户的拍板**——两者都要能回放；
2. 老轮次没有这两把键 → 读回来是空的，不编。
"""

import pytest

from pmstudio.domain.round.round import Round
from pmstudio.domain.round.web_query import WebQuery
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.infrastructure.sqlite.serialization import round_from_payload, round_to_payload
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_claim


def round_at_the_opening() -> Round:
    """一轮：主张已落，正停在复述卡上等人拍板。"""

    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="想做个订单导出的功能", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    return round_


def nominated(*queries: WebQuery) -> Round:
    round_ = round_at_the_opening()
    round_.attach_web_queries(list(queries))
    return round_


def restored(round_: Round) -> Round:
    return round_from_payload(
        round_to_payload(round_),
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )


def test_a_round_without_nominations_has_none():
    """没提名（常态）就是空，不是缺字段。"""

    round_ = round_at_the_opening()

    assert round_.web_queries == ()
    assert round_.web_queries_chosen == ()


def test_nominated_queries_are_kept_in_order_and_deduped():
    round_ = nominated(
        WebQuery(query="RBAC 权限模型", why="要查行业通行做法"),
        WebQuery(query="RBAC 权限模型", why="重复提了一次"),
        WebQuery(query="订单导出的行业规范", why="要查行业标准"),
    )

    assert [item.query for item in round_.web_queries] == [
        "RBAC 权限模型",
        "订单导出的行业规范",
    ]
    assert round_.web_queries[0].why == "要查行业通行做法"


def test_a_query_needs_a_query_and_a_reason():
    with pytest.raises(ShapeError):
        WebQuery(query="  ", why="要查行业标准")
    with pytest.raises(ShapeError):
        WebQuery(query="RBAC 权限模型", why="   ")


def test_the_nomination_cap_is_enforced_in_the_domain_too():
    """解析层已经丢过超出的那几条；这里拦住"手搓一个超限名单"的调用方。"""

    with pytest.raises(ShapeError):
        nominated(
            *[WebQuery(query=f"第{index}条", why="要查外部事实") for index in range(1, 5)]
        )


def test_submit_opening_records_what_the_user_checked():
    """勾选按用户给的顺序记；重复与空白去掉（前端可能会手滑）。"""

    round_ = nominated(
        WebQuery(query="RBAC 权限模型", why="要查行业通行做法"),
        WebQuery(query="订单导出的行业规范", why="要查行业标准"),
    )

    round_.submit_opening(
        seats=(SEAT,), web_queries_chosen=("RBAC 权限模型", "  ", "RBAC 权限模型")
    )

    assert round_.web_queries_chosen == ("RBAC 权限模型",)
    assert round_.seats == (SEAT,)


def test_payload_roundtrip_keeps_both_and_old_rows_fall_back():
    round_ = nominated(
        WebQuery(query="RBAC 权限模型", why="要查行业通行做法"),
        WebQuery(query="订单导出的行业规范", why="要查行业标准"),
    )
    round_.submit_opening(seats=(SEAT,), web_queries_chosen=("订单导出的行业规范",))

    again = restored(round_)

    assert [(item.query, item.why) for item in again.web_queries] == [
        ("RBAC 权限模型", "要查行业通行做法"),
        ("订单导出的行业规范", "要查行业标准"),
    ]
    assert again.web_queries_chosen == ("订单导出的行业规范",)

    old = round_to_payload(round_)
    del old["web_queries"]           # 老行：没有这两把键
    del old["web_queries_chosen"]
    back = round_from_payload(
        old,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )

    assert back.web_queries == ()
    assert back.web_queries_chosen == ()
