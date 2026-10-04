"""一次结构化调用：提示词 → 请求 → 信封 → 校验 → 报告。

**只做一次尝试，不重试。** 重试环归用例层（`use_cases._review_one`），因为它与事务 /
`attest` / 留痕耦合；搬进 harness 就得把事务也搬，直接违反"模型调用一律在事务外"。

测试用**假网关 + 假提示词库**（测试替身只许住在 tests/，见 `test_no_fakes_in_production`），
不联网、不需要 API key。
"""

import pytest

from pmstudio.harness.errors import ModelCallFailed, PacketParseError
from pmstudio.harness.model.contracts import ModelMessage, ModelReply, ModelRequest
from pmstudio.harness.model.envelope import Envelope
from pmstudio.harness.model.normalize import NormalizeRules
from pmstudio.harness.model.structured import StructuredCall, call_structured
from tests.harness.test_normalize import RULES, Roster
from tests.support.doubles import RecordingPrompts


class ScriptedGateway:
    """按脚本回一包，顺手把收到的请求留下来给断言看。"""

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        return ModelReply(text=self._reply, model="scripted", usage={"total_tokens": 7})


CALL = StructuredCall(
    purpose="seat_review",
    kind="seat_review",
    prompt="seat_review",
    packet=Roster,
    rules=RULES,
)


async def call(gateway: ScriptedGateway, **slots: str) -> object:
    return await call_structured(
        CALL,
        gateway=gateway,
        prompts=RecordingPrompts(),
        slots=slots,
        metadata={"seat_id": "seat.data_scale"},
        packet_id="pkt-r-1-seat.data_scale",
        run_id="r-1",
        produced_by="seat.data_scale",
    )


async def test_one_call_produces_packet_envelope_and_reports():
    gateway = ScriptedGateway('{"basis": "读了 s5", "items": [{"local_id": "O1", "claim": "x"}]}')
    outcome = await call(gateway)
    assert [item.local_id for item in outcome.packet.items] == ["O1"]
    assert outcome == Envelope(
        packet_id="pkt-r-1-seat.data_scale",
        run_id="r-1",
        kind="seat_review",
        produced_by="seat.data_scale",
        basis="读了 s5",
        payload={"basis": "读了 s5", "items": [{"local_id": "O1", "claim": "x"}]},
        packet=outcome.packet,
    )
    assert outcome.overflow == () and outcome.discarded == ()


async def test_the_envelope_does_not_carry_a_second_copy_of_the_raw_reply() -> None:
    """原文只有一份：网关旁路留样（`model_calls` 表）。

    信封曾经带 `raw` / `model` / `usage`，与那张表逐字同源、却没人读——
    那是第二真相源，而且是不完整的那一份（空回包 / 非 JSON 那两条路根本走不到信封）。
    """

    fields = set(Envelope.__dataclass_fields__)
    assert not fields & {"raw", "model", "usage", "duration_ms", "prompt_sha"}


async def test_request_carries_purpose_schema_name_and_metadata():
    """`purpose` 是留样归堆的键、`schema_name` 是结构化输出的名字——两者都不能丢。"""

    gateway = ScriptedGateway('{"basis": "b"}')
    await call(gateway)
    request = gateway.requests[0]
    assert request.purpose == "seat_review"
    assert request.schema_name == "Roster"
    assert request.metadata == {"seat_id": "seat.data_scale"}
    assert request.response_schema == Roster.model_json_schema()


async def test_schema_json_is_inlined_into_the_prompt():
    """生成 schema、把它内联进提示词、解析回来——这三件事以前每个 agent 各写一遍。"""

    prompts = RecordingPrompts()
    gateway = ScriptedGateway('{"basis": "b"}')
    await call_structured(
        CALL,
        gateway=gateway,
        prompts=prompts,
        slots={},
        metadata={},
        packet_id="pkt-1",
        run_id="r-1",
        produced_by="seat.x",
    )
    assert prompts.last("seat_review")["schema_json"]


