"""R0.6 契约冻结：17 个契约挂着"已冻结"标记，形状锁对不上就是有人没写变更记录。

形状锁在 `pmstudio/contracts/frozen.py`（代码），冻结的规则写在 `CONTRACTS.md` §0.2（文档）。
这里只做一件事：拿锁对现实。对不上时不只说"错了"，而是把改契约的顺序摆出来。
"""

import re
from datetime import date
from pathlib import Path

import pytest

from pmstudio.contracts import frozen
from pmstudio.contracts.frozen import (
    CHANGE_RECORDS,
    CONTRACT_SHAPES,
    CONTRACTS_BY_ID,
    FROZEN_CONTRACTS,
    INTERFACE_SHAPE_REASONS,
    INTERFACE_SHAPES,
    drift_detail,
    live_interface_shape,
    live_shape,
    main,
    undeclared_models,
)

CONTRACT_IDS = tuple(f"C{number}" for number in range(1, 18))


def _change_record(record_id: str) -> frozen.ContractChange:
    for record in CHANGE_RECORDS:
        if record.record_id == record_id:
            return record
    raise AssertionError(f"没有这条变更记录：{record_id}")


def _model_order(shape: tuple[str, ...]) -> list[str]:
    """形状行里模型的出场顺序（同一个模型的行必须连在一起）。"""
    order: list[str] = []
    for line in shape:
        model = line.split(".", 1)[0]
        if not order or order[-1] != model:
            order.append(model)
    return order


def _drift_message(key: str, locked: tuple[str, ...], live: tuple[str, ...]) -> str:
    steps = (
        f"{key} 的形状锁与实现不一致。契约已冻结，改它要先写变更记录：\n"
        "1) 在 pmstudio/contracts/frozen.py 的 CHANGE_RECORDS 追加一条"
        "（日期 / CONTRACTS.md 版本 / 为什么动 / 动了哪些契约）\n"
        f"2) 改契约模型，并把形状锁里的 {key} 那几行同步过来\n"
        "3) 把这个契约的 frozen_in 指向新记录（改的是文档口径，也要记进 CONTRACTS.md 的变更记录）\n"
        "差在哪：\n"
    )
    return steps + "\n".join(drift_detail(locked, live))


def test_seventeen_contracts_are_marked() -> None:
    """R0.6 的退出条件就是这一条：17 个契约一个不少、一个不多、不重号。"""
    ids = tuple(contract.contract_id for contract in FROZEN_CONTRACTS)
    assert ids == CONTRACT_IDS


def test_contract_entries_carry_their_home() -> None:
    for contract in FROZEN_CONTRACTS:
        assert contract.title.strip()
        assert contract.home.strip()
        assert contract.frozen_in in {record.record_id for record in CHANGE_RECORDS}


def test_only_c4_defers_its_shape() -> None:
    """C4 是层内契约，形状随 R1 的编排层一起定。**不假装它有形状**，但也不把它从 17 个里拿掉。"""
    deferred = [contract for contract in FROZEN_CONTRACTS if not contract.frozen]
    assert [contract.contract_id for contract in deferred] == ["C4"]
    assert deferred[0].models == ()
    assert deferred[0].note.strip(), "未冻形状必须写明为什么、什么时候补"

    for contract in FROZEN_CONTRACTS:
        if contract.frozen:
            assert contract.models, f"{contract.contract_id} 说冻了，却没有任何模型"
            assert contract.contract_id in CONTRACT_SHAPES


def test_c4_stays_out_of_the_contracts_package() -> None:
    """层内契约不进 `contracts/`（directory.md §3.1）：C4 名下不该有模型住在那里。"""
    assert CONTRACTS_BY_ID["C4"].models == ()
    assert "AgentPacket" not in frozen.declared_models()


@pytest.mark.parametrize("contract_id", sorted(CONTRACT_SHAPES))
def test_contract_shape_lock_matches_reality(contract_id: str) -> None:
    locked = CONTRACT_SHAPES[contract_id]
    live = live_shape(contract_id)
    assert live == locked, _drift_message(contract_id, locked, live)


