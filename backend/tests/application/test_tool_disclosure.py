"""联网工具的披露（工具 P4）：视图要带上是"这个工具会外发"这个**事实**。

夹具注意：`Harness(available_tool_keys=…)` 是既有形参（`tests/support/harness.py:254`），
默认 `frozenset({"tavily_api_key"})` = 工具可用。
"""

from tests.support.harness import Harness

INPUT = "预售模式，团长配送"


async def test_opening_view_carries_network_access():
    """R：联网工具在场时，复述卡的视图要带上"会发到本机之外"。"""

    harness = Harness()
    pid = harness.seed_project(title="社区团购")
    start = harness.start_round()

    round_id = await start.create(project_id=pid, user_input=INPUT)
    view = await harness.get_round()(round_id=round_id)

    assert view.web_search_available is True
    assert view.web_search_network_access is True


async def test_view_says_false_when_search_is_unavailable():
    """R：没配检索工具时不许凭空说"会外发"（两个字段必须同真同假）。"""

    harness = Harness(available_tool_keys=frozenset())
    pid = harness.seed_project(title="社区团购")
    start = harness.start_round()

    round_id = await start.create(project_id=pid, user_input=INPUT)
    view = await harness.get_round()(round_id=round_id)

    assert view.web_search_available is False
    assert view.web_search_network_access is False


async def test_workspace_round_carries_network_access():
    """R：初次加载/重连时复述卡走 workspace 那条路，同一个事实也得跟着到。"""

    harness = Harness()
    pid = harness.seed_project(title="社区团购")
    start = harness.start_round()

    round_id = await start.create(project_id=pid, user_input=INPUT)
    harness.index.set_latest_round(str(pid), round_id)  # 真实现由 SQL 查最新一轮
    workspace = await harness.get_workspace()(project_id=pid)

    assert workspace.round is not None
    assert workspace.round.web_search_available is True
    assert workspace.round.web_search_network_access is True


async def test_workspace_round_says_false_when_search_is_unavailable():
    """R：workspace 这条路同样不许凭空说"会外发"（两个字段同真同假）。"""

    harness = Harness(available_tool_keys=frozenset())
    pid = harness.seed_project(title="社区团购")
    start = harness.start_round()

    round_id = await start.create(project_id=pid, user_input=INPUT)
    harness.index.set_latest_round(str(pid), round_id)  # 真实现由 SQL 查最新一轮
    workspace = await harness.get_workspace()(project_id=pid)

    assert workspace.round is not None
    assert workspace.round.web_search_available is False
    assert workspace.round.web_search_network_access is False
