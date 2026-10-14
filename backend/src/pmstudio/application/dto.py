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
from pmstudio.application.sim_review import (
    MAX_SIM_REVIEW_FEATURES,
    MAX_SIM_REVIEW_PERSONAS,
    MAX_SIM_REVIEWS_PER_DRAFT,
)
from pmstudio.domain.authoring.diff import diff_snapshots
from pmstudio.domain.authoring.readiness import ReadinessItem
from pmstudio.domain.authoring.schema import FEATURE_FIELDS
from pmstudio.domain.authoring.version import Version
from pmstudio.domain.authoring.views import BlockView, DocumentView
from pmstudio.domain.discussion.thread import MAX_DISCUSSION_TURNS, Discussion
from pmstudio.domain.exploration.exploration import Exploration
from pmstudio.domain.memory.memory import MemoryEntry
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
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import EvidenceId, RoundId
from pmstudio.domain.shared.sections import SECTION_SLOTS, SECTIONS
from pmstudio.domain.shared.template import DocumentTemplate

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
    #: 这一包是谁交回的（§4.4 信封的 `packet_id`）。None = 修之前落库的老轮次，
    #: 前端按缺失回退 (seat_id, pass_index) 启发式——见 `frontend/.../dropped.ts`。
    packet_id: str | None


@dataclass(frozen=True, slots=True)
class DroppedView:
    """一条"没进卡片"的留痕：地址、席位、第几趟、原因、来源（`04` C5 后半）。

    `packet_id` 是**可选**的：`None` = 不来自模型回包（assembly 归因 / tool 失败），
    或老数据。前端 `dropsFor` 有它才按 id 精确挂载，没有就回退 `(seat_id, pass_index)`。
    """

    handle: str
    seat_id: str | None
    pass_index: int
    reason: str
    source: str
    packet_id: str | None = None


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
    #: 编号里有账本上的 SIMULATED（M11）：界面打「模拟评测依据」tag、eval 读它
    simulation_based: bool = False


@dataclass(frozen=True, slots=True)
class DraftView:
    draft_id: str
    groups: tuple[tuple[str, tuple[ProposalView, ...]], ...]
    proposals: tuple[ProposalView, ...]
    gaps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemoryProposalView:
    """草案卡上"这一轮攒下的记忆"的一行（M6）。

    它是**提议**：界面默认**不勾**，勾了才会随 `/confirm` 生效（`02` §11.0.44）——
    记忆写错的代价是污染以后每一轮的判断，所以比正文提案更保守。
    """

    proposal_id: str
    op: str
    kind: str
    text: str
    refs: tuple[str, ...]
    reason: str
    target_id: str | None = None


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
class SimSliceView:
    """模拟抽屉里的一条处境切片：分组展示 + 锚点可跳回原文。"""

    local_id: str
    situation: str
    job: str
    trigger: str
    workaround: str
    anchors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SimHypothesisView:
    """模拟抽屉里的一条假设：陈述 + 从哪推的 + 怎么被推翻 + 要什么证据。"""

    local_id: str
    slice_id: str
    statement: str
    grounding: str
    based_on: tuple[str, ...]
    falsification: str
    evidence_needed: str


@dataclass(frozen=True, slots=True)
class SimUnknownView:
    """模拟抽屉里的一条盲区：不知道什么 + 为什么值得问 + 怎么去验证。"""

    local_id: str
    statement: str
    why_it_matters: str
    how_to_find_out: str


@dataclass(frozen=True, slots=True)
class SimInquiryView:
    """一轮的模拟调研产物（没跑过是 None，不是空对象——抽屉靠它决定出不出现）。"""

    slices: tuple[SimSliceView, ...]
    hypotheses: tuple[SimHypothesisView, ...]
    unknowns: tuple[SimUnknownView, ...]
    runs: int
    chosen: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SimWalkStepView:
    step_index: int
    step_text: str
    from_doc: str
    action: str
    outcome: str
    need: str


