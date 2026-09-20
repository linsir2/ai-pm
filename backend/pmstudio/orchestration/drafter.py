"""M28 成稿器（R1.4）：用户确认理解后，生成填充卡。

使用 response_format=json_object 强制 JSON 输出。
模型返回 {字段名: 内容} 映射，Drafter 转为 Proposal 冻结 Schema。

**引用归因（v2）**：M13 组装上下文时给每个可引用块编了 `evidence_id`（E1..En）。
Drafter 把编号证据清单放进 prompt，模型引用时只能从这里面取（I21）；
模型返回的 citations 反查成真实 `Citation`（BLOCK 带 target_version / USER_INPUT 带 round_id），
填进 `Proposal.citations`（C7/C15）。纯字符串 value 保持向后兼容（无引用）。
"""

import json
from collections.abc import Sequence

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import BlockOpKind, CardKind, CitationTargetType, ContextBlockSource, PromptRole
from pmstudio.contracts.models.card import Card, Proposal
from pmstudio.contracts.models.citation import Citation
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.skeleton.board import ContextBlock, RoundRegion


def _evidence_index(blocks: Sequence[ContextBlock]) -> dict[str, ContextBlock]:
    """证据号 → 块。编号是 M13 发的（E1..En），Drafter 只反查，不重编。"""
    index: dict[str, ContextBlock] = {}
    for block in blocks:
        if block.evidence_id is not None:
            index[block.evidence_id] = block
    return index


def _format_evidence_list(blocks: Sequence[ContextBlock]) -> str:
    """把编号证据清单格式化成 prompt 段落——模型引用时只能从这里取号。"""
    lines: list[str] = []
    for block in blocks:
        if block.evidence_id is None:
            continue
        source = block.source.value
        version = f" (v{block.ref_version})" if block.ref_version is not None else ""
        content = block.content.strip() or "(空)"
        lines.append(f"[{block.evidence_id}] {source} {block.ref}{version}: {content}")
    return "\n".join(lines)


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
        evidence = _evidence_index(blocks)
        prompt_text = (
            "你是产品文档助手。根据用户输入和当前文档内容，为需要填充的字段生成内容。\n\n"
            f"用户本轮输入：{round_region.user_input}\n"
            f"需要填充的字段：{selected}\n"
            "当前文档内容：\n"
            f"{json.dumps(fields, ensure_ascii=False, indent=2)}\n\n"
            "=== 可引用证据（只能引用这些编号） ===\n"
            f"{_format_evidence_list(blocks)}\n\n"
            "请为每个需要填充的字段生成内容。以 JSON 对象返回，key 是字段名。\n"
            "value 可以是字符串（不带引用），也可以是一个对象：\n"
            '{"content": "填充内容", "citations": ["E1", "E2"]}\n'
            "citations 只能引用上面证据清单里的编号，且必须真实支撑该字段的内容。"
        )

        messages = [PromptMessage(role=PromptRole.SYSTEM, text=prompt_text)]

        # 3. response_format 强制 JSON
        response_format = {"type": "json_object"}

        raw = await self._harness.complete(  # type: ignore[attr-defined]
            "reg_model_default", messages, response_format=response_format,
        )

        # 4. 解析 JSON → Proposal 列表（引用归因：citations 反查证据）
        proposals = self._parse_proposals(raw, selected, evidence)

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

    def _parse_proposals(
        self,
        raw: str,
        selected: list[str],
        evidence: dict[str, ContextBlock],
    ) -> list[Proposal]:
        """解析模型返回的 JSON 对象 → Proposal 列表。

        value 两种形态：
        - 字符串：内容本身，无引用（向后兼容）；
        - `{"content": str, "citations": [证据号, ...]}`：内容 + 引用归因。
        citations 必须是证据集合里的（I21），Drafter 反查成真实 Citation。
        """
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
        for idx, (label, value) in enumerate(data.items()):
            if selected and label not in selected:
                continue

            content, citations = self._coerce_value(label, value, evidence)
            if not content.strip():
                continue
            try:
                proposal = Proposal(
                    proposal_id=self._ids.new_id("prp"),  # type: ignore[attr-defined]
                    target_label=label,
                    op=BlockOpKind.APPEND,
                    content=content.strip(),
                    citations=citations,
                )
                proposals.append(proposal)
            except (ContractViolation, ValueError) as error:
                raise ContractViolation(f"第 {idx + 1} 条提案校验失败：{error}") from error

        return proposals

    def _coerce_value(
        self,
        label: str,
        value: object,
        evidence: dict[str, ContextBlock],
    ) -> tuple[str, tuple[Citation, ...]]:
        """把模型返回的 value 归一成 (内容, 引用数组)。"""
        if isinstance(value, str):
            return value, ()

        if isinstance(value, dict):
            content = value.get("content")
            if not isinstance(content, str):
                raise ContractViolation(
                    f"字段 {label} 的 value 对象缺少字符串 content"
                )
            raw_citations = value.get("citations", [])
            if not isinstance(raw_citations, list):
                raise ContractViolation(
                    f"字段 {label} 的 citations 必须是数组，收到 {type(raw_citations).__name__}"
                )
            citations = self._resolve_citations(label, raw_citations, evidence)
            return content, citations

        raise ContractViolation(
            f"字段 {label} 的 value 必须是字符串或 {{content, citations}} 对象，"
            f"收到 {type(value).__name__}"
        )

    def _resolve_citations(
        self,
        label: str,
        raw_ids: Sequence[object],
        evidence: dict[str, ContextBlock],
    ) -> tuple[Citation, ...]:
        """把模型给的证据号反查成真实引用（I21：指不到的东西不算依据）。"""
        citations: list[Citation] = []
        for raw_id in raw_ids:
            if not isinstance(raw_id, str) or not raw_id.strip():
                raise ContractViolation(f"字段 {label} 的 citations 里有空编号")
            block = evidence.get(raw_id)
            if block is None:
                raise ContractViolation(
                    f"字段 {label} 引用了证据集合之外的编号 {raw_id}（I21：只能引用 M13 编号清单里的证据）"
                )
            citations.append(self._block_to_citation(block))
        return tuple(citations)

    def _block_to_citation(self, block: ContextBlock) -> Citation:
        """证据块 → Citation。DOCUMENT → BLOCK（带版本）；USER_INPUT → USER_INPUT（带 round_id）。"""
        if block.source is ContextBlockSource.DOCUMENT:
            if block.ref_version is None:  # pragma: no cover - ContextBlock 校验器保证非 None
                raise ContractViolation(f"文档块 {block.ref} 缺 ref_version——BLOCK 引用必须有版本（I6）")
            return Citation(
                target_type=CitationTargetType.BLOCK,
                target_id=block.ref,
                target_version=block.ref_version,
            )
        if block.source is ContextBlockSource.USER_INPUT:
            return Citation(
                target_type=CitationTargetType.USER_INPUT,
                target_id=block.ref,
            )
        raise ContractViolation(
            f"证据块 {block.evidence_id} 的来源 {block.source.value} 尚不支持作为填充卡引用"
        )
