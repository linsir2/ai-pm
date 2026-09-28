"""LiteLLM 网关：唯一碰网络的地方。

供应商错误、空内容、不是 JSON 的内容都在这里被翻译成 `ModelCallFailed`——
失败必须是失败，不静默返回空串（learnings L2）。
"""

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # 别在导入时去网上拉价目表

from litellm import acompletion  # noqa: E402

from pmstudio.application.errors import ModelCallFailed
from pmstudio.application.ports import ModelCallRecorder, ModelReply, ModelRequest

# 只重试"明显是抖动"的错误：连接失败、超时、限流、5xx。认证/参数类错误立刻失败。
# 失败语义不变：重试到上限仍然失败 → ModelCallFailed → 整轮失败、可重试。
TRANSIENT_HINTS = (
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
)
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
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._backoff = backoff
        # 留样（`02` §5.3）：默认不接——没给 recorder 就什么都不记
        self._recorder = recorder

    async def complete(self, request: ModelRequest) -> ModelReply:
        started = time.monotonic()
        kwargs: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": dict(request.response_schema),
                },
            },
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
            try:
                response = await acompletion(**kwargs)
                break
            except Exception as error:  # noqa: BLE001 —— 供应商异常类型太多，统一转译
                if is_transient(error) and attempt < self._max_attempts:
                    await asyncio.sleep(self._backoff[attempt - 1])
                    continue
                # 失败的这一次也留样（reply_text 空串）：查"为什么超时"要看**每一次**尝试，
                # 不只是最后成功的那次。
                self._record(
                    request,
                    reply_text="",
                    usage={},
                    duration_ms=int((time.monotonic() - started) * 1000),
                    model=self._model,  # 没拿到回复，只有我们配的那个名字
                )
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
            raise ModelCallFailed(
                "模型返回了空内容", purpose=request.purpose, model=self._model
            )
        text = strip_fence(raw)
        if request.response_schema and not _is_json(text):
            raise ModelCallFailed(
                "模型没有按约定返回 JSON（结构化输出契约被破坏）",
                purpose=request.purpose,
                model=self._model,
                head=text[:120],
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


def strip_fence(text: str) -> str:
    """模型经常把 JSON 包在 ```json 围栏里——围栏剥掉，别让下游在围栏上炸。"""

    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    return body.strip()


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return False
    return True


def is_transient(error: BaseException) -> bool:
    """这次失败值不值得重试：连接抖动、超时、限流、5xx 值得；认证与参数错误不值得。"""

    text = f"{type(error).__name__} {error}".lower()
    return any(hint in text for hint in TRANSIENT_HINTS)