@dataclass(frozen=True, slots=True)
class SimFrictionView:
    local_id: str
    kind: str
    entry_id: str
    field: str
    quote: str
    not_documented: bool
    blocks_flow: bool
    note: str


@dataclass(frozen=True, slots=True)
class SimAcceptanceCheckView:
    local_id: str
    entry_id: str
    status: str
    note: str


@dataclass(frozen=True, slots=True)
class SimFeatureReviewView:
    feature_slug: str
    started: bool
    walkthrough: tuple[SimWalkStepView, ...]
    frictions: tuple[SimFrictionView, ...]
    acceptance_checks: tuple[SimAcceptanceCheckView, ...]
    verdict: str
    verdict_because: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SimPersonaReviewView:
    persona_id: str
    title: str
    features: tuple[SimFeatureReviewView, ...]


@dataclass(frozen=True, slots=True)
class SimReviewSummaryView:
    """三档**原始计数**（按 persona×功能 对）：不给百分比、不给分数（口径红线）。"""

    smooth: int
    friction: int
    blocked: int


@dataclass(frozen=True, slots=True)
class SimReviewView:
    """一趟模拟评测的投影（没跑过是 None——抽屉靠它决定出不出现）。"""

    runs: int
    max_runs: int
    evaluated_version: int | None
    personas: tuple[SimPersonaReviewView, ...]
    summary: SimReviewSummaryView
    advisories: tuple[str, ...]


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
    #: **当前批**的题（界面用的就是这份：换一批就换一份）
    questions: tuple[QuestionView, ...]
    #: 本轮**问过的每一道题**（逐批追加，按出题顺序）——`answer_sets` 的问题侧。
    #: 为什么要有它：`questions` 换批就没了，而"这一轮问没问过那道要你拍板的题"
    #: 是**整轮**的事实（真跑 `r-8889fcf0`：互斥题在第 1 批、当前批只剩 gap）。
    #: 界面不用它，判据（eval）与复盘用它。
    asked_questions: tuple[QuestionView, ...]
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
    #: 本轮带进提示词但零引用的材料块出处编号（F35）：先量不修，只留数据。
    unreferenced_materials: tuple[str, ...] = ()
    #: 分析者提名的检索词（**只是提名**；勾了才会真去查）
    web_queries: tuple[WebQueryView, ...] = ()
    #: 模拟调研产物（没跑过是 None；抽屉与 eval 读它）
    sim_inquiry: SimInquiryView | None = None
    #: 模拟评测产物（M11；没跑过是 None）
    sim_review: SimReviewView | None = None
    #: 联网工具现在能不能用（没配密钥就是 False）——复述卡据此决定要不要出现那一栏
    web_search_available: bool = False
    #: 分析者交回的「我读了什么才切出这些主张」——复述卡在批准主张之前显示它，
    #: 用户据此看出分析者读没读对章节（老行没有 → 空串）
    analysis_basis: str = ""
    #: 这一轮攒下的记忆提议（M6）：**提议**，默认不勾；勾了随 `/confirm` 一起生效
    memory_proposals: tuple[MemoryProposalView, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectSummaryView:
    project_id: str
    title: str
    version_no: int
    updated_at: datetime
    #: 建项目时选的模板（行快照里带的定义；首页密行渲染用，`02` §11.0.68）。
    #: REQUIRED：构造时就必须想清楚，不许留空——缺省 standard 由调用方显式传。
    template_id: str
    template_title: str


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
class MemoryEntryView:
    """记忆抽屉读的一行（M6）。只读投影：真相源是 `project_memory` 表。"""

    memory_id: str
    kind: str
    text: str
    refs: tuple[str, ...]
    round_id: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    project: ProjectSummaryView
    document: DocumentView
    round: RoundView | None
    readiness: tuple[ReadinessItemView, ...] = ()
    #: 已确认的跨轮记忆（`05` 的"席位必读那一份"的现行版）。老 payload 没有 → 空。
    memory: tuple[MemoryEntryView, ...] = ()


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
    """几处上限：界面要显示"第 2/3 轮""已答 4/5"，不该自己抄一份。"""

    max_seat_rounds: int
    max_questionnaires: int
    max_open_items_per_batch: int
    #: 模拟评测（M11）：选择器的勾选上限
    max_sim_review_personas: int
    max_sim_review_features: int
    #: 讨论线程的 turn 上限（M13）：讨论 tab 的"还能说几轮"读这里，不再离线抄
    max_discussion_turns: int


@dataclass(frozen=True, slots=True)
class PersonaSchemaView:
    """选择器里的一个模拟使用者（M11）。真相源是 `personas.toml`，界面不抄。"""

    persona_id: str
    title: str
    situation: str


@dataclass(frozen=True, slots=True)
class RoleSchemaView:
    """开聊卡上的一个固定角色（M9）。真相源是 `infrastructure/discussion/roles.toml`，
    界面不再自己抄那三行——抄一份就是两份真相源，改了角色表只会看到一半生效。
    """

    role_id: str
    title: str
    focus: str


@dataclass(frozen=True, slots=True)
class TemplateSectionSchemaView:
    section_id: str
    title: str
    purpose: str
    role: str
    slots: tuple[SlotSchemaView, ...]


@dataclass(frozen=True, slots=True)
class TemplateSchemaView:
    template_id: str
    title: str
    sections: tuple[TemplateSectionSchemaView, ...]


@dataclass(frozen=True, slots=True)
class TemplateSectionView:
    section_id: str
    title: str
    purpose: str
    role: str
    slots: tuple[SlotSchemaView, ...]


@dataclass(frozen=True, slots=True)
class TemplateView:
    """模板全文（库表行 + 创建回显）。槽要全——创建页预览读它。"""

    template_id: str
    title: str
    sections: tuple[TemplateSectionView, ...]


def template_view(template: DocumentTemplate) -> TemplateView:
    """领域模板 → 视图。"""

    return TemplateView(
        template_id=template.template_id,
        title=template.title,
        sections=tuple(
            TemplateSectionView(
                section_id=str(section.section_id),
                title=section.title,
                purpose=section.purpose,
                role=section.role.value,
                slots=tuple(
                    SlotSchemaView(name=slot.name, title=slot.title, kind=slot.kind.value)
                    for slot in section.slots
                ),
            )
            for section in template.sections
        ),
    )




@dataclass(frozen=True, slots=True)
class SchemaView:
    """`GET /schema`：现行结构（13 章 + 功能字段表 + 上限常量 + 讨论角色）。

    它**不是**文档内容——`DocumentView` 才是；这里只是"这套结构长什么样"。
    """

    sections: tuple[SectionSchemaView, ...]
    feature_fields: tuple[FeatureFieldSchemaView, ...]
    limits: LimitsView
    #: 讨论区的三个固定角色（`dlg.*`）。**必填**：没有它灵感区就没有角色可显示。
    discussion_roles: tuple[RoleSchemaView, ...]
    #: 五个固定模拟使用者（M11）：评测选择器读它
    personas: tuple[PersonaSchemaView, ...]
    #: 模板库全文（`02` §11.0.68）：创建页的选择器读它。**只读投影**，不是真相源——
    #: 真相源是各文档行内的快照（`Document.template`）。
    templates: tuple[TemplateSchemaView, ...] = ()


def schema_view(
    *, discussion_roles: tuple = (), personas: tuple = (), templates: tuple = ()
) -> SchemaView:
    """把三张表、三个常量与讨论角色读出来。纯函数：无 IO、不开事务、不碰任何仓储。

    `discussion_roles` 按鸭子类型读 `.role_id/.title/.focus`——application 不 import
    infrastructure（分层守卫），形状由 `DiscussionRole` 保证，调用方注入元组。
    `templates` 同理读 `.template_id/.title/.sections[].section_id/.title/.purpose/.role`。
    """

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
            max_sim_review_personas=MAX_SIM_REVIEW_PERSONAS,
            max_sim_review_features=MAX_SIM_REVIEW_FEATURES,
            max_discussion_turns=MAX_DISCUSSION_TURNS,
        ),
        discussion_roles=tuple(
            RoleSchemaView(role_id=role.role_id, title=role.title, focus=role.focus)
            for role in discussion_roles
        ),
        personas=tuple(
            PersonaSchemaView(
                persona_id=persona.persona_id,
                title=persona.title,
                situation=persona.situation,
            )
            for persona in personas
        ),
        templates=tuple(
            TemplateSchemaView(
                template_id=template.template_id,
                title=template.title,
                sections=tuple(
                    TemplateSectionSchemaView(
                        section_id=str(section.section_id),
                        title=section.title,
                        purpose=section.purpose,
                        role=section.role.value,
                        slots=tuple(
                            SlotSchemaView(
                                name=slot.name, title=slot.title, kind=slot.kind.value
                            )
                            for slot in section.slots
                        ),
                    )
                    for section in template.sections
                ),
            )
            for template in templates
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
    #: 账本里的模拟编号集合：给提案打「模拟评测依据」tag（没有账本就都不标）
    simulated_eids = (
        frozenset(
            str(item.eid)
            for item in ledger.evidences()
            if item.kind is EvidenceKind.SIMULATED
        )
        if ledger is not None
        else frozenset()
    )
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
        draft_view = _draft_view(
            round_.draft, warnings=warnings, simulated_eids=simulated_eids
        )
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
                packet_id=item.packet_id,
            )
            for item in round_.dropped
        ),
        questions=_question_views(round_),
        asked_questions=_asked_question_views(round_),
        # 清单项的真相源是**轮次**（历次累积）：挂在当轮问卷上的那份换一批就没了（F8）
        list_items=tuple(item.text for item in round_.list_items),
        draft=draft_view,
        advisories=advisories,
        failure_reason=round_.failure.reason if round_.failure is not None else None,
        failure_at_step=(
            None if round_.failure is None else round_.failure.at_step.value
        ),
        analysis_basis=round_.analysis_basis,
        retry_from=(
            None if round_.failure is None else retry_from_label(round_.failure.at_step.value)
        ),
        abandon_reason=round_.abandon_reason,
        confirmed_version_no=round_.confirmed_version_no,
        form_id=None if questionnaire is None else str(questionnaire.form_id),
        answer_sets=answer_batches(round_),
        materials=round_material_views(round_, materials=materials),
        unreferenced_materials=tuple(round_.unreferenced_materials),
        web_queries=_web_query_views(round_),
        sim_inquiry=_sim_inquiry_view(round_),
        sim_review=_sim_review_view(round_),
        web_search_available=web_search_available,
        memory_proposals=tuple(
            MemoryProposalView(
                proposal_id=item.proposal_id,
                op=item.change.op.value,
                kind=item.change.kind.value,
                text=item.change.text,
                refs=tuple(str(ref) for ref in item.change.refs),
                reason=item.change.reason,
                target_id=item.change.target_id,
            )
            for item in round_.memory_proposals
        ),
    )


