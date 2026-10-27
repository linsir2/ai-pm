"""负载探针的失败判据：写动作只接受 2xx。"""

from scripts.load_probe import _bad_http, _bad_write


def test_write_actions_only_accept_2xx() -> None:
    assert _bad_write(200, {}, "") is False
    assert _bad_write(201, {}, "") is False
    for status in (301, 400, 401, 403, 409, 422, 429, 500, 503):
        assert _bad_write(status, {}, "") is True


def test_http_face_keeps_database_error_codes_as_failures() -> None:
    assert _bad_http(200, {}, "") is False
    assert _bad_http(404, {}, "") is False
    assert _bad_http(500, {}, "") is True
    assert _bad_http(503, {"code": "database_busy"}, "") is True
