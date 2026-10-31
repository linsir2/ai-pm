"""锁消费链守卫，补充真 PG 运行证据；不作为业务模型验收。"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "src/pmstudio"
FILES = ("use_cases.py", "round_context.py", "web_research.py", "project_research.py")


def calls(node, receiver, methods):
    return sorted((child for child in ast.walk(node) if isinstance(child, ast.Call)
                   and isinstance(child.func, ast.Attribute)
                   and child.func.attr in methods
                   and isinstance(child.func.value, ast.Attribute)
                   and child.func.value.attr == receiver), key=lambda child: child.lineno)


@pytest.mark.parametrize("filename", FILES)
def test_every_existing_ledger_write_consumer_locks_its_first_read(filename):
    tree = ast.parse((ROOT / "application" / filename).read_text())
    checked = 0
    for scope in (node for node in ast.walk(tree) if isinstance(node, ast.With)):
        if not calls(scope, "_evidence_writer", {"save"}):
            continue
        reads = calls(scope, "_evidence", {"get", "get_for_update"})
        assert reads, f"{filename}:{scope.lineno}: ledger 必须在发 E 的 UoW 内读取"
        assert reads[0].func.attr == "get_for_update", f"{filename}:{reads[0].lineno} 未首次锁账本"
        round_reads = calls(scope, "_rounds", {"get", "get_for_update"})
        if round_reads:
            assert round_reads[0].func.attr == "get_for_update"
            assert round_reads[0].lineno < reads[0].lineno, "Round → Ledger，不允许反序"
        document_reads = calls(scope, "_documents", {"get", "get_for_update"})
        if document_reads:
            assert document_reads[0].func.attr == "get_for_update"
            assert document_reads[0].lineno < reads[0].lineno, "Document → Ledger"
        exploration_reads = calls(scope, "_explorations", {"get"})
        if exploration_reads:
            control_writes = calls(scope, "_exploration_writer", {"save"})
            assert control_writes[0].lineno < reads[0].lineno, "控制行 UPDATE → Ledger"
        checked += 1
    assert checked, "守卫必须覆盖真实 E consumer"


def test_new_round_insert_precedes_ledger_lock_and_lease_fencing():
    tree = ast.parse((ROOT / "application/use_cases.py").read_text())
    start = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "StartRound")
    create = next(node for node in start.body
                  if isinstance(node, ast.AsyncFunctionDef) and node.name == "create")
    scope = next(node for node in ast.walk(create) if isinstance(node, ast.With))
    insert = calls(scope, "_round_writer", {"save"})[0]
    ledger = calls(scope, "_evidence", {"get", "get_for_update"})[0]
    assert insert.lineno < ledger.lineno, "新 Round 的 INSERT 建立控制行后才能拿 Ledger 锁"