def _sim_review_view(round_: Round) -> SimReviewView | None:
    """轮次里的评测产物 → 抽屉投影。没跑过是 None。"""

    payload = round_.sim_review
    if payload is None:
        return None
    personas = tuple(
        SimPersonaReviewView(
            persona_id=str(item.get("persona_id", "")),
            title=str(item.get("title", "")),
            features=tuple(
                _feature_review_view(feature) for feature in item.get("features", ())
            ),
        )
        for item in payload.get("personas", ())
    )
    counts = {"smooth": 0, "friction": 0, "blocked": 0}
    for persona in personas:
        for feature in persona.features:
            if feature.verdict in counts:
                counts[feature.verdict] += 1
    evaluated = payload.get("evaluated_version")
    return SimReviewView(
        runs=round_.sim_review_runs,
        max_runs=MAX_SIM_REVIEWS_PER_DRAFT,
        evaluated_version=None if evaluated is None else int(evaluated),
        personas=personas,
        summary=SimReviewSummaryView(
            smooth=counts["smooth"],
            friction=counts["friction"],
            blocked=counts["blocked"],
        ),
        advisories=tuple(str(item) for item in payload.get("advisories", ())),
    )


def _feature_review_view(feature: Mapping) -> SimFeatureReviewView:
    return SimFeatureReviewView(
        feature_slug=str(feature.get("feature_slug", "")),
        started=bool(feature.get("started", False)),
        walkthrough=tuple(
            SimWalkStepView(
                step_index=int(step.get("step_index", 0)),
                step_text=str(step.get("step_text", "")),
                from_doc=str(step.get("from_doc", "")),
                action=str(step.get("action", "")),
                outcome=str(step.get("outcome", "")),
                need=str(step.get("need", "")),
            )
            for step in feature.get("walkthrough", ())
        ),
        frictions=tuple(
            SimFrictionView(
                local_id=str(item.get("local_id", "")),
                kind=str(item.get("kind", "")),
                entry_id=str(item.get("entry_id", "")),
                field=str(item.get("field", "")),
                quote=str(item.get("quote", "")),
                not_documented=bool(item.get("not_documented", False)),
                blocks_flow=bool(item.get("blocks_flow", False)),
                note=str(item.get("note", "")),
            )
            for item in feature.get("frictions", ())
        ),
        acceptance_checks=tuple(
            SimAcceptanceCheckView(
                local_id=str(item.get("local_id", "")),
                entry_id=str(item.get("entry_id", "")),
                status=str(item.get("status", "")),
                note=str(item.get("note", "")),
            )
            for item in feature.get("acceptance_checks", ())
        ),
        verdict=str(feature.get("verdict", "")),
        verdict_because=tuple(str(item) for item in feature.get("verdict_because", ())),
    )