@pytest.mark.parametrize("name", sorted(INTERFACE_SHAPES))
def test_interface_shape_lock_matches_reality(name: str) -> None:
    locked = INTERFACE_SHAPES[name]
    live = live_interface_shape(name)
    assert live == locked, _drift_message(name, locked, live)


def test_shape_lock_is_grouped_by_model() -> None:
    """一个模型的字段连在一起、按声明顺序出场——锁是给人读的，行序不能乱。"""
    for contract_id, shape in CONTRACT_SHAPES.items():
        order = _model_order(shape)
        assert len(order) == len(set(order)), f"{contract_id} 的同一个模型被拆成了两段"
        assert order == list(CONTRACTS_BY_ID[contract_id].models), f"{contract_id} 的行序与声明不符"


def test_shape_lines_are_readable() -> None:
    """形状行的格式：`模型.字段: 类型 [约束] = 默认值`。"""
    for shape in (*CONTRACT_SHAPES.values(), *INTERFACE_SHAPES.values()):
        for line in shape:
            head, _, tail = line.partition(": ")
            model, dot, field = head.partition(".")
            assert model and dot and field, line
            assert model[0].isupper(), line
            assert tail.strip(), line


def test_every_contract_model_is_declared() -> None:
    """`contracts/` 里没有第三个去处：要么挂在某个 C 编号下，要么挂在一个接口形状里。"""
    ghosts, strays = undeclared_models()
    assert not ghosts, f"锁里登记了现实中不存在的模型：{sorted(ghosts)}"
    assert not strays, (
        f"这些模型住在 contracts/ 里却没进锁：{sorted(strays)}——"
        "要么挂到某个 C 编号下，要么加进 INTERFACE_SHAPES 并写明理由（frozen.py）"
    )


def test_interface_shapes_all_have_a_reason() -> None:
    assert set(INTERFACE_SHAPES) == set(INTERFACE_SHAPE_REASONS)
    for name, reason in INTERFACE_SHAPE_REASONS.items():
        assert reason.strip(), name


def test_change_records_are_well_formed() -> None:
    ids: set[str] = set()
    for record in CHANGE_RECORDS:
        assert record.record_id not in ids, f"变更记录重号：{record.record_id}"
        ids.add(record.record_id)
        assert record.record_id.startswith("CR-")
        date.fromisoformat(record.date)
        assert record.doc_version.strip() and record.summary.strip()
        assert record.contracts, f"{record.record_id} 没写改动了哪些契约"
        assert set(record.contracts) <= set(CONTRACT_IDS)


def test_every_contract_is_named_by_the_record_it_was_frozen_in() -> None:
    """`frozen_in` 指到的那条记录必须**列了它**，否则记了等于没记。"""
    for contract in FROZEN_CONTRACTS:
        assert contract.contract_id in _change_record(contract.frozen_in).contracts


def test_first_freeze_covers_all_seventeen() -> None:
    """首版冻结覆盖 C1–C17：冻结是一次性的基线，不是逐条冻结攒出来的。"""
    assert set(_change_record("CR-001").contracts) == set(CONTRACT_IDS)


def test_the_human_entry_point_reports_clean(capsys: pytest.CaptureFixture[str]) -> None:
    """`python -m pmstudio.contracts.frozen` 是给人看冻结表的入口，顺手也做一次核对。"""
    assert main() == 0
    output = capsys.readouterr().out
    assert "形状锁与代码一致" in output
    assert "覆盖对齐" in output


# --- 文档面同步 ---------------------------------------------------------------
#
# 契约有三层：**文档面**（`CONTRACTS.md`，人读）、**机器面**（`frozen.py` 的形状锁）、
# **实现面**（`contracts/` 里的模型）。机器面 ↔ 实现面由形状锁用例钉着；
# 而文档面 ↔ 机器面**原来一条用例都没有**——CR-008 / CR-009 就是这样只进了锁、没进文档，
# 于是"文档里的字段集是不是最新的"只能靠人记。
#
# 下面三条把这条缝补上。判据只碰结构化行（头部版本号、CR 编号、契约章节），
# 不解析表格排版——文档改个写法不该让测试红。

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_DOC = REPO_ROOT / ".codex" / "docs" / "CONTRACTS.md"

