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

from pydantic import BaseModel

from pmstudio.harness.model.contracts import (
    ModelGateway,
    ModelMessage,
    ModelRequest,
    PromptLibrary,
)
from pmstudio.harness.model.envelope import Envelope, basis_of
from pmstudio.harness.model.normalize import NormalizeRules, load_shell, parse_lenient_from


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

    schema = call.packet.model_json_schema()
    prompt = prompts.render(
        call.prompt, **dict(slots), schema_json=json.dumps(schema, ensure_ascii=False)
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
