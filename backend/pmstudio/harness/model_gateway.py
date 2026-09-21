"""M22 模型接入：把 litellm 的调用细节藏在 Harness 里。

它**只做一次调用**的重试与降级不在这里（那是 M26）。
"""

import os

# `GenerationFailure` 的家在 `common/errors.py`——L2（成稿器）、L6（模型网关）、HTTP 层都要用它，
# 它必须住在跨层那一层。这里 import 进来再抛，顺带让
# `pmstudio.harness.model_gateway.GenerationFailure` 这条老 import 路径继续可用。
from pmstudio.common.errors import GenerationFailure
from pmstudio.contracts.enums import PromptRole, RegistryKind
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.models.registry import ModelBody

try:
    import litellm
except ImportError:  # pragma: no cover — 开发期可选依赖未安装时仍能 import 包
    litellm = None  # type: ignore[assignment]


class ModelGateway:
    """M22：把 litellm 的调用细节藏在 Harness 里。"""

    def __init__(self, registry: object) -> None:
        self._registry = registry

    async def complete(
        self, model_ref: str, messages: list[PromptMessage],
        response_format: dict | None = None,
    ) -> str:
        """调一次模型，返回文本。失败抛 GenerationFailure。
        
        response_format: 可选，传入 JSON Schema 强制模型输出结构化数据。
        例：{"type": "json_object", "schema": MyModel.model_json_schema()}
        """
        if litellm is None:
            raise GenerationFailure(
                "litellm 未安装 — pip install litellm", retryable=False
            )

        entry = await self._registry.resolve(RegistryKind.MODEL, model_ref)
        body = ModelBody.model_validate(entry.content)

        key = os.environ.get(body.api_key_env)
        if not key:
            raise GenerationFailure(
                f"模型 {model_ref} 需要环境变量 {body.api_key_env}，当前未设置",
                retryable=False,
            )

        try:
            kwargs: dict = dict(
                model=body.model,
                messages=self._to_litellm_messages(messages),
                api_key=key,
                timeout=body.timeout_seconds,
            )
            if response_format is not None:
                kwargs["response_format"] = response_format
                kwargs["enable_json_schema_validation"] = True
            response = await litellm.acompletion(**kwargs)
        except Exception as error:  # noqa: BLE010 — litellm 的错误类型不稳定
            raise GenerationFailure(
                f"模型调用失败: {error}", retryable=_is_retryable(error)
            ) from error

        text = response.choices[0].message.content or ""
        text = text.strip()

        if not text:
            raise GenerationFailure("模型返回了空文本", retryable=True)

        return text

    def _to_litellm_messages(
        self, messages: list[PromptMessage]
    ) -> list[dict[str, str]]:
        role_map = {
            PromptRole.SYSTEM: "system",
            PromptRole.USER: "user",
            PromptRole.ASSISTANT: "assistant",
        }
        return [{"role": role_map[m.role], "content": m.text} for m in messages]

def _is_retryable(error: Exception) -> bool:
    """判断一条 litellm 错误值不值得重试。"""
    name = type(error).__name__
    msg = str(error).lower()
    if any(kw in msg for kw in ("authentication", "api key", "401", "403", "forbidden")):
        return False
    if any(kw in msg for kw in ("rate limit", "429", "too many requests")):
        return True
    if any(kw in msg for kw in ("timeout", "timed out", "connection", "server", "500", "502", "503")):
        return True
    # litellm 的特定类型
    if "AuthenticationError" in name:
        return False
    if "RateLimitError" in name:
        return True
    # 未知错误默认重试
    return True
