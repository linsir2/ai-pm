"""失败分类的**单点权威表**。

为什么要有这张表（2026-10-04 挖出的文档缺口）：失败处置口径散在设计文档四处——
`02-编排与数据.md §7` 按载体分、`§11.0.21` 按"对模型有没有信息量"分、
`§11.0.2` 按后果分、`§11.0.3` 按可追溯性分。加一种新错误时没有一张表可以照着填，
于是每处各写各的 `if`。

**这张表只回答一件事**：这次失败要不要重试、怎么重试。

"错在哪一层"**不**编码在这里——那由异常类型本身承担（`ToolCallFailed` / `FetchRefused` /
`ModelCallFailed` / `PacketParseError` 就是来源），再编一套 `TOOL` / `MODEL` 分类
就是重复真相源。所以分类刻意只有"处置"这一维。

`TRANSIENT_HINTS` 是**消息兜底**那一半的口径（裸数字故意留着：认不出类型的供应商错误
只能靠它）。另一半是按异常类名判的类型表（见下面 `_TRANSIENT_TYPES` 等）——
`classify()` 是全仓唯一的重试判据，M4b 起网关直接调它。
"""

import asyncio
from enum import StrEnum

from litellm import exceptions as litellm_exceptions

from pmstudio.harness.errors import PacketParseError

#: 只重试"明显是抖动"的错误：连接失败、超时、限流、5xx。认证/参数类错误立刻失败。
TRANSIENT_HINTS: tuple[str, ...] = (
    "connection",
    "timeout",
    "timed out",
    "internalservererror",
    "service unavailable",
    "server error",
    "rate limit",
    "429",
    "500",
    "502",
    "503",
    "504",
    # 供应商间歇性拒收结构化输出（2026-10-03 08:15–08:24 实测：两分钟窗口里连回 400，
    # 08:17 又全通、08:20 连打 8/8 过；08:24 打到一次真实调用 → 整轮失败）。
    # **只认它自己这句话**——`invalid_parameter` 这个大类**不放宽**：
    # 参数真的写错（如 glm 那条 `enable_thinking` 互斥）必须立刻失败，
    # 闸门正是靠那个把"换模型忘了改参数"当场拦下来的。
    "response_format type is unavailable",
)

#: 配额类：退避也不解决问题，应该停下来而不是一轮轮重试。
QUOTA_HINTS: tuple[str, ...] = ("quota", "insufficient", "billing", "payment required")

#: 供应商自己那句"这个 response_format 类型**现在**不可用"。
#: 同一个字符串在 `TRANSIENT_HINTS` 里也有一份，两处用途不同、**都要留**：
#: 那边管"短暂拒收 → 重试"（qwen 实测抖过两分钟），这边管"**稳定**拒收 → 换写法"
#: （`deepseek-v4.1-flash` 实测连续 400）。稳不稳由网关用**次数**判断，不由分类判断。
STRUCTURED_OUTPUT_REFUSAL_HINT = "response_format type is unavailable"


class FailureKind(StrEnum):
    """处置类。`UNKNOWN` 的含义是"不重试"——不是"不知道怎么办"。"""

    TRANSIENT = "transient"
    QUOTA = "quota"
    TIMEOUT = "timeout"
    CANCEL = "cancel"
    POLICY = "policy"
    VALIDATION = "validation"
    UNKNOWN = "unknown"

    @property
    def retryable(self) -> bool:
        """该不该退避后重试。

        **这张表与消息兜底不是两套权威**——`classify()` 先问类型，问不到才问消息，
        所以对同一个异常只有一个结论。

        M4b 之前确实有过两套权威（`is_transient` 与本属性表），它们在交叉语料上相反：
        消息既像校验错又像抖动时，`is_transient` 认裸数字说重试、`classify` 认校验词
        说不重试。实测 4 条，分歧被类型表消掉了（`tests/harness/test_failure.py`
        的 `CROSS_CORPUS` 现在断言的是"不再相反"，防回退）。

        要再动判定顺序，先看 `classify()` 那份顺序说明——每一步的位置都有理由，
        挪一步就是改网关的热路径重试行为。
        """

        return self in _RETRYABLE


_RETRYABLE: frozenset[FailureKind] = frozenset({FailureKind.TRANSIENT, FailureKind.TIMEOUT})


