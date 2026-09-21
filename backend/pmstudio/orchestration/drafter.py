"""M28 成稿器（R1.4）：用户确认理解后，生成填充卡。

使用 response_format=json_object 强制 JSON 输出。

**模型返回的 JSON 收三种形态**（判据是"内容能不能落位"，不是"模型长什么样"）：
以字段名为 key 的对象（prompt 里要的）、提案数组（`DECISIONS.md` §20 的原始口径）、
模型自己包一层 `{"proposals": [...]}`。一条都落不了位时**大声失败**（`GenerationFailure`），
绝不静默产一张空填充卡——C7 的填充卡必须有提案数组。

**引用归因（v2）**：M13 组装上下文时给每个可引用块编了 `evidence_id`（E1..En）。
Drafter 把编号证据清单放进 prompt，模型引用时只能从这里面取（I21）；
模型返回的 citations 反查成真实 `Citation`（BLOCK 带 target_version / USER_INPUT 带 round_id），
填进 `Proposal.citations`（C7/C15）。纯字符串 value 保持向后兼容（无引用）。
"""

import json
from collections.abc import Sequence

from pmstudio.common.errors import ContractViolation, GenerationFailure
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


# 提案对象里"字段名"可能用的 key：契约叫 `target_label`，模型常写 `label`。
_LABEL_KEYS = ("target_label", "label")


def _op_of(value: object) -> BlockOpKind:
    """模型给的 `op`（可选）。认不出来就按 `append`——追加不会覆盖已有内容，是安全的默认。"""
    raw_op = value.get("op") if isinstance(value, dict) else None
    if isinstance(raw_op, str):
        try:
            return BlockOpKind(raw_op.strip().lower())
        except ValueError:
            return BlockOpKind.APPEND
    return BlockOpKind.APPEND


def _proposal_entry(item: object) -> tuple[str, object]:
    """提案数组里的一项 → `(字段名, value)`。"""
    if not isinstance(item, dict):
        raise ContractViolation(f"提案数组里每一项都必须是对象，收到 {type(item).__name__}")
    for key in _LABEL_KEYS:
        label = item.get(key)
        if isinstance(label, str) and label.strip():
            return label.strip(), item
    raise ContractViolation(f"提案缺少 target_label（收到 {sorted(item)}）")


def _proposal_entries(data: object) -> list[tuple[str, object]]:
    """把模型返回的 JSON 归一成 `[(字段名, value)]`。

    收三种形态：

    ① 以字段名为 key 的对象（prompt 里要的形态）：`{"功能清单": "…"}`；
    ② 提案数组：`[{"target_label": …, "op": …, "content": …, "citations": [...]}]`
       （`DECISIONS.md` §20 记的原始口径就是数组）；
    ③ 模型自己包一层：`{"proposals": [提案, ...]}`（只有一个 key、值是非空对象数组）。

    归一不了就抛 `ContractViolation`——**绝不返回空**（空数组要由调用方说清是什么失败）。
    """
    if isinstance(data, list):
        return [_proposal_entry(item) for item in data]
    if isinstance(data, dict):
        if "content" in data and any(key in data for key in _LABEL_KEYS):
            return [_proposal_entry(data)]
        single_list = _unwrap_single_list(data)
        if single_list is not None:
            return [_proposal_entry(item) for item in single_list]
        return list(data.items())
    raise ContractViolation(f"模型返回的 JSON 必须是对象或提案数组，收到 {type(data).__name__}")


def _unwrap_single_list(data: dict) -> list | None:
    """`{"proposals": [...]}` 这类只有一层包装的对象 → 取出里面的数组。"""
    if len(data) != 1:
        return None
    (only_value,) = data.values()
    if (
        isinstance(only_value, list)
        and only_value
        and all(isinstance(item, dict) for item in only_value)
    ):
        return only_value
    return None


def _json_keys(data: object) -> str:
    """报错时告诉人"模型到底给了什么"——不然只能靠猜该改 prompt 还是换模型。"""
    if isinstance(data, dict):
        return "、".join(str(key) for key in data) or "(空对象)"
    if isinstance(data, list):
        return f"数组 {len(data)} 项"
    return type(data).__name__


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
        """解析模型返回的 JSON → Proposal 列表（形态容差见 `_proposal_entries`）。

        value 两种形态：
        - 字符串：内容本身，无引用（向后兼容）；
        - `{"content": str, "citations": [证据号, ...], "op": str}`：内容 + 引用归因 + 落位方式。
        citations 必须是证据集合里的（I21），Drafter 反查成真实 Citation。

        **一条都落不了位时大声失败**：C7 要求填充卡必须带提案数组，"0 条提案"永远不是一张
        合法卡片；静默返回空只会让上层拿到裸 `ValidationError`，用户既不知道卡在哪一步、
        也不知道能不能重试（AC12）。
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

        proposals: list[Proposal] = []
        for idx, (label, value) in enumerate(_proposal_entries(data)):
            if selected and label not in selected:
                continue

            content, citations = self._coerce_value(label, value, evidence)
            if not content.strip():
                continue
            try:
                proposal = Proposal(
                    proposal_id=self._ids.new_id("prp"),  # type: ignore[attr-defined]
                    target_label=label,
                    op=_op_of(value),
                    content=content.strip(),
                    citations=citations,
                )
                proposals.append(proposal)
            except (ContractViolation, ValueError) as error:
                raise ContractViolation(f"第 {idx + 1} 条提案校验失败：{error}") from error

        if not proposals:
            raise GenerationFailure(
                "模型返回的 JSON 一条提案都落不了位——"
                f"允许的字段：{selected or '不限'}；模型给的 key：{_json_keys(data)}",
                retryable=True,
                step="drafting",
            )

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
