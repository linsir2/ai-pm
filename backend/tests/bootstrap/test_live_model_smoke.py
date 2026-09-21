"""真链路冒烟：有密钥就真的调一次模型，把「输入 → 复述 → 填充卡 → 写入 → 文档多一版」跑通。

**不给密钥就跳过**——无密钥那条路由 `FakeHarness` 覆盖，全量测试照样全绿。

它不是回归测试（网络与模型输出都不确定，换个模型答案就变），而是**人工核验入口**：

    DEEPSEEK_API_KEY=sk-... uv run pytest tests/bootstrap/test_live_model_smoke.py -v -s

密钥只从环境变量读（C10 `ModelBody.api_key_env` 那条口径）：**测试里不写死任何密钥**。
种子里的默认模型条目是 dashscope，这里在运行时把它换成"有密钥的那一家"——
注册中心里"唯一一条 active 的 model 条目 = 默认模型"（DECISIONS §20 的解析规则），
所以换法是「删掉种子那条、注册一条真的」，**不改 `seeds/` 里的数据**。
"""

import asyncio
import os
from pathlib import Path

import pytest

from pmstudio.bootstrap.runtime import Runtime, build_runtime_sync
from pmstudio.contracts.enums import (
    CardGroupState,
    CardKind,
    CardStatus,
    ProposalState,
    RegionName,
    RegistryKind,
    RegistryOwner,
    RoundEntry,
    RoundPhase,
    VersionTrigger,
)
from pmstudio.contracts.models.card import CardAnswer
from pmstudio.contracts.models.registry import RegistryEntry
from pmstudio.contracts.models.scope import Scope

# `(环境变量名, 要换成的模型名)`；`None` = 种子里那条本来就是这家，不用换。
LIVE_PROVIDERS: tuple[tuple[str, str | None], ...] = (
    ("DEEPSEEK_API_KEY", "deepseek/deepseek-chat"),
    ("DASHSCOPE_API_KEY", None),
)

DEFAULT_MODEL_ID = "reg_model_default"
# 保守窗口：与种子里的默认值一致，M24 的预算口径不变（DeepSeek 的真实窗口比它大）
LIVE_CONTEXT_WINDOW = 32768
PROMPT = "把功能清单细化成可验收的行为规则"


def _live_provider() -> tuple[str, str | None] | None:
    for key_env, model in LIVE_PROVIDERS:
        if os.environ.get(key_env):
            return key_env, model
    return None


async def _switch_default_model(runtime: Runtime, key_env: str, model: str) -> None:
    """把默认模型条目换成有密钥的那一家（`seeds/` 里的数据一个字不动）。"""
    await runtime.registry.remove(DEFAULT_MODEL_ID)
    await runtime.registry.register(
        RegistryEntry(
            id=DEFAULT_MODEL_ID,
            kind=RegistryKind.MODEL,
            name=f"Live smoke · {model}",
            owner=RegistryOwner.PRESET,
            content={
                "model": model,
                "api_key_env": key_env,
                "context_window_tokens": LIVE_CONTEXT_WINDOW,
                "timeout_seconds": 60,
            },
        )
    )


@pytest.mark.skipif(
    _live_provider() is None,
    reason="没有模型密钥（DEEPSEEK_API_KEY / DASHSCOPE_API_KEY），真链路冒烟跳过",
)
def test_live_model_end_to_end_writes_a_document_version(tmp_path: Path) -> None:
    provider = _live_provider()
    assert provider is not None
    key_env, model = provider

    runtime = build_runtime_sync(tmp_path / "live.sqlite3")
    try:
        if model is not None:
            asyncio.run(_switch_default_model(runtime, key_env, model))

        project = asyncio.run(
            runtime.project_service.create_project("reg_tpl_initial", "Live smoke")
        )

        # ① 开轮：M13 组装 → M24 裁剪 → M8 复述理解（第一次真模型调用）
        asyncio.run(
            runtime.round_driver.start_round(project.project_id, RoundEntry.MAIN, PROMPT, Scope())
        )
        understanding = asyncio.run(runtime.board.read(RegionName.CARD_GROUP))
        assert understanding is not None
        assert understanding.state is CardGroupState.ANSWERING
        card = understanding.cards[0]
        assert card.kind is CardKind.UNDERSTANDING
        assert card.prompt.strip(), "模型没吐出复述文本"
        print(f"\n[真链路] 理解卡：{card.prompt[:200]}")

        # ② 确认理解卡：M28 成稿（第二次真模型调用，要求 JSON 对齐冻结 Schema）
        fill_group = asyncio.run(
            runtime.round_driver.submit_cards(
                understanding.group_id,
                [CardAnswer(card_id=card.card_id, verdict="confirm", status=CardStatus.ANSWERED)],
            )
        )
        fill_card = fill_group.cards[0]
        assert fill_card.kind is CardKind.FILL
        assert fill_card.proposals, "模型没有产出任何提案（M28 要求返回 JSON 数组）"
        print(f"[真链路] 填充卡 {len(fill_card.proposals)} 条提案：")
        for proposal in fill_card.proposals:
            print(f"  - {proposal.target_label} / {proposal.op.value}: {proposal.content[:80]}")

        # ③ 确认填充卡：M20 事务写入 → doc.changed → 轮次 done
        answer = CardAnswer(
            card_id=fill_card.card_id,
            verdict="confirm",
            status=CardStatus.ANSWERED,
            proposal_states={
                proposal.proposal_id: ProposalState.KEPT for proposal in fill_card.proposals
            },
        )
        confirmed = asyncio.run(
            runtime.round_driver.submit_cards(fill_group.group_id, [answer])
        )
        assert confirmed.state is CardGroupState.CONFIRMED

        # ④ 核验：文档多了一版、那一版由这次确认产生、AI 写入留痕（I3）
        versions = runtime.ledger.list_versions(project.doc_id)
        assert versions, "写入之后文档应当有一版"
        latest = max(versions, key=lambda version: version.seq)
        assert latest.trigger is VersionTrigger.SUBMIT
        assert latest.group_id == fill_group.group_id

        written = [block for block in runtime.ledger.read_blocks(project.doc_id) if block.is_ai_written]
        assert written, "没有任何块带 source_card_id——AI 写入没留痕"

        round_region = asyncio.run(runtime.board.read(RegionName.ROUND))
        assert round_region is not None
        assert round_region.phase is RoundPhase.DONE

        print(
            f"[真链路] 文档 {len(versions)} 版（最新 seq={latest.seq}，trigger=submit）、"
            f"AI 写入 {len(written)} 块、轮次 {round_region.phase.value}"
        )
    finally:
        runtime.close()
