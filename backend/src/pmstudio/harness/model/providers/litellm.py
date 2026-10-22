"""LiteLLM 网关：唯一碰网络的地方。

供应商错误、空内容、不是 JSON 的内容都在这里被翻译成 `ModelCallFailed`——
失败必须是失败，不静默返回空串（learnings L2）。
"""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

from litellm import acompletion  # noqa: E402

from pmstudio.harness.errors import ModelCallFailed, ModelShed
from pmstudio.harness.model import failure
from pmstudio.harness.model.agentic import tool_call_from_payload
from pmstudio.harness.model.contracts import ModelCallRecorder, ModelReply, ModelRequest
from pmstudio.harness.model.jsonio import is_json, strip_fence
from pmstudio.harness.model.resilience import (  # noqa: E402
    BreakerConfig,
    Bulkhead,
    BulkheadConfig,
    BulkheadFullError,
    CircuitBreaker,
    CircuitOpenError,
    RetryBudget,
    RetryBudgetConfig,
    jittered_backoff,
)
from pmstudio.harness.tools.spec import ToolCall, as_mapping, tool_payload

#: 结构化输出的两种写法。`json_schema`（默认）能帮模型守规；供应商不吃它时退到 `json_object`。
JSON_SCHEMA = "json_schema"
JSON_OBJECT = "json_object"

logger = logging.getLogger(__name__)

def response_format_for(mode: str, *, schema_name: str, schema: Mapping[str, object]) -> dict:
    """这一次请求的 `response_format` 怎么写。

    **契约不在这里**：`LLM 契约`要的是"输出必须过 schema"，那由我们自己的解析与校验强制
    （`_is_json` + 各 agent 的 pydantic 校验 + 失败重试）。这一层只决定"怎么开口要 JSON"。
    """

    if mode == JSON_SCHEMA:
        return {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": dict(schema)},
        }
    return {"type": "json_object"}


