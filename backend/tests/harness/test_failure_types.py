"""重试判据**按 litellm 的真实异常类型**判，而不是在消息里找裸数字（2026-10-04 · M4b）。

## 为什么要改（根因，不是"忘了接线"）

`TRANSIENT_HINTS` 里有一项叫 `"internalservererror"`（无空格）——**这说明旧的口径
早就在匹配异常类名了**，因为网关传进来的是 `f"{type(error).__name__} {error}"`。
也就是说类型名本来就在被匹配的字符串里，只是被当成**匿名子串**匹配。

子串匹配在**双向**都不可靠（下面三条都是实测出来的，不是推测）：

- **假阴性**：`ServiceUnavailableError: upstream overloaded` —— 真的 503，
  但消息里没有匹配的短语 → **不重试，直接判死**。
- **假阳性**：`JSONSchemaValidationError: schema mismatch, expected 500 items` ——
  裸 `500` 命中 → 白白重试 3 次（约 4 秒 + 3 行留样）。
- **假阳性**：`BadRequestError: InvalidParameter: maxItems 500 exceeds limit` —— 同上。

而 litellm **早就是把 HTTP 状态码归一化成了异常类型**（见下面那条对齐守卫）。

## 为什么用真实 litellm 异常而不是同名替身

先前的版本按"类名 + 沿 MRO 匹配"，所以测试能用本地替身类。改成 `isinstance` 之后
替身不再命中——这正是我们要的：**测试必须跑在真的 SDK 类型上**，否则"表和 SDK 脱节"
这件事只有在生产里才会暴露。

代价是这些异常都要 2~4 个关键字参数才造得出实例（`message` / `model` / `llm_provider`），
所以下面用 `_err()` 收一下。造不出实例的那几个（`BudgetExceededError`、
`JSONSchemaValidationError`）单独处理。
"""

import asyncio
import inspect

import httpx
import pytest
from litellm import exceptions as litellm_exceptions

from pmstudio.harness.model.failure import (
    STRUCTURED_OUTPUT_REFUSAL_HINT,
    FailureKind,
    classify,
    classify_litellm_type,
)

_CTX = {"model": "qwen3.8-flash", "llm_provider": "dashscope"}


def _err(cls: type[BaseException], message: str) -> BaseException:
    """造一个**真的** litellm 异常——签名大多收 `message` + 那两个固定参数。"""

    return cls(message=message, **_CTX)


# ── 修好的三条：每一条都对着一个真实缺陷 ──────────────────────────────────────


def test_a_real_503_with_an_unmatched_message_is_retried():
    """**假阴性修复**：真的 503 不能因为消息 phrasing 不匹配就判死。

    旧口径在消息里找不到 "service unavailable"（类名里是 `ServiceUnavailableError`，
    没空格），于是当成不可重试 → 一次供应商抖动 = 整轮失败。
    """

    error = _err(litellm_exceptions.ServiceUnavailableError, "upstream overloaded")

    assert classify(error).retryable is True


def test_a_bare_500_inside_a_schema_message_is_not_a_reason_to_retry():
    """**假阳性修复**：消息里出现 `500`（条数上限）不等于服务端 500。

    这条确定性失败重试 3 次 = 白等约 4 秒 + 3 行无用留样。
    """

    error = litellm_exceptions.JSONSchemaValidationError(
        model="qwen3.8-flash",
        llm_provider="dashscope",
        raw_response="...",
        schema="...maxItems: 500...",
    )

    assert classify(error).retryable is False


def test_a_parameter_limit_named_500_is_not_a_reason_to_retry():
    """**假阳性修复**：`maxItems 500` 这类参数报错同理（真实的参数写错闸门）。"""

    error = _err(litellm_exceptions.BadRequestError, "InvalidParameter: maxItems 500 exceeds limit")

    assert classify(error).retryable is False


# ── 不许被上面三条带偏的那些 ──────────────────────────────────────────────────


def test_an_untyped_exception_still_falls_back_to_the_message():
    """**认不出类型时仍然认消息**——我们自己抛的异常、或没归一化的供应商错误。

    这条是防"过度矫正"：类型表不是要取代消息判据，是要在有类型时更准。
    """

    assert classify(RuntimeError("503")).retryable is True
    assert classify(RuntimeError("connection reset by peer")).retryable is True


def test_quota_beats_rate_limit_because_backoff_cannot_fix_a_billing_problem():
    """额度用完常被供应商当 429 限流发下来，但退避解决不了它 → 不重试。

    两条路径都要挡住：类型是 `BudgetExceededError` 的，以及消息里带 `quota` 的
    （后者即使类型是 `RateLimitError`）。
    """

    budget = litellm_exceptions.BudgetExceededError(max_budget=0.0, current_cost=1.0)
    assert classify(budget).retryable is False
    assert classify(
        _err(litellm_exceptions.RateLimitError, "You exceeded your current quota")
    ).retryable is False


def test_a_plain_rate_limit_is_still_retried():
    error = _err(litellm_exceptions.RateLimitError, "429 Too Many Requests")

    assert classify(error).retryable is True


def test_structured_output_refusal_stays_retryable_despite_being_a_bad_request():
    """供应商**间歇性**拒收 `json_schema`（qwen 实测抖过两分钟）。

    这条必须在类型表**之前**生效：`BadRequestError` 是校验类，但它带着
    "response_format type is unavailable" 时那次抖动是真跑证据（见 `test_llm.py`）。
    """

    error = _err(litellm_exceptions.BadRequestError, STRUCTURED_OUTPUT_REFUSAL_HINT)

    assert classify(error).retryable is True


