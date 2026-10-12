"""Analyzer：把一段用户输入切分成带标签与去处的主张，并挑出这一轮该请的席位。

挑席位放在这里，是因为它同时读到了「本轮输入」与「文档现状」——只有它能在同一口气里
判断"这段话触到了哪些专业面"。目录只提供"有哪些席位"，请谁由这次判断决定；
用户拿到的是建议，可以删也可以从目录里补（`02-编排与数据.md` §4.6）。
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from pmstudio.application.agents import schemas  # noqa: I001 — schemas 必须在 definitions 之前
from pmstudio.application.agents.definitions import ANALYZER
from pmstudio.application.context import (
    FEATURE_FIELDS_TEXT,
    LABELS_TEXT,
    NO_MATERIAL_TEXT,
    SECTION_SLOTS_TEXT,
    SECTIONS_TEXT,
    render_seat_catalog,
)
from pmstudio.application.evidence_refs import resolve_evidence_refs
from pmstudio.application.ports import SeatCatalog
from pmstudio.domain.round.claim import Assumption, Claim, Contradiction
from pmstudio.domain.round.round import DroppedItem, DropSource, SeatNomination
from pmstudio.domain.round.web_query import MAX_WEB_QUERIES, WebQuery
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.ids import EvidenceId, RoundId
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.harness.agents.agent import Agent
from pmstudio.harness.errors import PacketParseError
from pmstudio.harness.model.contracts import ModelGateway, PromptLibrary
from pmstudio.harness.model.envelope import Envelope


@dataclass(frozen=True, slots=True)
class Analysis:
    """一次分析的全部产出：主张 + 席位提名 + 被丢掉的提名留痕。"""

    claims: tuple[Claim, ...]
    seats: tuple[SeatNomination, ...]
    #: §4.4 的信封。与另外四个 agent 一样：这一趟的全部事实都在它身上。
    #: **必填**：可选的话就成了"分析者可以没有信封"——那正是这轮要消掉的不对称，
    #: 只是换了个更隐蔽的形式（`use_cases` 目前不读它，填漏了没人会发现）。
    envelope: Envelope
    #: 分析者交回的「我读了什么才切出这些主张」——原样带到 Round 上，
    #: 让复述卡能在用户批准主张之前显示它（2026-10-10：这原本是个
    #: 每轮必填却被整体丢弃的字段，见 `tests/domain/round/test_analysis_basis.py`）。
    basis: str = ""
    dropped: tuple[DroppedItem, ...] = ()
    #: 本轮输入内部**立场互斥**的主张对子（没判出来就是空）
    contradictions: tuple[Contradiction, ...] = ()
    #: 这一轮建议联网查什么（**只是提名**；用户在复述卡上勾了才真去查）
    web_queries: tuple[WebQuery, ...] = ()



def normalize(text: str) -> str:
    """空白归一化：模型抄原话时常用不同的换行与空格。"""

    return " ".join(text.split())


MIN_SPAN_CHARS = 6
MIN_SPAN_RATIO = 0.5

#: 模型把**两段不连续的原话**拼成一条复述时用的"拼接标记"：
#: `case-a` 实测稳定写出 `'想做个订单导出的功能...最好能按时间筛选'`（两段都真实存在）。
JOINERS = re.compile(r"\.{3,}|…+|。{3,}")


def longest_common_substring(left: str, right: str) -> str:
    """两串的最长公共连续子串（输入都很短，DP 足够）。"""

    best = ""
    best_len = 0
    previous = [0] * (len(right) + 1)
    for i in range(1, len(left) + 1):
        current = [0] * (len(right) + 1)
        for j in range(1, len(right) + 1):
            if left[i - 1] != right[j - 1]:
                continue
            current[j] = previous[j - 1] + 1
            if current[j] > best_len:
                best_len = current[j]
                best = left[i - best_len : i]
        previous = current
    return best


def ground_one(candidate: str, source: str) -> str | None:
    """**一段**（不含拼接标记的）候选怎么对齐：逐字命中最好；命不中取最长连续匹配，够长才算。

    这是唯一的判据，`grounded_span` 的"整串"与"切出来的每一段"都走它——不引第二把尺子。
    返回的一定是 `source` 里真实存在的连续片段（最长公共子串必然如此）。
    """

    if not candidate:
        return None
    if candidate in source:
        return candidate
    best = longest_common_substring(candidate, source)
    if len(best) >= MIN_SPAN_CHARS and len(best) >= MIN_SPAN_RATIO * len(candidate):
        return best
    return None


def grounded_span(raw_span: str, source: str) -> str | None:
    """把 raw_span 对齐到原文里**真实存在**的一段，返回它；对不上就返回 None。

    真模型实测：它会把两段不连续的原话拼成一句（"运营每天都要用，顺便改一下"），
    严格子串匹配会把整轮判死。这里的规则是——逐字命中最好；命不中就在原文里取
    **最长的那段连续匹配**（够长才算），因为 raw_span 的用途是"点编号跳回原文"，
    跳到一段真实存在的原话就达到了目的。纯粹编造（对不上任何一段）仍然失败。

    拼接标记（`...` / `…` / `。。。`）按**拼接**处理，不算内容——真模型实测：
    `'想做个订单导出的功能...最好能按时间筛选'` 两段都逐字存在，只因省略号 3 个字
    也计进分母，差 0.5 字把整轮判死；而同一句话把省略号写成 `…`（1 个字）就能过——
    能不能过取决于模型的标点习惯，这不是判据该管的事。所以：

    1. **整串逐字命中**（原文里真的写了 `...`）→ 原样返回，不切段；
    2. **明写了拼接标记** → 按段判：每段各自走同一条判据，**全段都要站得住**
       （任何一段是编的就整条不认——不许"一半真一半编"混进来），空段（`A...`）忽略，
       最后返回**最长的那段真实片段**（并列取靠前的），保证跳回原文落在真实文本上；
    3. **没写标记**（逗号拼出来的那种）→ 走整串的最长连续匹配（`MIN_SPAN_RATIO` 那条），
       与改动前一字不变。

    为什么第 2 条比第 3 条严：模型写了 `...` 就等于**自己把切分点交出来了**，
    这时按段判是它应得的对待；没写标记才只能整串估。
    """

    normalized_source = normalize(source)
    candidate = normalize(raw_span)
    if not candidate:
        return None
    if candidate in normalized_source:
        return candidate
    if not JOINERS.search(candidate):
        return ground_one(candidate, normalized_source)

    spans: list[str] = []
    for part in (piece.strip() for piece in JOINERS.split(candidate)):
        if not part:  # `A...` 这种收尾的拼接标记没有内容，忽略
            continue
        span = ground_one(part, normalized_source)
        if span is None:
            return None  # 有一段不认，整条不认（一分编造都不要）
        spans.append(span)
    return max(spans, key=len, default=None)


class ClaimAnalyzer(Agent[Analysis, schemas.ClaimsPacket]):
    definition = ANALYZER

    def __init__(
        self, *, model: ModelGateway, prompts: PromptLibrary, catalog: SeatCatalog
    ) -> None:
        super().__init__(model=model, prompts=prompts)
        self._catalog = catalog

    async def analyze(
        self,
        *,
        user_input: str,
        document_state: str,
        external_material: str = NO_MATERIAL_TEXT,
        project_context: str = "",
        round_id: RoundId | None = None,
        validation_error: str = "",
        packet_slot: str = "",
        material_eids: frozenset[EvidenceId] = frozenset(),
    ) -> Analysis:
        outcome = await self._call(
            slots={
                "document_state": document_state,
                "user_input": user_input,
                "labels": LABELS_TEXT,
                "sections": SECTIONS_TEXT,
                "feature_fields": FEATURE_FIELDS_TEXT,
                "section_slots": SECTION_SLOTS_TEXT,
                "seat_catalog": render_seat_catalog(self._catalog.filter_by_input(user_input)),
                "external_material": external_material,
                "project_context": project_context,
                "validation_error": validation_error,
            },
            metadata={} if round_id is None else {"round_id": str(round_id)},
            packet_id=self._make_packet_id(
                str(round_id or "new"), "analyzer", slot=packet_slot
            ),
            run_id=str(round_id or "new"),
            produced_by="analyzer",
        )
        # 主张这一包**超限不判死**（P21 F16-a）：席位（`use_cases`）与整合者早就走这条路，
        # 只有分析者还在严格解析——而它是**唯一一条没有 `/retry` 可点**的失败路
        # （轮次都还没建出来）。截断保留它自己排的前 N 条，溢出写成留痕。
        packet, overflow, discarded = outcome.packet, outcome.overflow, outcome.discarded
        seats, dropped = nominations_from_packet(packet, catalog=self._catalog)
        claims, claim_drops = claims_from_packet(
            packet, user_input=user_input, material_eids=material_eids
        )
        # 被摘掉的那些条（根本不是对象）会让后面主张的序号整体前移，而互斥对子是按
        # **模型自己写的序号**指认的——所以要用"原始序号 → 落下来的第几条"换算，
        # 不能直接拿下标去取（真跑 F17 那一包只错了一条，换算错了就等于把互斥指到别人身上）。
        claims_discarded = tuple(item for item in discarded if item.container == "claims")
        positions = ClaimPositions.after_discarding(kept=len(claims), discarded=claims_discarded)
        contradictions, contradiction_drops = contradictions_from_packet(
            packet, claims=claims, positions=positions
        )
        web_queries, query_drops = web_queries_from_packet(packet)
        # 留痕统一在**出口**补 `packet_id`：9 处构造点散在 6+ 个函数里，穿参数要改
        # 6+ 个签名；而这里 envelope 就在手边，一处 `replace()` 覆盖全部。
        # 不区分 source——它们都来自这一包（analyzer 的留痕全是"模型交回的东西没被采纳"）。
        packet_id = outcome.packet_id
        return Analysis(
            claims=claims,
            seats=seats,
            basis=packet.basis,
            dropped=tuple(
                replace(item, packet_id=packet_id)
                for item in (
                    dropped
                    + claims_overflow_drops(overflow)
                    + discarded_claim_drops(claims_discarded)
                    + claim_drops
                    + contradiction_drops
                    + query_drops
                )
            ),
            contradictions=contradictions,
            web_queries=web_queries,
            envelope=outcome,
        )


def claims_overflow_drops(overflows: Sequence[schemas.Overflow]) -> tuple[DroppedItem, ...]:
    """主张被截断的留痕（`04-未决.md` C5 后半：一条都不许默默丢掉）。

    与席位（`use_cases.py` 的 `:O{kept+1}..`）和整合（`提案 #{kept+1} 起`）同一个口径：
    说清"从第几条起、一共几条"，**不把丢掉的内容再抄一遍**——留给用户的是
    "这里少了东西"，不是一段没人认领的正文。
    """

    return tuple(
        DroppedItem(
            handle=f"主张 #{item.kept + 1} 起（共 {item.dropped} 条）",
            reason=f"超出上限 {item.kept} 条：保留了它自己排的前 {item.kept} 条，其余没切进来",
            source=DropSource.ASSEMBLY,
            seat_id=None,
            pass_index=1,
        )
        for item in overflows
    )


def discarded_claim_drops(
    discarded: Sequence[schemas.Discarded],
) -> tuple[DroppedItem, ...]:
    """切不出来（那一"条"根本不是对象）的主张：**丢那一条 + 留痕**（审计 F17）。

    与"超限截断"（`claims_overflow_drops`）同一个口径：说清**第几条**、**为什么**，
    不把模型写的那串占位符抄进留痕——回喂原串等于给它一个可以照抄的模板（learnings L20）。
    """

    return tuple(
        DroppedItem(
            handle=f"主张 #{item.index}（不是对象）",
            reason=item.reason,
            source=DropSource.ASSEMBLY,
            seat_id=None,
            pass_index=1,
        )
        for item in discarded
    )


@dataclass(frozen=True, slots=True)
class ClaimPositions:
    """模型交回的第 i 条主张（1 起）→ 落下来的第几条（1 起）；被摘掉的不在表里。

    序号只有这一处有权威：`c{i}` 是我们按数组下标发的，提示词里从没出现过。
    """

    total: int
    mapping: Mapping[int, int]

    @classmethod
    def identity(cls, *, total: int) -> "ClaimPositions":
        return cls(total=total, mapping={index: index for index in range(1, total + 1)})

    @classmethod
    def after_discarding(
        cls, *, kept: int, discarded: Sequence[schemas.Discarded]
    ) -> "ClaimPositions":
        """`kept` = 落下来的条数；被摘掉的原始位置从序号里剔除，其余依次前移。"""

        lost = {item.index for item in discarded}
        total = kept + len(discarded)
        return cls(
            total=total,
            mapping={
                original: new
                for new, original in enumerate(
                    (index for index in range(1, total + 1) if index not in lost), start=1
                )
            },
        )

    def in_range(self, position: int) -> bool:
        return 1 <= position <= self.total

    def resolve(self, position: int) -> int | None:
        """原始序号 → 落下来的第几条；被摘掉（或越界）返回 `None`。"""

        return self.mapping.get(position)


def nominations_from_packet(
    packet: schemas.ClaimsPacket, *, catalog: SeatCatalog
) -> tuple[tuple[SeatNomination, ...], tuple[DroppedItem, ...]]:
    """模型挑的席位 → 领域值对象 + **丢掉的那几条提名的留痕**。

    真跑实测（`.codex/runs/2026-09-28/报告.md` F13）：目录里没有 `seat.observability`
    （s8 的**章节槽**里有这个名字），模型照着章节槽提名它 → 两次重装都失败 →
    **连轮次都创建不了**。一条提名写错，不该把整轮打死。

    所以口径改成：目录外的那条**丢提名 + 留痕**，其余照跑；只有"一条都不剩"才判死
    （`04-未决.md` R7：一轮至少要有一个席位）。
    """

    known = {seat.seat_id for seat in catalog.all()}
    picked: list[SeatNomination] = []
    dropped: list[DroppedItem] = []
    seen: set[str] = set()
    for item in packet.seats:
        if item.seat_id not in known:
            dropped.append(
                DroppedItem(
                    handle=f"席位提名 {item.seat_id}",
                    reason="目录里没有这个席位（席位只能从目录里挑），已丢弃该条提名",
                    source=DropSource.ASSEMBLY,
                    seat_id=None,
                    pass_index=1,
                )
            )
            continue
        if item.seat_id in seen:
            continue
        seen.add(item.seat_id)
        try:
            picked.append(SeatNomination(seat_id=item.seat_id, why=item.why.strip()))
        except DomainError as error:
            raise PacketParseError(
                f"席位 {item.seat_id} 的提名过不了领域校验：{error.message}",
                purpose=ANALYZER.purpose,
                seat_id=item.seat_id,
            ) from error
    if not picked:
        unknown = sorted({item.seat_id for item in packet.seats})
        raise PacketParseError(
            "这一轮挑的席位一个都不在目录里："
            + "、".join(unknown)
            + "——目录里没有这些席位",
            purpose=ANALYZER.purpose,
            seat_id=unknown[0] if unknown else None,
            seat_ids=unknown,
            position=1,
        )
    return tuple(picked), tuple(dropped)


def claims_from_packet(
    packet: schemas.ClaimsPacket,
    *,
    user_input: str,
    material_eids: frozenset[EvidenceId] = frozenset(),
) -> tuple[tuple[Claim, ...], tuple[DroppedItem, ...]]:
    """报文 → 主张 + **就地修掉的那些字段的留痕**（P21 F16-b）。

    只有一类毛病就地修：**非 `guess` 标签却带了 `assumption`**（领域规则见
    `domain/round/claim.py` 的"只有猜测才带假设"）。修法是**把假设摘掉、主张本体留下**——
    主张是用户原话在文档里的落点（`c{i}` 还被互斥对子按"第几条"指认着），
    丢一条会让后面所有序号整体错位；而字段写歪只是模型的笔误。

    仍然判死的两类：**原话在输入里找不到**（编造，`04-未决.md` 的可追溯不变式）、
    以及主张本体过不了领域校验（空文本 / 猜测不带假设 / 决定不给拍板人）——
    那些不是"修一下"能解决的，交给重装一次去处理。
    """

    claims: list[Claim] = []
    dropped: list[DroppedItem] = []
    for index, draft in enumerate(packet.claims, start=1):
        claim_id = f"c{index}"
        span = grounded_span(draft.raw_span, user_input)
        if span is None:
            raise PacketParseError(
                "主张里的原话片段在用户输入里找不到（复述必须能跳回原文）",
                purpose=ANALYZER.purpose,
                claim_id=claim_id,
                position=index,
                raw_span=draft.raw_span,
            )
        assumption = draft.assumption
        # 读过的材料：只能是本轮挑中（进提示词）的块——许它引没见过的等于许它编。
        # 未知编号走重装（与原话找不到同级：都是"编造"，代价都是整包重装一次）。
        material_refs = resolve_evidence_refs(
            draft.material_eids,
            known=material_eids,
            what="主张引用了不存在或本轮没带进来的材料块编号",
            local_id=claim_id,
            purpose=ANALYZER.purpose,
        )
        if assumption is not None and draft.label.strip().lower() != ClaimLabel.GUESS.value:
            dropped.append(
                DroppedItem(
                    handle=f"主张 {claim_id} 的假设",
                    reason=(
                        f"只有猜测才带假设：这条主张标的是 {draft.label}，"
                        "假设字段已丢掉；主张本身保留"
                    ),
                    source=DropSource.ASSEMBLY,
                    seat_id=None,
                    pass_index=1,
                )
            )
            assumption = None
        try:
            claims.append(
                Claim(
                    claim_id=claim_id,
                    text=draft.text.strip(),
                    label=ClaimLabel(draft.label),
                    target=schemas.normalize_claim_target(draft, label=draft.label),
                    raw_span=span,
                    assumption=(
                        Assumption(
                            failure_consequence=assumption.failure_consequence,
                            verification=assumption.verification,
                        )
                        if assumption is not None
                        else None
                    ),
                    decided_by=draft.decided_by,
                    material_eids=material_refs,
                )
            )
        except DomainError as error:
            raise PacketParseError(
                f"主张 {claim_id} 过不了领域校验：{error.message}",
                purpose=ANALYZER.purpose,
                claim_id=claim_id,
                domain_code=error.code,
            ) from error
    return tuple(claims), tuple(dropped)


def contradictions_from_packet(
    packet: schemas.ClaimsPacket,
    *,
    claims: Sequence[Claim],
    positions: ClaimPositions | None = None,
) -> tuple[tuple[Contradiction, ...], tuple[DroppedItem, ...]]:
    """报文里的"第几条 ↔ 第几条" → 领域值对象；写歪的**丢那一条 + 留痕**。

    三种写歪：序号越界、指向自己、领域校验不过（比如空 why）。一律不判死整轮——
    与"未知席位提名"同一个口径：一条判断写错，不该让整轮重跑（模型重跑一次要几十秒）。

    序号 → 主张 id（`c{i}`）的映射只有这一处：id 是我们按数组下标发的，提示词里从没出现过。
    `positions` 把"模型交回的原始序号"换算成"落下来的第几条"——有主张被摘掉时两者不同
    （不传就是恒等映射，老调用方照旧）。
    """

    table = positions or ClaimPositions.identity(total=len(claims))
    keep: list[Contradiction] = []
    dropped: list[DroppedItem] = []
    for item in packet.contradictions:
        handle = f"互斥对子 {item.claim_a}↔{item.claim_b}"
        reason: str | None = None
        left, right = table.resolve(item.claim_a), table.resolve(item.claim_b)
        if not (table.in_range(item.claim_a) and table.in_range(item.claim_b)):
            reason = f"这一轮只有 {table.total} 条主张，序号越界；已丢弃该条互斥判断"
        elif item.claim_a == item.claim_b:
            reason = "自己跟自己不叫互斥；已丢弃该条互斥判断"
        elif left is None or right is None:
            # 指到的那条主张没切进来（它根本不是对象）：这对互斥也就没有两侧了。
            reason = "这条互斥判断指到的主张没切进来（它不是对象，已被摘掉）；已丢弃该条互斥判断"
        if reason is not None:
            dropped.append(
                DroppedItem(
                    handle=handle,
                    reason=reason,
                    source=DropSource.ASSEMBLY,
                    seat_id=None,
                    pass_index=1,
                )
            )
            continue
        assert left is not None and right is not None  # 上面两个分支已把 None 挡掉
        try:
            keep.append(
                Contradiction(
                    claim_id_a=claims[left - 1].claim_id,
                    claim_id_b=claims[right - 1].claim_id,
                    why=item.why,
                )
            )
        except DomainError as error:
            dropped.append(
                DroppedItem(
                    handle=handle,
                    reason=f"互斥判断过不了领域校验：{error.message}",
                    source=DropSource.ASSEMBLY,
                    seat_id=None,
                    pass_index=1,
                )
            )
    return tuple(keep), tuple(dropped)


def web_queries_from_packet(
    packet: schemas.ClaimsPacket,
) -> tuple[tuple[WebQuery, ...], tuple[DroppedItem, ...]]:
    """报文的检索词提名 → 领域值对象；写多了 / 写歪了的**丢那一条 + 留痕**。

    与"未知席位提名""互斥对子序号越界"同一个口径：一条判断写歪，不该让整轮重跑
    （真跑一次重装几十秒）。三种写歪：超过 `MAX_WEB_QUERIES`、没写"为什么"、检索词为空。
    """

    keep: list[WebQuery] = []
    dropped: list[DroppedItem] = []
    seen: set[str] = set()
    for item in packet.web_queries:
        query = item.query.strip()
        handle = f"检索词 {query or '(空)'}"
        if len(keep) >= MAX_WEB_QUERIES:
            dropped.append(
                DroppedItem(
                    handle=handle,
                    reason=(
                        f"一轮最多 {MAX_WEB_QUERIES} 条检索词提名，多出来的这一条已丢弃"
                    ),
                    source=DropSource.ASSEMBLY,
                    seat_id=None,
                    pass_index=1,
                )
            )
            continue
        if query in seen:
            continue
        try:
            candidate = WebQuery(query=query, why=item.why)
        except DomainError as error:
            dropped.append(
                DroppedItem(
                    handle=handle,
                    reason=f"这条检索词过不了领域校验：{error.message}",
                    source=DropSource.ASSEMBLY,
                    seat_id=None,
                    pass_index=1,
                )
            )
            continue
        seen.add(query)
        keep.append(candidate)
    return tuple(keep), tuple(dropped)
