"""结构化调用：把"5 个 agent 各写一遍的 12 行"收成一处。

原来每个 agent 都自己写这四步（`analyzer.py:177-207`、`seat.py:79-107`、
`integrator.py:186-217` 与 `:249-266`、`memory_curator.py:52-70`）：

1. `schema = Packet.model_json_schema()`
2. `prompts.render(PROMPT, …, schema_json=json.dumps(schema), validation_error=…)`
3. `await gateway.complete(ModelRequest(purpose, (msg,), schema_name, schema, metadata))`
4. `parse_packet_lenient(text, Packet, purpose)`

第 1 步和第 4 步是**机制**（"要模型吐 JSON"这件事怎么落地），第 2 步的槽位内容和
第 3 步的 `purpose` 是**产品知识**。这里收走机制，把产品知识变成 `StructuredCall` 声明 + 调用参数。

**只做一次尝试**：重试环在 `use_cases._review_one`，它与事务 / `attest` / 留痕耦合；
搬进 harness 就得把事务也搬，违反"模型调用一律在事务外"（守卫
`tests/architecture/test_no_await_in_uow.py` 会拦）。

**抛出的异常类型与搬移前逐字一致**：`ModelCallFailed`（网关失败）与 `PacketParseError`
（形状错）——用例层重试环只认这两个，换掉等于整条自愈链断掉。
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode, JsonSchemaValue

from pmstudio.harness.model.contracts import (
    ModelGateway,
    ModelMessage,
    ModelRequest,
    PromptLibrary,
)
from pmstudio.harness.model.envelope import Envelope, basis_of
from pmstudio.harness.model.normalize import NormalizeRules, load_shell, parse_lenient_from


class _LeanSchemaGenerator(GenerateJsonSchema):
    """给模型看的 schema 要**瘦**：pydantic 自动塞的英文名与开发者笔记一个字都不许进提示词。

    `title`（`Raw Span` / `ClaimsPacket`）是 pydantic 从字段名与类名机械生成的，对中文模型
    零信息量；**模型级** `description` 是类 docstring 漏出来的——那些 docstring 写的是
    `M6/M11`、`02 §11.0.38`、`domain/memory/memory.py::screen_changes` 这类只有作者看得懂
    的笔记。两样都逐字进 `prompt_text` 与 `prompt_sha`（实测 21,064 字符的 schema 里，
    6,132 字是它们）。

    **删的判据是"这个节点描述一个 BaseModel"**（有没有 `properties` 兄弟键），不是"有没有
    `title`/`description` 这两个键"——`title` 同时是本项目一个**真实字段名**
    （`QuestionDraft.title`，题面）。按键名无差别地`pop` 会把它连同它在 `required`
    里的义务一起删掉：模型看不见这个字段，改为把题干写进 `why`，于是整包
    `questions[*].title is required` 校验失败，两次重装都不行 → 整合降级、s11 全空
    （真跑实测：瘦版三次连跑，草案里 0 条功能内容）。

    字段级 `description` 是 `02` §11.0.4「口径的入口不止一个，改一处要说三处」的既定冗余，
    一律保留，由 `tests/application/test_agent_schemas.py:21-29` 与
    `tests/harness/test_structured.py::test_field_level_descriptions_survive_the_slimming` 钉着。

    供应商侧零损失：`providers/litellm.py` 把它塞进 `response_format` 给约束解码用，
    而约束引擎只消费 `type` / `enum` / `required` / `properties` / `items`。
    """

    def generate(
        self, schema: Any, mode: JsonSchemaMode = "validation"
    ) -> JsonSchemaValue:
        out = super().generate(schema, mode)
        _strip_machine_words(out)
        return out


def _strip_machine_words(node: object) -> None:
    """就地剥掉 BaseModel 节点上的 pydantic 机造词。

    **只动"描述一个 BaseModel"的那些节点**（有 `properties` 兄弟键的）：它们的 `title` 是
    类名机造的、`description` 是类 docstring 漏出来的。字段名一律不动——`title` 在本项目
    里是一个真实字段名（`QuestionDraft.title`），按键名无差别 `pop` 会把它从
    `properties` 与 `required` 里一起删掉（见 `_LeanSchemaGenerator` 的 docstring 记的那次回归）。
    """

    if isinstance(node, dict):
        if "properties" in node:
            node.pop("title", None)
            node.pop("description", None)
            for child in node["properties"].values():
                _strip_machine_words(child)
        for key in ("items", "anyOf", "oneOf", "allOf", "prefixItems"):
            _strip_machine_words(node.get(key))
        for defs in node.get("$defs", {}).values():
            _strip_machine_words(defs)


@dataclass(frozen=True, slots=True)
class StructuredCall[P: BaseModel]:
    """"这一趟要模型按什么形状说话"的声明。全部是产品知识，harness 只负责照做。"""

    purpose: str
    #: §4.4 的 kind。取值由调用方声明——`02` §4.4 那份清单其实漏了 `opinion_dispositions`
    #: 与 `memory_curation` 两个真实存在的包，且整合者**一包两用**（可能产出问卷也可能产出
    #: 草案），所以这里不做枚举校验：真相源在 `Round.step` / `waiting_on`，不在信封里再编一份。
    kind: str
    #: 提示词文件名（去 `.md`）
    prompt: str
    packet: type[P]
    rules: NormalizeRules = field(default_factory=NormalizeRules)

    @property
    def schema_name(self) -> str:
        """结构化输出的名字。今天 5 处硬编码（`SeatPacket` 等）全等于类名——派生即可，
        由 `tests/application/test_agent_calls.py` 钉住防漂移。"""

        return self.packet.__name__


async def call_structured[P: BaseModel](
    call: StructuredCall[P],
    *,
    gateway: ModelGateway,
    prompts: PromptLibrary,
    slots: Mapping[str, str],
    metadata: Mapping[str, str],
    packet_id: str,
    run_id: str,
    produced_by: str,
) -> "Envelope":
    """跑一次结构化调用。

    `slots` 是提示词里除 `schema_json` 以外的全部槽位（`validation_error` 由用例层的重试环
    递进来——它属于"重试策略"，不归这里管）。`schema_json` 由本函数注入：把 schema 内联进
    提示词是机制的一部分（`schemas.py` 拆出前的头注就写着这三件事同源）。
    """

    schema = call.packet.model_json_schema(schema_generator=_LeanSchemaGenerator)
    prompt = prompts.render(
        call.prompt,
        **dict(slots),
        # 紧凑分隔符：`json.dumps` 默认的 `", "` / `": "` 在这份 schema 里是纯空白
        # （实测 9 个报文共约 1,650 字符）。它逐字进提示词，也就逐字进 `prompt_sha`。
        schema_json=json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
    )
    reply = await gateway.complete(
        ModelRequest(
            purpose=call.purpose,
            messages=(ModelMessage(role="user", content=prompt),),
            schema_name=call.schema_name,
            response_schema=schema,
            # `trace_id` 是**中立键**：留样要的是"这一趟属于谁"，而 `run_id` 本来就有。
            # 在这一层注入，谁调 `call_structured` 都自动带上——否则网关只能去猜上下文键
            #（`round_id` / `exploration_id` / `discussion_id`…），每加一种调用就得补一次
            #（讨论那一种就漏过一次，留样记成 NULL）。
            metadata={**metadata, "trace_id": run_id},
        )
    )
    # 补壳只做一次，而且要拿**补壳之后**那个对象当 payload：模型把整包回成数组时
    # （留样 15/66 实测），`json.loads` 拿到的是 list，补壳把它改写成对象——
    # 自己再 loads 一次就等于绕过补壳，同一个形状在两处得到两个理解（曾因此 AttributeError）。
    shell = load_shell(reply.text, call.packet, purpose=call.purpose, rules=call.rules)
    packet, overflow, discarded = parse_lenient_from(
        shell, call.packet, purpose=call.purpose, rules=call.rules
    )
    return Envelope(
        packet_id=packet_id,
        run_id=run_id,
        kind=call.kind,
        produced_by=produced_by,
        basis=basis_of(shell),
        payload=shell,
        packet=packet,
        overflow=overflow,
        discarded=discarded,
    )