def _sim_inquiry_view(round_: Round) -> SimInquiryView | None:
    """轮次里的模拟产物 → 抽屉投影。没跑过是 None（抽屉靠它决定出不出现）。"""

    if not round_.sim_slices and not round_.sim_hypotheses and not round_.sim_unknowns:
        return None
    return SimInquiryView(
        slices=tuple(
            SimSliceView(
                local_id=str(item["local_id"]),
                situation=str(item["situation"]),
                job=str(item["job"]),
                trigger=str(item["trigger"]),
                workaround=str(item["workaround"]),
                anchors=tuple(str(eid) for eid in item.get("anchors") or ()),
            )
            for item in round_.sim_slices
        ),
        hypotheses=tuple(
            SimHypothesisView(
                local_id=str(item["local_id"]),
                slice_id=str(item.get("slice_id") or ""),
                statement=str(item["statement"]),
                grounding=str(item["grounding"]),
                based_on=tuple(str(eid) for eid in item.get("based_on") or ()),
                falsification=str(item["falsification"]),
                evidence_needed=str(item["evidence_needed"]),
            )
            for item in round_.sim_hypotheses
        ),
        unknowns=tuple(
            SimUnknownView(
                local_id=str(item["local_id"]),
                statement=str(item["statement"]),
                why_it_matters=str(item["why_it_matters"]),
                how_to_find_out=str(item["how_to_find_out"]),
            )
            for item in round_.sim_unknowns
        ),
        runs=round_.sim_inquiry_runs,
        chosen=tuple(round_.sim_inquiry_chosen),
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
        # **按文档顺序**排：卡片是给人读的（原文顺序），不是相关度榜单。
        # 提示词那一节也是文档序（U 型已撤回）——卡与提示词同一个顺序。
        ordered = sorted(entries, key=lambda item: by_chunk[item[0]].ordinal)
        chunks = tuple(
            RoundMaterialChunkView(
                chunk_id=chunk_id,
                eid=eid,
                anchor=by_chunk[chunk_id].anchor,
                text=by_chunk[chunk_id].text,
            )
            for chunk_id, eid in ordered
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
    memory: Sequence[MemoryEntry] = (),
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
        memory=tuple(memory_entry_view(item) for item in memory),
    )


def memory_entry_view(entry: MemoryEntry) -> MemoryEntryView:
    return MemoryEntryView(
        memory_id=entry.memory_id,
        kind=entry.kind.value,
        text=entry.text,
        refs=tuple(str(ref) for ref in entry.refs),
        round_id=entry.round_id,
        updated_at=entry.updated_at,
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
                packet_id=review.packet_id if review is not None else None,
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


def _question_view(question: Question) -> QuestionView:
    """一道题的投影——**唯一出处**：当前批与整轮两份投影都走这里，免得两处漂移。"""

    return QuestionView(
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


def _question_views(round_: Round) -> tuple[QuestionView, ...]:
    """**当前批**的题：还没答的那一批（界面只认这份）。"""

    questionnaire = round_.questionnaire
    if questionnaire is None:
        return ()
    return tuple(_question_view(question) for question in questionnaire.questions)


def _asked_question_views(round_: Round) -> tuple[QuestionView, ...]:
    """本轮**问过的每一道题**：按 `questionnaire_history` 逐批展开。

    与 `answer_sets`（每批答了什么）对称：这份是"每批问了什么"。
    不按题号去重——题号轮内唯一是 D4（2026-09-29）之后的事，老库真出现过同轮两个 `q1`
    （`02` §11.0.12 记过），投影如实展开：重复本身就是事实，不该在这里被抹掉。
    """

    return tuple(
        _question_view(question)
        for form in round_.questionnaire_history
        for question in form.questions
    )


def _proposal_view(
    proposal: Proposal,
    *,
    warning: str | None,
    simulated_eids: frozenset[str] = frozenset(),
) -> ProposalView:
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
        simulation_based=any(
            str(eid) in simulated_eids for eid in proposal.origin.refs
        ),
    )


# ── 探索（M8）：调研运行与候选的投影 ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CandidateView:
    """过目台/详情看到的一条候选。字段按类型二选一：假设带证伪两行，盲区带下一步。"""

    candidate_id: str
    kind: str  # hypothesis | blind_spot
    statement: str
    state: str  # new | kept | dismissed
    kept_as: str | None = None
    kept_ref: str | None = None
    grounding: str | None = None
    based_on: tuple[str, ...] = ()
    falsification: str | None = None
    evidence_needed: str | None = None
    next_step: str | None = None


@dataclass(frozen=True, slots=True)
class ExplorationView:
    exploration_id: str
    project_id: str
    kind: str
    status: str
    scope_kind: str
    scope_ref: str | None
    scope_text: str | None
    created_at: datetime
    failure_reason: str | None
    abandon_reason: str | None
    overflow_count: int
    candidates: tuple[CandidateView, ...]


def exploration_view(exploration: Exploration) -> ExplorationView:
    """一次探索运行的只读投影（过目台与详情共用它；投影不是第二份存储）。"""

    return ExplorationView(
        exploration_id=str(exploration.exploration_id),
        project_id=str(exploration.project_id),
        kind=exploration.kind.value,
        status=exploration.status.value,
        scope_kind=exploration.scope.kind.value,
        scope_ref=exploration.scope.ref,
        scope_text=exploration.scope.text,
        created_at=exploration.created_at,
        failure_reason=(
            exploration.failure.reason if exploration.failure is not None else None
        ),
        abandon_reason=exploration.abandon_reason,
        overflow_count=exploration.overflow_count,
        candidates=tuple(
            CandidateView(
                candidate_id=item.candidate_id,
                kind=item.kind,
                statement=item.statement,
                state=item.state.value,
                kept_as=item.kept_as,
                kept_ref=item.kept_ref,
                grounding=item.grounding.value if item.grounding is not None else None,
                based_on=tuple(str(eid) for eid in item.based_on),
                falsification=item.falsification,
                evidence_needed=item.evidence_needed,
                next_step=item.next_step,
            )
            for item in exploration.candidates()
        ),
    )


# ── 讨论（M9-T3）：线程与卡片的投影 ────────────────────────────────────────────
# 字段名冻结（T6 前端逐字对）：thread_id/project_id/topic/question/role_id/role_title/
# status/turns/cards/created_at；TurnView{index,role,text,based_on}；
# CardView{card_id,statement,grounding,based_on,kept_ref,state,kept_as}（M12 加过目状态）。


@dataclass(frozen=True, slots=True)
class DiscussionQuestionView:
    """开聊时带的问卷题快照（只存题面与原因，不读轮）。"""

    title: str
    why: str | None = None


@dataclass(frozen=True, slots=True)
class ParticipantView:
    role_id: str
    role_title: str
    joined_round: int


@dataclass(frozen=True, slots=True)
class RoundSummaryView:
    index: int
    agreed: tuple[str, ...] = ()
    disagreed: tuple[str, ...] = ()
    open: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TurnView:
    index: int
    role: str
    text: str
    based_on: tuple[str, ...] = ()
    author: str = "assistant"
    round: int = 1


@dataclass(frozen=True, slots=True)
class CardView:
    card_id: str
    statement: str
    grounding: str
    based_on: tuple[str, ...] = ()
    kept_ref: str | None = None
    state: str = "new"
    kept_as: str | None = None


@dataclass(frozen=True, slots=True)
class ThreadView:
    thread_id: str
    project_id: str
    topic: str
    question: DiscussionQuestionView | None
    role_id: str
    role_title: str
    status: str
    turns: tuple[TurnView, ...]
    cards: tuple[CardView, ...]
    #: 开题调研底稿条数（M13）：真身在探索表，界面只要数（"开题调研底稿 N 条"）
    findings_count: int
    created_at: datetime
    #: panel 成员（M18）：单成员即旧 1:1 线程
    participants: tuple[ParticipantView, ...] = ()
    #: 每轮综合（M18）：只增，给人看的副栏
    summaries: tuple[RoundSummaryView, ...] = ()


def thread_view(thread: Discussion) -> ThreadView:
    """一条讨论线程的只读投影（列表与展开共用它；投影不是第二份存储）。"""

    question = thread.question
    return ThreadView(
        thread_id=str(thread.discussion_id),
        project_id=str(thread.project_id),
        topic=thread.topic,
        question=(
            None
            if question is None
            else DiscussionQuestionView(title=question.title, why=question.why)
        ),
        role_id=thread.role_id,
        role_title=thread.role_title,
        status=thread.status.value,
        turns=tuple(
            TurnView(
                index=turn.index,
                role=turn.role,
                text=turn.text,
                based_on=tuple(str(eid) for eid in turn.based_on),
                author=turn.author,
                round=turn.round,
            )
            for turn in thread.turns
        ),
        cards=tuple(
            CardView(
                card_id=card.card_id,
                statement=card.statement,
                grounding=card.grounding.value,
                based_on=tuple(str(eid) for eid in card.based_on),
                kept_ref=card.kept_ref,
                state=card.state.value,
                kept_as=card.kept_as,
            )
            for card in thread.cards
        ),
        findings_count=len(thread.findings),
        created_at=thread.created_at,
        participants=tuple(
            ParticipantView(
                role_id=member.role_id,
                role_title=member.role_title,
                joined_round=member.joined_round,
            )
            for member in thread.participants
        ),
        summaries=tuple(
            RoundSummaryView(
                index=summary.index,
                agreed=tuple(summary.agreed),
                disagreed=tuple(summary.disagreed),
                open=tuple(summary.open),
            )
            for summary in thread.summaries
        ),
    )


def _draft_view(
    draft: Draft,
    *,
    warnings: Mapping[str, str],
    simulated_eids: frozenset[str] = frozenset(),
) -> DraftView:
    proposals = tuple(
        _proposal_view(
            proposal,
            warning=warnings.get(str(proposal.proposal_id)),
            simulated_eids=simulated_eids,
        )
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