def test_a_real_parameter_mismatch_is_not_retryable():
    """反面对照：**别把 `BadRequestError` 整个当成可重试**。

    闸门靠这条把"换模型忘了改参数"当场拦下来。
    """

    error = _err(litellm_exceptions.BadRequestError, "InvalidParameter: field enable_thinking")

    assert classify(error).retryable is False


def test_subclasses_inherit_their_base_class_verdict():
    """`isinstance` 天然覆盖子类，所以表里**不必**列每个子类。

    `MidStreamFallbackError` 是 litellm 自己的 `ServiceUnavailableError` 子类 → 抖动；
    `ContextWindowExceededError` 是 `BadRequestError` 子类 → 校验（不是抖动，尽管它叫 Error）。
    """

    mid_stream = litellm_exceptions.MidStreamFallbackError(message="x", **_CTX)
    assert classify(mid_stream).retryable is True
    assert classify(
        _err(litellm_exceptions.ContextWindowExceededError, "maximum context length is 8192 tokens")
    ) is FailureKind.VALIDATION


def test_authentication_and_permission_failures_never_retry():
    bad_key = _err(litellm_exceptions.AuthenticationError, "invalid api key")
    assert classify(bad_key).retryable is False
    # `PermissionDeniedError` 的 openai 基类会去读 `response.request`，
    # 所以这里得给一个真的响应，不能拿 `None` 糊弄。
    denied = litellm_exceptions.PermissionDeniedError(
        message="forbidden",
        response=httpx.Response(403, request=httpx.Request("POST", "https://example.test")),
        **_CTX,
    )
    assert classify(denied).retryable is False


def test_a_content_policy_violation_is_policy_not_validation():
    """它是 `BadRequestError` 的子类，靠**阶梯顺序**拿到 POLICY 而不是 VALIDATION。

    两者都不重试，所以行为上等价；但归错类会让"为什么这次没重试"答不上来。
    """

    error = _err(litellm_exceptions.ContentPolicyViolationError, "content policy violation")

    assert classify(error) is FailureKind.POLICY


def test_a_timeout_is_its_own_kind():
    assert classify(_err(litellm_exceptions.Timeout, "Request timed out")) is FailureKind.TIMEOUT


def test_a_cancelled_call_is_not_a_failure():
    """取消不是失败——它得原样往上抛，不许被当成"抖动"重试三次。"""

    assert classify(asyncio.CancelledError()) is FailureKind.CANCEL
    assert classify(asyncio.CancelledError()).retryable is False


# ── 与真实 SDK 的对齐守卫 ────────────────────────────────────────────────────

#: 故意**不进**表的三个，各有理由。litellm 新增异常类型时这个集合必须跟着长，
#: 否则 `test_every_litellm_exception_is_classified` 会红——那是提醒，不是障碍。
UNCLASSIFIED: dict[str, str] = {
    "MockException": "litellm 自测用的替身，生产路径不会抛",
    "ModifyResponseException": "litellm 改写响应时的内部异常，本项目没挂 response hooks，走不到",
    "OpenAIError": "openai 的根基类，也是认证类的祖先；收进任何一张表都会一锅端",
}

_EXPECTED = {
    litellm_exceptions.APIConnectionError: FailureKind.TRANSIENT,
    litellm_exceptions.RateLimitError: FailureKind.TRANSIENT,
    litellm_exceptions.ServiceUnavailableError: FailureKind.TRANSIENT,
    litellm_exceptions.BadGatewayError: FailureKind.TRANSIENT,
    litellm_exceptions.InternalServerError: FailureKind.TRANSIENT,
    litellm_exceptions.Timeout: FailureKind.TIMEOUT,
    litellm_exceptions.AuthenticationError: FailureKind.POLICY,
    litellm_exceptions.PermissionDeniedError: FailureKind.POLICY,
    litellm_exceptions.ContentPolicyViolationError: FailureKind.POLICY,
    litellm_exceptions.BadRequestError: FailureKind.VALIDATION,
    litellm_exceptions.JSONSchemaValidationError: FailureKind.VALIDATION,
    litellm_exceptions.ContextWindowExceededError: FailureKind.VALIDATION,
    litellm_exceptions.BudgetExceededError: FailureKind.QUOTA,
}


@pytest.mark.parametrize(
    ("cls", "expected"),
    list(_EXPECTED.items()),
    ids=lambda v: getattr(v, "__name__", ""),
)
def test_the_table_says_what_we_think_it_says(cls: type[BaseException], expected: FailureKind):
    assert classify_litellm_type(cls) is expected


def test_every_litellm_exception_is_classified():
    """**表不许与真实 SDK 漂移**：litellm 每个异常类都要有归宿（或有明确的排除理由）。

    为什么这条必须钉死：漏掉一个类型，它就会掉进消息兜底——而消息兜底认裸数字，
    于是"确定性失败"又被当成抖动重试。这正是本轮要修的那类缺陷换个马甲再来一次。

    绑定 litellm 之后漂移已经很难发生（改名会导入期报错），但**新增**一个类仍然会
    静默漏进来，所以这条守卫还得留着。
    """

    classes = {
        name: obj
        for name, obj in vars(litellm_exceptions).items()
        if inspect.isclass(obj) and issubclass(obj, BaseException)
    }

    orphans = sorted(
        name
        for name, cls in classes.items()
        if name not in UNCLASSIFIED and classify_litellm_type(cls) is None
    )
    assert orphans == [], f"这些 litellm 异常没有进任何一张表：{orphans}"

    # 反向：UNCLASSIFIED 里不许有 litellm 已经不存在的类名（否则这条守卫自己腐烂）
    stale = sorted(set(UNCLASSIFIED) - set(classes))
    assert stale == [], f"UNCLASSIFIED 里这些类 litellm 已经没有了，请删掉理由：{stale}"