#: ── 按异常**类型**判的那一半（M4b 新增）──────────────────────────────────────
#:
#: **为什么要有这张表**：`TRANSIENT_HINTS` 里有一项叫 `"internalservererror"`（无空格）——
#: 这说明旧口径早就在匹配异常类名了，因为网关传进来的是
#: `f"{type(error).__name__} {error}"`。也就是说类型名本来就在被匹配的字符串里，
#: 只是被当成**匿名子串**匹配。子串匹配双向都不可靠：
#: - 假阴性：`ServiceUnavailableError: upstream overloaded` —— 真的 503，判死了；
#: - 假阳性：`...schema mismatch, expected 500 items` —— 裸 `500` 命中，白重试 3 次。
#:
#: **为什么直接 `isinstance` 真实的 litellm 类型**（2026-10-04 定稿，先前是按类名 + MRO 匹配）：
#: 按类名那张表是**手抄**的——litellm 改个类名，它就静默失效，退回消息兜底，
#: 于是本轮要修的"确定性失败被当成抖动"换个马甲再来一次，而测试要等到有人想起来
#: 同步才红。绑上 litellm 之后，**改名变成导入期就报错**，漂移无处可藏。
#: 代价是本模块从"SDK 中立的通用件"变成"litellm 的分类表"——本项目只有一个供应商，
#: 这个代价划算（真要换 provider，`providers/` 换实现 + 这里换表，两处而己）。
#:
#: **表里为什么也有 openai 的基类**：litellm 的异常继承 openai 的
#: （`Timeout` ← `APITimeoutError`，`BadRequestError` ← `BadRequestError` ← `APIStatusError`
#: ← `APIError`），只列 litellm 自己那层会漏掉 MRO 中间那截。
#:
#: **不需要列子类**：`isinstance` 天然覆盖。litellm 有一堆子类
#: （`MidStreamFallbackError(ServiceUnavailableError)`、`ImageFetchError(BadRequestError)`、
#: `LiteLLMUnknownProvider(BadRequestError)`），它们自动跟着基类走。

_QUOTA_TYPES: tuple[type[BaseException], ...] = (
    litellm_exceptions.BudgetExceededError,
)

_TIMEOUT_TYPES: tuple[type[BaseException], ...] = (
    # 只列 litellm 自己的 `Timeout`（它继承 openai 的 `APITimeoutError`）。
    # 不另外 import openai：网关只可能拿到 litellm 包出来的异常——httpx 的超时也由
    # litellm 包成 `Timeout`。真出现裸 openai 异常时，下面消息兜底那支会接住。
    litellm_exceptions.Timeout,
)

#: 策略类：鉴权 / 权限 / 内容被拦。**先于校验判**——"被拒绝"比"请求有问题"更具体。
#: 注意 `ContentPolicyViolationError` 是 `BadRequestError` 的子类，靠顺序拿到 POLICY。
_POLICY_TYPES: tuple[type[BaseException], ...] = (
    litellm_exceptions.AuthenticationError,
    litellm_exceptions.PermissionDeniedError,
    litellm_exceptions.ContentPolicyViolationError,
    litellm_exceptions.GuardrailRaisedException,
    litellm_exceptions.BlockedPiiEntityError,
    litellm_exceptions.SensitiveDataRouteException,
)

#: 校验类：请求本身不合法，重试多少次结果都一样。
#: **必须先于 `_TRANSIENT_TYPES` 判**——litellm 的 `BadRequestError` 沿 MRO 带着
#: `APIError`，而 `APIError` 在抖动表里（它是 openai 的通用 5xx 基类，收进表里是为了
#: 兜住"供应商抛了个没归一化的错误"）。顺序反了，`BadRequestError` 就会被当成抖动。
_VALIDATION_TYPES: tuple[type[BaseException], ...] = (
    litellm_exceptions.BadRequestError,
    litellm_exceptions.UnprocessableEntityError,
    litellm_exceptions.NotFoundError,
    litellm_exceptions.APIResponseValidationError,
    litellm_exceptions.JSONSchemaValidationError,
    litellm_exceptions.ContextWindowExceededError,
    litellm_exceptions.RejectedRequestError,
    litellm_exceptions.UnsupportedParamsError,
    litellm_exceptions.InvalidRequestError,
)

#: 抖动类：连接挂了、超了、限流了、服务端 5xx。
_TRANSIENT_TYPES: tuple[type[BaseException], ...] = (
    litellm_exceptions.APIConnectionError,
    litellm_exceptions.APIError,  # openai 通用 5xx 基类：兜住没归一化的供应商错误
    litellm_exceptions.RateLimitError,
    litellm_exceptions.ServiceUnavailableError,
    litellm_exceptions.BadGatewayError,
    litellm_exceptions.InternalServerError,
)

#: 阶梯。**顺序即优先级，不可随意挪动**——每一步为什么在这个位置见 `classify()`。
_LITELLM_LADDER: tuple[tuple[tuple[type[BaseException], ...], FailureKind], ...] = (
    (_QUOTA_TYPES, FailureKind.QUOTA),
    (_TIMEOUT_TYPES, FailureKind.TIMEOUT),
    (_POLICY_TYPES, FailureKind.POLICY),
    (_VALIDATION_TYPES, FailureKind.VALIDATION),
    (_TRANSIENT_TYPES, FailureKind.TRANSIENT),
)


