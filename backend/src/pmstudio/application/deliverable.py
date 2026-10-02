"""交付物渲染：把文档 + 账本 + 版本变成一份可以直接递出去的 Markdown。

**纯函数**：同一份 `DocumentView` + 同一份账本 + 同一个 `exported_at`，永远同一份字节
（"权威"的前提是确定性）。没有 IO、不调模型、不持有写入能力。

四条硬规矩（`01-规格.md` §7.3）：

1. 空章显式写出"本章为空 + 差什么"，**不许静默省略**；
2. **s13 版本历史由 `Version[]` 生成**，不读 block 树；
3. 偏好区不进正文，只作附录 B，并标明"不进交付物"；
4. 附录 A 只给 `编号 + 类型 + 一句话摘要`，原话（`raw_span`）留在系统内（`04` D5）；
   而且**只列本文引用过的编号**——账本是项目级、跨轮、只增的，没进正文的那些
   （未被采纳的提案、被跳过的题、更早的轮次）读者查不回去，进交付物只会稀释真正的出处
   （2026-10-02 P31；真跑 59 份导出实测：附录占全文 51%（中位），其中 63% 从未被引用）。
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime

from pmstudio.domain.authoring.blocks import Entry
from pmstudio.domain.authoring.readiness import ReadinessItem, Severity
from pmstudio.domain.authoring.schema import FEATURE_FIELDS
from pmstudio.domain.authoring.version import Version, VersionTrigger
from pmstudio.domain.authoring.views import BlockView, DocumentView, SectionView
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.domain.shared.writing import BlockKind

KIND_LABELS = {
    EvidenceKind.USER_INPUT: "用户输入",
    EvidenceKind.CARD_ANSWER: "问卷回答",
    EvidenceKind.AGENT_DERIVED: "席位判断",
    EvidenceKind.AGENT_PROPOSED: "席位提议",
    EvidenceKind.SYSTEM_DEFAULT: "系统默认",
    EvidenceKind.MANUAL: "手工编辑",
    EvidenceKind.EXTERNAL: "外部材料",
}

TRIGGER_LABELS = {
    VersionTrigger.CONFIRM: "草案确认",
    VersionTrigger.MANUAL: "手工编辑",
    VersionTrigger.ROLLBACK: "回退",
}

SUMMARY_LIMIT = 60


@dataclass(frozen=True, slots=True)
class Deliverable:
    filename: str
    version_no: int
    markdown: str


def render_markdown(
    *,
    view: DocumentView,
    readiness: Sequence[ReadinessItem],
    versions: Sequence[Version],
    ledger: EvidenceLedger,
    exported_at: datetime,
) -> Deliverable:
    parts: list[str] = [_header(view, readiness, exported_at)]
    for section in view.sections:
        parts.append(_section(section, readiness=readiness, versions=versions))
    parts.append(_appendix_a(view, ledger))
    if view.preference_entries:
        parts.append(_appendix_b(view))
    unfinished = _unfinished(readiness)
    if unfinished:
        parts.append(unfinished)
    return Deliverable(
        filename=f"{view.title}-v{view.version_no}.md",
        version_no=view.version_no,
        markdown="\n\n".join(part for part in parts if part) + "\n",
    )


def _header(
    view: DocumentView, readiness: Sequence[ReadinessItem], exported_at: datetime
) -> str:
    blockers = sum(1 for item in readiness if item.severity is Severity.BLOCKER)
    warns = sum(1 for item in readiness if item.severity is Severity.WARN)
    if not readiness:
        stamp = "就绪度：未评估（这次导出没带判据结果）"
    elif blockers == 0:
        stamp = f"就绪度：通过（无阻塞项，提示 {warns} 项）"
    else:
        stamp = f"就绪度：未通过（阻塞 {blockers} 项 / 提示 {warns} 项）"
    return "\n".join(
        [
            f"# 产品文档 · {view.title}（v{view.version_no}）",
            f"> 导出时间 {exported_at:%Y-%m-%d %H:%M} · 文档版本 v{view.version_no}",
            f"> {stamp}",
        ]
    )


def _section(
    section: SectionView,
    *,
    readiness: Sequence[ReadinessItem],
    versions: Sequence[Version],
) -> str:
    number = str(section.section_id).lstrip("s")
    lines = [f"## {number} {section.title}"]
    if str(section.section_id) == "s13":
        lines += _versions_table(versions)
        return "\n".join(lines)
    features = [block for block in section.blocks if block.kind is BlockKind.FEATURE]
    if features:
        for index, feature in enumerate(features, start=1):
            lines += _feature(feature, index=index)
    plain = [block for block in section.blocks if block.kind is not BlockKind.FEATURE]
    for block in plain:
        lines += _block_lines(block)
    if not _has_entries(section) and not features:
        lines.append(_empty_note(section.section_id, readiness))
    return "\n".join(lines)


def _feature(feature: BlockView, *, index: int) -> list[str]:
    slug = str(feature.feature_slug)
    name_block = _child(feature, "name")
    if name_block and name_block.entries:
        name_entry = name_block.entries[0]
        name, refs = name_entry.text, " ".join(str(eid) for eid in name_entry.refs)
        # 名称也是正文内容，界面在功能名旁边挂了出处胶囊（`FeatureBlock.tsx`）；
        # 导出以前把它的编号吞了，于是附录里会出现一个正文根本看不见的编号
        # （P31 真跑对账抓到：L2 的 E170、L3 的 E28 都是 `s5/…name` 那条）
        title = f"### 5.{index} {name}（{slug}）" + (f"〔{refs}〕" if refs else "")
    else:
        title = f"### 5.{index} {slug}（{slug}）"
    lines = [title]
    missing: list[str] = []
    for spec in FEATURE_FIELDS:
        if spec.name == "name":
            continue
        block = _child(feature, spec.name)
        if block is None or not block.entries:
            missing.append(spec.title)
            continue
        if spec.multiple:
            lines.append(f"- **{spec.title}**：")
            lines += [f"  - {_entry_line(entry)}" for entry in block.entries]
        else:
            lines.append(f"- **{spec.title}**：{_entry_line(block.entries[0])}")
    if missing:
        lines.append(f"- （待补：{'、'.join(missing)}）")
    return lines


def _block_lines(block: BlockView, *, indent: str = "") -> list[str]:
    lines: list[str] = []
    for entry in block.entries:
        lines.append(f"{indent}- {_entry_line(entry)}")
    for child in block.children:
        lines += _block_lines(child, indent=indent)
    return lines


def _entry_line(entry) -> str:  # type: ignore[no-untyped-def]
    refs = " ".join(str(eid) for eid in entry.refs)
    return f"{entry.text}〔{refs}〕" if refs else entry.text


def _empty_note(section_id: object, readiness: Sequence[ReadinessItem]) -> str:
    item = next(
        (
            candidate
            for candidate in readiness
            if candidate.section_id == str(section_id)
        ),
        None,
    )
    if item is None:
        return "（本章为空）"
    return f"（本章为空：{item.detail}）"


def _versions_table(versions: Sequence[Version]) -> list[str]:
    lines = ["", "| 版本 | 时间 | 触发 | 摘要 |", "|---|---|---|---|"]
    for version in reversed(tuple(versions)):
        trigger = TRIGGER_LABELS.get(version.trigger, str(version.trigger))
        lines.append(
            f"| v{version.version_no} | {version.created_at:%Y-%m-%d %H:%M} | {trigger} "
            f"| {version.summary} |"
        )
    return lines


def _appendix_a(view: DocumentView, ledger: EvidenceLedger) -> str:
    """附录 A 只列**本文引用过**的编号（P31）。

    准入判据 = 编号出现在 `DocumentView` 的任一条目 `refs` 里（13 章正文 + 偏好区）。
    其余的不静默消失：末尾一行说清还有多少条留在系统里——账本本身一个字不动（E1/E2）。
    """

    listed_ids = cited_evidence_ids(view)
    lines = ["## 附录 A · 出处（只给编号与一句话摘要）"]
    total = 0
    listed = 0
    for evidence in ledger.evidences():
        total += 1
        if evidence.eid not in listed_ids:
            continue
        listed += 1
        label = KIND_LABELS.get(evidence.kind, str(evidence.kind))
        # 外部材料要写清"哪份材料的哪一块 / 哪个 URL"，否则读者不知道这句话从哪来（工具层 C-2）
        where = evidence.source.external_ref
        source = f"{where} · " if where else ""
        lines.append(f"- {evidence.eid} · {label} · {source}{_summarize(evidence.text)}")
    if not listed:
        lines.append("（还没有任何出处）" if not total else "（本文没有引用任何出处）")
    if total > listed:
        lines.append(
            f"（另有 {total - listed} 条出处没有被本文引用——未被采纳的提案、"
            "被跳过的题与更早的轮次都留在系统里，可按编号查）"
        )
    return "\n".join(lines)


def _appendix_b(view: DocumentView) -> str:
    lines = ["## 附录 B · 偏好区（不进交付物）"]
    lines += [f"- {_entry_line(entry)}" for entry in view.preference_entries]
    return "\n".join(lines)


def _unfinished(readiness: Sequence[ReadinessItem]) -> str:
    if not readiness:
        return ""
    lines = ["## 附录 C · 未完成项（系统按合格标准算的）"]
    for item in readiness:
        tag = "阻塞" if item.severity is Severity.BLOCKER else "提示"
        lines.append(f"- 〔{tag}〕{item.section_id} {item.title}：{item.detail}")
    return "\n".join(lines)


def summarize_line(text: str) -> str:
    """一句话摘要：章节、出处附录、出处查询共用同一条压缩规则。"""

    flat = " ".join(text.split())
    return flat if len(flat) <= SUMMARY_LIMIT else f"{flat[:SUMMARY_LIMIT]}…"


_summarize = summarize_line  # 兼容既有调用点


def _has_entries(section: SectionView) -> bool:
    return any(True for block in section.blocks for _ in iter_block_entries(block))


def iter_block_entries(block: BlockView) -> Iterator[Entry]:
    """一块里的全部条目（含子块，深度优先、保持渲染顺序）。"""

    yield from block.entries
    for child in block.children:
        yield from iter_block_entries(child)


def iter_document_entries(view: DocumentView) -> Iterator[Entry]:
    """文档视图里的全部条目：13 章（含功能子树字段）+ 偏好区。

    正文渲染、"这个编号被谁引用了"（`dto.used_by_entries`）、附录 A 的准入判据
    共用这一条走法——三处各写一遍就是三颗雷。
    """

    for section in view.sections:
        for block in section.blocks:
            yield from iter_block_entries(block)
    yield from view.preference_entries


def cited_evidence_ids(view: DocumentView) -> frozenset[EvidenceId]:
    """本文引用到的编号集合（附录 A 的准入判据，`01-规格.md` §7.3）。"""

    return frozenset(eid for entry in iter_document_entries(view) for eid in entry.refs)


def _child(feature: BlockView, field: str) -> BlockView | None:
    for child in feature.children:
        if child.field == field:
            return child
    return None
