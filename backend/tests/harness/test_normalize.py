"""输出归一化引擎搬进 harness 后的形状。

**这个测试文件故意不碰产品的报文**（`ClaimsPacket` 等在 `application/agents/schemas.py`）：
harness 不许知道产品长什么样，所以这里自带两个最小 pydantic 模型 + 自己的签名表与黑名单，
证明"引擎只靠 `NormalizeRules` 就能干活"。

产品侧那 102 条原有测试（`test_agent_schemas` / `test_packet_overflow` / `test_seat_agent` /
`test_integrator` / `test_analyzer`）经 `schemas.py` 的薄封装**传递性覆盖同一份引擎**，
断言一字未改——那是等价性的证据，不是重复。
"""

import pytest
from pydantic import BaseModel, Field

from pmstudio.harness.errors import PacketParseError
from pmstudio.harness.model.normalize import (
    Discarded,
    NormalizeRules,
    Overflow,
    parse_lenient,
)


class Item(BaseModel):
    local_id: str
    claim: str


class Note(BaseModel):
    text: str


class Roster(BaseModel):
    """刻意做成"容器都有默认值"——补壳才不必凭空造必填字段。

    这不是巧合：产品里唯一用签名表的两个包（整合包 / 记忆包）都满足这个前提，
    主张包与席位包因为必填 `basis` 所以**不给**签名表（`test_unplaceable...` 那条就是这个道理）。
    """

    basis: str = ""
    items: list[Item] = Field(default_factory=list, max_length=3)
    notes: list[Note] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    note: str = "默认"
    op: str = "keep"


#: 顺序本身是判据的一部分：命中即停，先查特征最强的键。
SIGNATURES = {
    Roster: (
        ("items", (frozenset({"local_id"}),)),
        ("notes", (frozenset({"text"}),)),
    )
}

#: 这三个字段用默认值兜底会**悄悄改语义**（写/改/删哪一条、这条进不进账本）。
WRITE_SEMANTICS = frozenset({"op", "entry_id", "form"})

RULES = NormalizeRules(signatures=SIGNATURES, write_semantics_fields=WRITE_SEMANTICS)


def roster(**kwargs: object) -> str:
    base: dict[str, object] = {"basis": "读了 E1-E3"}
    base.update(kwargs)
    import json

    return json.dumps(base, ensure_ascii=False)


# —— 补壳（顶层给了数组）——


def test_top_level_array_is_rebuilt_into_buckets():
    """模型把整包回成数组：只要每个元素能用签名唯一归位，就机械补壳。"""

    text = '[{"local_id": "O1", "claim": "x"}, {"text": "一条说明"}]'
    packet, _, _ = parse_lenient(text, Roster, purpose="t", rules=RULES)
    assert [item.local_id for item in packet.items] == ["O1"]
    assert [note.text for note in packet.notes] == ["一条说明"]


def test_unplaceable_array_is_a_shape_error_not_a_content_error():
    text = '[{"unknown": 1}]'
    with pytest.raises(PacketParseError) as caught:
        parse_lenient(text, Roster, purpose="t", rules=RULES)
    assert caught.value.details["packet_shape"] == {
        "got": "array",
        "expected_keys": tuple(Roster.model_fields),
    }


def test_empty_array_is_not_rebuilt():
    """空返回不等于没意见（L2）：空数组**不**补壳，交给原路判死。"""

    with pytest.raises(PacketParseError):
        parse_lenient("[]", Roster, purpose="t", rules=RULES)


# —— 字段级归一化三档（`02` §11.0.9）——


def test_null_list_field_becomes_empty_list():
    packet, _, _ = parse_lenient(roster(tags=None), Roster, purpose="t", rules=RULES)
    assert packet.tags == []


def test_single_string_in_list_field_becomes_one_element_list():
    packet, _, _ = parse_lenient(roster(tags="E4"), Roster, purpose="t", rules=RULES)
    assert packet.tags == ["E4"]


def test_null_field_with_default_falls_back_to_the_default():
    """③「没给值」用分类默认，**不猜内容**。"""

    packet, _, _ = parse_lenient(roster(note=None), Roster, purpose="t", rules=RULES)
    assert packet.note == "默认"