def classify_litellm_type(cls: type[BaseException]) -> FailureKind | None:
    """litellm 的异常**类** → 处置类。认不出返回 `None`（交给消息判据）。

    收**类**而不是实例，是为了那条对齐守卫不必造实例——litellm 的异常大多要
    2~4 个关键字参数（`message`/`model`/`llm_provider`…），而"这张表有没有覆盖
    真实 SDK"这个问题只需要类。
    """

    for types, kind in _LITELLM_LADDER:
        if issubclass(cls, types):
            return kind
    return None


def _message_says_transient(text: str) -> bool:
    """这段错误消息值不值得退避后重试。**兜底零件，不是决策入口**（M4b 起私有）。

    它降为私有是有意的：公开它就会有人拿它当第二个判据，于是 M4 发现的那个根因
    （"两套权威做同一个决定"）又长回来。决策入口只有 `classify()` 一个。

    裸数字（429/500/502/503/504）**故意留着**：类型表认不出的供应商错误（没归一化的）
    仍然只能靠消息判断，而这时数字是唯一的线索。有类型线索时它们不参与判断——
    那些数字会被 `maxItems: 500` 之类的无关内容误伤。
    """

    lowered = text.lower()
    return any(hint in lowered for hint in TRANSIENT_HINTS)


def is_structured_output_refusal(text: str) -> bool:
    """供应商**稳定**拒收 `json_schema` → 该换写法（`04-未决.md` M2b 已拍板）。"""

    return STRUCTURED_OUTPUT_REFUSAL_HINT in text.lower()


def classify(error: BaseException) -> FailureKind:
    """异常 → 处置类。**全仓唯一的重试判据**（M4b 起）。纯函数，可离线测。

    ## 一句话规则

    **有类型线索以类型为准，没类型线索才用消息词。**

    ## 顺序，以及每一步为什么在这个位置

    1. **取消**最先——它不是失败。
    2. **结构化拒收提示**在类型表之前——供应商**间歇性**拒收 `json_schema`
       （qwen 实测抖过两分钟，见 `tests/infrastructure/test_llm.py`）。它带着
       `BadRequestError` 这个"校验类"外壳，但那次抖动是真的，不重试就等于一次调用判死。
    3. **配额**先于抖动——额度用完常被供应商当 429 限流发下来（类型是 `RateLimitError`），
       但退避解决不了账单问题。
    4. **超时**。
    5. **策略**先于校验——"被拒绝"比"请求有问题"更具体。
    6. **校验**先于抖动——`BadRequestError` 沿 MRO 带着 `APIError`（抖动表里有它）。
    7. **抖动**：类型命中，或消息命中。
    8. 消息词兜底**只在认不出类型时**才生效（`classify_litellm_type` 返回 `None`）。

    ## 与旧口径的差别（M4b 实测，不是推测）

    - 修好：`ServiceUnavailableError: upstream overloaded` 从"不重试"变成"重试"
      （真的 503，消息里没有匹配短语，旧口径判死）。
    - 修好：`...schema mismatch, expected 500 items` 与 `maxItems 500 exceeds limit`
      从"重试"变成"不重试"（裸数字误伤，白等约 4 秒 + 3 行无用留样）。
    - **没变**：结构化拒收、参数写错、认证失败、上下文超限、配额——全部原有行为不变。
    """

    message = f"{type(error).__name__} {error}"
    lowered = message.lower()

    if isinstance(error, asyncio.CancelledError):
        return FailureKind.CANCEL
    if STRUCTURED_OUTPUT_REFUSAL_HINT in lowered:
        return FailureKind.TRANSIENT
    if any(hint in lowered for hint in QUOTA_HINTS):
        return FailureKind.QUOTA

    by_type = classify_litellm_type(type(error))
    if by_type is not None:
        # 配额/超时已经按消息判过了（供应商常把配额/超时包成别的类型）；
        # 剩下的类型结论都比消息词准，直接采用。
        return by_type

    if isinstance(error, PacketParseError) or any(
        hint in lowered for hint in ("validation", "schema", "invariant")
    ):
        return FailureKind.VALIDATION
    if "timeout" in lowered or "timed out" in lowered:
        return FailureKind.TIMEOUT
    if any(hint in lowered for hint in ("policy", "denied", "forbidden")):
        return FailureKind.POLICY
    if _message_says_transient(lowered):
        return FailureKind.TRANSIENT
    return FailureKind.UNKNOWN
