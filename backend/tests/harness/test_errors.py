"""运行时错误搬进 harness 后的形状。

**每个 `code` 字符串一个字都不许改**：`api/errors.py` 的 CODE_STATUS 表按字符串映射 HTTP 状态码，
改字符串等于静默改 HTTP 语义（`unknown_*` 是 404、`invariant:*` 是 409…）。
"""

import pytest

from pmstudio.harness.errors import (
    ApplicationError,
    FetchRefused,
    ModelCallFailed,
    PacketParseError,
    ToolCallFailed,
)


def code_of(error: BaseException) -> str:
    return str(error.code)


def test_base_error_defaults_to_application_error():
    assert code_of(ApplicationError("boom")) == "application_error"


def test_packet_parse_error_keeps_its_code_and_purpose():
    error = PacketParseError("报文没过", purpose="seat_review", offset=12)
    assert code_of(error) == "model_packet_unparsable"
    assert error.purpose == "seat_review"
    assert error.details["offset"] == 12


def test_model_call_failed_hint_is_only_for_what_the_model_can_fix():
    """`hint` 只给空回包 / 非 JSON 这两种模型自己能修的；供应商错误不给。"""

    assert ModelCallFailed("空的", purpose="claims", hint="再给一次").hint == "再给一次"
    assert ModelCallFailed("502", purpose="claims").hint is None


def test_tool_call_failed_keeps_tool_id_and_status():
    error = ToolCallFailed("抓取超时", tool_id="tool.web_fetch", status=504)
    assert code_of(error) == "tool_call_failed"
    assert error.tool_id == "tool.web_fetch"
    assert error.status == 504


def test_fetch_refused_is_422_not_a_tool_failure():
    """`FetchRefused` 与 `ToolCallFailed` 分开：前者是"你给的东西不行"，后者是"抓的时候出问题"。

    注意一个**搬移前就存在**的不对称：`ToolCallFailed` 把 `tool_id` 挂成属性，
    `FetchRefused` 只放进 `details`。这里逐字保留原行为，不趁搬家改语义。
    """

    error = FetchRefused("内网地址")
    assert code_of(error) == "fetch_refused"
    assert error.details["tool_id"] == "tool.web_fetch"
    assert not hasattr(error, "tool_id")


@pytest.mark.parametrize(
    "error",
    [
        ApplicationError("x"),
        PacketParseError("x", purpose="p"),
        ToolCallFailed("x", tool_id="t"),
        ModelCallFailed("x", purpose="p"),
    ],
)
def test_all_runtime_errors_share_one_base(error: BaseException):
    assert isinstance(error, ApplicationError)