async def test_missing_slot_still_blows_up():
    """槽位缺一个就报错（`FilePromptLibrary` 的既定行为）：提示词里出现裸占位符是更糟的失败。"""

    prompts = RecordingPrompts()
    prompts.render = lambda name, **slots: (_ for _ in ()).throw(  # type: ignore[method-assign]
        KeyError("missing")
    )
    with pytest.raises(KeyError):
        await call_structured(
            CALL,
            gateway=ScriptedGateway("{}"),
            prompts=prompts,
            slots={},
            metadata={},
            packet_id="pkt-1",
            run_id="r-1",
            produced_by="seat.x",
        )


async def test_shape_error_keeps_raising_packet_parse_error():
    """用例层的重试环只认这一个异常类型——换了它，整条自愈链就断了。"""

    with pytest.raises(PacketParseError):
        await call(ScriptedGateway("这不是 JSON"))


async def test_overflow_is_reported_not_raised():
    """超限不判死：报回 `Overflow`，由用例层决定"先重装一次、再截断"（报告 F4 的口径）。"""

    gateway = ScriptedGateway(
        '{"basis": "b", "items": ['
        + ", ".join(f'{{"local_id": "O{i}", "claim": "x"}}' for i in range(5))
        + "]}"
    )
    outcome = await call(gateway)
    assert len(outcome.packet.items) == 3
    assert outcome.overflow[0].container == "items"
    assert outcome.overflow[0].dropped == 2


async def test_network_failure_stays_a_model_call_error():
    """网关抛的错原样透出：提示词类失败要带 hint，供应商类不给，语义一个字不动。"""

    class Broken(ScriptedGateway):
        async def complete(self, request: ModelRequest) -> ModelReply:
            raise ModelCallFailed("空回包", purpose="seat_review", hint="再给一次")

    with pytest.raises(ModelCallFailed) as caught:
        await call(Broken(""))
    assert caught.value.hint == "再给一次"


async def test_prompt_is_a_single_user_message():
    """今天四个 agent 都发单条 user 消息——模板收进来后不许悄悄变成多轮。"""

    gateway = ScriptedGateway('{"basis": "b"}')
    await call(gateway)
    messages = gateway.requests[0].messages
    assert len(messages) == 1
    assert isinstance(messages[0], ModelMessage)
    assert messages[0].role == "user"


def test_schema_name_is_derived_from_the_packet_class():
    """今天 5 处硬编码（`SeatPacket` 等）全等于类名；派生之后要钉住，防未来漂移。"""

    assert CALL.schema_name == "Roster"


async def test_top_level_array_reply_is_rebuilt_before_it_reaches_the_envelope():
    """回归：模型把整包回成**数组**（留样 15/66 实测）时，信封拿到的必须是**补壳之后**的对象。

    曾经的 bug：`call_structured` 自己 `json.loads(reply.text)` 拿 payload，拿到的是 list，
    紧接着 `payload.get("basis")` → AttributeError。那条路**没有任何处理器接得住**
    （`api/app.py` 只注册 `DomainError` / `ApplicationError`，用例层只 catch
    `ModelCallFailed` / `PacketParseError` / `DomainError` / `ShapeError`），
    于是 500 且轮次永远停在"生成中"、连失败都没记一笔。

    所有对象形状的用例都发现不了它——必须是数组形状。
    """

    gateway = ScriptedGateway(
        '[{"local_id": "O1", "claim": "x"}, {"local_id": "O2", "claim": "y"}]'
    )
    outcome = await call(gateway)
    assert isinstance(outcome.payload, dict)
    assert outcome.payload["items"] == [
        {"local_id": "O1", "claim": "x"},
        {"local_id": "O2", "claim": "y"},
    ]
    assert [item.local_id for item in outcome.packet.items] == ["O1", "O2"]


async def test_non_mapping_payload_degrades_to_no_basis_instead_of_crashing():
    """`load_shell` 不保证返回对象（模型回字符串/数字时原样返回）——信封必须降级，不是崩。"""

    from pmstudio.harness.model.envelope import basis_of

    assert basis_of(["不是对象"]) == ""
    assert basis_of("字符串") == ""
    assert basis_of(7) == ""
    assert basis_of(None) == ""


def test_rules_default_to_nothing_rather_than_guessing():
    """没有规则表就等于不开补壳——不许有一套隐式默认。"""

    bare = StructuredCall(purpose="p", kind="p", prompt="p", packet=Roster, rules=NormalizeRules())
    assert bare.rules.signatures == {}
    assert bare.rules.write_semantics_fields == frozenset()
