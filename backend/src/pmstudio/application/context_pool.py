"""证据池与每步投影：**纯函数**，不碰 IO、不调模型。

两个职责：

1. `build_stage_query`：把"这一步该看什么"压成一句 query（席位职责 / 阻塞题 / 缺口）。
   它不是用户原话——SEED 才用原话，所以这里拒绝 SEED。
2. `select_projection`：从池里选出这一步真正进提示词的块。
   顺序写死：manual → 手勾（全局 ≤60%）→ 本步命中 → 池里其余（2-gram）。

## 为什么 60% 是**全局**的

手勾材料如果只在"pinned 阶段"受限，它还能在 stage_hits / pool rest 阶段被选回来，
自动段仍然可能是 0（实测：长文 13/15 篇手勾吃满 4000 字）。所以任何
`material_id ∈ pinned` 的块，无论从哪一步选中，都计入同一个 `pinned_used`。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pmstudio.application.context import (
    MATERIAL_BUDGET,
    SECTION_OVERHEAD,
    block_score,
    material_block_cost,
)
from pmstudio.application.dto import MaterialChunk, MaterialPacket
from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.authoring.readiness import ReadinessItem, Severity
from pmstudio.domain.round.retrieval import ManualChunk, RetrievalHit, RetrievalStage
from pmstudio.domain.round.round import Round

#: 手勾来源（手勾材料 + 手动带料）在一份投影里最多占多少预算。
PINNED_BUDGET_SHARE = 0.6
#: 自动材料（非手勾）每步最多几个槽——与检索器的 `_MATERIAL_SLOTS` **不是**同一个概念：
#: 那个管"检索结果多样性"，这个管"每步上下文深度"。
PROJECTION_SLOTS_PER_MATERIAL = 2
#: stage query 的字数上限（它只是检索的输入，不该变成第二份上下文）。
_MAX_STAGE_QUERY_CHARS = 300


@dataclass(frozen=True, slots=True)
class BudgetSplit:
    """手勾/自动的预算切分：有可行的自动候选时手勾硬上限，否则手勾可用满。"""

    usable: int
    pinned_cap: int
    auto_feasible: bool


def split_material_budget(
    *,
    usable: int,
    pinned_share: float,
    auto_costs: Sequence[int],
) -> BudgetSplit:
    """手勾/自动的预算切分。纯函数；不读库、不读 round。

    `auto_costs` 只许传**已过滤的自动候选**成本；调用方负责排除手勾、web_search、
    跨项目、已删除材料。自动候选存在且有一块能装进余量时才给自动段留位置；
    否则手勾可以用满预算（不人为留一段空预算）。
    """

    if not 0.0 <= pinned_share <= 1.0:
        raise ValueError("pinned_share 必须在 [0, 1]")
    pinned_cap = int(usable * pinned_share)
    reserve = usable - pinned_cap
    feasible = bool(auto_costs) and any(cost <= reserve for cost in auto_costs)
    if not feasible:
        return BudgetSplit(usable=usable, pinned_cap=usable, auto_feasible=False)
    return BudgetSplit(usable=usable, pinned_cap=pinned_cap, auto_feasible=True)


def build_stage_query(
    stage: RetrievalStage,
    *,
    round_: Round,
    seats: Sequence[SeatDefinition] = (),
    readiness: Sequence[ReadinessItem] = (),
) -> str:
    """这一步检索用的 query。

    - `SEAT`：待跑席位的职责 + 本轮主张 + 阻塞题 + 就绪度缺口；
    - `INTEGRATE`：就绪度缺口（blocker 优先）+ 轮内未决队列 + 互斥对子 +
      **未被本趟席位意见引用**的主张；
    - `SEED`：拒绝——那一步的 query 是用户原话，由 `StartRound` 直接传。

    结果去重、保序、截 300 字；同样的输入逐字相同。
    """

    if stage is RetrievalStage.SEED:
        raise ValueError("SEED 的 query 是用户原话，由 StartRound 直接传")

    parts: list[str] = []
    if stage is RetrievalStage.SEAT:
        for seat in seats:
            parts.extend((seat.title, seat.focus, *seat.sections, *seat.fields))
        parts.extend(claim.text for claim in round_.claims)
        parts.extend(
            item.title for item in _questions_of(round_) if item.blocking
        )
        parts.extend(_readiness_lines(readiness))
    else:
        parts.extend(_readiness_lines(readiness, blockers_first=True))
        parts.extend(item.title for item in round_.deferred)
        parts.extend(item.why for item in round_.contradictions)
        referenced = {
            eid
            for review in round_.current_reviews
            for opinion in review.opinions
            for eid in opinion.evidence_refs
        }
        parts.extend(
            claim.text for claim in round_.claims if claim.eid not in referenced
        )

    unique = dict.fromkeys(item.strip() for item in parts if item.strip())
    return " ".join(unique)[:_MAX_STAGE_QUERY_CHARS]


def _questions_of(round_: Round):
    """本轮相关的题：轮内未决队列 + 当前这批问卷。"""

    current = () if round_.questionnaire is None else round_.questionnaire.questions
    return (*round_.deferred, *current)


def _readiness_lines(
    items: Sequence[ReadinessItem], *, blockers_first: bool = False
) -> list[str]:
    ordered = (
        sorted(
            items,
            key=lambda item: 0 if item.severity is Severity.BLOCKER else 1,
        )
        if blockers_first
        else list(items)
    )
    return [f"{item.title}：{item.detail}" for item in ordered]


@dataclass(frozen=True, slots=True)
class Selection:
    """一步投影的结果。`used` 是按成本尺子算的预估值；渲染后还要再断言一次。"""

    chunks: tuple[tuple[str, MaterialChunk], ...]
    hit_count: int
    used: int
    skipped_manual: tuple[str, ...]


def select_projection(
    *,
    packets: Mapping[str, MaterialPacket],
    pool: Mapping[str, frozenset[str]],
    pinned: Sequence[str],
    manual: Sequence[ManualChunk],
    stage_hits: Sequence[RetrievalHit],
    query: str,
    budget: int = MATERIAL_BUDGET,
    section_overhead: int = SECTION_OVERHEAD,
    pinned_share: float = PINNED_BUDGET_SHARE,
) -> Selection:
    """从池里选出本步进提示词的块。

    **前提**：`packets` / `pool` 只含可投影材料（调用方负责排除 `web_search`：
    它一条一个号、走 `WEB_BUDGET`，混进来会破"编号==进提示词的块"）。

    选块顺序：manual（硬保）→ 每份手勾至少一块（地板）→
    手勾按相关度 → 本步命中 → 池里其余。
    手勾/手动合计受 `split_material_budget` 的 `pinned_cap` 约束：池里有可行的
    自动候选时是 `pinned_share`（默认 60%），没有时就是整个可用预算。
    装不下就跳过（继续试后面的块），不 break——高分块贵、后面的便宜块还该有机会。
    """

    usable = max(0, budget - section_overhead)
    pinned_order = tuple(dict.fromkeys(pinned))
    # 手动带的材料也算"手勾来源"：不占用户预算，但必须计入同一个 60% 闸。
    pinned_set = frozenset(pinned_order) | frozenset(item.material_id for item in manual)

    picked: list[tuple[str, MaterialChunk]] = []
    taken: set[str] = set()
    taken_materials: set[str] = set()
    auto_slots: dict[str, int] = {}
    skipped_manual: list[str] = []
    used = 0
    pinned_used = 0

    def chunks_of(material_id: str) -> tuple[MaterialChunk, ...]:
        packet = packets.get(material_id)
        if packet is None:
            return ()
        allowed = pool.get(material_id, frozenset())
        return tuple(chunk for chunk in packet.parsed.chunks if chunk.chunk_id in allowed)

    # 自动候选只算**非手勾**材料的块；每块都按"作为该材料第一块"渲染成本判断。
    auto_costs = [
        material_block_cost(packet, chunk, first_of_material=True)
        for material_id, packet in packets.items()
        if material_id not in pinned_set
        for chunk in chunks_of(material_id)
    ]
    split = split_material_budget(
        usable=usable, pinned_share=pinned_share, auto_costs=auto_costs
    )
    pinned_cap = split.pinned_cap

    def take(
        material_id: str, chunk: MaterialChunk, *, respect_pinned_cap: bool = True
    ) -> bool:
        nonlocal used, pinned_used
        if chunk.chunk_id in taken:
            return False
        packet = packets.get(material_id)
        if packet is None:
            return False
        first = material_id not in taken_materials
        cost = material_block_cost(packet, chunk, first_of_material=first)
        if used + cost > usable:
            return False
        if (
            respect_pinned_cap
            and material_id in pinned_set
            and pinned_used + cost > pinned_cap
        ):
            return False
        picked.append((material_id, chunk))
        taken.add(chunk.chunk_id)
        taken_materials.add(material_id)
        used += cost
        if material_id in pinned_set:
            pinned_used += cost
        return True

    # ① 手动带进的块：先装；装不下记下来（调用方负责留痕）。
    for item in manual:
        chunk = next(
            (one for one in chunks_of(item.material_id) if one.chunk_id == item.chunk_id),
            None,
        )
        if chunk is None:
            continue
        if not take(item.material_id, chunk) and chunk.chunk_id not in taken:
            skipped_manual.append(item.chunk_id)

    # ② 地板：每份手勾材料至少露一块，但**不越过 `pinned_cap`**。
    #    没有这一步，两份手勾材料时第一份长文能吃满 cap，第二份每一步都进不来。
    #    连最便宜的一块都放不进 cap → 当步不选，也不写 per-step drop（它仍在池里）。
    for material_id in pinned_order:
        if any(mid == material_id for mid, _ in picked):
            continue
        candidates = [
            chunk for chunk in chunks_of(material_id) if chunk.chunk_id not in taken
        ]
        candidates.sort(key=lambda chunk: (-block_score(chunk.text, query), chunk.ordinal))
        for chunk in candidates:
            if take(material_id, chunk):
                break

    # ③ 手勾材料：按本步 query 的相关度取，仍受 `pinned_cap` 闸。
    for material_id in pinned_order:
        candidates = [
            chunk for chunk in chunks_of(material_id) if chunk.chunk_id not in taken
        ]
        candidates.sort(key=lambda chunk: (-block_score(chunk.text, query), chunk.ordinal))
        for chunk in candidates:
            if pinned_used >= pinned_cap:
                break
            take(material_id, chunk)

    # ④ 本步检索命中：按检索分数（调用方已排好序）。
    for item in stage_hits:
        if item.chunk_id in taken:
            continue
        if item.material_id not in pinned_set and (
            auto_slots.get(item.material_id, 0) >= PROJECTION_SLOTS_PER_MATERIAL
        ):
            continue
        chunk = next(
            (one for one in chunks_of(item.material_id) if one.chunk_id == item.chunk_id),
            None,
        )
        if chunk is None:
            continue
        if take(item.material_id, chunk) and item.material_id not in pinned_set:
            auto_slots[item.material_id] = auto_slots.get(item.material_id, 0) + 1

    # ⑤ 池里其余：按 2-gram 相关度填满。
    rest: list[tuple[str, MaterialChunk]] = []
    for material_id in pool:
        rest.extend(
            (material_id, chunk)
            for chunk in chunks_of(material_id)
            if chunk.chunk_id not in taken
        )
    rest.sort(
        key=lambda item: (-block_score(item[1].text, query), item[0], item[1].ordinal)
    )
    for material_id, chunk in rest:
        if material_id not in pinned_set and (
            auto_slots.get(material_id, 0) >= PROJECTION_SLOTS_PER_MATERIAL
        ):
            continue
        if take(material_id, chunk) and material_id not in pinned_set:
            auto_slots[material_id] = auto_slots.get(material_id, 0) + 1

    # 后续阶段可能把 `①` 里记下的 manual 块又装回来：
    # 已经 `taken` 的不再算"没进来"，否则提示词/留痕会说反话。
    skipped_manual = [chunk_id for chunk_id in skipped_manual if chunk_id not in taken]
    return Selection(
        chunks=tuple(picked),
        hit_count=len(stage_hits),
        used=used,
        skipped_manual=tuple(skipped_manual),
    )
