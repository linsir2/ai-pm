"""只读投影：界面只认这些结构，不认识聚合内部。

这里不做任何业务判断——"要不要出问卷""能不能写"全由领域决定，这里只是把结论摆出来。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from pmstudio.application.deliverable import (
    KIND_LABELS,
    TRIGGER_LABELS,
    iter_document_entries,
    summarize_line,
)
from pmstudio.application.ports import SeatCatalog, SeatDefinition
from pmstudio.domain.authoring.diff import diff_snapshots
from pmstudio.domain.authoring.readiness import ReadinessItem
from pmstudio.domain.authoring.schema import FEATURE_FIELDS
from pmstudio.domain.authoring.version import Version
from pmstudio.domain.authoring.views import BlockView, DocumentView
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import Draft, Proposal
from pmstudio.domain.round.opinion import OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    MAX_OPEN_ITEMS_PER_BATCH,
    MAX_QUESTIONNAIRES,
    Answer,
    Question,
)
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import MAX_SEAT_ROUNDS
from pmstudio.domain.services.scope_guard import ScopeGuard
from pmstudio.domain.shared.ids import EvidenceId, RoundId
from pmstudio.domain.shared.sections import SECTION_SLOTS, SECTIONS

SEAT_COMPLETED = "completed"
SEAT_PENDING = "pending"


@dataclass(frozen=True, slots=True)
class SeatSuggestionView:
    """开场卡上的一个席位提名。

    "为什么请它"分三层说清：
    `why`（**分析者读完这段输入之后给的理由**，这是主口径）、
    `focus`（它管什么）、以及机械事实 `always_on` / `matched_keywords`（目录里的配置）。
    `source` 标出这条是分析者挑的（`agent`）还是老轮次回落目录匹配来的（`catalog`）——
    界面照实说，用户才好做减法。
    """

    seat_id: str
    title: str
    focus: str
    why: str = ""
    source: str = "agent"
    always_on: bool = False
    matched_keywords: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClaimView:
    claim_id: str
    eid: str
    text: str
    label: str
    target_label: str
    raw_span: str
    assumption_note: str | None = None


@dataclass(frozen=True, slots=True)
class OpinionView:
    local_id: str
    form: str
    about: str
    claim: str
    because: str
    eids: tuple[str, ...]
    basis_note: str | None = None
    eid: str | None = None  # 这条判断自己拿到的编号（账本按 seat_id+local_id 反查）


@dataclass(frozen=True, slots=True)
class SeatPacketView:
    seat_id: str
    title: str
    status: str
    pass_index: int
    basis: str
    opinions: tuple[OpinionView, ...]
    judgment_count: int
    question_count: int


@dataclass(frozen=True, slots=True)
class DroppedView:
    """一条"没进卡片"的留痕：地址、席位、第几趟、原因、来源（`04` C5 后半）。"""

    handle: str
    seat_id: str | None
    pass_index: int
    reason: str
    source: str


@dataclass(frozen=True, slots=True)
class QuestionView:
    question_id: str
    kind: str
    type: str
    title: str
    why: str
    target_label: str
    options: tuple[tuple[str, str], ...]
    consequence_if_skipped: str
    eids: tuple[str, ...]
    asked_by: tuple[str, ...]
    blocking: bool


@dataclass(frozen=True, slots=True)
class ProposalView:
    proposal_id: str
    entry_id: str
    op: str
    target_label: str
    text: str
    origin_type: str
    eids: tuple[str, ...]
    rationale: str | None
    kind: str | None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class DraftView:
    draft_id: str
    groups: tuple[tuple[str, tuple[ProposalView, ...]], ...]
    proposals: tuple[ProposalView, ...]
    gaps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnswerBatchView:
    """一批问卷的答题情况（`04-未决.md` F3）：答了几条、跳了几条、答了什么。

    答案文本用 `answer_text` 渲染成人的话（与写进账本的那一条同一份口径），
    **不带选项 id**——界面要给人看，不是要人读 id。
    """

    form_id: str
    answered: int
    skipped: int
    answers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoundMaterialChunkView:
    """材料卡里的一块：编号（能点回原话）+ 锚点 + 正文。"""

    chunk_id: str
    eid: str
    anchor: str
    text: str


@dataclass(frozen=True, slots=True)
class RoundMaterialView:
    """这一轮带进来的**一份材料**（只含带进来的那些块）。"""

    material_id: str
    title: str
    source_ref: str
    origin: str
    parser_id: str
    #: 这份材料总共有多少块（"只带了 3 块 / 共 20 块"靠它）
    chunk_count: int
    chunks: tuple[RoundMaterialChunkView, ...]
    truncated: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WebQueryView:
    """复述卡上的一条检索词提名：查什么、为什么查、用户勾没勾。"""

    query: str
    why: str
    chosen: bool


@dataclass(frozen=True, slots=True)
class RoundView:
    """一轮的只读投影。"""

    round_id: str
    project_id: str
    user_input: str
    step: str
    waiting_on: str
    seat_round_count: int
    seats: tuple[str, ...]
    active_seats: tuple[str, ...]
    nominated_seats: tuple[SeatSuggestionView, ...]
    available_seats: tuple[SeatSuggestionView, ...]
    claims: tuple[ClaimView, ...]
    seat_packets: tuple[SeatPacketView, ...]
    dropped: tuple[DroppedView, ...]
    questions: tuple[QuestionView, ...]
    list_items: tuple[str, ...]
    draft: DraftView | None
    advisories: tuple[str, ...]
    failure_reason: str | None
    #: 失败时停在哪个 step（`failed` 之外的那个）——"重试会从哪继续"由它翻译
    failure_at_step: str | None
    #: 那句人话（认不出的 step → None，不猜）
    retry_from: str | None
    abandon_reason: str | None
    confirmed_version_no: int | None
    form_id: str | None
    #: 这一轮每一批问卷答了什么（`04-未决.md` F3）。没问过就是空。
    answer_sets: tuple[AnswerBatchView, ...] = ()
    #: 这一轮**带进提示词**的材料块（工具层 C-2）：界面据此显示材料卡并支持跳转。
    materials: tuple[RoundMaterialView, ...] = ()
    #: 分析者提名的检索词（**只是提名**；勾了才会真去查）
    web_queries: tuple[WebQueryView, ...] = ()
    #: 联网工具现在能不能用（没配密钥就是 False）——复述卡据此决定要不要出现那一栏
    web_search_available: bool = False


@dataclass(frozen=True, slots=True)
class ProjectSummaryView:
    project_id: str
    title: str
    version_no: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RoundOutcomeView:
    """一轮的**结果投影**（不是整轮）：C4 只需要"给了几条 / 收了几条"。

    只读 `rounds.payload` 里那几个键，不 `round_from_payload`——查历史不该把整轮重建出来。
    """

    round_id: str
    step: str
    proposed_offered: int | None
    proposed_accepted: int | None


@dataclass(frozen=True, slots=True)
class RoundHeadView:
    """一轮的**头部**（不看全文）：闭环区顶上那串"这个项目走过哪几轮"的一行。

    与 `RoundOutcomeView` 一样只读 `rounds.payload` 的几个键；区别是它给**人**看
    （输入摘要 / 数 / 版本号），而那个只给 C4 的判据看。
    """

    round_id: str
    created_at: datetime
    step: str
    user_input: str
    claims_count: int
    questions_count: int
    confirmed_version_no: int | None
    #: 头 3 条主张的摘要（"上一轮你提过什么"）；没有就是空
    claim_headlines: tuple[str, ...] = ()


def round_head_view(
    *,
    round_id: str,
    step: str,
    created_at: datetime,
    payload: Mapping[str, object],
) -> RoundHeadView:
    """payload → 时间线一行。**纯投影**：缺键回落、坏形状当空，绝不抛。"""

    def _count(value: object) -> int:
        if isinstance(value, Sequence) and not isinstance(value, str):
            return len(value)
        return 0

    history = payload.get("questionnaire_history")
    if not isinstance(history, Sequence) or isinstance(history, str):
        # 老行没有历史键：退化成"只有当前这一批"
        current = payload.get("questionnaire")
        history = [] if current is None else [current]

    questions_count = 0
    for form in history:
        if isinstance(form, Mapping):
            questions_count += _count(form.get("questions"))

    version_no = payload.get("confirmed_version_no")
    headlines: list[str] = []
    claims = payload.get("claims")
    if isinstance(claims, Sequence) and not isinstance(claims, str):
        for claim in claims:
            if isinstance(claim, Mapping):
                text = str(claim.get("text") or "").strip()
                if text:
                    headlines.append(summarize_line(text))
            if len(headlines) == 3:
                break

    return RoundHeadView(
        round_id=round_id,
        created_at=created_at,
        step=step,
        user_input=summarize_line(str(payload.get("user_input") or "")),
        claims_count=_count(payload.get("claims")),
        questions_count=questions_count,
        confirmed_version_no=version_no if isinstance(version_no, int) else None,
        claim_headlines=tuple(headlines),
    )


@dataclass(frozen=True, slots=True)
class OpinionSourceView:
    """一条可被搜到的席位发言（F6）：它出自哪一轮、哪个席位。"""

    round_id: str
    seat_id: str
    claim: str


@dataclass(frozen=True, slots=True)
class SearchHitView:
    """一条搜索结果。`ref` 是"去哪儿找"：条目 id / 块 id / 编号 / 轮次 id。"""

    kind: str  # "entry" | "feature" | "evidence" | "opinion"
    label: str
    ref: str
    where: str


@dataclass(frozen=True, slots=True)
class SearchResultsView:
    query: str
    hits: tuple[SearchHitView, ...]
    truncated: bool


#: 一次搜索最多回多少条（超过就明确说"截断了"，不静默丢）
SEARCH_LIMIT = 50


# ── 材料信息包（工具层 C-2）：解析器产出、存储层盖章、渲染器消费 ─────────────


@dataclass(frozen=True, slots=True)
class MaterialChunk:
    """材料里的一个块：检索、引用、渲染都以它为单位。"""

    chunk_id: str
    ordinal: int
    text: str
    #: 章节链（("2 目标", "2.1 非目标")）；抽不出层级就是 ()
    heading_path: tuple[str, ...]
    #: 人话锚点："第 3 页" / "第 12–40 行"——引用与跳转都靠它
    anchor: str


@dataclass(frozen=True, slots=True)
class ParsedMaterial:
    """解析器的产出：**草稿**（还没有 id、不知道属于哪个项目）。

    盖章（material_id / project_id）只发生在 `MaterialStore.put()`——所以"谁赋值"没有歧义。
    """

    origin: str  # "upload" | "web_search" | "web_fetch"
    title: str
    source_ref: str  # "attachment:需求.md" / URL
    media_type: str
    bytes_len: int
    chunks: tuple[MaterialChunk, ...]
    parser_id: str  # "local-text" | "pdfminer" | "bs4-html" | "tavily"
    parser_version: str
    truncated: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MaterialPacket:
    """一份材料的信息包——**底层唯一可消费的形态**（完整形态只从存储层出来）。"""

    material_id: str
    project_id: str
    parsed: ParsedMaterial
    created_at: datetime
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class MaterialSummaryView:
    """材料列表里的一行：**不带块正文**（50 份 × 400 块不能塞进一个响应）。"""

    material_id: str
    origin: str
    title: str
    source_ref: str
    media_type: str
    bytes_len: int
    chunk_count: int
    chars: int
    parser_id: str
    parser_version: str
    truncated: bool
    warnings: tuple[str, ...]
    #: 这份包是用**旧解析器**读的吗（界面提示"建议重解析"）
    stale: bool
    #: 这份材料被**哪些编号**引用过（`P19`）：空 = 没被用过 = 可以删、可以就地重解析。
    #: 有编号的**冻结**：编号指向它里面的块，删了会让 `E7` 指向空气，改形状会让编号改指。
    eids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MaterialHit:
    """检索结果：块级，带来源与锚点（不是一段裸文本）。

    `score` 的消费者是 **F6 的展示顺序**（检索器给什么顺序就显示什么顺序，
    `search_hits` 不再重排）；`excerpt` 是命中词前后的一小段，用来在列表里放一行。
    """

    material_id: str
    chunk_id: str
    score: float
    excerpt: str
    anchor: str
    source_ref: str
    #: 材料标题（列表那行的开头："权限系统设计详解：<片段>"）
    title: str


#: 包结构版本：换形状时 +1，`needs_reparse` 会因此判"该重解析"
MATERIAL_SCHEMA_VERSION = 1


def material_summary_view(
    packet: MaterialPacket,
    *,
    current: frozenset[tuple[str, str]],
    chunk_count: int | None = None,
    chars: int | None = None,
) -> MaterialSummaryView:
    """包 → 列表里的一行。

    `current` 是**当前这套解析器能产出的身份集合**（`(parser_id, parser_version)` 的集合）——
    一个解析器可能产好几种（本地的文本/PDF/HTML 三种），所以这里收集合而不是单个 id。
    `stale` 因此是**算出来的**（不存）；
    `chunk_count` / `chars` 允许调用方直接给（列表是按 SQL 算的，没把块正文读出来）。
    """

    parsed = packet.parsed
    return MaterialSummaryView(
        material_id=packet.material_id,
        origin=parsed.origin,
        title=parsed.title,
        source_ref=parsed.source_ref,
        media_type=parsed.media_type,
        bytes_len=parsed.bytes_len,
        chunk_count=len(parsed.chunks) if chunk_count is None else chunk_count,
        chars=sum(len(chunk.text) for chunk in parsed.chunks) if chars is None else chars,
        parser_id=parsed.parser_id,
        parser_version=parsed.parser_version,
        truncated=parsed.truncated,
        warnings=parsed.warnings,
        stale=needs_reparse(packet, current=current),
    )


def needs_reparse(
    packet: MaterialPacket, *, current: frozenset[tuple[str, str]]
) -> bool:
    """这份包该不该用当前解析器重读一遍（C-2c 的 `reparse` 与列表里的提示都用它）。

    `current` 是"当前解析器能产出的身份集合"；不在里面就是旧的（换了 docling、或解析器升级过）。
    """

    if packet.schema_version != MATERIAL_SCHEMA_VERSION:
        return True
    parsed = packet.parsed
    return (parsed.parser_id, parsed.parser_version) not in current


def _blocks_in(section_blocks: Sequence[BlockView]):
    for block in section_blocks:
        yield block
        yield from _blocks_in(block.children)


def _where_of(section_id: str, block: BlockView) -> str:
    """这一块在文档里的地址（形状与 `Target.label()` 一致，便于对照）。"""

    if block.feature_slug is not None and block.field is not None:
        return f"{section_id}/{block.feature_slug}.{block.field}"
    if block.feature_slug is not None:
        return f"{section_id}/{block.feature_slug}"
    if block.field is not None:
        return f"{section_id}.{block.field}"
    return section_id


def search_hits(
    query: str,
    *,
    document: DocumentView,
    evidence: Sequence[tuple[str, str]],
    opinions: Sequence[OpinionSourceView],
    materials: Sequence[MaterialHit] = (),
    limit: int = SEARCH_LIMIT,
) -> SearchResultsView:
    """在这个项目里搜（F6）。**纯函数**：现查现算，不建索引、不缓存。

    四条规矩：
    ① **空查询返回空**——绝不"不填就是全量"；
    ② 大小写不敏感的子串匹配（单用户够用；不做模糊排序）；
    ③ 先到先得、到 `limit` 就停，并明确 `truncated=True`；
    ④ 条目先于功能与出处（人最常找的是"文档里哪句写了"）。
    """

    needle = query.strip().casefold()
    if needle == "":
        return SearchResultsView(query=query, hits=(), truncated=False)

    found: list[SearchHitView] = []

    def full() -> bool:
        return len(found) > limit

    def add(hit: SearchHitView) -> None:
        if not full():
            found.append(hit)

    for section in document.sections:
        section_id = str(section.section_id)
        for block in _blocks_in(section.blocks):
            where = _where_of(section_id, block)
            if block.feature_slug is not None:
                # 功能命中：slug / 名称 / **这棵子树里的任何一条内容**（"这个功能里提到过"）
                name = " ".join(
                    entry.text
                    for child in block.children
                    if child.field == "name"
                    for entry in child.entries
                )
                haystack = " ".join(
                    [str(block.feature_slug), name]
                    + [
                        entry.text
                        for child in _blocks_in(block.children)
                        for entry in child.entries
                    ]
                )
                if needle in haystack.casefold():
                    add(
                        SearchHitView(
                            kind="feature",
                            label=summarize_line(name or str(block.feature_slug)),
                            ref=str(block.block_id),
                            where=where,
                        )
                    )
            for entry in block.entries:
                if needle in entry.text.casefold():
                    add(
                        SearchHitView(
                            kind="entry",
                            label=summarize_line(entry.text),
                            ref=str(entry.entry_id),
                            where=where,
                        )
                    )

    for eid, text in evidence:
        if needle in text.casefold():
            add(SearchHitView(kind="evidence", label=summarize_line(text), ref=eid, where="出处"))

    for opinion in opinions:
        if needle in opinion.claim.casefold():
            add(
                SearchHitView(
                    kind="opinion",
                    label=summarize_line(opinion.claim),
                    ref=opinion.round_id,
                    where=opinion.seat_id,
                )
            )

    # 第五类来源：材料块（`P18` C-2c-1）。排在最后——它是外部事实、不算权威；
    # 顺序就用检索器给的顺序（`MaterialHit.score` 的消费者在这一处，不再重排）。
    for material in materials:
        add(
            SearchHitView(
                kind="material",
                label=f"{material.title}：{summarize_line(material.excerpt)}",
                # 落点是**块 id**：前端据此滚到本轮材料卡的那一块（没进过轮次就没有块可跳）
                ref=material.chunk_id,
                where=f"材料 · {material.anchor}",
            )
        )

    return SearchResultsView(query=query, hits=tuple(found[:limit]), truncated=full())


def answer_text(question: Question, answer: Answer) -> str:
    """一条答案的人话写法：「题目 → 选项；备注」。

    这是**唯一一份**：写进账本的那条出处（`use_cases`）与界面读的历史答案
    （`answer_batches`）都用它，两处不能各渲染一套。
    """

    chosen = "、".join(
        option.text for option in question.options if option.option_id in answer.selected
    )
    head = f"{question.title} → {chosen or answer.text.strip() or '（空答）'}"
    note = answer.text.strip()
    return f"{head}；备注：{note}" if chosen and note else head


def answer_batches(round_: Round) -> tuple[AnswerBatchView, ...]:
    """每一批问卷的答题情况。题面从**问卷历史**里按 `(批次, 题号)` 回查（不猜、不编）。"""

    by_id: dict[tuple[str, str], Question] = {
        (str(form.form_id), str(question.question_id)): question
        for form in round_.questionnaire_history
        for question in form.questions
    }
    batches: list[AnswerBatchView] = []
    for answer_set in round_.answer_sets:
        form_id = str(answer_set.form_id)
        batches.append(
            AnswerBatchView(
                form_id=form_id,
                answered=len(answer_set.answers),
                skipped=len(answer_set.skipped),
                answers=tuple(
                    answer_text(by_id[(form_id, str(answer.question_id))], answer)
                    for answer in answer_set.answers
                    if (form_id, str(answer.question_id)) in by_id
                ),
            )
        )
    return tuple(batches)


@dataclass(frozen=True, slots=True)
class ReadinessItemView:
    section_id: str
    code: str
    title: str
    detail: str
    severity: str


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    project: ProjectSummaryView
    document: DocumentView
    round: RoundView | None
    readiness: tuple[ReadinessItemView, ...] = ()


@dataclass(frozen=True, slots=True)
class VersionSummaryView:
    """版本列表里的一行（底栏版本历史抽屉读它）。"""

    version_no: int
    trigger: str
    trigger_label: str
    summary: str
    created_at: datetime
    changed_block_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VersionListView:
    versions: tuple[VersionSummaryView, ...]
    current: int


@dataclass(frozen=True, slots=True)
class EntrySnapshotView:
    entry_id: str
    text: str
    refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChangedEntryView:
    before: EntrySnapshotView
    after: EntrySnapshotView


@dataclass(frozen=True, slots=True)
class DiffGroupView:
    target_label: str
    added: tuple[EntrySnapshotView, ...]
    removed: tuple[EntrySnapshotView, ...]
    changed: tuple[ChangedEntryView, ...]


@dataclass(frozen=True, slots=True)
class DiffView:
    from_version_no: int
    to_version_no: int
    groups: tuple[DiffGroupView, ...]


@dataclass(frozen=True, slots=True)
class RollbackResult:
    version_no: int
    document: DocumentView


@dataclass(frozen=True, slots=True)
class ChainItemView:
    eid: str
    kind: str
    kind_label: str
    text: str
    summary: str


@dataclass(frozen=True, slots=True)
class EvidenceLookupView:
    """按编号查处方：原话、来路、链条、以及"现在被谁用着"（`04` F5）。"""

    eid: str
    kind: str
    kind_label: str
    text: str
    summary: str
    raw_span: str | None
    rationale: str | None
    derived_from: tuple[str, ...]
    round_id: str | None
    seat_id: str | None
    question_id: str | None
    #: 外部材料的指称：`attachment:需求.md#c00` / URL（工具层 C-2；别的 kind 是 None）
    external_ref: str | None
    chain: tuple[ChainItemView, ...]
    used_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SlotSchemaView:
    """章节槽（只有 s8 有六个）：界面靠它知道"这一章还有哪些固定的子块"。"""

    name: str
    title: str
    kind: str


@dataclass(frozen=True, slots=True)
class SectionSchemaView:
    section_id: str
    title: str
    #: 这一章是干什么的（`domain/shared/sections.py::SectionSpec.purpose` 的唯一一份）
    purpose: str
    allows_features: bool
    slots: tuple[SlotSchemaView, ...]


@dataclass(frozen=True, slots=True)
class FeatureFieldSchemaView:
    name: str
    title: str
    kind: str
    multiple: bool
    required: bool


@dataclass(frozen=True, slots=True)
class LimitsView:
    """三处上限：界面要显示"第 2/3 轮""已答 4/5"，不该自己抄一份。"""

    max_seat_rounds: int
    max_questionnaires: int
    max_open_items_per_batch: int


@dataclass(frozen=True, slots=True)
class SchemaView:
    """`GET /schema`：现行结构（13 章 + 功能字段表 + 上限常量）。

    它**不是**文档内容——`DocumentView` 才是；这里只是"这套结构长什么样"。
    """

    sections: tuple[SectionSchemaView, ...]
    feature_fields: tuple[FeatureFieldSchemaView, ...]
    limits: LimitsView


def schema_view() -> SchemaView:
    """把三张表与三个常量读出来。纯函数：无 IO、不开事务、不碰任何仓储。"""

    return SchemaView(
        sections=tuple(
            SectionSchemaView(
                section_id=str(spec.section_id),
                title=spec.title,
                purpose=spec.purpose,
                allows_features=spec.allows_features,
                slots=tuple(
                    SlotSchemaView(name=slot.name, title=slot.title, kind=slot.kind.value)
                    for slot in SECTION_SLOTS.get(spec.section_id, ())
                ),
            )
            for spec in SECTIONS
        ),
        feature_fields=tuple(
            FeatureFieldSchemaView(
                name=spec.name,
                title=spec.title,
                kind=spec.kind.value,
                multiple=spec.multiple,
                required=spec.required,
            )
            for spec in FEATURE_FIELDS
        ),
        limits=LimitsView(
            max_seat_rounds=MAX_SEAT_ROUNDS,
            max_questionnaires=MAX_QUESTIONNAIRES,
            max_open_items_per_batch=MAX_OPEN_ITEMS_PER_BATCH,
        ),
    )


def feature_coverage_advisory(round_: Round) -> str | None:
    """这一轮"点到了功能、却一条功能内容都没有"的置顶告警（报告 F1）。

    纯投影：只读 claims 与 draft，不写任何东西、不进文档——所以它只出现在**这一轮**的卡片上
    （草案卡的最上面，前端 `DraftDrawer` 已经按这个顺序渲染 `advisories`）。

    判据刻意保守（宁可漏报，不许骚扰）：
    - 只有在"主张点名过功能"（claims 里有 `feature_slug`）时才可能报；
    - 草案里**一条** s5 功能提案都没有，才报；
    - 输入本来没提功能（如纯偏好那一轮）→ 不报。
    """

    if round_.draft is None:
        return None
    named = sorted(
        {
            str(claim.target.feature_slug)
            for claim in round_.claims
            if claim.target.feature_slug is not None
        }
    )
    if not named:
        return None
    if any(proposal.target.feature_slug is not None for proposal in round_.draft.proposals):
        return None
    return (
        f"⚠ 这一轮一条功能内容都没有：你点到了「{'、'.join(named)}」"
        f"{len(named)} 个功能，但草案里 s5 没有任何提案。"
        "要么这一轮补上功能内容（再提一轮，把每个功能一句话说清），"
        "要么明确「这轮不写功能」。"
    )


#: "重试会从哪继续"的人话，对着 `graph.RESUME_ROUTES` 写：认不出的 step → None（不猜）
RETRY_FROM_LABELS: dict[str, str] = {
    "started": "从切分重新开始",
    "claims_ready": "等你确认复述卡（不用重跑模型）",
    "seats_running": "从席位轮继续（已交回的不重跑）",
}


def retry_from_label(step: str) -> str | None:
    """失败停在 `step` 时，重试会从哪儿继续（纯函数，认不出就 None）。"""

    return RETRY_FROM_LABELS.get(step)


#: C4：连续几轮"给过它提议的、却一条都没被采用"，才值得提醒一句
PROPOSAL_IGNORE_THRESHOLD = 3

#: 那句话（**纯投影**：只有这一轮在等确认、且连续轮数到阈值时才出现）
PROPOSAL_IGNORE_ADVISORY = (
    "⚠ 最近连续 {n} 轮你一条「它提议的」都没采用——下次开场卡可以少请几个席位"
    "（席位出题与判断照旧）"
)


def proposal_ignore_streak(outcomes: Sequence[RoundOutcomeView]) -> int:
    """最近**连续**几轮"给过它提议的、但一条都没被采用"（C4 的判据）。

    从最新往回数，遇到下面任一条就**停**（不跨过去数）：
    - 那一轮没走到确认（`accepted is None`）——什么也说明不了；
    - 那一轮压根没有"它提议的"（`offered` 是 None 或 0）——没给过，谈不上不采用；
    - 那一轮采用了至少一条——streak 断了。
    """

    streak = 0
    for outcome in outcomes:
        if outcome.proposed_offered is None or outcome.proposed_accepted is None:
            break
        if outcome.proposed_offered <= 0 or outcome.proposed_accepted > 0:
            break
        streak += 1
    return streak


def round_view(
    round_: Round,
    *,
    seat_titles: Mapping[str, str] = {},
    catalog: SeatCatalog | None = None,
    ledger: EvidenceLedger | None = None,
    materials: Sequence[MaterialPacket] = (),
    web_search_available: bool = False,
) -> RoundView:
    warnings: dict[str, str] = {}
    advisories: tuple[str, ...] = ()
    draft_view: DraftView | None = None
    if round_.draft is not None:
        violations = ScopeGuard().check(round_.draft.proposals, write_scope=round_.write_scope)
        warnings = {str(item.proposal_id): item.message for item in violations}
        # 置顶告警（报告 F1）："点到了功能、却一条功能内容都没有"比"哪条越界"更要紧
        coverage = feature_coverage_advisory(round_)
        streak = round_.proposal_ignore_streak
        ignore_hint = (
            (PROPOSAL_IGNORE_ADVISORY.format(n=streak),)
            if streak >= PROPOSAL_IGNORE_THRESHOLD
            else ()
        )
        advisories = (
            ((coverage,) if coverage else ())
            + ignore_hint
            + tuple(f"{item.target.label()}：{item.message}" for item in violations)
        )
        draft_view = _draft_view(round_.draft, warnings=warnings)
    questionnaire = round_.questionnaire
    nominated = _suggestions(round_, catalog=catalog, seat_titles=seat_titles)
    return RoundView(
        round_id=str(round_.round_id),
        project_id=str(round_.project_id),
        user_input=round_.user_input,
        step=str(round_.step),
        waiting_on=str(round_.waiting_on),
        seat_round_count=round_.seat_round_count,
        seats=tuple(round_.seats),
        active_seats=tuple(round_.active_seats),
        nominated_seats=nominated,
        available_seats=_available_seats(
            round_,
            catalog=catalog,
            already={seat.seat_id for seat in nominated},
        ),
        claims=tuple(_claim_view(claim) for claim in round_.claims),
        seat_packets=_seat_packets(
            round_, seat_titles=seat_titles, ledger=ledger, catalog=catalog
        ),
        dropped=tuple(
            DroppedView(
                handle=item.handle,
                seat_id=item.seat_id,
                pass_index=item.pass_index,
                reason=item.reason,
                source=item.source.value,
            )
            for item in round_.dropped
        ),
        questions=_question_views(round_),
        # 清单项的真相源是**轮次**（历次累积）：挂在当轮问卷上的那份换一批就没了（F8）
        list_items=tuple(item.text for item in round_.list_items),
        draft=draft_view,
        advisories=advisories,
        failure_reason=round_.failure.reason if round_.failure is not None else None,
        failure_at_step=(
            None if round_.failure is None else round_.failure.at_step.value
        ),
        retry_from=(
            None if round_.failure is None else retry_from_label(round_.failure.at_step.value)
        ),
        abandon_reason=round_.abandon_reason,
        confirmed_version_no=round_.confirmed_version_no,
        form_id=None if questionnaire is None else str(questionnaire.form_id),
        answer_sets=answer_batches(round_),
        materials=round_material_views(round_, materials=materials),
        web_queries=_web_query_views(round_),
        web_search_available=web_search_available,
    )


def _web_query_views(round_: Round) -> tuple[WebQueryView, ...]:
    """提名的检索词 + "用户勾没勾"。

    勾选按**文本**对（用户勾的就是那一句原话）；投影里给一个布尔，
    是为了刷新之后复述卡还能显示"你勾的是这条"——UI 不用自己维护第二份状态。
    """

    chosen = set(round_.web_queries_chosen)
    return tuple(
        WebQueryView(query=item.query, why=item.why, chosen=item.query in chosen)
        for item in round_.web_queries
    )


def round_material_views(
    round_: Round, *, materials: Sequence[MaterialPacket]
) -> tuple[RoundMaterialView, ...]:
    """`Round.materials`（引用）+ 材料包 → 界面要的材料卡。

    只有**这一轮真的引用过**的块才进卡片——材料是"带进来的"，不是"项目里有的"。
    """

    refs: dict[str, list[tuple[str, str]]] = {}
    for ref in round_.materials:
        refs.setdefault(ref.material_id, []).append((ref.chunk_id, str(ref.eid)))
    by_id = {packet.material_id: packet for packet in materials}

    cards: list[RoundMaterialView] = []
    for material_id, entries in refs.items():
        packet = by_id.get(material_id)
        if packet is None:
            continue
        by_chunk = {chunk.chunk_id: chunk for chunk in packet.parsed.chunks}
        chunks = tuple(
            RoundMaterialChunkView(
                chunk_id=chunk_id,
                eid=eid,
                anchor=by_chunk[chunk_id].anchor,
                text=by_chunk[chunk_id].text,
            )
            for chunk_id, eid in entries
            if chunk_id in by_chunk
        )
        cards.append(
            RoundMaterialView(
                material_id=material_id,
                title=packet.parsed.title,
                source_ref=packet.parsed.source_ref,
                origin=packet.parsed.origin,
                parser_id=packet.parsed.parser_id,
                chunk_count=len(packet.parsed.chunks),
                chunks=chunks,
                truncated=len(chunks) < len(packet.parsed.chunks),
                warnings=packet.parsed.warnings,
            )
        )
    return tuple(cards)


def workspace_view(
    *,
    project: ProjectSummaryView,
    document: DocumentView,
    round_: Round | None,
    seat_titles: Mapping[str, str] = {},
    catalog: SeatCatalog | None = None,
    ledger: EvidenceLedger | None = None,
    materials: Sequence[MaterialPacket] = (),
    web_search_available: bool = False,
    readiness: Sequence[ReadinessItem] = (),
) -> WorkspaceView:
    return WorkspaceView(
        project=project,
        document=document,
        round=(
            None
            if round_ is None
            else round_view(
                round_,
                seat_titles=seat_titles,
                catalog=catalog,
                ledger=ledger,
                materials=materials,
                web_search_available=web_search_available,
            )
        ),
        readiness=tuple(readiness_view(item) for item in readiness),
    )


def readiness_view(item: ReadinessItem) -> ReadinessItemView:
    return ReadinessItemView(
        section_id=item.section_id,
        code=item.code,
        title=item.title,
        detail=item.detail,
        severity=item.severity.value,
    )


def version_summary_view(version: Version) -> VersionSummaryView:
    return VersionSummaryView(
        version_no=version.version_no,
        trigger=version.trigger.value,
        trigger_label=TRIGGER_LABELS[version.trigger],
        summary=version.summary,
        created_at=version.created_at,
        changed_block_ids=tuple(str(item) for item in version.changed_block_ids),
    )


def _entry_snapshot_view(item) -> EntrySnapshotView:  # type: ignore[no-untyped-def]
    return EntrySnapshotView(
        entry_id=str(item.entry_id),
        text=item.text,
        refs=tuple(str(ref) for ref in item.refs),
    )


def diff_view(before: Version, after: Version) -> DiffView:
    """两个版本 → 界面要的形状（分组、前后对照、编号照抄）。"""

    return DiffView(
        from_version_no=before.version_no,
        to_version_no=after.version_no,
        groups=tuple(
            DiffGroupView(
                target_label=group.target_label,
                added=tuple(_entry_snapshot_view(item) for item in group.added),
                removed=tuple(_entry_snapshot_view(item) for item in group.removed),
                changed=tuple(
                    ChangedEntryView(
                        before=_entry_snapshot_view(item.before),
                        after=_entry_snapshot_view(item.after),
                    )
                    for item in group.changed
                ),
            )
            for group in diff_snapshots(before.snapshot, after.snapshot)
        ),
    )


def _chain_item_view(evidence) -> ChainItemView:  # type: ignore[no-untyped-def]
    return ChainItemView(
        eid=str(evidence.eid),
        kind=evidence.kind.value,
        kind_label=KIND_LABELS[evidence.kind],
        text=evidence.text,
        summary=summarize_line(evidence.text),
    )


def used_by_entries(view: DocumentView, eid: EvidenceId) -> tuple[str, ...]:
    """文档里哪些条目引用了这个编号（界面据此滚到那条内容并闪一下）。

    与导出附录 A 的准入判据（`deliverable.cited_evidence_ids`）**共用同一份走法**——
    两处各写一遍，迟早出现"界面说引用了、附录里查不到"。
    """

    return tuple(
        str(entry.entry_id)
        for entry in iter_document_entries(view)
        if eid in entry.refs
    )


def evidence_lookup_view(
    evidence,  # type: ignore[no-untyped-def]
    *,
    chain: Sequence,  # type: ignore[type-arg]
    used_by: tuple[str, ...],
) -> EvidenceLookupView:
    source = evidence.source
    return EvidenceLookupView(
        eid=str(evidence.eid),
        kind=evidence.kind.value,
        kind_label=KIND_LABELS[evidence.kind],
        text=evidence.text,
        summary=summarize_line(evidence.text),
        # 原话只在界面上给（点编号能看），导出里没有（`04` D5）
        raw_span=source.raw_span,
        rationale=source.rationale,
        derived_from=tuple(str(ref) for ref in source.derived_from),
        round_id=None if source.round_id is None else str(source.round_id),
        seat_id=source.seat_id,
        question_id=None if source.question_id is None else str(source.question_id),
        external_ref=source.external_ref,
        chain=tuple(_chain_item_view(item) for item in chain),
        used_by=used_by,
    )


def _suggestions(
    round_: Round, *, catalog: SeatCatalog | None, seat_titles: Mapping[str, str]
) -> tuple[SeatSuggestionView, ...]:
    """开场卡要的就是"提名了谁 + 为什么请它"（F8）。三种情形，优先级从高到低：

    1. 用户已经确认过编制 → 展示他选的那批（可能加了名单外的席位），
       名单里有的仍带上分析者给的理由；
    2. 还没确认、但有分析者的名单 → 展示名单（`source="agent"`）；
    3. 都没有（老轮次、或分析失败停在 started）→ 回落目录匹配，并标 `source="catalog"`。
    """

    if catalog is None:
        return tuple(
            SeatSuggestionView(seat_id=seat_id, title=seat_titles.get(seat_id, seat_id), focus="")
            for seat_id in round_.seats
        )
    by_nomination = {item.seat_id: item for item in round_.nominated_seats}

    def for_seat(seat: SeatDefinition) -> SeatSuggestionView:
        suggestion = _suggestion(seat, user_input=round_.user_input)
        item = by_nomination.get(seat.seat_id)
        if item is None:
            return suggestion
        return replace(suggestion, why=item.why, source="agent")

    if round_.seats:
        return tuple(for_seat(catalog.by_id(seat_id)) for seat_id in round_.seats)
    if round_.nominated_seats:
        return tuple(
            for_seat(catalog.by_id(item.seat_id)) for item in round_.nominated_seats
        )
    # 老轮次：分析那会儿还没有"挑席位"这一步，只好按目录匹配，并如实标出来源
    return tuple(
        replace(_suggestion(seat, user_input=round_.user_input), source="catalog")
        for seat in catalog.nominate(user_input=round_.user_input)
    )


def _available_seats(
    round_: Round, *, catalog: SeatCatalog | None, already: set[str]
) -> tuple[SeatSuggestionView, ...]:
    """目录里还有哪些席位可以自己加（名单与已选之外的全部）。"""

    if catalog is None:
        return ()
    return tuple(
        replace(_suggestion(seat, user_input=round_.user_input), source="catalog")
        for seat in catalog.all()
        if seat.seat_id not in already
    )


def _suggestion(seat: SeatDefinition, *, user_input: str) -> SeatSuggestionView:
    return SeatSuggestionView(
        seat_id=seat.seat_id,
        title=seat.title,
        focus=seat.focus,
        always_on=seat.always_on,
        matched_keywords=tuple(
            keyword for keyword in seat.trigger_keywords if keyword in user_input
        ),
    )


def _claim_view(claim) -> ClaimView:  # type: ignore[no-untyped-def]
    note = None
    if claim.assumption is not None:
        note = (
            f"失败后果：{claim.assumption.failure_consequence}；"
            f"验证：{claim.assumption.verification}"
        )
    return ClaimView(
        claim_id=claim.claim_id,
        eid=str(claim.eid),
        text=claim.text,
        label=claim.label.value,
        target_label=claim.target.label(),
        raw_span=claim.raw_span,
        assumption_note=note,
    )


def _seat_packets(
    round_: Round,
    *,
    seat_titles: Mapping[str, str],
    ledger: EvidenceLedger | None = None,
    catalog: SeatCatalog | None = None,
) -> tuple[SeatPacketView, ...]:
    by_seat = {review.seat_id: review for review in round_.current_reviews}
    # 还没选席位时（开场卡阶段）显示被提名的席位，状态是"待启动"——
    # 界面在开场卡上就要看到"接下来谁会跑"。
    seat_ids = (
        round_.seats
        or tuple(item.seat_id for item in round_.nominated_seats)
        or tuple(
            seat.seat_id
            for seat in (catalog.nominate(user_input=round_.user_input) if catalog else ())
        )
    )
    packets: list[SeatPacketView] = []
    for seat_id in seat_ids:
        review = by_seat.get(seat_id)
        opinions = (
            tuple(
                OpinionView(
                    local_id=opinion.local_id,
                    form=opinion.form.value,
                    about=opinion.about,
                    claim=opinion.claim,
                    because=opinion.because,
                    eids=tuple(str(eid) for eid in opinion.evidence_refs),
                    basis_note=opinion.basis_note,
                    eid=_eid_for(review, opinion, ledger=ledger, round_id=round_.round_id),
                )
                for opinion in review.opinions
            )
            if review is not None
            else ()
        )
        packets.append(
            SeatPacketView(
                seat_id=seat_id,
                title=seat_titles.get(seat_id, seat_id),
                status=SEAT_COMPLETED if review is not None else SEAT_PENDING,
                pass_index=review.pass_index if review is not None else round_.seat_round_count,
                basis=review.basis if review is not None else "",
                opinions=opinions,
                judgment_count=sum(
                    1 for opinion in opinions if opinion.form == OpinionForm.JUDGMENT.value
                ),
                question_count=sum(
                    1 for opinion in opinions if opinion.form == OpinionForm.QUESTION.value
                ),
            )
        )
    return tuple(packets)


def _eid_for(
    review: SeatReview | None,  # type: ignore[no-untyped-def]
    opinion,
    *,
    ledger: EvidenceLedger | None,
    round_id: RoundId,
) -> str | None:
    """判断自己拿到的编号：按 **(轮次, seat_id, f"p{pass}:{local_id}")** 从账本反查。

    轮次不能省：趟数每轮从 1 重来，只按 (席位, 条目) 反查会把上一轮的编号显示成这一轮的
    （真跑抓到过，P22 回归）。
    """

    if ledger is None or review is None:
        return None
    evidence = ledger.find_by_local(
        seat_id=review.seat_id,
        local_id=f"p{review.pass_index}:{opinion.local_id}",
        round_id=round_id,
    )
    return None if evidence is None else str(evidence.eid)


def _question_views(round_: Round) -> tuple[QuestionView, ...]:
    questionnaire = round_.questionnaire
    if questionnaire is None:
        return ()
    return tuple(
        QuestionView(
            question_id=str(question.question_id),
            kind=question.kind.value,
            type=question.type.value,
            title=question.title,
            why=question.why,
            target_label=question.target.label(),
            options=tuple((option.option_id, option.text) for option in question.options),
            consequence_if_skipped=question.consequence_if_skipped,
            eids=tuple(str(eid) for eid in question.evidence_refs),
            asked_by=tuple(question.asked_by),
            blocking=question.blocking,
        )
        for question in questionnaire.questions
    )


def _proposal_view(proposal: Proposal, *, warning: str | None) -> ProposalView:
    return ProposalView(
        proposal_id=str(proposal.proposal_id),
        entry_id=str(proposal.entry_id),
        op=proposal.op.value,
        target_label=proposal.target.label(),
        text=proposal.text,
        origin_type=proposal.origin.type.value,
        eids=tuple(str(eid) for eid in proposal.origin.refs),
        rationale=proposal.origin.rationale,
        kind=proposal.kind.value if proposal.kind is not None else None,
        warning=warning,
    )


def _draft_view(draft: Draft, *, warnings: Mapping[str, str]) -> DraftView:
    proposals = tuple(
        _proposal_view(proposal, warning=warnings.get(str(proposal.proposal_id)))
        for proposal in draft.proposals
    )
    by_id = {item.proposal_id: item for item in proposals}
    groups = tuple(
        (title, tuple(by_id[str(proposal.proposal_id)] for proposal in items))
        for title, items in draft.grouped()
    )
    return DraftView(
        draft_id=str(draft.draft_id),
        groups=groups,
        proposals=proposals,
        gaps=tuple(draft.gaps),
    )
