"""失败分类的**单点权威表**。

为什么要有这张表（2026-10-04 挖出的文档缺口）：失败处置口径散在设计文档四处——
`02 §7` 按载体分、`02 §11.0.21` 按"对模型有没有信息量"分、`02 §11.0.2` 按后果分、
`02 §11.0.3` 按可追溯性分。加一种新错误时没有一张表可以照着填。

这张表只回答一个问题：**这次失败要不要重试、怎么重试。**
"错在哪一层"不编码在这里——那由异常类型本身承担（`ToolCallFailed` / `ModelCallFailed` /
`PacketParseError` 就是来源），再编一套 `TOOL` / `MODEL` 分类就是重复真相源。
"""

import asyncio

import pytest

from pmstudio.harness.errors import ModelCallFailed, PacketParseError, ToolCallFailed
from pmstudio.harness.model.failure import FailureKind, classify, is_transient


def kind_of(error: BaseException) -> FailureKind:
    return classify(error)


def test_supplier_5xx_is_transient():
    """实测：网关对 5xx / 限流 / 连接抖动都重试，认证与参数错误立刻失败。"""

    assert kind_of(ModelCallFailed("InternalServerError", purpose="claims")) is (
        FailureKind.TRANSIENT
    )
    assert kind_of(ModelCallFailed("Rate limit exceeded", purpose="claims")) is (
        FailureKind.TRANSIENT
    )


def test_structured_output_refusal_counts_as_transient():
    """真跑实测（2026-10-03 08:15–08:24）：供应商两分钟里连回 400，之后又全通。

    所以"拒收 json_schema"先按抖动重试；稳不稳定由网关用**次数**判断，不靠分类判断。
    """

    assert kind_of(ModelCallFailed("response_format type is unavailable", purpose="x")) is (
        FailureKind.TRANSIENT
    )


def test_quota_is_not_retried_by_backoff():
    """配额问题退避也没用——停下来换账号/换模型，不是一轮轮重试。"""

    assert kind_of(Exception("insufficient_quota")) is FailureKind.QUOTA
    assert kind_of(Exception("exceeded your current quota")) is FailureKind.QUOTA


def test_authentication_error_is_not_transient():
    assert kind_of(ModelCallFailed("AuthenticationError", purpose="x")) is (
        FailureKind.UNKNOWN
    )
    assert kind_of(ModelCallFailed("invalid_parameter", purpose="x")) is FailureKind.UNKNOWN


def test_tool_errors_are_classified_by_the_english_exception_name_inside_them():
    """本仓自造的工具错误是中文散文，但消息里裹着**英文异常名**——那才是判据。

    真实例子（`infrastructure/tools/fetch.py:140`）：`f"抓这个网址失败：{type(error).__name__}"`
    → `httpx.ConnectTimeout` 的类名落进消息，词表才认得出来。
    """

    assert kind_of(ToolCallFailed("抓这个网址失败：ConnectTimeout", tool_id="t")) is (
        FailureKind.TIMEOUT
    )


def test_pure_chinese_tool_messages_default_to_do_not_retry():
    """中文自造消息落 `UNKNOWN`（= 不重试），这是**安全的默认**。

    现状本来就是这样：`fetch.py` / `tavily.py` 的失败只留痕、不驱动重试
    （工具失败不判死整轮，见 `harness.errors.ToolCallFailed` 的类注释）。
    分类表不替它们编一个"看起来该重试"的判断。
    """

    assert kind_of(ToolCallFailed("这个网址跳转太多（超过 3 跳）", tool_id="t")) is (
        FailureKind.UNKNOWN
    )
    assert kind_of(ToolCallFailed("鉴权失败", tool_id="t")) is FailureKind.UNKNOWN



def test_packet_error_is_validation():
    assert kind_of(PacketParseError("报文没过", purpose="seat_review")) is (
        FailureKind.VALIDATION
    )


def test_cancellation_is_its_own_kind():
    """取消不是失败——它不该被重试逻辑看见就重试一遍。"""

    assert kind_of(asyncio.CancelledError()) is FailureKind.CANCEL


def test_policy_denial_never_becomes_transient():
    assert kind_of(Exception("policy denied")) is FailureKind.POLICY


def test_unknown_is_the_default_and_means_do_not_retry():
    assert kind_of(Exception("看不懂")) is FailureKind.UNKNOWN


def test_is_transient_keeps_the_gateways_existing_semantics():
    """逐条对着网关 `TRANSIENT_HINTS` 核过：语义必须一模一样，否则重试次数就变了。"""

    for transient in (
        "connection reset",
        "Read timed out",
        "Service Unavailable",
        "429 Too Many Requests",
        "500 Internal Server Error",
        "502 Bad Gateway",
        "503",
        "504 Gateway Timeout",
        "response_format type is unavailable",
    ):
        assert is_transient(transient) is True, transient
    for permanent in (
        "AuthenticationError",
        "invalid_parameter",
        "context length exceeded",
        "content policy violation",
        "insufficient_quota",
    ):
        assert is_transient(permanent) is False, permanent


