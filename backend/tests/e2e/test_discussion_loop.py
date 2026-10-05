"""讨论接进主闭环（M9-T7）：自由主题 → 追问 → 总结成卡 → 收下 → 带进某一轮
→ 账本里那条 `simulated` 编号的外部指称**全串相等**。

只脚本化模型；其余全真：Harness 的真领域 + 真用例 + 真图（`RoundRunner`）。
"""

import json

from pmstudio.application.commands import RoundCommand
from pmstudio.domain.provenance.evidence import EvidenceKind
from pmstudio.domain.shared.ids import DiscussionId
from tests.support.builders import PROJECT, document_with_feature
from tests.support.harness import Harness

SAY_REPLY = json.dumps(
    {"text": "导出范围先收到本店本月，全量另开一轮说。", "based_on": []},
    ensure_ascii=False,
)

CARDS_REPLY = json.dumps(
    {
        "cards": [
            {
                "statement": "导出先收窄到本店本月，并给一个按客户名找单的入口",
                "grounding": "invented",
                "based_on": [],
            }
        ]
    },
    ensure_ascii=False,
)

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了文档与本轮输入",
        "claims": [
            {
                "text": "讨论里说的导出按客户名找单，先记下来",
                "raw_span": "讨论说导出按客户名找单",
                "label": "need",
                "zone": "body",
                "section_id": "s10",
            }
        ],
        "seats": [{"seat_id": "seat.data_scale", "why": "与量级有关"}],
    },
    ensure_ascii=False,
)


def _harness() -> Harness:
    harness = Harness(
        replies={
            "discussion": SAY_REPLY,
            "discussion_cards": CARDS_REPLY,
            "claims": CLAIMS_REPLY,
        }
    )
    harness.seed_project()
    harness.documents.seed(document_with_feature())
    return harness


async def test_free_topic_thread_to_kept_card_flows_into_a_round_as_simulated() -> None:
    """主链：自由主题开聊 → 追问两轮 → 总结 → 收下第一张卡 → 当材料带进下一轮。"""

    harness = _harness()
    started = await harness.start_discussion()(
        project_id=PROJECT, topic="导出找单太慢", role_id="dlg.challenger", question=None
    )
    did = started.thread_id
    assert started.status == "open"
    assert started.turns == ()

    said = await harness.say_to_thread()(
        discussion_id=DiscussionId(did), text="我要做导出找单"
    )
    assert [turn.role for turn in said.turns] == ["user", "assistant"]
    said = await harness.say_to_thread()(
        discussion_id=DiscussionId(did), text="全量导出也得能用"
    )
    # 追问两轮 = 四个 turn，user/assistant 交替；第三次说之前不会被上限挡住
    assert [turn.role for turn in said.turns] == ["user", "assistant"] * 2

    assembled = await harness.assemble_cards()(discussion_id=DiscussionId(did))
    assert [card.card_id for card in assembled.cards] == ["C1"]

    kept = await harness.keep_discussion_card()(
        discussion_id=DiscussionId(did), card_id="C1"
    )
    card = next(item for item in kept.cards if item.card_id == "C1")
    assert card.kept_ref
    material_id = card.kept_ref

    view = await harness.runner().run(
        RoundCommand(
            kind="start",
            project_id=str(PROJECT),
            user_input="讨论说导出按客户名找单，先记下来",
            material_ids=(material_id,),
        )
    )

    assert [item.material_id for item in view.materials] == [material_id]
    ledger = harness.evidences.get(PROJECT)
    simulated = [item for item in ledger.evidences() if item.kind is EvidenceKind.SIMULATED]
    assert len(simulated) == 1
    # 账本里的外部指称沿用 `_external_ref` 的约定：非 URL 的 source_ref 追加块号。
    # **全串相等**：discussion 命名空间 → 线程号 → 卡号 → 块号，缺一段就说明拼错了。
    assert simulated[0].source.external_ref == f"discussion:{did}#C1#c00"