"""LiteLLM 网关：唯一碰网络的地方。

供应商错误、空内容、不是 JSON 的内容都在这里被翻译成 `ModelCallFailed`——
失败必须是失败，不静默返回空串（learnings L2）。
"""

import asyncio
import json
import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # 别在导入时去网上拉价目表

from litellm import acompletion  # noqa: E402

from pmstudio.application.errors import ModelCallFailed
from pmstudio.application.ports import ModelReply, ModelRequest

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


class LiteLLMGateway:
    def __init__(
        self, *, model: str, api_key: str | None = None, api_base: str | None = None
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base

    async def complete(self, request: ModelRequest) -> ModelReply:
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
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base
        response = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await acompletion(**kwargs)
                break
            except Exception as error:  # noqa: BLE001 —— 供应商异常类型太多，统一转译
                if is_transient(error) and attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(BACKOFF_SECONDS[attempt - 1])
                    continue
                raise ModelCallFailed(
                    f"模型调用失败：{type(error).__name__}"
                    + (f"（重试 {attempt - 1} 次后仍失败）" if attempt > 1 else ""),
                    purpose=request.purpose,
                    cause=str(error),
                    model=self._model,
                    attempts=attempt,
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
        return ModelReply(
            text=text,
            model=getattr(response, "model", self._model),
            usage=dict(getattr(response, "usage", {}) or {}),
        )


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
