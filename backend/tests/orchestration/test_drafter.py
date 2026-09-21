"""M28 成稿器：模型返回 JSON → 冻结 Schema 校验 → 填充卡。"""

import asyncio
import json
import os
from datetime import UTC, datetime

import pytest

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from pmstudio.common.errors import ContractViolation, GenerationFailure
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
        self.last_messages: list[PromptMessage] = []

    async def complete(
        self, model_ref: str, messages: list[PromptMessage], response_format: dict | None = None,
    ) -> str:
        self.last_messages = list(messages)
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


def _evidence_block(evidence_id: str, ref: str, content: str = "依据内容", ref_version: int = 1):
    return ContextBlock(
        source=ContextBlockSource.DOCUMENT,
        ref=ref,
        content=content,
        priority=80,
        evidence_id=evidence_id,
        ref_version=ref_version,
    )


def test_citations_from_evidence_ids_are_mapped() -> None:
    """模型返回 {字段: {content, citations: [证据号]}} → Proposal.citations 被映射为真实 Citation。

    引用归因的前提：citations 必须来自证据集合（M13 编号清单），Drafter 反查 ref / 版本。
    """
    from pmstudio.contracts.enums import CitationTargetType
    from pmstudio.contracts.models.citation import Citation

    data = {"功能清单": {"content": "要能支持登录", "citations": ["E1"]}}
    harness = _FakeHarness(json.dumps(data, ensure_ascii=False))
    drafter = Drafter(harness, _FakeIds())
    evidence = [_evidence_block("E1", "blk_0", "现有功能清单为空")]

    card = asyncio.run(drafter.draft_fill_card(_round_region(), evidence))

    assert len(card.proposals) == 1
    proposal = card.proposals[0]
    assert proposal.content == "要能支持登录"
    assert len(proposal.citations) == 1
    assert proposal.citations[0] == Citation(
        target_type=CitationTargetType.BLOCK,
        target_id="blk_0",
        target_version=1,
    )


def test_plain_string_content_keeps_no_citations() -> None:
    """向后兼容：模型返回纯字符串 value → citations 为空（引用归因是可选增强）。"""
    data = {"功能清单": "只要内容"}
    harness = _FakeHarness(json.dumps(data, ensure_ascii=False))
    drafter = Drafter(harness, _FakeIds())

    card = asyncio.run(drafter.draft_fill_card(_round_region(), []))

    assert card.proposals[0].content == "只要内容"
    assert card.proposals[0].citations == ()


def test_unknown_evidence_id_is_rejected() -> None:
    """模型引用了证据集合之外的编号 → I21：引用必须能定位，直接拒。"""
    data = {"功能清单": {"content": "内容", "citations": ["E99"]}}
    harness = _FakeHarness(json.dumps(data, ensure_ascii=False))
    drafter = Drafter(harness, _FakeIds())
    evidence = [_evidence_block("E1", "blk_0")]

    with pytest.raises(ContractViolation, match="证据"):
        asyncio.run(drafter.draft_fill_card(_round_region(), evidence))


def test_prompt_includes_numbered_evidence_list() -> None:
    """prompt 里必须带上编号证据清单——模型引用时只能从这里面取。"""
    evidence = [
        _evidence_block("E1", "blk_0", "现有功能清单为空", ref_version=2),
        _evidence_block("E2", "blk_1", "目标是好工具", ref_version=1),
    ]
    harness = _FakeHarness(json.dumps({"功能清单": "内容"}, ensure_ascii=False))
    drafter = Drafter(harness, _FakeIds())

    asyncio.run(drafter.draft_fill_card(_round_region(), evidence))

    assert len(harness.last_messages) == 1
    text = harness.last_messages[0].text
    assert "[E1]" in text and "现有功能清单为空" in text
    assert "[E2]" in text and "目标是好工具" in text


def test_citation_claim_target_is_rejected_for_fill_card() -> None:
    """C7 填充卡不允许 claim 引用（引用归因：claim 只在编排层内部）。"""
    data = {"功能清单": {"content": "内容", "citations": ["C1"]}}
    harness = _FakeHarness(json.dumps(data, ensure_ascii=False))
    drafter = Drafter(harness, _FakeIds())
    evidence = [
        _evidence_block("E1", "blk_0"),
        # 编排层主张不在证据集合里——模型无权限引用
    ]

    with pytest.raises(ContractViolation, match="证据"):
        asyncio.run(drafter.draft_fill_card(_round_region(), evidence))


