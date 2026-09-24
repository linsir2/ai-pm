from pmstudio.api.app import create_app
from tests.support.harness import Harness

EXPECTED_PATHS = {
    "/api/health",
    "/api/events",
    "/api/projects",
    "/api/projects/{project_id}/workspace",
    "/api/projects/{project_id}/document",
    "/api/projects/{project_id}/edits",
    "/api/projects/{project_id}/rounds",
    "/api/rounds/{round_id}",
    "/api/rounds/{round_id}/opening",
    "/api/rounds/{round_id}/answers",
    "/api/rounds/{round_id}/confirm",
    "/api/rounds/{round_id}/retry",
}


def test_openapi_paths_are_frozen():
    """契约冻结：路径少一条、多一条都要改这条测试 + 计划 §5.2 表。"""

    harness = Harness(replies={}, seat_replies={})
    schema = create_app(harness.api_services(model_name="m")).openapi()
    assert set(schema["paths"]) == EXPECTED_PATHS
