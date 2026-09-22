import inspect

from pmstudio.infrastructure.graph.graph import RoundCommand, RoundRunner, RoundState


def test_graph_state_whitelist_is_exactly_three_keys():
    """图的 state 只放引用与本次命令——真相源永远是 Round 聚合（`05` §7）。"""

    assert set(RoundState.__annotations__) == {"command", "round_id", "view"}


def test_round_command_holds_no_business_state():
    """命令只带本次动作的参数，不带聚合字段。"""

    fields = set(RoundCommand.__dataclass_fields__)
    assert fields == {
        "kind",
        "project_id",
        "round_id",
        "user_input",
        "seats",
        "claim_edits",
        "answers",
        "skipped",
        "form_id",
        "accepted",
    }


def test_every_graph_node_and_router_is_async():
    """实测：同步的节点 / 路由器在 langgraph 1.2.11 上会让图挂死（计划 §3.7）。"""

    for name in (
        "node_start",
        "node_analyze",
        "node_opening",
        "node_seat_round",
        "node_integrate",
        "node_answers",
        "node_commit",
        "node_resume",
        "route_by_command",
        "route_after_resume",
    ):
        assert inspect.iscoroutinefunction(getattr(RoundRunner, name)), name