# --- 真实模型输出的形态容差 + "不静默产空卡" -------------------------------------
#
# 这五条来自 R1.6 的真链路冒烟：同一段 prompt，DeepSeek 一次返回了以字段名为 key 的对象
# （9 条提案，正常），另一次返回的 key 一个都落不进允许字段，旧实现把它们全部 `continue`
# 掉，最后构造出 `proposals=()` 的填充卡——而 C7 要求填充卡**必须有提案数组**，
# 于是用户看到的是裸 `ValidationError`（500）：既不知道卡在哪一步，也不知道能不能重试（AC12）。


def test_proposal_array_shape_is_accepted() -> None:
    """模型按 DECISIONS §20 的原始口径返回**提案数组** → 一样能成稿。

    prompt 里要的是"以字段名为 key 的对象"，但换一家模型就可能给数组。
    能不能落位取决于**每条提案有没有字段名与内容**，而不是模型长什么样。
    """
    raw = json.dumps(
        [
            {"target_label": "功能清单", "op": "append", "content": "支持讨论候选"},
            {"target_label": "目标", "content": "做一个好工具"},
        ],
        ensure_ascii=False,
    )
    drafter = Drafter(_FakeHarness(raw), _FakeIds())

    card = asyncio.run(drafter.draft_fill_card(_round_region(), []))

    assert [proposal.target_label for proposal in card.proposals] == ["功能清单", "目标"]
    assert card.proposals[0].op is BlockOpKind.APPEND


def test_wrapped_proposal_array_is_unwrapped() -> None:
    """模型自己包一层 `{"proposals": [...]}` → 也要认，不许当成"不认识的 key"丢掉。"""
    raw = json.dumps(
        {"proposals": [{"target_label": "功能清单", "content": "包了一层"}]},
        ensure_ascii=False,
    )
    drafter = Drafter(_FakeHarness(raw), _FakeIds())

    card = asyncio.run(drafter.draft_fill_card(_round_region(), []))

    assert [proposal.target_label for proposal in card.proposals] == ["功能清单"]


def test_replace_op_from_the_model_is_honored() -> None:
    """模型说 `replace` 就 `replace`——`op` 不许被静默丢掉再当成 `append`。"""
    raw = json.dumps(
        [{"target_label": "目标", "op": "replace", "content": "整段替换"}],
        ensure_ascii=False,
    )
    drafter = Drafter(_FakeHarness(raw), _FakeIds())

    card = asyncio.run(drafter.draft_fill_card(_round_region(), []))

    assert card.proposals[0].op is BlockOpKind.REPLACE


def test_keys_outside_the_allowed_fields_are_a_loud_generation_failure() -> None:
    """模型返回了 JSON，但 key 一个都不在允许字段里 → **大声失败**，不产空卡。

    这就是真链路上那次失败：报错要带上模型实际给的 key，否则查不出是哪一步坏、该怎么调 prompt。
    """
    raw = json.dumps({"分析结果": "我先想了想，但没给字段名"}, ensure_ascii=False)
    drafter = Drafter(_FakeHarness(raw), _FakeIds())

    with pytest.raises(GenerationFailure) as exc_info:
        asyncio.run(drafter.draft_fill_card(_round_region(selected=["功能清单"]), []))

    assert exc_info.value.retryable is True
    assert "分析结果" in str(exc_info.value)
    assert "功能清单" in str(exc_info.value), "报错要说清允许哪些字段"


def test_nothing_usable_is_a_loud_generation_failure() -> None:
    """全空白内容、空对象 → 同样算生成失败：填充卡不可能"一条提案都没有"。"""
    for raw in ('{"功能清单": "   ", "目标": ""}', "{}"):
        drafter = Drafter(_FakeHarness(raw), _FakeIds())
        with pytest.raises(GenerationFailure) as exc_info:
            asyncio.run(drafter.draft_fill_card(_round_region(), []))
        assert exc_info.value.retryable is True
