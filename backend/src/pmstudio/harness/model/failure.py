"""失败分类的**单点权威表**。

为什么要有这张表（2026-10-04 挖出的文档缺口）：失败处置口径散在设计文档四处——
`02-编排与数据.md §7` 按载体分、`§11.0.21` 按"对模型有没有信息量"分、
`§11.0.2` 按后果分、`§11.0.3` 按可追溯性分。加一种新错误时没有一张表可以照着填，
于是每处各写各的 `if`。

**这张表只回答一件事**：这次失败要不要重试、怎么重试。

"错在哪一层"**不**编码在这里——那由异常类型本身承担（`ToolCallFailed` / `FetchRefused` /
`ModelCallFailed` / `PacketParseError` 就是来源），再编一套 `TOOL` / `MODEL` 分类
就是重复真相源。所以分类刻意只有"处置"这一维。

`TRANSIENT_HINTS` 从 `infrastructure/llm/litellm_gateway.py` 搬来，是全仓唯一一份
"什么算抖动"的口径；网关的 `is_transient` 改为调用这里，语义一字未改。
"""

import asyncio
from enum import StrEnum

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

        **这张属性表是"什么算抖动"的第二个入口，所以它必须和 `is_transient` 一致**——
        `tests/harness/test_failure.py` 有一条语料级断言把两者钉在一起。
        原因：网关现在用的是 `is_transient`（有真跑证据），而 `classify` 更细；
        两张表一旦漂移，后面接线的人会以为"超时该重试"却实际不重试，而测试全绿。
        `TIMEOUT` 在重试侧算抖动——`TRANSIENT_HINTS` 里本来就有 `"timeout"`。
        """

        return self in _RETRYABLE


_RETRYABLE: frozenset[FailureKind] = frozenset({FailureKind.TRANSIENT, FailureKind.TIMEOUT})


def is_transient(text: str) -> bool:
    """这段错误消息值不值得退避后重试。判定口径与网关搬移前逐条一致。"""

    lowered = text.lower()
    return any(hint in lowered for hint in TRANSIENT_HINTS)


def is_structured_output_refusal(text: str) -> bool:
    """供应商**稳定**拒收 `json_schema` → 该换写法（`04-未决.md` M2b 已拍板）。"""

    return STRUCTURED_OUTPUT_REFUSAL_HINT in text.lower()


def classify(error: BaseException) -> FailureKind:
    """异常 → 处置类。纯函数，可离线测。

    顺序有意为之：**取消最先**（它不是失败），然后是"与模型说话说错了"（validation），
    再是与供应商/配额有关，最后才看抖动。抖动放最后是因为它最容易被别的词误伤
    （"timeout" 里也可能裹着别的话）。
    """

    text = f"{type(error).__name__} {error}"
    lowered = text.lower()
    if isinstance(error, asyncio.CancelledError) or "cancellederror" in lowered:
        return FailureKind.CANCEL
    if isinstance(error, PacketParseError) or any(
        hint in lowered for hint in ("validation", "schema", "invariant")
    ):
        return FailureKind.VALIDATION
    if any(hint in lowered for hint in QUOTA_HINTS):
        return FailureKind.QUOTA
    if "timeout" in lowered or "timed out" in lowered:
        return FailureKind.TIMEOUT
    if any(hint in lowered for hint in ("policy", "denied", "forbidden")):
        return FailureKind.POLICY
    if is_transient(lowered):
        return FailureKind.TRANSIENT
    return FailureKind.UNKNOWN
