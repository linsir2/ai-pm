"""每步上下文准备：**检索 → 投影 → 发号 → 一个事务落库 → 渲染**。

它是 `RunSeatRound` / `IntegrateRound` 与材料/账本之间的唯一接缝：

- 检索在事务外（可能跑 ONNX）；
- 选块是纯函数（`context_pool.select_projection`）；
- E 号只发给**本步真正选中**的块（`Round.materials` 仍是"进过上下文的块"）；
- `RetrievalTrace` 与材料、账本在**同一个 UoW** 写完；
- 同一 `(stage, pass_index)` 重入不重查、不重发号（恢复/重试幂等）。

**不处理 SEED**：那一步的检索与发号仍在 `StartRound.create`（Analyzer 需要开轮时的
一次宽读；它没有"上一步的池"可投影）。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from pmstudio.application.context import (
    MATERIAL_BUDGET,
    NO_MATERIAL_TEXT,
    SECTION_OVERHEAD,
    material_header,
    missing_line,
    render_materials,
    render_web_results,
)
from pmstudio.application.context_pool import (
    PINNED_BUDGET_SHARE,
    Selection,
    select_projection,
)
from pmstudio.application.dto import SEARCH_LIMIT, MaterialChunk, MaterialPacket
from pmstudio.application.material_refs import external_ref, material_kind
from pmstudio.application.ports import Clock, MaterialRetriever, MaterialStore
from pmstudio.domain.ports import (
    EvidenceReader,
    EvidenceWriter,
    RoundReader,
    RoundWriter,
    UnitOfWork,
)
from pmstudio.domain.provenance.evidence import EvidenceSource
from pmstudio.domain.round.retrieval import (
    RetrievalHit,
    RetrievalStage,
    RetrievalTrace,
)
from pmstudio.domain.round.round import (
    DroppedItem,
    DropSource,
    MaterialRef,
    Round,
)
from pmstudio.domain.shared.ids import EvidenceId, RoundId

#: 每**阶段**最多几次检索。**不共用一本账**：正常的两批问卷流程是
#: SEAT(1)→INTEGRATE(1)→SEAT(2)→INTEGRATE(2)，共用一个 3 会把最后一次整合饿死。
#: 额度与既有配额对齐：席位轮最多 3 趟、整合最多 3 次。
MAX_SEAT_RETRIEVALS = 3
MAX_INTEGRATE_RETRIEVALS = 3
#: 联网材料的 origin。与 `use_cases.WEB_ORIGIN` 同一个产品词；定义在这里是为了不让
#: 本模块反向 import 用例（会成环）。
WEB_ORIGIN = "web_search"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedContext:
    """一步投影的结果：给提示词的文本 + 这一步的计数。"""

    text: str
    hit_count: int
    selected_count: int
    pool_count: int
    skipped_manual: tuple[str, ...]


class RoundContextPreparer:
    """把"这一步该看什么"准备成一段文本，并把新证据写进 Round。"""

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        materials: MaterialStore,
        retriever: MaterialRetriever | None,
        uow: UnitOfWork,
        clock: Clock,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._materials = materials
        self._retriever = retriever
        self._uow = uow
        self._clock = clock

    def prepare(
        self, *, round_id: RoundId, stage: RetrievalStage, query: str
    ) -> PreparedContext:
        round_ = self._rounds.get(round_id)
        if stage is RetrievalStage.SEED:
            raise ValueError("SEED 的上下文由 StartRound 准备，不走这里")

        pass_index = self._pass_index(round_, stage)
        existing = self._trace_of(round_, stage, pass_index)
        hits: tuple[RetrievalHit, ...] = ()
        error: str | None = None
        attempted = False
        if existing is None:
            hits, error, attempted = self._retrieve(round_, query, stage)

        with self._uow:
            # 写锁内**重新读**：检索在事务外，另一个 worker 可能已经把这步做完了。
            # 发号也必须在这里——账本整份 JSON 覆盖写，事务外读会互相覆盖。
            target = self._rounds.get(round_id)
            pass_index = self._pass_index(target, stage)
            existing = self._trace_of(target, stage, pass_index)
            eid_of = {
                (ref.material_id, ref.chunk_id): ref.eid
                for ref in target.materials
            }
            if existing is None:
                packets, pool, web_by_material, missing = self._pool(
                    target, eid_of, extra_hits=hits if attempted else ()
                )
            else:
                packets, pool, web_by_material, missing = self._pool(target, eid_of)
            pool_count = sum(len(chunk_ids) for chunk_ids in pool.values())

            if existing is not None:
                # 幂等：恢复/重试（或另一个 worker 先完成）不重查、不重发号。
                selection = Selection(
                    chunks=tuple(
                        (item.material_id, chunk)
                        for item in existing.selected
                        if (chunk := self._chunk_of(packets, item)) is not None
                    ),
                    hit_count=len(existing.hits),
                    used=0,
                    skipped_manual=(),
                )
            else:
                selection = select_projection(
                    packets=packets,
                    pool=pool,
                    pinned=target.pinned_materials,
                    manual=target.manual_chunks,
                    stage_hits=hits,
                    query=query,
                    budget=MATERIAL_BUDGET,
                    section_overhead=SECTION_OVERHEAD,
                    pinned_share=PINNED_BUDGET_SHARE,
                )

            ledger = None
            if existing is None:
                ledger = self._evidence.get(target.project_id)
                new_refs: list[MaterialRef] = []
                for material_id, chunk in selection.chunks:
                    key = (material_id, chunk.chunk_id)
                    if key in eid_of:
                        continue
                    packet = packets[material_id]
                    eid = ledger.record(
                        kind=material_kind(packet),
                        text=chunk.text,
                        source=EvidenceSource(external_ref=external_ref(packet, chunk)),
                        now=self._clock.now(),
                    )
                    eid_of[key] = eid
                    new_refs.append(
                        MaterialRef(
                            material_id=material_id, chunk_id=chunk.chunk_id, eid=eid
                        )
                    )
                if new_refs:
                    target.add_materials(new_refs)

            score_by_chunk = {hit.chunk_id: hit.score for hit in hits}
            selected = tuple(
                RetrievalHit(
                    material_id=material_id,
                    chunk_id=chunk.chunk_id,
                    score=score_by_chunk.get(chunk.chunk_id, 0.0),
                )
                for material_id, chunk in selection.chunks
            )
            # 到上限时**不新增 trace**：只投影、不再查；重入仍用同一份池。
            trace = None
            if existing is None and attempted and self._can_retrieve(target, stage):
                trace = RetrievalTrace(
                    stage=stage,
                    pass_index=pass_index,
                    query=query,
                    hits=tuple(hits),
                    selected=selected,
                    ok=error is None,
                    error=error,
                    created_at=self._clock.now(),
                )
            elif existing is None and not self._can_retrieve(target, stage):
                logger.info(
                    "material_search caller=round stage=%s pass=%d skipped=cap",
                    stage.value,
                    pass_index,
                )
            if trace is not None:
                target.note_retrieval(trace)
            if selection.skipped_manual:
                seen = {
                    (item.handle, item.source, item.pass_index)
                    for item in target.dropped
                }
                fresh = [
                    DroppedItem(
                        handle=chunk_id,
                        reason="手动带进的块因本步预算没进来（它还在证据池里）",
                        source=DropSource.TOOL,
                        seat_id=None,
                        pass_index=pass_index,
                    )
                    for chunk_id in selection.skipped_manual
                    if (chunk_id, DropSource.TOOL, pass_index) not in seen
                ]
                if fresh:
                    target.note_dropped(fresh)
            if ledger is not None:
                self._evidence_writer.save(ledger)
            self._round_writer.save(target)

            prepared = self._finish(
                selection=selection,
                packets=packets,
                pool_count=pool_count,
                eid_of=eid_of,
                web_by_material=web_by_material,
                missing=missing,
            )
        return prepared

    # —— 内部 ——

    def _retrieve(
        self, round_: Round, query: str, stage: RetrievalStage
    ) -> tuple[tuple[RetrievalHit, ...], str | None, bool]:
        if self._retriever is None or not self._can_retrieve(round_, stage):
            # 没接线 / 到上限 = **没查**，不是"查了零命中"。空 query 仍算查过
            # （检索器会回空），重入时据此不重查。
            return (), None, False
        try:
            hits = self._retriever.search(
                project_id=round_.project_id, query=query, limit=SEARCH_LIMIT
            )
        except Exception as error:  # noqa: BLE001 —— 检索是增强项，失败留痕不判死
            return (), f"{type(error).__name__}: {error}", True
        # `MaterialRetriever` 回的是 `MaterialHit`（带摘要/锚点）；池与 trace 只认
        # `RetrievalHit`（material_id/chunk_id/score）。在**这一处**转换，别让下游猜。
        return (
            tuple(
                RetrievalHit(
                    material_id=hit.material_id,
                    chunk_id=hit.chunk_id,
                    score=hit.score,
                )
                for hit in hits
            ),
            None,
            True,
        )

    def _can_retrieve(self, round_: Round, stage: RetrievalStage) -> bool:
        limit = (
            MAX_SEAT_RETRIEVALS
            if stage is RetrievalStage.SEAT
            else MAX_INTEGRATE_RETRIEVALS
        )
        used = sum(1 for trace in round_.retrievals if trace.stage is stage)
        return used < limit

    @staticmethod
    def _pass_index(round_: Round, stage: RetrievalStage) -> int:
        if stage is RetrievalStage.SEAT:
            return round_.seat_round_count
        # **第几次整合**（1 起），不是 `integration_seq` 的当前值：后者在
        # `Round.integrate` 成功后才 +1。用 +1 让 trace 的 pass 与整合成功后的
        # `RoundView.integration_seq` 对得上（前端据此取"本次整合"那条，不回退旧 trace）。
        return round_.integration_seq + 1

    @staticmethod
    def _trace_of(
        round_: Round, stage: RetrievalStage, pass_index: int
    ) -> RetrievalTrace | None:
        return next(
            (
                trace
                for trace in round_.retrievals
                if trace.stage is stage and trace.pass_index == pass_index
            ),
            None,
        )

    def _pool(
        self,
        round_: Round,
        eid_of: dict[tuple[str, str], EvidenceId],
        *,
        extra_hits: Sequence[RetrievalHit] = (),
    ) -> tuple[
        dict[str, MaterialPacket],
        dict[str, frozenset[str]],
        dict[str, dict[str, EvidenceId]],
        tuple[str, ...],
    ]:
        """池 = 所有 trace 命中 ∪ 已投影 ∪ 手动带料 ∪ 手勾材料的全部块。

        **联网材料不进池**：它一条一个号、走 `WEB_BUDGET`；混进材料投影会破
        "编号==进提示词的块"。它们单独收集给 `render_web_results`。
        """

        allowed: dict[str, set[str]] = {}
        missing: list[str] = []
        # 本步刚检索到的块**必须先入池**：否则下面的 chunks_of 会把它们过滤掉，
        # 表现就是"检索命中 K、带进 0"（真跑会像检索器坏了）。
        for hit in extra_hits:
            allowed.setdefault(hit.material_id, set()).add(hit.chunk_id)
        for trace in round_.retrievals:
            for hit in trace.hits:
                allowed.setdefault(hit.material_id, set()).add(hit.chunk_id)
        for ref in round_.materials:
            allowed.setdefault(ref.material_id, set()).add(ref.chunk_id)
        for item in round_.manual_chunks:
            allowed.setdefault(item.material_id, set()).add(item.chunk_id)
        for material_id in round_.pinned_materials:
            packet = self._materials.get(material_id)
            if packet is None or packet.project_id != str(round_.project_id):
                # 手勾材料也不许静默消失：提示词里要说清它不在了。
                if material_id not in missing:
                    missing.append(material_id)
                continue
            if packet.parsed.origin == WEB_ORIGIN:
                continue
            allowed.setdefault(material_id, set()).update(
                chunk.chunk_id for chunk in packet.parsed.chunks
            )

        packets: dict[str, MaterialPacket] = {}
        web_by_material: dict[str, dict[str, EvidenceId]] = {}
        for material_id in list(allowed):
            packet = self._materials.get(material_id)
            if packet is None or packet.project_id != str(round_.project_id):
                # 材料不在了 / 串项目：不编内容，也不把别家的材料写进本轮。
                if material_id not in missing:
                    missing.append(material_id)
                allowed.pop(material_id)
                continue
            if packet.parsed.origin == WEB_ORIGIN:
                # 联网材料：只保留"已发号的那些块"给 web 段；不进材料池。
                selected = _selected_web_chunks(round_)
                web_by_material[material_id] = {
                    chunk_id: eid_of[(material_id, chunk_id)]
                    for chunk_id in allowed.pop(material_id)
                    if (material_id, chunk_id) in eid_of
                    and (selected is None or (material_id, chunk_id) in selected)
                }
                continue
            packets[material_id] = packet

        pool = {
            material_id: frozenset(chunk_ids)
            for material_id, chunk_ids in allowed.items()
            if material_id in packets
        }
        return packets, pool, web_by_material, tuple(missing)

    @staticmethod
    def _chunk_of(
        packets: dict[str, MaterialPacket], hit: RetrievalHit
    ) -> MaterialChunk | None:
        packet = packets.get(hit.material_id)
        if packet is None:
            return None
        return next(
            (chunk for chunk in packet.parsed.chunks if chunk.chunk_id == hit.chunk_id),
            None,
        )

    def _finish(
        self,
        *,
        selection: Selection,
        packets: dict[str, MaterialPacket],
        pool_count: int,
        eid_of: dict[tuple[str, str], EvidenceId],
        web_by_material: dict[str, dict[str, EvidenceId]],
        missing: Sequence[str],
    ) -> PreparedContext:
        if selection.chunks:
            header = material_header(
                len(selection.chunks), pool_count, stage="本步"
            )
            by_material: dict[str, dict[str, EvidenceId]] = {}
            for material_id, chunk in selection.chunks:
                key = (material_id, chunk.chunk_id)
                if key not in eid_of:
                    # 理论上不会发生（trace 与材料同事务）；材料被外力删掉时也不编。
                    continue
                by_material.setdefault(material_id, {})[chunk.chunk_id] = eid_of[key]
            material_text = render_materials(
                [(packets[material_id], eids) for material_id, eids in by_material.items()]
            ).text
        else:
            header = (
                f"（本步没有选中材料；池里有 {pool_count} 块）" if pool_count else ""
            )
            material_text = ""

        web_entries = []
        for material_id, eids in web_by_material.items():
            packet = self._materials.get(material_id)
            if packet is None:
                continue
            chunks = [
                chunk for chunk in packet.parsed.chunks if chunk.chunk_id in eids
            ]
            if chunks:
                web_entries.append((packet, chunks, next(iter(eids.values()))))
        web_text = render_web_results(web_entries).text if web_entries else ""

        parts = [part for part in (header, material_text, web_text) if part]
        if missing:
            parts.append(
                missing_line(
                    len(missing), missing[:3], more=len(missing) > 3
                )
            )
        if not parts:
            parts.append(NO_MATERIAL_TEXT)
        return PreparedContext(
            text="\n".join(parts),
            hit_count=selection.hit_count,
            selected_count=len(selection.chunks),
            pool_count=pool_count,
            skipped_manual=selection.skipped_manual,
        )


def _selected_web_chunks(round_: Round) -> frozenset[tuple[str, str]] | None:
    """本步 web 快照：`None` = 旧 payload，渲染层回退到全部旧 web refs。"""

    return (
        None
        if round_.web_selection is None
        else frozenset(round_.web_selection)
    )