# 机器面加了字段、文档面必须跟着写：`(契约, 模型) -> 那次 CR 新加的字段`。
# 新写一条 CR 时往这张表补一行——这是"文档面不许落后"的显式账本。
DOCUMENTED_FIELD_ADDITIONS: dict[tuple[str, str], tuple[str, ...]] = {
    ("C3", "BlockOp"): ("source_card_id",),
    ("C10", "ModelBody"): ("model", "api_key_env", "timeout_seconds"),
    ("C12", "ContextBlock"): ("evidence_id", "ref_version"),
}


def _contracts_doc_text() -> str:
    assert CONTRACTS_DOC.exists(), (
        f"找不到契约的文档面：{CONTRACTS_DOC}——它是字段级的唯一真相源，不该缺席"
    )
    return CONTRACTS_DOC.read_text(encoding="utf-8")


def _doc_version_token(record: frozen.ContractChange) -> str:
    """`CONTRACTS.md v0.24` → `v0.24`。"""
    return record.doc_version.rsplit(" ", 1)[-1]


def _version_key(record: frozen.ContractChange) -> tuple[int, ...]:
    return tuple(int(part) for part in _doc_version_token(record).lstrip("v").split("."))


def test_every_change_record_is_recorded_in_the_contracts_doc() -> None:
    """变更记录不能只活在代码里：每条 CR 都要在 `CONTRACTS.md` 里查得到。"""
    text = _contracts_doc_text()
    missing = [
        f"{record.record_id}（{record.doc_version}）"
        for record in CHANGE_RECORDS
        if record.record_id not in text or _doc_version_token(record) not in text
    ]
    assert not missing, (
        "这些变更记录没同步到 CONTRACTS.md：" + "、".join(missing) + "\n"
        "冻结的口径是「先写变更记录、文档同步一条」（CONTRACTS.md §0.2）："
        "记录追加进 frozen.py 之后，本文档的变更记录也要跟着写一行"
    )


def test_contracts_doc_header_version_matches_the_latest_record() -> None:
    """头部版本号 = 最新一条记录的 doc_version——两处各说各话，就没人知道该信哪个。"""
    text = _contracts_doc_text()
    header = re.search(r"^- 版本：(v\d+\.\d+)", text, re.MULTILINE)
    assert header, "CONTRACTS.md 头部少了 `- 版本：vX.Y` 这一行"

    latest = max(CHANGE_RECORDS, key=_version_key)
    assert header.group(1) == _doc_version_token(latest), (
        f"文档头写 {header.group(1)}，最新变更记录是 {latest.record_id} 的 "
        f"{_doc_version_token(latest)}"
    )


@pytest.mark.parametrize(("contract_id", "model"), sorted(DOCUMENTED_FIELD_ADDITIONS))
def test_fields_added_by_recent_changes_are_documented(contract_id: str, model: str) -> None:
    """CR 加的字段必须在那个契约的正文里看得见——锁里有、文档里没有，等于没写。"""
    shape = CONTRACT_SHAPES[contract_id]
    assert any(line.startswith(f"{model}.") for line in shape), f"{model} 不在 {contract_id} 的形状锁里"

    text = _contracts_doc_text()
    start = text.find(f"### {contract_id} ")
    assert start >= 0, f"CONTRACTS.md 里找不到 {contract_id} 的章节"
    end = text.find("\n### ", start + 1)
    section = text[start : end if end > 0 else len(text)]

    undocumented = [
        field for field in DOCUMENTED_FIELD_ADDITIONS[(contract_id, model)] if field not in section
    ]
    assert not undocumented, (
        f"{contract_id} 的 {model} 在形状锁里，但文档章节里没写这些字段：{undocumented}"
    )
