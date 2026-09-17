"""M13 上下文组装器 —— 说"这一轮该带什么"，不说"能带多少"。

**一个 token 都不裁**（PRD）：它给每个块标 `priority`，裁多少由 M24 按预算决定。所以这里
只做三件事：按来源取料、给每个块标优先级、顺手产出 `base_versions` 快照（I10 的眼睛）。

数据来源与"从哪拿"：

| 来源 | 谁生产 | M13 怎么拿 | `ref` |
|---|---|---|---|
| 文档全文 | M20 / 用户手改 | L7 `blocks`（同层读 L7） | `block_id` |
| 本轮输入 | 用户 | 入参 `round.user_input` | `round_id` |
| 简报 | 用户 / M14 | 层内调 M14（R4） | `project_id` |
| 生效记忆 | M17 | 层内读 L7 的 memories（R4） | `memory_id` |
| 检索材料 | M16 / M18 | 层内调 M16（R3/R5），**由 M13 转成块并带 `credibility`** | `material_id` |
| 历史输入 | 用户 | 层内调 M15（R4），它直接返回块 | 该消息的 `round_id` |

**R1.2 只有前两个来源有数据**（简报、记忆、材料、历史要等 R3/R4）。给本类加构造参数即可接上，
`assemble_context` 的入参形状不变（它只有 `round`）——所以加来源不会破坏任何调用方。
"""

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import ContextBlockSource
from pmstudio.contracts.models.results import AssembleResult
from pmstudio.contracts.skeleton.board import ContextBlock, RoundRegion
from pmstudio.storage.ledger import Ledger

# 优先级档位（C12：`≥ 0` 整数，越大越先保留；同值按 `(source, ref)` 排——排序由 M24 做，
# 用 `invariants.order_context_blocks` 那一条唯一规则）。档位之间留空，是为了以后加来源不用重排。
#
# 顺序的道理：这一轮要干什么（100）> 正在编辑的产物（80）> 常驻的简报与记忆（70/60）>
# 任务上下文（50）> 按需证据（40/35/30，讨论来源的 credibility 是 low，不能单独作为依据）。
# 带 R 标的是还没落地的来源——数值先定下来，免得各自为政。
PRIORITY_CURRENT_INPUT = 100
PRIORITY_DOCUMENT = 80
PRIORITY_BRIEF = 70  # R4（M14）
PRIORITY_MEMORY = 60  # R4（M17）
PRIORITY_HISTORY = 50  # R4（M15）
PRIORITY_MATERIAL_RETRIEVAL = 40  # R5（M16）
PRIORITY_MATERIAL_WEB = 35  # R5（M18）
PRIORITY_MATERIAL_DISCUSSION = 30  # R3（讨论区）


class ContextAssembler:
    """`MemoryPort.assemble_context` 的实现（M13）。"""

    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger

    async def assemble_context(self, round_region: RoundRegion) -> AssembleResult:
        document = self._ledger.read_document_by_project(round_region.project_id)
        if document is None:
            raise ContractViolation(
                f"项目 {round_region.project_id} 还没有文档——组装不出上下文，"
                "复述就只能靠猜（I14）"
            )

        blocks: list[ContextBlock] = []
        base_versions: dict[str, int] = {}

        for block in self._ledger.read_blocks(document.doc_id):
            if block.parent_id is not None:
                continue  # 定位只发生在顶层（与 I22 同一口径）
            blocks.append(
                ContextBlock(
                    source=ContextBlockSource.DOCUMENT,
                    ref=block.block_id,
                    content=block.content,
                    priority=PRIORITY_DOCUMENT,
                )
            )
            # 快照覆盖全部顶层块——包括当前还空着的：漏一个，那次手改就查不出来
            base_versions[block.block_id] = block.version

        blocks.append(
            ContextBlock(
                source=ContextBlockSource.USER_INPUT,
                ref=round_region.round_id,
                content=round_region.user_input,
                priority=PRIORITY_CURRENT_INPUT,
            )
        )
        return AssembleResult(blocks=tuple(blocks), base_versions=base_versions)
