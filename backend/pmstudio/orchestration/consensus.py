"""M8 共识生成器（最简版 R1.3）：用户输入后先复述理解。

每轮用户输入之后，把"当前文档内容 + 本轮输入"组装成一组 PromptMessage，
调一次模型产出一张确认理解卡。模型只吐文本（理解卡的 prompt），
不解析结构化输出 — JSON 解析的复杂度推迟到 R1.4。
"""

from collections.abc import Sequence

from pmstudio.common.ids import IdGenerator
from pmstudio.contracts.enums import CardKind, PromptRole
from pmstudio.contracts.models.card import Card
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.skeleton.board import ContextBlock, RoundRegion


def _format_blocks_as_doc(blocks: Sequence[ContextBlock]) -> str:
    """把上下文块列表格式化为 label: content 的纯文本。"""
    lines: list[str] = []
    for block in blocks:
        if block.source.value == "document":
            label = block.ref
            lines.append(f"{label}:")
            if block.content.strip():
                lines.append(f"  {block.content.strip()}")
    return "\n".join(lines)


class ConsensusGenerator:
    """M8 最简版：构建 system prompt → 调模型 → 返回理解卡。"""

    def __init__(self, harness: object, ids: IdGenerator) -> None:
        self._harness = harness
        self._ids = ids

    async def produce_understanding_card(
        self, round_region: RoundRegion, blocks: Sequence[ContextBlock],
    ) -> Card:
        """产出一张确认理解卡。"""
        system_text = (
            "你是产品文档助手。你的任务是复述用户对这一轮修改的理解。\n"
            "规则：\n"
            "1. 复述必须包含至少一个只有读了文档才知道的信息。\n"
            "2. 只复述理解，不写入任何内容。\n"
            "3. 用中文回答，简短直接。\n\n"
            "=== 当前文档内容 ===\n"
            f"{_format_blocks_as_doc(blocks)}"
            "\n\n=== 用户本轮输入 ===\n"
            f"{round_region.user_input}"
        )

        messages = [PromptMessage(role=PromptRole.SYSTEM, text=system_text)]
        text = await self._harness.complete("reg_model_default", messages)  # type: ignore[attr-defined]

        return Card(
            card_id=self._ids.new_id("crd"),  # type: ignore[attr-defined]
            kind=CardKind.UNDERSTANDING,
            prompt=text,
        )
