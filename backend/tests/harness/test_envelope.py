"""信封是 agent 输出的信息包——它必须**唯一可寻**、**有真实消费者**、且**只有一处口径**。

三条硬要求（2026-10-04 自审加进来的）：

1. **唯一**：`packet_id` 在生产路径上不重复。身份由**协调者**（用例层）分配——
   agent 只知道自己这一趟是谁产出的，不知道这是同一席位第几趟第几次重试。
2. **有消费者**：`packet.emitted` 事件带 `packet_id`，界面与留痕都能按它回查。
3. **只有一处口径**：`overflow` / `discarded` / `basis` 只存在信封里一处；
   领域投影上的同名字段是**转发属性**，不是第二份存储。
"""

import pytest

from pmstudio.harness.model.envelope import Envelope, basis_of, make_packet_id
from tests.harness.test_normalize import Roster


def an_envelope(**overrides: object) -> Envelope:
    base: dict[str, object] = {
        "packet_id": "pkt-r-1-seat.data_scale",
        "run_id": "r-1",
        "kind": "seat_review",
        "produced_by": "seat.data_scale",
        "basis": "读了 s5 / s8 与本轮 E1–E8",
        "payload": {"opinions": []},
        "packet": Roster(),
    }
    base.update(overrides)
    return Envelope(**base)  # type: ignore[arg-type]


# —— §4.4 的六字段，一个都不许少 ——


def test_the_six_design_doc_fields_are_all_present():
    fields = set(Envelope.__dataclass_fields__)
    assert {
        "packet_id",
        "run_id",
        "kind",
        "produced_by",
        "basis",
        "payload",
    } <= fields


def test_deleted_envelope_fields_stay_deleted():
    """§4.4 明确删掉了 `confidence` 与 `emitted_refs`——它们没有消费者，加了就是第二真相源。"""

    for gone in ("confidence", "emitted_refs", "score", "duration_ms"):
        assert gone not in Envelope.__dataclass_fields__


def test_envelope_carries_the_validated_packet_and_the_arrival_report():
    """信封同时装三样，缺一不可：

    - `packet`：校验后的报文（业务用它）
    - `overflow` / `discarded`：这一趟**没进卡片**的那些条（留痕用它，`02` §11.0.35）
    以前 `overflow` / `discarded` 挂在各 agent 的 Outcome 上、信封是另一份载体，
    现在只有信封一处。
    """

    assert an_envelope().packet == Roster()
    assert an_envelope().overflow == ()
    assert an_envelope().discarded == ()


# —— 唯一性 ——


def test_packet_id_format_is_readable_and_carries_the_discriminator():
    assert (
        make_packet_id("r-1", "seat_review", "seat.data_scale")
        == "pkt-r-1-seat_review-seat.data_scale"
    )
    assert (
        make_packet_id("r-1", "seat_review", "seat.data_scale", slot="p2a1")
        == "pkt-r-1-seat_review-seat.data_scale-p2a1"
    )


def test_the_same_slot_produces_the_same_id_but_different_slots_do_not():
    """槽位是协调者给的"第几趟第几次"——同一槽位可复现，不同槽位必须不同。"""

    first = make_packet_id("r-1", "seat_review", "seat.x", slot="p1a1")
    again = make_packet_id("r-1", "seat_review", "seat.x", slot="p1a1")
    second_pass = make_packet_id("r-1", "seat_review", "seat.x", slot="p2a1")
    retry = make_packet_id("r-1", "seat_review", "seat.x", slot="p1a2")
    assert first == again
    assert len({first, second_pass, retry}) == 3


def test_no_two_produced_by_in_one_run_can_collide_under_the_default_scheme():
    """默认（无槽位）也必须靠 produced_by 区分开——否则同席位两趟会撞。"""

    ids = {
        make_packet_id("r-1", kind, produced)
        for kind in ("claims", "seat_review", "integration", "memory_curation")
        for produced in ("seat.permissions", "seat.tech_approach", "integrator")
    }
    assert len(ids) == 12


@pytest.mark.parametrize("missing", ["packet_id", "run_id", "kind", "produced_by"])
def test_the_four_identity_fields_are_mandatory(missing: str):
    with pytest.raises(ValueError, match=missing):
        an_envelope(**{missing: ""})
    with pytest.raises(ValueError, match=missing):
        an_envelope(**{missing: None})  # type: ignore[arg-type]


# —— basis：模型说了什么，不是我们替它说什么 ——


def test_basis_comes_from_the_payload_when_the_model_wrote_one():
    assert basis_of({"basis": "读了 s5", "opinions": []}) == "读了 s5"


def test_basis_is_empty_when_the_packet_has_none():
    assert basis_of({"dispositions": []}) == ""


def test_basis_ignores_a_non_string_value_instead_of_crashing():
    assert basis_of({"basis": ["读了 s5"]}) == ""


@pytest.mark.parametrize("junk", [["不是对象"], "字符串", 7, None])
def test_non_mapping_payload_degrades_to_no_basis(junk: object):
    """补壳之前的形状可能是数组 / 字符串 / 数字。崩在信封这一行最致命：
    它绕过所有重试与 HTTP 处理器，轮次会永远停在"生成中"且没记失败。"""

    assert basis_of(junk) == ""


def test_envelope_refuses_an_empty_packet_id_even_when_a_slot_was_given():
    """槽位给了也救不了空 id——`make_packet_id` 永远先拼上 `pkt-` 前缀，
    但直接构造 `Envelope` 时那条不变式要自己站得住。"""

    with pytest.raises(ValueError, match="packet_id"):
        an_envelope(packet_id="")
