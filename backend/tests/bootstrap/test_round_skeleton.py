"""一条端到端的验收：把一轮主闭环从建项目跑到轮末，全程不绕过契约。

它不接真模型（M22 在 R5），但**每一跳都走真实事务、黑板、事件与账本**——这正是审查报告
点出的缺口："契约齐了，但没有一条路被跑过一遍"。R1 每落一个模块，这条用例就多接一段真实现。

放在 `tests/bootstrap/` 而不是 `scripts/`：`backend/` 的顶层结构是冻结的（directory.md §2），
没有脚本目录；而 bootstrap 正是"把实现装配起来"的家。
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.bootstrap.runtime import Runtime, build_runtime_sync
from pmstudio.common.errors import ContractViolation
from pmstudio.common.ids import TimestampIdGenerator
from pmstudio.communication.board import Blackboard, BoardEditor, BoardReader
from pmstudio.contracts.enums import (
    BlockOpKind,
    CardGroupState,
    CardKind,
    CardStatus,
    EventType,
    MemoryStatus,
    MemoryType,
    ProducerIdentity,
    ProposalState,
    RegionName,
    RoundEndReason,
    RoundEntry,
    RoundPhase,
    VersionTrigger,
)
from pmstudio.contracts.invariants import check_proposal_states
from pmstudio.contracts.models.card import Card, CardAnswer, Proposal
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.contracts.models.document import BlockOp
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import ContextRegion, RoundRegion
from pmstudio.contracts.skeleton.events import (
    DocChangedPayload,
    Event,
    MemoryUpdatedPayload,
)
from pmstudio.storage.board_store import BoardStore
from pmstudio.storage.ledger import Ledger

AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
TEMPLATE = "reg_tpl_initial"
NINE_FIELDS = (
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


class FixedClock:
    def now(self) -> datetime:
        return AT


@pytest.fixture
def runtime(tmp_path: Path):
    built = build_runtime_sync(tmp_path / "pmstudio.sqlite3", clock=FixedClock())
    yield built
    built.close()


def _round_region(
    phase: RoundPhase, project_id: str, reason: RoundEndReason | None = None
) -> RoundRegion:
    finished = phase in (RoundPhase.DONE, RoundPhase.FAILED)
    return RoundRegion(
        round_id="rnd_demo",
        project_id=project_id,
        entry=RoundEntry.MAIN,
        user_input="细化一下功能清单",
        scope=Scope(selected_fields=("功能清单",)),
        phase=phase,
        ended_at=AT if finished else None,
        end_reason=reason if finished else None,
    )


def _fill_card() -> Card:
    return Card(
        card_id="crd_fill",
        kind=CardKind.FILL,
        prompt="这次我打算改动 2 处",
        proposals=(
            Proposal(
                proposal_id="prp_1",
                target_label="功能清单",
                op=BlockOpKind.APPEND,
                content="支持把讨论候选带进主闭环",
            ),
            Proposal(
                proposal_id="prp_2",
                target_label="风险",
                op=BlockOpKind.APPEND,
                content="外部模型限流是主要风险",
            ),
        ),
    )


def test_a_whole_round_runs_end_to_end(runtime: Runtime) -> None:
    ledger = Ledger(runtime.db, TimestampIdGenerator())
    blackboard = Blackboard(BoardStore(runtime.db), runtime.event_bus, runtime.clock)
    # 运行时故意不暴露黑板写句柄（I19）：写句柄只交给编排层。R1 会由 bootstrap 单开一个入口，
    # 这里按那时要走的方式手工接一次。
    writer = BoardEditor(blackboard)
    board = BoardReader(blackboard)

    # ── 1. 建项目：项目 + 文档 + 9 个字段块，一次事务（R1.1 起走真实现）
    created = asyncio.run(runtime.project_service.create_project(TEMPLATE, "PM Studio Demo"))
    project_id, doc_id = created.project_id, created.doc_id
    blocks = ledger.read_blocks(doc_id)
    assert [block.schema_label for block in blocks] == list(NINE_FIELDS)
    assert all(block.version == 1 and not block.is_ai_written for block in blocks)

    # ── 2. 开轮；context 区块不发事件（只有 round / card_group 写即广播）
    asyncio.run(writer.open_round("rnd_demo"))
    round_region = _round_region(RoundPhase.ASSEMBLING, project_id)
    asyncio.run(writer.write(RegionName.ROUND, round_region))

    # ── 2b. 组装上下文（M13）→ 按预算裁剪（M24）→ 写进 context 区块（R1.2）
    assembled = asyncio.run(runtime.context_assembler.assemble_context(round_region))
    assert len(assembled.blocks) == 10  # 9 个文档块 + 1 个本轮输入
    assert set(assembled.base_versions) == {block.block_id for block in blocks}
    trimmed = asyncio.run(
        runtime.trimmer.trim(assembled.blocks, project_id, "reg_model_default")
    )
    assert trimmed.dropped == ()  # 默认模型窗口装得下
    asyncio.run(
        writer.write(
            RegionName.CONTEXT,
            ContextRegion(blocks=trimmed.blocks, dropped=trimmed.dropped, assembled_at=AT),
        )
    )

    # ── 3. 出一张填充卡（两处改动），等用户逐条裁决
    card = _fill_card()
    group = CardGroup(group_id="grp_1", cards=(card,), round_id="rnd_demo")
    asyncio.run(writer.write(RegionName.CARD_GROUP, group))
    asyncio.run(writer.write(RegionName.ROUND, _round_region(RoundPhase.AWAITING_USER, project_id)))

    # ── 4. 逐条裁决：要 prp_1、不要 prp_2（CR-002）；少列一条必须被拒
    answer = CardAnswer(
        card_id="crd_fill",
        verdict="confirm",
        status=CardStatus.ANSWERED,
        proposal_states={"prp_1": ProposalState.KEPT, "prp_2": ProposalState.REMOVED},
    )
    check_proposal_states(card, answer)
    with pytest.raises(ContractViolation):
        check_proposal_states(
            card,
            CardAnswer(
                card_id="crd_fill",
                verdict="confirm",
                status=CardStatus.ANSWERED,
                proposal_states={"prp_1": ProposalState.KEPT},
            ),
        )
    kept = [
        proposal
        for proposal in card.proposals
        if answer.proposal_states[proposal.proposal_id] is ProposalState.KEPT
    ]
    assert [proposal.proposal_id for proposal in kept] == ["prp_1"]
    asyncio.run(
        writer.write(
            RegionName.CARD_GROUP,
            group.model_copy(update={"state": CardGroupState.CONFIRMED}),
        )
    )

    # ── 5. 写文档：按 label 定位顶层块（I22 保证唯一），整批一个版本
    by_label = {
        block.schema_label: block for block in ledger.read_blocks(doc_id) if block.parent_id is None
    }
    ops = [
        BlockOp(
            block_id=by_label[proposal.target_label].block_id,
            op=proposal.op,
            content=proposal.content,
            expected_version=by_label[proposal.target_label].version,
        )
        for proposal in kept
    ]
    version = ledger.write_blocks(doc_id, ops, VersionTrigger.SUBMIT, group_id="grp_1")
    written = {block.schema_label: block for block in ledger.read_blocks(doc_id)}
    assert written["功能清单"].content == "支持把讨论候选带进主闭环"
    assert written["功能清单"].version == 2
    assert written["风险"].content == ""  # 用户删掉的那条，一个字都不该进文档

    # ── 6. 发事件：文档写入 + 记忆失效，都带 round_id（CR-003）
    asyncio.run(
        runtime.event_bus.publish(
            Event(
                type=EventType.DOC_CHANGED,
                payload=DocChangedPayload(
                    doc_id=doc_id,
                    version_id=version.version_id,
                    seq=version.seq,
                    trigger=VersionTrigger.SUBMIT,
                    block_ids=tuple(op.block_id for op in ops),
                    round_id="rnd_demo",
                ),
                at=AT,
            ),
            producer=ProducerIdentity.DOCUMENT_WRITER,
        )
    )
    asyncio.run(
        runtime.event_bus.publish(
            Event(
                type=EventType.MEMORY_UPDATED,
                payload=MemoryUpdatedPayload(
                    memory_id="mry_1",
                    project_id=project_id,
                    type=MemoryType.LESSON,
                    status=MemoryStatus.INVALID,
                    from_status=MemoryStatus.ACTIVE,
                    round_id="rnd_demo",
                ),
                at=AT,
            ),
            producer=ProducerIdentity.MEMORY,
        )
    )

    # ── 7. 轮末：先收尾、再清理（R1 必修 2 的后半）；未收尾就清理会被拒
    asyncio.run(writer.write(RegionName.ROUND, _round_region(RoundPhase.WORKING, project_id)))
    with pytest.raises(ContractViolation):
        asyncio.run(writer.drop_round())
    asyncio.run(
        writer.write(
            RegionName.ROUND,
            _round_region(RoundPhase.DONE, project_id, RoundEndReason.COMPLETED),
        )
    )
    asyncio.run(writer.drop_round())

    store = BoardStore(runtime.db)
    assert asyncio.run(board.read(RegionName.CARD_GROUP)) is None
    kept_row = asyncio.run(store.read_round("rnd_demo"))
    assert kept_row is not None and kept_row.phase is RoundPhase.DONE
    assert asyncio.run(store.unfinished_round_ids()) == ()

    # ── 8. 复盘：这一轮发生的事，按 round_id 全查得回来
    rows = runtime.event_log.read_by_round("rnd_demo")
    assert [row.type for row in rows] == [
        EventType.ROUND_UPDATED,
        EventType.CARD_GROUP_UPDATED,
        EventType.ROUND_UPDATED,
        EventType.CARD_GROUP_UPDATED,
        EventType.DOC_CHANGED,
        EventType.MEMORY_UPDATED,
        EventType.ROUND_UPDATED,
        EventType.ROUND_UPDATED,
    ]
    assert all(row.round_id == "rnd_demo" for row in rows)
    doc_changed = next(row for row in rows if row.type is EventType.DOC_CHANGED)
    assert isinstance(doc_changed.payload, DocChangedPayload)
    assert doc_changed.payload.block_ids == (written["功能清单"].block_id,)
