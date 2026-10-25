from pmstudio.api.app import create_app
from tests.support.harness import Harness

EXPECTED_PATHS = {
    "/api/health",
    # 多用户认证（Task 3）：注册/登录/登出/me + 管理端（审批/拒绝/封禁/用户列表）
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/auth/users",
    # 管理端用量（Ruling 7）：按 owner 聚合其轮次的 model_calls 行数
    "/api/auth/usage",
    # 2026-10-16 管理后台重做：每用户一行的活动读模型（`06` §2.4）。
    # 与 /auth/users、/auth/usage 并存不替代——那两个是既有窄口径契约。
    "/api/auth/stats",
    "/api/auth/approve",
    "/api/auth/reject",
    "/api/auth/ban",
    "/api/events",
    "/api/schema",
    "/api/projects",
    "/api/projects/{project_id}/workspace",
    "/api/projects/{project_id}/document",
    "/api/projects/{project_id}/export/markdown",
    "/api/projects/{project_id}/edits",
    "/api/projects/{project_id}/attachments",
    "/api/projects/{project_id}/attachments/fetch",
    "/api/projects/{project_id}/attachments/{material_id}",
    "/api/projects/{project_id}/attachments/{material_id}/reparse",
    "/api/projects/{project_id}/rounds",
    "/api/projects/{project_id}/search",
    "/api/projects/{project_id}/versions",
    "/api/projects/{project_id}/versions/{version_no}/diff",
    "/api/projects/{project_id}/versions/{version_no}/rollback",
    "/api/projects/{project_id}/evidence/{eid}",
    # M6：忘掉一条记忆（记忆抽屉上的删除按钮）——唯一一条不走确认卡的记忆写入
    "/api/projects/{project_id}/memory/{memory_id}",
    # 批 4 Task 2：最近 10 轮未命中的记忆 id（记忆抽屉「久未使用」标签读它，只读）
    "/api/projects/{project_id}/memory/usage",
    "/api/rounds/{round_id}",
    # 可观测性 v2：这一轮的外部调用轨迹（模型 + 工具，时间序）
    "/api/rounds/{round_id}/calls",
    "/api/rounds/{round_id}/opening",
    # P3a：逐条批准/拒绝联网申请（批准即执行）
    "/api/rounds/{round_id}/web-requests/{request_id}/decision",
    "/api/rounds/{round_id}/materials",
    "/api/rounds/{round_id}/answers",
    "/api/rounds/{round_id}/confirm",
    "/api/rounds/{round_id}/retry",
    "/api/rounds/{round_id}/dig",
    "/api/rounds/{round_id}/abandon",
    "/api/rounds/{round_id}/skip-seat",
    # M10 模拟调研：跑一趟（轮内动作）+ 收下勾选为材料
    "/api/rounds/{round_id}/sim-inquiry",
    "/api/rounds/{round_id}/sim-inquiry/keep",
    # M11 模拟评测：跑一趟（轮内动作，无 keep——减提案随草案确认）
    "/api/rounds/{round_id}/sim-review",
    # M8 探索：发起/列表（POST 与 GET 同路径）+ 重试/放弃 + 候选的收下/删掉
    "/api/projects/{project_id}/explorations",
    "/api/explorations/{exploration_id}/retry",
    "/api/explorations/{exploration_id}/abandon",
    "/api/explorations/{exploration_id}/candidates/{candidate_id}/keep",
    "/api/explorations/{exploration_id}/candidates/{candidate_id}/dismiss",
    # M9 讨论：发起/列表（POST 与 GET 同路径）＋追问/总结/收卡/关闭
    # M12 灵感箱：卡可收种子/收材料（keep 带 body）+ 忽略
    "/api/projects/{project_id}/discussions",
    "/api/discussions/{discussion_id}/say",
    "/api/discussions/{discussion_id}/cards",
    "/api/discussions/{discussion_id}/cards/{card_id}/keep",
    "/api/discussions/{discussion_id}/cards/{card_id}/dismiss",
    "/api/discussions/{discussion_id}/close",
    # M18 自主 panel：跑一轮 / 中途拉人 / 停掉在跑的一轮
    "/api/discussions/{discussion_id}/run-round",
    "/api/discussions/{discussion_id}/join",
    "/api/discussions/{discussion_id}/abort-run",
    # 模板库（`02` §11.0.68，切片 1）：列表+新建（同路径）+ 取全文；创建项目时选模板
    "/api/templates",
    "/api/templates/{template_id}",
}


def test_openapi_paths_are_frozen():
    """契约冻结：路径少一条、多一条都要改这条测试 + 计划 §5.2 表。"""

    harness = Harness(replies={}, seat_replies={})
    schema = create_app(harness.api_services(model_name="m")).openapi()
    assert set(schema["paths"]) == EXPECTED_PATHS
