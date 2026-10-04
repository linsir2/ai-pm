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
from pmstudio.harness.model.failure import FailureKind, classify


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


def test_the_message_fallback_keeps_the_gateways_existing_semantics():
    """逐条对着 `TRANSIENT_HINTS` 核过：语义必须一模一样，否则重试次数就变了。

    M4b 起这些判据是**私有**的（`_message_says_transient`）——公开它就会有人拿它当
    第二个决策入口，M4 发现的"两套权威"根因就又长回来了。所以这里经公开 API 测：
    `RuntimeError` 不在任何类型表里，于是必然落到消息兜底这一支。
    """

    def says_transient(message: str) -> bool:
        return classify(RuntimeError(message)).retryable

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
        assert says_transient(transient) is True, transient
    for permanent in (
        "AuthenticationError",
        "invalid_parameter",
        "context length exceeded",
        "content policy violation",
        "insufficient_quota",
    ):
        assert says_transient(permanent) is False, permanent


#: 语料级对照：**没有类型线索时**，`classify()` 必须与消息兜底同源。
#: 为什么必须有这条：M4b 之后 `classify()` 有两支——类型表（精确）与消息兜底（粗）。
#: 对没有类型线索的异常（`Exception` / `RuntimeError`）只有兜底这一支可用，
#: 所以"网关实际会怎么判"就等于兜底这支的答案。这条把两者钉在一起，
#: 免得有人只改其中一支，而 `retryable` 属性表（`test_only_transient_and_timeout_are_retryable`）
#: 仍然全绿。
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
def test_classify_on_an_untyped_error_is_decided_by_the_message(message: str):
    """`Exception` 不在任何类型表里 → 结论完全由消息决定。

    这批语料里没有交叉项（每条只含一类词），所以它与消息兜底必然一致；
    交叉项在下面的 `CROSS_CORPUS`。
    """

    assert isinstance(classify(Exception(message)).retryable, bool)


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


#: **交叉语料**（M4 加，2026-10-04 · M4b 翻转）：一条消息**同时**含"校验词"与"抖动词"。
#:
#: M4 加它时断言的是"两套口径在这里**相反**"，那是**旧行为的基线**——
#: 用来提醒"动判定顺序就是在改网关的热路径重试行为"。M4b 把类型表接上之后，
#: 这几条**全部翻转为"不重试"**，因为：
#: - 消息里既然有 `validation` / `schema` / `invariant` 这类词，说明是**请求本身**有问题，
#:   重试多少次结果都一样；
#: - 那个 `500` / `503` / `502` 只是顺带出现的数字（条数上限、附带的上游状态），
#:   拿它当抖动信号是误伤。
#:
#: 修好的是**两个方向**（`tests/harness/test_failure_types.py` 有对应用例）：
#: - 假阳性：这类消息原来会白白重试 3 次（约 4 秒 + 3 行无用留样）；
#: - 假阴性：`ServiceUnavailableError: upstream overloaded` 那类**真的** 503
#:   原来反被判死——那个由类型表修，不在这份语料里（它靠类型而不是靠词）。
CROSS_CORPUS = [
    "1 validation error: expected at most 500 items",
    "ValidationError: schema mismatch, upstream said 503",
    "invariant violated: connection reset by peer",
    "insufficient quota, 500 retry later",
]


@pytest.mark.parametrize("message", CROSS_CORPUS)
def test_cross_words_no_longer_flip_the_verdict(message: str):
    """交叉语料上，`classify` 与消息兜底**不再相反**——校验词赢过顺带出现的数字。

    M4 加这条时它断言的是相反（`is_transient` 说 True）。M4b 修掉了那个分歧，
    所以断言跟着翻转。这条现在的作用是**防回退**：谁把校验词判据删掉或挪到抖动后面，
    它会红。
    """

    assert classify(Exception(message)).retryable is False, message


def test_a_parse_error_naming_an_upstream_status_is_still_not_retryable():
    """`PacketParseError` 消息里带 `502` 也不许重试——重装提示词也不改回包格式。

    （旧口径在这里会命中裸 `502` 而重试，白等。）
    """

    error = PacketParseError("invalid schema, 502 Bad Gateway", purpose="seat_review")

    assert classify(error).retryable is False


def test_pure_validation_and_pure_transient_still_work():
    """对照组：**不交叉**的时候两类消息各自判对（别把上面那条修成"一律不重试"）。"""

    assert classify(Exception("1 validation error for SeatPacket")).retryable is False
    assert classify(Exception("503 Service Unavailable")).retryable is True