def test_write_semantics_field_is_never_defaulted():
    """黑名单字段收到 `null` 必须**留在原地让 pydantic 报错**，不许悄悄改语义。"""

    with pytest.raises(PacketParseError):
        parse_lenient(roster(op=None), Roster, purpose="t", rules=RULES)


# —— 摘非对象元素（真跑 F17：占位符字符串把整轮打掉）——


def test_non_object_element_is_dropped_and_reported():
    text = (
        '{"basis": "b", "items": ['
        '{"local_id": "O1", "claim": "x"}, "O2_占位", {"local_id": "O3", "claim": "y"}]}'
    )
    packet, _, discarded = parse_lenient(text, Roster, purpose="t", rules=RULES)
    assert [item.local_id for item in packet.items] == ["O1", "O3"]
    assert discarded == (Discarded(container="items", index=2),)


def test_discard_reason_is_a_fixed_phrase_not_the_models_own_text():
    """不复制模型写的那串（`learnings` L20）：留痕要的是"少了一条、为什么少"。"""

    text = '{"basis": "b", "items": ["模型自己写的占位符"]}'
    _, _, discarded = parse_lenient(text, Roster, purpose="t", rules=RULES)
    assert discarded[0].reason == "这一条不是对象（模型写了占位符），没有内容可收"


def test_object_with_a_bad_field_is_never_masked_by_dropping():
    """反例钉子：元素**是对象但字段写错** → 照旧抛，不许被"丢那一条"掩盖。

    （另一条边界"只摘非对象、摘了必上报"见上面两条。）
    """

    text = '{"basis": "b", "items": [{"local_id": "O1"}, {"local_id": "O2"}]}'
    with pytest.raises(PacketParseError):
        parse_lenient(text, Roster, purpose="t", rules=RULES)


# —— 超限截断 ——


def test_overflow_is_trimmed_to_the_limit_and_reported():
    text = roster(items=[{"local_id": f"O{i}", "claim": "x"} for i in range(4)])
    packet, overflow, _ = parse_lenient(text, Roster, purpose="t", rules=RULES)
    assert len(packet.items) == 3
    assert overflow == (Overflow(container="items", kept=3, dropped=1),)


def test_trimming_keeps_the_models_own_order():
    """保留**模型给出的前 N 条**：顺序是它自己排的优先级，我们不替它重排。"""

    text = roster(items=[{"local_id": f"O{i}", "claim": "x"} for i in range(4)])
    packet, _, _ = parse_lenient(text, Roster, purpose="t", rules=RULES)
    assert [item.local_id for item in packet.items] == ["O0", "O1", "O2"]


def test_other_errors_are_never_hidden_by_trimming():
    """只有当**所有**报错都是 `too_long` 时才截断；出现别的错照旧抛——"错就是错"不许放松。"""

    text = roster(items=[{"local_id": "O1", "claim": "好"}, {"local_id": "O2"}])
    with pytest.raises(PacketParseError):
        parse_lenient(text, Roster, purpose="t", rules=RULES)


def test_the_overflow_report_carries_both_numbers() -> None:
    """重装指引要能说人话："给了几条、上限几条"两个数字都在报告里。

    这条能力原先挂在严格档（把溢出量塞进 `PacketParseError.details`），
    但生产恒走宽松档、那条路没人调——真正把这两个数字递给 `repair_note` 的是
    `use_cases._review_one` 自己合成的那个 `PacketParseError`（已被重装测试覆盖）。
    引擎这一侧只负责**报告**，`Overflow(kept, dropped)` 就是那两个数字。
    """

    text = roster(items=[{"local_id": f"O{i}", "claim": "x"} for i in range(4)])
    _, overflow, _ = parse_lenient(text, Roster, purpose="t", rules=RULES)
    assert overflow == (Overflow(container="items", kept=3, dropped=1),)
    assert overflow[0].kept + overflow[0].dropped == 4


# —— 非 JSON ——


def test_non_json_reports_the_offset():
    with pytest.raises(PacketParseError) as caught:
        parse_lenient("这是模型的解释，不是 JSON", Roster, purpose="t", rules=RULES)
    assert "offset" in caught.value.details
    assert caught.value.purpose == "t"
