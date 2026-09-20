"""M28 成稿器（R1.4）：用户确认理解后，生成填充卡。

使用 response_format=json_object 强制 JSON 输出。
模型返回 {字段名: 内容} 映射，Drafter 转为 Proposal 冻结 Schema。
"""

import json
from collections.abc import Sequence

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import BlockOpKind, CardKind, PromptRole
from pmstudio.contracts.models.card import Card, Proposal
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.skeleton.board import ContextBlock, RoundRegion


class Drafter:
    """M28 成稿器（R1.4）：用户确认理解后，生成填充卡。"""

    def __init__(self, harness: object, ids: object, ledger: object | None = None) -> None:
        self._harness = harness
        self._ids = ids
        self._ledger = ledger

    async def draft_fill_card(
        self, round_region: RoundRegion, blocks: Sequence[ContextBlock],
    ) -> Card:
        """生成填充卡。使用 response_format=json_object 强制 JSON 输出。"""
        # 1. 构建字段名→内容映射
        fields = await self._build_fields(round_region.project_id)

        # 2. 构建 prompt
        selected = list(
            round_region.scope.selected_fields
            if round_region.scope.selected_fields
            else fields.keys()
        )
        prompt_text = (
            "你是产品文档助手。根据用户输入和当前文档内容，为需要填充的字段生成内容。\n\n"
            f"用户本轮输入：{round_region.user_input}\n"
            f"需要填充的字段：{selected}\n"
            "当前文档内容：\n"
            f"{json.dumps(fields, ensure_ascii=False, indent=2)}\n\n"
            "请为每个需要填充的字段生成内容。以 JSON 对象返回，key 是字段名，value 是填充内容。"
        )

        messages = [PromptMessage(role=PromptRole.SYSTEM, text=prompt_text)]

        # 3. response_format 强制 JSON
        response_format = {"type": "json_object"}

        raw = await self._harness.complete(  # type: ignore[attr-defined]
            "reg_model_default", messages, response_format=response_format,
        )

        # 4. 解析 JSON → Proposal 列表
        proposals = self._parse_proposals(raw, selected)

        # 5. 构造填充卡
        return Card(
            card_id=self._ids.new_id("crd"),  # type: ignore[attr-defined]
            kind=CardKind.FILL,
            prompt=f"这次我打算改动 {len(proposals)} 处",
            proposals=tuple(proposals),
        )

    async def _build_fields(self, project_id: str) -> dict[str, str]:
        """从 ledger 读真实块 → {schema_label: content}。"""
        if self._ledger is not None:
            doc = self._ledger.read_document_by_project(project_id)  # type: ignore[attr-defined]
            if doc is not None:
                blocks = self._ledger.read_blocks(doc.doc_id)  # type: ignore[attr-defined]
                return {
                    b.schema_label: b.content.strip()
                    for b in blocks
                    if b.parent_id is None
                }
        return {}

    def _parse_proposals(self, raw: str, selected: list[str]) -> list[Proposal]:
        """解析模型返回的 JSON 对象 → Proposal 列表。"""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise ContractViolation(
                f"模型返回的不是合法 JSON（M28 要求 JSON 对齐冻结 Schema）：{error}"
            ) from error

        if not isinstance(data, dict):
            raise ContractViolation(f"模型返回的 JSON 必须是对象，收到 {type(data).__name__}")

        proposals: list[Proposal] = []
        for idx, (label, content) in enumerate(data.items()):
            if selected and label not in selected:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            try:
                proposal = Proposal(
                    proposal_id=self._ids.new_id("prp"),  # type: ignore[attr-defined]
                    target_label=label,
                    op=BlockOpKind.APPEND,
                    content=content.strip(),
                )
                proposals.append(proposal)
            except (ContractViolation, ValueError) as error:
                raise ContractViolation(f"第 {idx + 1} 条提案校验失败：{error}") from error

        return proposals