def _message_payload(message: object) -> dict[str, object]:
    """一条消息发给供应商时的形状。

    **为什么不能只吐 `role` + `content`**（MCP 批次实测）：
    OpenAI 兼容的 function-calling 协议要求 `role="tool"` 的消息**带 `tool_call_id`**，
    且前面要有一条带 `tool_calls` 的助手轮。缺了它们，严格实现（OpenAI / vLLM / Ollama）
    直接 400；宽松实现（DashScope）不拒收，但模型认不出工具结果是给谁的——
    实测开思考模式时会**重复要同一个工具**（3/3）。

    **只在非空时吐**：普通消息（user 角色）多带一个空字段可能被部分供应商拒收。
    """

    payload: dict[str, object] = {"role": message.role, "content": message.content}
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(dict(call.arguments), ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
    return payload


def _tool_calls_of(message: object) -> tuple[ToolCall, ...]:
    """从供应商回包里摘出 `tool_calls`。

    供应商给的是 `{"id":..., "type":"function", "function":{"name":..., "arguments":"{...}"}}`
    的列表（`arguments` 是**字符串**）。摘不出来就当没有——
    **不猜**：猜一个工具名会让模型收到一份对不上的结果。

    ⚠️ 元素是 **pydantic 对象**而不是 dict（litellm 的形状）——
    `as_mapping` 统一处理（就在本文件上面）。

    **缺 `id` 的条目会被丢掉**，而不是硬造一个空 id：
    空 id 会让后面的 `ToolCall.__post_init__` 抛**裸 `ValueError`**，
    绕过网关的失败分类（API 层会把它当 500，而不是 `tool_call_failed`）。
    供应商给了 `tool_calls` 却不给 id 是**上游的错**，按上游错处理。
    """
    raw = getattr(message, "tool_calls", None) or ()
    calls: list[ToolCall] = []
    for item in raw:
        payload = as_mapping(item)
        function = as_mapping(payload.get("function") or {})
        if not function.get("name"):
            continue
        call_id = str(payload.get("id") or "")
        if not call_id:
            logger.warning(
                "供应商的 tool_call 缺 id（工具名 %r），丢弃该条——"
                "没有 id 就无法把工具结果对应回它",
                function.get("name"),
            )
            continue
        calls.append(tool_call_from_payload(payload))
    return tuple(calls)


#: ── 控制平面的三个上限（**不是**产品上限）─────────────────────────────────
#:
#: 这三个属于"怎么调模型"这一层，所以归 harness。产品上限（问几轮席位、分几批问卷、
#: 各 agent 重装几次）在 `domain/` 与 `application/`，**不许搬进来**——
#: 现有守卫的 `PRODUCT_VOCABULARY` 是精确匹配，`MAX_SEAT_ROUNDS` ≠ `seat_id`，
#: 搬进去它一条都拦不住。那个盲区由 `tests/contracts/test_limits.py` 的
#: `test_product_limits_never_enter_harness` 堵着。
#:
#: 这四个数与它们的文档依据，由 `tests/contracts/test_limits.py` 钉住。

MAX_ATTEMPTS = 3

#: **退避带抖动**（高并发 P2）。原先是固定 `(1.0, 3.0)`：所有同时失败的调用方
#: 在同一时刻一起重试——重试风暴恰好在最需要上游喘息的时刻把它再打一遍。
#: 这里只给基线，实际等待由 `jittered_backoff()` 在 ±50% 内散开。
BACKOFF_SECONDS = (1.0, 3.0)
#: 默认每次调用的墙钟上限（秒）。真值由 `Settings.model_timeout` 传进来（默认 90）。
#: 单次上游调用的墙钟上限。**从真跑数据定的**：167 次成功调用的耗时是
#: p50 18.5s / p95 89.9s / p99 131.3s / **最大 143.0s**。所以 150s 能覆盖全部
#: 已观测到的成功，而 `.env` 里配的 300s 只是在多等 107 秒去看一个必然超时的结果
#: （失败调用的耗时中位恰好是 300.2s、最大 900.5s = 300 × 3 次尝试）。
DEFAULT_TIMEOUT = 150.0


class LiteLLMGateway:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = MAX_ATTEMPTS,
        backoff: tuple[float, ...] = BACKOFF_SECONDS,
        recorder: ModelCallRecorder | None = None,
        model_params: Mapping[str, object] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        bulkhead: Bulkhead | None = None,
        breaker: CircuitBreaker | None = None,
        retry_budget: RetryBudget | None = None,
        breaker_config: BreakerConfig | None = None,
        bulkhead_config: BulkheadConfig | None = None,
        retry_budget_config: RetryBudgetConfig | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._backoff = backoff
        # 韧性层（高并发 P2）。都可注入：测试传替身，生产用默认参数。
        # **三者都只包住上游调用这一层**，不进 domain。
        self._bulkhead_config = bulkhead_config or BulkheadConfig()
        self._breaker_config = breaker_config or BreakerConfig()
        self._bulkhead = bulkhead or Bulkhead(self._bulkhead_config)
        self._breaker = breaker or CircuitBreaker(self._breaker_config)
        self._retry_budget = retry_budget or RetryBudget(retry_budget_config or RetryBudgetConfig())
        # 留样（`02` §5.3）：默认不接——没给 recorder 就什么都不记
        self._recorder = recorder
        # 供应商特有参数（如 DashScope 关思考）：当**基底**用，我们自己的键永远覆盖它
        self._model_params = dict(model_params or {})
        #: 这家供应商吃不吃 `json_schema`。被明确拒过一次就记住，后续调用直接用 `json_object`
        #: ——省掉每次先撞一次 400（`deepseek-v4.1-flash` 实测稳定拒收）。
        self._structured_output = JSON_SCHEMA
        #: 退避的睡眠。**注入口，不是配置项**：生产就是 `asyncio.sleep`。
        #: 为什么要有这个口——退避时刻（1 秒 / 3 秒）是**要被断言的行为**，而直接打桩
        #: `asyncio.sleep` 会改掉**全进程**的那个函数（`litellm.asyncio` 就是 asyncio 本身），
        #: 于是测试期间任何 await 都受影响。改成注入后，睡眠从"全局副作用"变成
        #: "这个网关对象的依赖"，测试能直接读到它睡了几秒，生产路径一字不变。
        self._sleep = sleeper or asyncio.sleep

    async def complete(self, request: ModelRequest) -> ModelReply:
        started = time.monotonic()
        kwargs: dict[str, object] = {
            **self._model_params,
            "model": self._model,
            "messages": [_message_payload(message) for message in request.messages],
            "response_format": response_format_for(
                self._structured_output,
                schema_name=request.schema_name,
                schema=request.response_schema,
            ),
            # 包一层墙钟。**必须同时关掉 SDK 自己的重试**（num_retries=0），
            # 否则只有 timeout 时墙钟会被放大数倍（实测 timeout=2 → 7.8 秒；
            # 加 num_retries=0 → 2.0 秒）。重试只由下面这一圈负责，时间才算得出来。
            "timeout": self._timeout,
            "num_retries": 0,
        }
        if request.tools:
            # **带工具**是 agentic 那条路（`model/agentic.py`）。给 tools 的时候
            # **不能给 response_format**：一部分供应商同时收到两者会拒收
            # （"json_schema and tools are mutually exclusive"）。agentic 路径要的是
            # "自由文本 + tool_calls"，本来也不该要 JSON schema。
            kwargs["tools"] = tool_payload(request.tools)
            kwargs.pop("response_format", None)
        if request.max_tokens is not None:
            # **显式给上限时真的要发出去**（2026-10-22，批 14）：不设就撞供应商默认，
            # 而"按预算限条数"这件事只有在**上限真的生效**时才成立。
            kwargs["max_tokens"] = request.max_tokens
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base
        response = None
        self._retry_budget.record_original()
        for attempt in range(1, self._max_attempts + 1):
            attempt_started = time.monotonic()
            # **熔断先判**：已经开闸就别占隔离舱的槽位、别发网络请求。
            try:
                self._breaker.allow()
            except CircuitOpenError as open_error:
                raise ModelShed(
                    "模型调用已熔断：上游正在恢复中，稍后再试",
                    purpose=request.purpose,
                    # 重试窗口就是熔断的恢复窗口——客户端照这个数等，别硬重试。
                    retry_after=int(self._breaker_config.recovery_timeout),
                    cause=str(open_error),
                    model=self._model,
                    attempts=attempt - 1,
                ) from open_error
            try:
                # **隔离舱**：同时在飞的上游调用数有上限，超出排队，排队超预算就快速失败。
                # 熔断包在**舱内侧**：`call_upstream` 里含熔断判定与记账，
                # 探针必须是那次真实调用（见 `CircuitBreaker.call_upstream` 的注释）。
                response = await self._bulkhead.run(
                    lambda: self._breaker.call_upstream(lambda: acompletion(**kwargs))
                )
                break
            except BulkheadFullError as full_error:
                # **舱满**：我们主动不接。**这不是上游失败**——把它混进下面的
                # 统一转译会变成 502，客户端以为该重试上游，而问题在我们这边。
                raise ModelShed(
                    f"模型并发已满，请 {int(self._bulkhead_config.queue_wait_seconds)} 秒后重试",
                    purpose=request.purpose,
                    retry_after=int(self._bulkhead_config.queue_wait_seconds) or 1,
                    cause=str(full_error),
                    model=self._model,
                    capacity=self._bulkhead_config.capacity,
                    in_flight=self._bulkhead.in_flight,
                ) from full_error
            except CircuitOpenError as open_error:
                # 舱**内侧**的熔断（`allow()` 那一道只挡住"还没进舱"的）：
                # 这一路上游可能刚刚连续失败够数，开闸就发生在两次调用之间。
                raise ModelShed(
                    "模型调用已熔断：上游正在恢复中，稍后再试",
                    purpose=request.purpose,
                    retry_after=int(self._breaker_config.recovery_timeout),
                    cause=str(open_error),
                    model=self._model,
                ) from open_error
            except Exception as error:  # noqa: BLE001 —— 供应商异常类型太多，统一转译
                # **每一次尝试都留样**（含"重试后成功"的那几次失败）：查"为什么超时/为什么重试"
                # 要看每一次尝试，只有最后一次失败被记下来的话，那次 120 秒会看起来像一次慢成功。
                self._record(
                    request,
                    reply_text="",
                    usage={},
                    duration_ms=int((time.monotonic() - attempt_started) * 1000),
                    model=self._model,  # 没拿到回复，只有我们配的那个名字
                    attempt=attempt,
                    ok=False,
                    error=f"{type(error).__name__}: {error}",
                )
                # 供应商**稳定**不吃 `json_schema` → 换写法**立刻**重发一次
                # （不退避、不占抖动次数）。
                # 真跑：`deepseek-v4.1-flash` 对 json_schema 连续 400，而 json_object 正常；
                # 产品原来每次都带 json_schema，于是那个模型上**一次都发不出去**（P34 · M2b）。
                if (
                    self._structured_output == JSON_SCHEMA
                    and is_structured_output_refusal(error)
                ):
                    self._structured_output = JSON_OBJECT
                    # **带 tools 的请求不许带 `response_format`**——同文件上面写着
                    # "一部分供应商同时收到两者会拒收"。这一条在**降级路径上同样成立**：
                    # 重发时若把它加回去，请求就又回到了那个会被拒收的组合，
                    # 而且 agentic 路径会收到一段 JSON 文本被当成最终答复。
                    if not request.tools:
                        kwargs["response_format"] = response_format_for(
                            JSON_OBJECT,
                            schema_name=request.schema_name,
                            schema=request.response_schema,
                        )
                    else:
                        kwargs.pop("response_format", None)
                    try:
                        response = await acompletion(**kwargs)
                        break
                    except Exception as second:  # noqa: BLE001 —— 回落也不是万灵药
                        self._record(
                            request,
                            reply_text="",
                            usage={},
                            duration_ms=int((time.monotonic() - attempt_started) * 1000),
                            model=self._model,
                            attempt=attempt,
                            ok=False,
                            error=f"{type(second).__name__}: {second}",
                        )
                        error = second
                if (
                    failure.classify(error).retryable
                    and attempt < self._max_attempts
                    # **重试预算**：没令牌就不重试。直接失败比放大上游好。
                    and self._retry_budget.take()
                ):
                    # 退避**带抖动**（P2）：固定退避会让所有调用方同一刻重试 = 重试风暴
                    await self._sleep(jittered_backoff(self._backoff[attempt - 1]))
                    continue
                raise ModelCallFailed(
                    f"模型调用失败：{type(error).__name__}"
                    + (f"（重试 {attempt - 1} 次后仍失败）" if attempt > 1 else ""),
                    purpose=request.purpose,
                    cause=str(error),
                    model=self._model,
                    attempts=attempt,
                    timeout_seconds=self._timeout,
                ) from error
        assert response is not None
        message = response.choices[0].message
        raw = (message.content or "").strip()
        # **模型要调工具时，正文是空的**——那不是"空回包"，那是"它在要工具"。
        # 所以先把 tool_calls 摘出来：有 tool_calls 时空正文是**正常**的。
        tool_calls = _tool_calls_of(message)
        if not raw and not tool_calls:
            # 空回包也留痕（P35）：原来这两条"回包本身有问题"的路**一行都不落库**，
            # 于是复盘时只剩一句"模型返回了空内容"，原文永远补不回来（真踩到过两次）。
            self._record(
                request,
                reply_text="",
                usage={},
                duration_ms=int((time.monotonic() - attempt_started) * 1000),
                model=getattr(response, "model", self._model),
                attempt=attempt,
                ok=False,
                error="EmptyReply: 模型返回了空内容",
            )
            raise ModelCallFailed(
                "模型返回了空内容",
                purpose=request.purpose,
                model=self._model,
                hint="上一次你什么都没返回。请只输出那个 JSON 对象，不要空回复。",
            )
        text = strip_fence(raw) if raw else ""
        if request.response_schema and not tool_calls and not is_json(text):
            # `usage` 与 `finish_reason` 一起记：回包是**截断的 JSON**时（P35 真踩到：
            # 21.7K 字符断在半句上），只有 finish_reason=length 能一眼说明"是输出被截断"，
            # 而不是"模型没按格式答"。
            finish = getattr(response.choices[0], "finish_reason", None)
            self._record(
                request,
                reply_text=text,
                usage=dict(getattr(response, "usage", {}) or {}),
                duration_ms=int((time.monotonic() - attempt_started) * 1000),
                model=getattr(response, "model", self._model),
                attempt=attempt,
                ok=False,
                error=(
                    f"NonJsonReply: 回包不是合法 JSON（finish_reason={finish}，"
                    f"开头：{text[:120]}）"
                ),
            )
            raise ModelCallFailed(
                "模型没有按约定返回 JSON（结构化输出契约被破坏）",
                purpose=request.purpose,
                model=self._model,
                head=text[:120],
                hint=(
                    "上一次的回复不是合法 JSON"
                    + (f"（开头是：{text[:120]}）" if text else "")
                    + "。请只输出那个 JSON 对象：不要解释文字、不要代码块围栏。"
                ),
            )
        reply = ModelReply(
            text=text,
            model=getattr(response, "model", self._model),
            usage=dict(getattr(response, "usage", {}) or {}),
            tool_calls=tool_calls,
        )
        self._record(
            request,
            reply_text=reply.text,
            usage=reply.usage,
            duration_ms=int((time.monotonic() - started) * 1000),
            model=reply.model,  # 供应商回填的模型名（能看出实际是哪个快照答的）
            attempt=attempt,
            ok=True,
        )
        return reply

    def _record(
        self,
        request: ModelRequest,
        *,
        reply_text: str,
        usage: Mapping[str, object],
        duration_ms: int,
        model: str,
        attempt: int = 1,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        """旁路留样：没接 recorder 就什么都不做（默认就是没接）。"""

        if self._recorder is None:
            return
        prompt = "\n".join(message.content for message in request.messages)
        self._recorder.record(
            trace_id=(
                # 中立键优先（`call_structured` 注入）；后面两个是**兜底**：直接调网关时
                # 没有这一层，于是按上下文键认——顺序：新键 → 轮次 → 探索（M8.1）。
                request.metadata.get("trace_id")
                or request.metadata.get("round_id")
                or request.metadata.get("exploration_id")
            ),
            purpose=request.purpose,
            model=model,
            prompt_sha=hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
            prompt_text=prompt,
            reply_text=reply_text,
            # litellm 的 usage 里可能混着 pydantic 包装对象（真模型实测踩到过）：
            # 先转成 JSON 能写的形状，别让留样把这一轮搞失败。
            usage=_jsonable(usage),
            duration_ms=duration_ms,
            created_at=datetime.now(UTC),
            attempt=attempt,
            ok=ok,
            error=error,
        )


def _jsonable(value: object) -> object:
    """把任意值转成 `json.dumps` 写得出来的形状（认不出来的就 `str()`）。"""

    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _jsonable(dump())
    return str(value)


def is_structured_output_refusal(error: BaseException) -> bool:
    """供应商**稳定**拒收 `json_schema` → 换写法（`04-未决.md` M2b 已拍板）。

    与重试判据分开：那边管"短暂拒收 → 退避重试"（qwen 实测抖过两分钟），
    这边管"换写法"（`deepseek-v4.1-flash` 实测连续 400，换 `json_object` 就通）。
    稳不稳由**重试次数**判断，不靠分类判断。
    """

    return failure.is_structured_output_refusal(f"{type(error).__name__} {error}")
