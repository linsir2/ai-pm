"""LiteLLM 网关：唯一碰网络的地方。

供应商错误、空内容、不是 JSON 的内容都在这里被翻译成 `ModelCallFailed`——
失败必须是失败，不静默返回空串（learnings L2）。
"""

import asyncio
import hashlib
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # 别在导入时去网上拉价目表

from litellm import acompletion  # noqa: E402

from pmstudio.harness.errors import ModelCallFailed
from pmstudio.harness.model import failure
from pmstudio.harness.model.contracts import ModelCallRecorder, ModelReply, ModelRequest
from pmstudio.harness.model.jsonio import is_json, strip_fence

#: 结构化输出的两种写法。`json_schema`（默认）能帮模型守规；供应商不吃它时退到 `json_object`。
JSON_SCHEMA = "json_schema"
JSON_OBJECT = "json_object"

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
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 3.0)
#: 默认每次调用的墙钟上限（秒）。真值由 `Settings.model_timeout` 传进来（默认 90）。
DEFAULT_TIMEOUT = 90.0


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
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._backoff = backoff
        # 留样（`02` §5.3）：默认不接——没给 recorder 就什么都不记
        self._recorder = recorder
        # 供应商特有参数（如 DashScope 关思考）：当**基底**用，我们自己的键永远覆盖它
        self._model_params = dict(model_params or {})
        #: 这家供应商吃不吃 `json_schema`。被明确拒过一次就记住，后续调用直接用 `json_object`
        #: ——省掉每次先撞一次 400（`deepseek-v4.1-flash` 实测稳定拒收）。
        self._structured_output = JSON_SCHEMA

    async def complete(self, request: ModelRequest) -> ModelReply:
        started = time.monotonic()
        kwargs: dict[str, object] = {
            **self._model_params,
            "model": self._model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
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
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base
        response = None
        for attempt in range(1, self._max_attempts + 1):
            attempt_started = time.monotonic()
            try:
                response = await acompletion(**kwargs)
                break
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
                    kwargs["response_format"] = response_format_for(
                        JSON_OBJECT,
                        schema_name=request.schema_name,
                        schema=request.response_schema,
                    )
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
                if is_transient(error) and attempt < self._max_attempts:
                    await asyncio.sleep(self._backoff[attempt - 1])
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
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
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
        text = strip_fence(raw)
        if request.response_schema and not is_json(text):
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
            round_id=request.metadata.get("round_id"),
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


def is_transient(error: BaseException) -> bool:
    """这次失败值不值得重试：连接抖动、超时、限流、5xx 值得；认证与参数错误不值得。

    **判据只有一份**，在 `harness.model.failure`（全仓唯一的"什么算抖动"口径）；
    这里只做"异常 → 文本"这一下接缝，函数名与语义搬移前逐字一致。
    """

    return failure.is_transient(f"{type(error).__name__} {error}")


def is_structured_output_refusal(error: BaseException) -> bool:
    """供应商**稳定**拒收 `json_schema` → 换写法（`04-未决.md` M2b 已拍板）。

    与 `is_transient` 分开：那边管"短暂拒收 → 重试"（qwen 实测抖过两分钟），
    这边管"**稳定**拒收 → 换写法"（`deepseek-v4.1-flash` 实测连续 400）。
    稳不稳由**重试次数**判断，不靠分类判断。
    """

    return failure.is_structured_output_refusal(f"{type(error).__name__} {error}")
