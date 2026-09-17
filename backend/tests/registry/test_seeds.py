"""种子数据：`backend/seeds/*.json` 是数据不是代码（directory.md §2）。"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import RegistryKind, RegistryOwner
from pmstudio.contracts.models.registry import TemplateBody
from pmstudio.contracts.skeleton.events import Event
from pmstudio.registry.entries import InMemoryRegistry
from pmstudio.registry.seeds import load_entries, seed

# tests/registry/ → tests/ → backend/
SEEDS = Path(__file__).resolve().parents[2] / "seeds"

PRD_APPENDIX_B_LABELS = (
    "项目是什么",
    "目标",
    "用户与场景",
    "功能清单",
    "依赖与边界",
    "风险",
    "验收条件",
    "待确认问题",
    "开放问题",
)
PRD_APPENDIX_B_REQUIRED = frozenset(
    {"项目是什么", "目标", "用户与场景", "功能清单", "依赖与边界", "验收条件"}
)
# 「待确认问题」在 PRD 里是**自动维护**（跳过卡落这里）、「开放问题」是**用户维护**——
# 两者都不该让 M28 产补信息卡，所以 `required = false`。这不是"可以不填"，是"不由 AI 填"。


class _Bus:
    """`register` 不发事件（只有 `update_status` 发），所以这里只需要一个形状对的替身。"""

    async def publish(self, event: Event, *, producer: object) -> None:
        raise AssertionError("灌种子不该发事件")


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 17, 10, 0, tzinfo=UTC)


def _registry() -> InMemoryRegistry:
    return InMemoryRegistry(_Bus(), _Clock())  # type: ignore[arg-type]


def test_seed_file_loads_the_initial_template() -> None:
    entries = load_entries(SEEDS)

    assert [entry.id for entry in entries] == ["reg_tpl_initial"]
    assert entries[0].kind is RegistryKind.TEMPLATE
    assert entries[0].owner is RegistryOwner.PRESET


def test_template_labels_match_prd_appendix_b() -> None:
    """模板内容与 PRD 附录 B 逐字同序——它是文档分区的唯一依据。"""
    body = TemplateBody.model_validate(load_entries(SEEDS)[0].content)

    assert [field.label for field in body.fields] == list(PRD_APPENDIX_B_LABELS)
    assert {field.label for field in body.fields if field.required} == PRD_APPENDIX_B_REQUIRED


def test_seeded_ids_are_stable_across_loads() -> None:
    """种子条目的 id 由数据文件给定：`Document.template_id` 要跨重启指得到同一个模板（I21）。"""
    first = load_entries(SEEDS)
    second = load_entries(SEEDS)

    assert [entry.id for entry in first] == [entry.id for entry in second]


def test_seeding_registers_every_entry() -> None:
    registry = _registry()

    registered = asyncio.run(seed(registry, SEEDS))

    assert registered == ("reg_tpl_initial",)
    assert asyncio.run(registry.resolve(RegistryKind.TEMPLATE, "reg_tpl_initial")).name


def test_a_broken_seed_is_refused_at_load_time(tmp_path: Path) -> None:
    """坏掉的种子走的是同一条校验路径：登记时炸，不留到建项目才炸。"""
    broken = dict(json.loads((SEEDS / "template.initial.json").read_text(encoding="utf-8")))
    broken["content"] = {
        "fields": [
            {"label": "目标", "required": True},
            {"label": "目标", "required": False},
        ]
    }
    (tmp_path / "broken.json").write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ContractViolation):
        asyncio.run(seed(_registry(), tmp_path))
