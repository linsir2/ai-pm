"""M22 模型接入：把 litellm 的调用细节藏在 Harness 里。

它**只做一次调用**的重试与降级不在这里（那是 M26）。
"""

import os

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import PromptRole, RegistryKind
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.models.registry import ModelBody

try:
    import litellm
except ImportError:  # pragma: no cover — 开发期可选依赖未安装时仍能 import 包
    litellm = None  # type: ignore[assignment]


class GenerationFailure(Exception):
    """一次模型调用失败。`retryable` 告诉调用方值不值得重试。"""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelGateway:
    """M22：把 litellm 的调用细节藏在 Harness 里。"""

    def __init__(self, registry: object) -> None:
        self._registry = registry

    async def complete(self, model_ref: str, messages: list[PromptMessage]) -> str:
        """调一次模型，返回文本。失败抛 GenerationFailure。"""
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
            response = await litellm.acompletion(
                model=body.model,
                messages=self._to_litellm_messages(messages),
                api_key=key,
                timeout=body.timeout_seconds,
            )
        except Exception as error:  # noqa: BLE010 — litellm 的错误类型不稳定
            raise GenerationFailure(
                f"模型调用失败: {error}", retryable=_is_retryable(error)
            ) from error

        text = response.choices[0].message.content or ""
        text = self._strip_thinking(text).strip()

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

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """剥掉 thinking … 或 <think>…</think> 的思考过程。

        两种格式都处理：
        - "thinking\\n思考内容\\n正文" → 取 "正文"
        - "<think>思考内容</think>正文" → 取 "正文"
        """
        for tag in ("<think>", "<thinking>"):
            if tag in text:
                text = text.split(tag, 1)[-1]
        for tag in ("</think>", "</thinking>"):
            if tag in text:
                text = text.split(tag, 1)[0]
                return text

        # "thinking" 作为行首标记 → 剥掉标记行 + 紧跟的思考内容行
        if "thinking" in text:
            after_marker = text.split("thinking", 1)[-1]
            # 跳过标记行的剩余部分 + 紧跟的一行思考内容
            lines = after_marker.split("\n")
            # lines[0] = 标记行剩余（通常为空）, lines[1] = 思考内容, lines[2:] = 正文
            remaining = lines[2:] if len(lines) > 2 else []
            text = "\n".join(remaining)

        return text


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
