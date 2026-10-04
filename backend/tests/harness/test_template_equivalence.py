"""M2b 的等价性兜底：`call_structured` 必须与直连引擎**行为一致**。

**为什么需要这条**：M2a 搬引擎时，102 条产品测试经 `schemas.py` 的薄封装传递性覆盖了
同一份引擎，所以那次搬移有等价性证据；M2b 改的是**调用路径**（5 个 agent 从 12 行样板
换成模板），而 `tests/harness/test_structured.py` 全部用**对象形状**的回包——
于是"顶层数组回包"这条真跑验证过的形状（留样 15/66）在 M2b 全程无人覆盖：
`call_structured` 二次 `json.loads` 拿到 list、`basis_of` 崩成 `AttributeError`，
而那条路没有任何处理器接得住（`api/app.py` 只注册 `DomainError` / `ApplicationError`），
测试却全绿。

**这里断言的是不变式，不是某几份 payload 的结果**：对任意回包文本，模板路径与引擎路径
必须「都成功且结果相等」或「抛同一个异常类」。这样写不需要为 5 个报文各手写一份合法
payload（那是脆弱的），而且以后谁改了引擎或模板，第一批挂的就是它。
"""

import pytest

from pmstudio.application.agents import analyzer, integrator, memory_curator, schemas, seat
from pmstudio.harness.errors import ApplicationError
from pmstudio.harness.model import normalize
from pmstudio.harness.model.contracts import ModelReply
from pmstudio.harness.model.structured import call_structured
from tests.support.doubles import RecordingPrompts

ALL_CALLS = [
    (analyzer.ClaimAnalyzer.CALL, "analyzer"),
    (seat.SeatReviewer.CALL, "seat.data_scale"),
    (integrator.Integrator.CALL, "integrator"),
    (integrator.Integrator.DISPOSITIONS_CALL, "integrator"),
    (memory_curator.MemoryCurator.CALL, "memory_curator"),
]

#: 覆盖四类形状：正常对象 / 顶层数组（真跑留样 15/66）/ 非 JSON / 空与全 null。
REPLIES = [
    '{"basis": "b", "claims": [], "opinions": [], "questions": [], "changes": []}',
    '{"basis": "b", "claims": [{"text": "t", "raw_span": "s", "label": "fact"}]}',
    '[{"local_id": "O1", "claim": "x"}, {"local_id": "O2", "claim": "y"}]',
    "[]",
    '{"basis": null, "claims": null, "opinions": null, "questions": null, "changes": null}',
    '{"basis": "b", "claims": ["占位符"]}',
    '{"basis": "b", "claims": ['
    + ", ".join(['{"text": "t", "raw_span": "s", "label": "fact"}'] * 25)
    + "]}",
    '{"basis": 123}',
    "这是模型的解释，不是 JSON",
    "",
]


class OneShot:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def complete(self, request):  # type: ignore[no-untyped-def]
        return ModelReply(text=self._reply, model="scripted", usage={})


def via_engine(call, text: str) -> tuple[str, object]:  # type: ignore[no-untyped-def]
    """直连引擎（产品三个薄封装背后就是它）。返回 `(结局标签, 载荷)`。"""

    try:
        packet, overflow, discarded = normalize.parse_lenient(
            text, call.packet, purpose=call.purpose, rules=call.rules
        )
    except ApplicationError as error:
        return (type(error).__name__, None)
    return ("ok", (packet, overflow, discarded))


async def via_template(call, produced_by: str, text: str) -> tuple[str, object]:  # type: ignore[no-untyped-def]
    try:
        outcome = await call_structured(
            call,
            gateway=OneShot(text),
            prompts=RecordingPrompts(),
            slots={"validation_error": ""},
            metadata={},
            packet_id="pkt-r-1-x",
            run_id="r-1",
            produced_by=produced_by,
        )
    except ApplicationError as error:
        return (type(error).__name__, None)
    return ("ok", (outcome.packet, outcome.overflow, outcome.discarded))


@pytest.mark.parametrize("call,produced_by", ALL_CALLS)
@pytest.mark.parametrize("text", REPLIES, ids=range(len(REPLIES)))
async def test_template_and_engine_agree(call, produced_by, text) -> None:  # type: ignore[no-untyped-def]
    assert await via_template(call, produced_by, text) == via_engine(call, text)


@pytest.mark.parametrize("call,produced_by", ALL_CALLS)
@pytest.mark.parametrize("text", REPLIES, ids=range(len(REPLIES)))
async def test_payload_is_always_the_rebuilt_shell(call, produced_by, text) -> None:  # type: ignore[no-untyped-def]
    """`payload` 必须等于引擎补壳后的那个对象——不是原文，也不是没补壳的数组。

    这一条在"顶层数组"那档尤其要紧：补壳把数组改写成对象，信封必须跟着改写，
    否则同一个回包在"校验"与"通信"两处得到两个理解（真踩过）。
    """

    try:
        outcome = await call_structured(
            call,
            gateway=OneShot(text),
            prompts=RecordingPrompts(),
            slots={},
            metadata={},
            packet_id="pkt-1",
            run_id="r-1",
            produced_by=produced_by,
        )
    except ApplicationError:
        return  # 这一路本来就判死，payload 没被用到
    assert outcome.payload == normalize.load_shell(
        text, call.packet, purpose=call.purpose, rules=call.rules
    )


def test_the_corpus_really_exercises_both_outcomes() -> None:
    """防"全部用例都走同一条分支"这种永绿：语料整体上必须既有成功也有失败。"""

    outcomes = {
        via_engine(call, text)[0] for call, _ in ALL_CALLS for text in REPLIES
    }
    assert "ok" in outcomes, "语料里没有任何一条能走通成功路径——它测不到东西"
    assert len(outcomes - {"ok"}) >= 1, "语料里没有任何一条会失败——它测不到容错路径"
    assert schemas.RULES.signatures, "补壳表为空，数组形状那条用例测不到东西"
