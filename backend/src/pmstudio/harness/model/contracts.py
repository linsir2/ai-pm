"""模型调用契约：请求 / 回复 / 网关协议 / 留样协议 / 提示词库协议。

搬自 `application/ports.py`（原来与 `SeatDefinition` / `MaterialStore` / `WorkspaceIndex`
等二十个业务端口混在一个文件里）。搬进来的理由：它们只讲"怎么跟模型说话"，
没有一个字是产品经理的；而它们是四个 agent + 网关 + container 的共同底座，
放在业务端口文件里会让"模型能力"这个概念没有住处。

**结构化输出是一等公民**：`ModelRequest` 带 `schema_name` + `response_schema`，
所以"要模型吐 JSON"这件事在契约层就成立，不必每个 agent 自己拼一遍。

实现在 `harness/model/providers/litellm.py`（2026-10-04 M4 搬入），由 bootstrap 注入。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelRequest:
    purpose: str
    messages: tuple[ModelMessage, ...]
    schema_name: str
    response_schema: Mapping[str, object]
    metadata: Mapping[str, str] = field(default_factory=dict)
    #: **输出 token 上限**（`None` = 不设，供应商用它自己的默认）。
    #:
    #: 为什么加（2026-10-22，批 14）：**不设**就撞**供应商默认**输出上限，
    #: 而调用方（整合器）需要按**算好的预算**限制条数 → 两把尺子对不上，
    #: 结果是模型写的条数装不下 → **事后截断**（用户拿不到那几条）。
    #:
    #: **给了就真的发给供应商**（`providers/litellm.py`），不给就不发（保持现状）。
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ModelReply:
    text: str
    model: str
    usage: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class ModelGateway(Protocol):
    async def complete(self, request: ModelRequest) -> ModelReply: ...


@runtime_checkable
class ModelCallRecorder(Protocol):
    """留样的旁路出口（`02` §5.3）：记下每一次模型调用，给排查用，不参与业务。

    实现是 `infrastructure/sqlite/model_calls.py`；网关默认不接（`recorder=None` 时什么都不记）。
    提示词给的是**原文**——只存哈希复现不了，而复现是这张表唯一的目的。

    关联键叫 `trace_id`（M4 从 `round_id` 改名）：它只是"这次调用挂在哪个上下文下"的
    定位信息，模型层不该知道产品有"轮次"这个东西。
    **但存储侧的列名仍是 `round_id`**——那列里装的就是 `r-xxxx`，改名叫 `trace_id`
    等于让列名撒谎。改名只发生在协议边界上：harness 说中性名，产品自己的仓库说产品名。
    """

    def record(
        self,
        *,
        trace_id: str | None,
        purpose: str,
        model: str,
        prompt_sha: str,
        prompt_text: str,
        reply_text: str,
        usage: Mapping[str, object],
        duration_ms: int,
        created_at: datetime,
        attempt: int = 1,
        ok: bool = True,
        error: str | None = None,
    ) -> None: ...


@runtime_checkable
class PromptLibrary(Protocol):
    def render(self, name: str, **slots: str) -> str: ...
