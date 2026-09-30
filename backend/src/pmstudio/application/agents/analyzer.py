"""Analyzer：把一段用户输入切分成带标签与去处的主张，并挑出这一轮该请的席位。

挑席位放在这里，是因为它同时读到了「本轮输入」与「文档现状」——只有它能在同一口气里
判断"这段话触到了哪些专业面"。目录只提供"有哪些席位"，请谁由这次判断决定；
用户拿到的是建议，可以删也可以从目录里补（`02-编排与数据.md` §4.6）。
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.context import (
    FEATURE_FIELDS_TEXT,
    LABELS_TEXT,
    NO_MATERIAL_TEXT,
    SECTION_SLOTS_TEXT,
    SECTIONS_TEXT,
    render_seat_catalog,
)
from pmstudio.application.errors import PacketParseError
from pmstudio.application.ports import (
    ModelGateway,
    ModelMessage,
    ModelRequest,
    PromptLibrary,
    SeatCatalog,
)
from pmstudio.domain.round.claim import Assumption, Claim, Contradiction
from pmstudio.domain.round.round import DroppedItem, DropSource, SeatNomination
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.ids import RoundId
from pmstudio.domain.shared.label import ClaimLabel

PURPOSE = "claims"


@dataclass(frozen=True, slots=True)
class Analysis:
    """一次分析的全部产出：主张 + 席位提名 + 被丢掉的提名留痕。"""

    claims: tuple[Claim, ...]
    seats: tuple[SeatNomination, ...]
    dropped: tuple[DroppedItem, ...] = ()
    #: 本轮输入内部**立场互斥**的主张对子（没判出来就是空）
    contradictions: tuple[Contradiction, ...] = ()


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


class ClaimAnalyzer:
    PURPOSE = PURPOSE
    PROMPT = "analyzer"
    PROMPT_SLOTS = (
        "document_state",
        "user_input",
        "labels",
        "sections",
        "feature_fields",
        "section_slots",
        "seat_catalog",
        "external_material",
        "schema_json",
        "validation_error",
    )

    def __init__(
        self, *, model: ModelGateway, prompts: PromptLibrary, catalog: SeatCatalog
    ) -> None:
        self._model = model
        self._prompts = prompts
        self._catalog = catalog

    async def analyze(
        self,
        *,
        user_input: str,
        document_state: str,
        external_material: str = NO_MATERIAL_TEXT,
        round_id: RoundId | None = None,
        validation_error: str = "",
    ) -> Analysis:
        schema = schemas.ClaimsPacket.model_json_schema()
        prompt = self._prompts.render(
            self.PROMPT,
            document_state=document_state,
            user_input=user_input,
            labels=LABELS_TEXT,
            sections=SECTIONS_TEXT,
            feature_fields=FEATURE_FIELDS_TEXT,
            section_slots=SECTION_SLOTS_TEXT,
            seat_catalog=render_seat_catalog(self._catalog.all()),
            external_material=external_material,
            schema_json=json.dumps(schema, ensure_ascii=False),
            validation_error=validation_error,
        )
        reply = await self._model.complete(
            ModelRequest(
                purpose=self.PURPOSE,
                messages=(ModelMessage(role="user", content=prompt),),
                schema_name="ClaimsPacket",
                response_schema=schema,
                # 留样按 round_id 归堆（`02` §5.3）：排查时要能一次捞出一整轮
                metadata={} if round_id is None else {"round_id": str(round_id)},
            )
        )
        packet = schemas.parse_packet(reply.text, schemas.ClaimsPacket, purpose=self.PURPOSE)
        seats, dropped = nominations_from_packet(packet, catalog=self._catalog)
        claims = claims_from_packet(packet, user_input=user_input)
        contradictions, contradiction_drops = contradictions_from_packet(packet, claims=claims)
        return Analysis(
            claims=claims,
            seats=seats,
            dropped=dropped + contradiction_drops,
            contradictions=contradictions,
        )


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
                purpose=PURPOSE,
                seat_id=item.seat_id,
            ) from error
    if not picked:
        unknown = sorted({item.seat_id for item in packet.seats})
        raise PacketParseError(
            "这一轮挑的席位一个都不在目录里："
            + "、".join(unknown)
            + "——目录里没有这些席位",
            purpose=PURPOSE,
            seat_id=unknown[0] if unknown else None,
            seat_ids=unknown,
            position=1,
        )
    return tuple(picked), tuple(dropped)


def claims_from_packet(packet: schemas.ClaimsPacket, *, user_input: str) -> tuple[Claim, ...]:
    claims: list[Claim] = []
    for index, draft in enumerate(packet.claims, start=1):
        claim_id = f"c{index}"
        span = grounded_span(draft.raw_span, user_input)
        if span is None:
            raise PacketParseError(
                "主张里的原话片段在用户输入里找不到（复述必须能跳回原文）",
                purpose=PURPOSE,
                claim_id=claim_id,
                position=index,
                raw_span=draft.raw_span,
            )
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
                            failure_consequence=draft.assumption.failure_consequence,
                            verification=draft.assumption.verification,
                        )
                        if draft.assumption is not None
                        else None
                    ),
                    decided_by=draft.decided_by,
                )
            )
        except DomainError as error:
            raise PacketParseError(
                f"主张 {claim_id} 过不了领域校验：{error.message}",
                purpose=PURPOSE,
                claim_id=claim_id,
                domain_code=error.code,
            ) from error
    return tuple(claims)


def contradictions_from_packet(
    packet: schemas.ClaimsPacket, *, claims: Sequence[Claim]
) -> tuple[tuple[Contradiction, ...], tuple[DroppedItem, ...]]:
    """报文里的"第几条 ↔ 第几条" → 领域值对象；写歪的**丢那一条 + 留痕**。

    三种写歪：序号越界、指向自己、领域校验不过（比如空 why）。一律不判死整轮——
    与"未知席位提名"同一个口径：一条判断写错，不该让整轮重跑（模型重跑一次要几十秒）。

    序号 → 主张 id（`c{i}`）的映射只有这一处：id 是我们按数组下标发的，提示词里从没出现过。
    """

    total = len(claims)
    keep: list[Contradiction] = []
    dropped: list[DroppedItem] = []
    for item in packet.contradictions:
        handle = f"互斥对子 {item.claim_a}↔{item.claim_b}"
        reason: str | None = None
        if not (1 <= item.claim_a <= total and 1 <= item.claim_b <= total):
            reason = f"这一轮只有 {total} 条主张，序号越界；已丢弃该条互斥判断"
        elif item.claim_a == item.claim_b:
            reason = "自己跟自己不叫互斥；已丢弃该条互斥判断"
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
        try:
            keep.append(
                Contradiction(
                    claim_id_a=claims[item.claim_a - 1].claim_id,
                    claim_id_b=claims[item.claim_b - 1].claim_id,
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