#: 语料级对照：把两套口径放在同一批消息上跑，证明它们**永远一致**。
#: 为什么必须有这条：`classify()` 更细（能区分超时 / 配额 / 校验），`is_transient()` 更粗
#:（只有"抖动"与"不抖动"），而网关今天用的是粗的那一套。两套一旦漂移——比如有人往
#: `FailureKind.retryable` 里少算一类——后面接线的人会以为该重试而实际不重试，
#: 而两边各自的单元测试都还是绿的。
CORPUS = [
    "connection reset by peer",
    "Read timed out",
    "ConnectTimeout",
    "Service Unavailable",
    "429 Too Many Requests",
    "500 Internal Server Error",
    "502 Bad Gateway",
    "503",
    "504 Gateway Timeout",
    "response_format type is unavailable",
    "AuthenticationError",
    "invalid_parameter",
    "insufficient_quota",
    "exceeded your current quota",
    "context length exceeded",
    "content policy violation",
    "policy denied by provider",
    "1 validation error for SeatPacket",
    "CriminatedError",
    "这是模型的解释，不是 JSON",
    "",
]


@pytest.mark.parametrize("message", CORPUS)
def test_classify_retryable_agrees_with_is_transient(message: str):
    assert classify(Exception(message)).retryable is is_transient(message), message


@pytest.mark.parametrize("message", CORPUS)
def test_classify_is_total_and_never_raises(message: str):
    """任何消息都要有一个处置类——"分不清"落 UNKNOWN（= 不重试），不许抛。"""

    assert isinstance(classify(Exception(message)), FailureKind)


def test_only_transient_and_timeout_are_retryable():
    retryable = {kind for kind in FailureKind if kind.retryable}
    assert retryable == {FailureKind.TRANSIENT, FailureKind.TIMEOUT}
    for kind in (
        FailureKind.QUOTA,
        FailureKind.CANCEL,
        FailureKind.POLICY,
        FailureKind.VALIDATION,
        FailureKind.UNKNOWN,
    ):
        assert kind.retryable is False, kind


#: **交叉语料基线**（M4 加，2026-10-04）：一条消息**同时**含"校验词"与"抖动词"时，
#: 两套口径给出**相反**的答案。这不是 bug 记录，是**行为基线**——
#: 将来谁要动 `classify()` 的判定顺序或给 `TRANSIENT_HINTS` 增删词，这几条会红，
#: 提醒他"你正在改网关的重试行为，那是热路径"。
#:
#: 为什么原来没抓到：上面的 `CORPUS` 每条只含一类词，交叉情形一条都没有。
#: 根因在 `TRANSIENT_HINTS` 里有五个**裸数字**（429/500/502/503/504）——
#: 任何提到这些数字的消息都算抖动，包括 `maxItems: 500`、`context_length: 500`。
#: 而 `classify()` 先判校验词，`is_transient()` 只做子串匹配，于是：
#: `classify` 说不重试，`is_transient` 说重试。
#:
#: **网关今天用的是 `is_transient`**（`providers/litellm.py:145`），有真跑证据。
#: 所以这些分歧目前不影响生产行为；但它意味着"把网关换成 `classify()`"
#: **不是一次搬迁，是一次有意的行为变更**（M4b 单独立项，前置条件是先修裸数字提示词）。
CROSS_CORPUS = [
    "1 validation error: expected at most 500 items",
    "ValidationError: schema mismatch, upstream said 503",
    "invariant violated: connection reset by peer",
    "insufficient quota, 500 retry later",
    "PacketParseError: invalid schema, 502 Bad Gateway",
]


@pytest.mark.parametrize("message", CROSS_CORPUS)
def test_cross_words_are_where_the_two_disagree(message: str):
    """交叉语料上 `classify` 与 `is_transient` **不一致**——把现状钉住。

    这条断言的是**分歧本身存在**。它哪天不红了，只有两种可能：
    有人修好了其中一套（好，那是 M4b 的成果，届时删掉这条），或者有人改坏了（更糟）。
    """

    assert classify(Exception(message)).retryable is False, message
    assert is_transient(message) is True, message


def test_pure_validation_and_pure_transient_still_agree():
    """对照组：**不交叉**的时候两套口径必须一致（现有语料已覆盖，这里只钉住边界）。"""

    for message in ("1 validation error for SeatPacket", "503 Service Unavailable"):
        assert classify(Exception(message)).retryable is is_transient(message), message
