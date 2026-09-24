"""Analyzer：把一段用户输入切分成带标签与去处的主张，并挑出这一轮该请的席位。

挑席位放在这里，是因为它同时读到了「本轮输入」与「文档现状」——只有它能在同一口气里
判断"这段话触到了哪些专业面"。目录只提供"有哪些席位"，请谁由这次判断决定；
用户拿到的是建议，可以删也可以从目录里补（`02-编排与数据.md` §4.6）。
"""

import json
from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.context import (
    FEATURE_FIELDS_TEXT,
    LABELS_TEXT,
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
from pmstudio.domain.round.claim import Assumption, Claim
from pmstudio.domain.round.round import SeatNomination
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.label import ClaimLabel

PURPOSE = "claims"


@dataclass(frozen=True, slots=True)
class Analysis:
    """一次分析的全部产出：主张 + 席位提名。"""

    claims: tuple[Claim, ...]
    seats: tuple[SeatNomination, ...]


def normalize(text: str) -> str:
    """空白归一化：模型抄原话时常用不同的换行与空格。"""

    return " ".join(text.split())


MIN_SPAN_CHARS = 6
MIN_SPAN_RATIO = 0.5


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


def grounded_span(raw_span: str, source: str) -> str | None:
    """把 raw_span 对齐到原文里**真实存在**的一段，返回它；对不上就返回 None。

    真模型实测：它会把两段不连续的原话拼成一句（"运营每天都要用，顺便改一下"），
    严格子串匹配会把整轮判死。这里的规则是——逐字命中最好；命不中就在原文里取
    **最长的那段连续匹配**（够长才算），因为 raw_span 的用途是"点编号跳回原文"，
    跳到一段真实存在的原话就达到了目的。纯粹编造（对不上任何一段）仍然失败。
    """

    normalized_source = normalize(source)
    candidate = normalize(raw_span)
    if not candidate:
        return None
    if candidate in normalized_source:
        return candidate
    best = longest_common_substring(candidate, normalized_source)
    if len(best) >= MIN_SPAN_CHARS and len(best) >= MIN_SPAN_RATIO * len(candidate):
        return best
    return None


class ClaimAnalyzer:
    PURPOSE = PURPOSE
    PROMPT = "analyzer"
    PROMPT_SLOTS = (
        "document_state",
        "user_input",
        "labels",
        "sections",
        "feature_fields",
        "seat_catalog",
        "schema_json",
    )

    def __init__(
        self, *, model: ModelGateway, prompts: PromptLibrary, catalog: SeatCatalog
    ) -> None:
        self._model = model
        self._prompts = prompts
        self._catalog = catalog

    async def analyze(self, *, user_input: str, document_state: str) -> Analysis:
        schema = schemas.ClaimsPacket.model_json_schema()
        prompt = self._prompts.render(
            self.PROMPT,
            document_state=document_state,
            user_input=user_input,
            labels=LABELS_TEXT,
            sections=SECTIONS_TEXT,
            feature_fields=FEATURE_FIELDS_TEXT,
            seat_catalog=render_seat_catalog(self._catalog.all()),
            schema_json=json.dumps(schema, ensure_ascii=False),
        )
        reply = await self._model.complete(
            ModelRequest(
                purpose=self.PURPOSE,
                messages=(ModelMessage(role="user", content=prompt),),
                schema_name="ClaimsPacket",
                response_schema=schema,
            )
        )
        packet = schemas.parse_packet(reply.text, schemas.ClaimsPacket, purpose=self.PURPOSE)
        return Analysis(
            claims=claims_from_packet(packet, user_input=user_input),
            seats=nominations_from_packet(packet, catalog=self._catalog),
        )


def nominations_from_packet(
    packet: schemas.ClaimsPacket, *, catalog: SeatCatalog
) -> tuple[SeatNomination, ...]:
    """模型挑的席位 → 领域值对象。目录里没有的 id 直接判整轮失败。

    为什么不"悄悄丢掉"：挑到不存在的席位说明它没看目录，这一轮的编制就不可信了；
    按这个仓库一贯的规矩（raw_span 编造、引用不存在的编号都判死），宁可整轮失败可重试。
    """

    known = {seat.seat_id for seat in catalog.all()}
    picked: list[SeatNomination] = []
    seen: set[str] = set()
    for index, item in enumerate(packet.seats, start=1):
        if item.seat_id not in known:
            raise PacketParseError(
                "席位提名里出现了目录里没有的席位",
                purpose=PURPOSE,
                seat_id=item.seat_id,
                position=index,
            )
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
        raise PacketParseError("这一轮一个席位都没挑", purpose=PURPOSE)
    return tuple(picked)


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
