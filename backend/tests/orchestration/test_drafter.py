"""M28 成稿器：模型返回 JSON → 冻结 Schema 校验 → 填充卡。"""

import asyncio
import json
import os
from datetime import UTC, datetime

import pytest

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import BlockOpKind, CardKind, ContextBlockSource, RoundEntry, RoundPhase
from pmstudio.contracts.models.card import Card
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import ContextBlock, RoundRegion
from pmstudio.orchestration.drafter import Drafter

AT = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)


class _FakeHarness:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def complete(
        self, model_ref: str, messages: list[PromptMessage], response_format: dict | None = None,
    ) -> str:
        return self._reply


class _FakeIds:
    def __init__(self) -> None:
        self._counter = 0

    def new_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter}"


def _round_region(selected: list[str] | None = None) -> RoundRegion:
    return RoundRegion(
        round_id="rnd_1",
        project_id="prj_1",
        entry=RoundEntry.MAIN,
        user_input="细化功能清单",
        scope=Scope(selected_fields=tuple(selected) if selected else ()),
        phase=RoundPhase.RESTATING,
    )


def test_valid_json_object_produces_fill_card() -> None:
    """合法 JSON 对象 → 填充卡。"""
    data = {"功能清单": "支持讨论候选", "目标": "做一个好工具"}
    harness = _FakeHarness(json.dumps(data, ensure_ascii=False))
    drafter = Drafter(harness, _FakeIds())

    card = asyncio.run(drafter.draft_fill_card(_round_region(), []))

    assert isinstance(card, Card)
    assert card.kind is CardKind.FILL
    assert len(card.proposals) == 2
    assert card.proposals[0].target_label == "功能清单"
    assert card.proposals[0].op is BlockOpKind.APPEND
    assert card.proposals[0].content == "支持讨论候选"


def test_invalid_json_is_rejected() -> None:
    """模型返回非 JSON → ContractViolation。"""
    harness = _FakeHarness("这不是 JSON")
    drafter = Drafter(harness, _FakeIds())

    with pytest.raises(ContractViolation):
        asyncio.run(drafter.draft_fill_card(_round_region(), []))


def test_invalid_proposal_schema_is_rejected() -> None:
    """空 target_label → ContractViolation。"""
    data = {"": "内容"}  # target_label 为空
    harness = _FakeHarness(json.dumps(data, ensure_ascii=False))
    drafter = Drafter(harness, _FakeIds())

    with pytest.raises(ContractViolation, match="校验失败"):
        asyncio.run(drafter.draft_fill_card(_round_region(), []))


def test_json_with_code_fence_is_parsed() -> None:
    """模型可能包裹 ```json ... ``` → 正确提取。"""
    data = {"功能清单": "内容A"}
    raw = "```json\n" + json.dumps(data, ensure_ascii=False) + "\n```"
    harness = _FakeHarness(raw)
    drafter = Drafter(harness, _FakeIds())

    card = asyncio.run(drafter.draft_fill_card(_round_region(), []))
    assert len(card.proposals) == 1
    assert card.proposals[0].target_label == "功能清单"


def test_scope_filters_fields() -> None:
    """有选区时，只生成选区内的字段。"""
    data = {"功能清单": "内容A", "风险": "内容B", "目标": "内容C"}
    harness = _FakeHarness(json.dumps(data, ensure_ascii=False))
    drafter = Drafter(harness, _FakeIds())

    card = asyncio.run(drafter.draft_fill_card(_round_region(selected=["功能清单", "目标"]), []))
    labels = {p.target_label for p in card.proposals}
    assert labels == {"功能清单", "目标"}
