"""模拟评测的机械层（M11）：文档索引、筛、R1/R2/R3 减建议规则。全部纯函数。

三件事同住一个模块，因为它们是同一条流水线（读文档 → 拦垃圾 → 出建议），
拆开只会让"说不清每一处作用"的嫌疑变大：

- `document_anchors`：把文档视图走成"条目 → 正文/落点/所属功能"的索引——
  这是**唯一的文档索引**，筛（锚点校验/覆盖率）与规则（抄条文/找优先级条目）共用；
- `review_issues`：机械筛。引用落空、验收没核完、quote 与 not_documented 两头空、
  身份词/编数字——抓住就重装；抓两次整趟失败（口径见 `02` §11.0.48）；
- `evaluate_rules`：R1 删条目 / R2 降级 / R3 建议复核 + 提案上限。
  **减建议只能从事实里长出来**，不经过模型发挥。

引用地址（`SimCitation.key` / `review_source_ref`）是账本上的 `external_ref` 形状：
`simulation:review:{round_id}#{persona}@{feature}#{local_id}`，可读、可审计、与
M10 的 `simulation:inquiry:` 分得开。
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp

if TYPE_CHECKING:  # dto 反向 import 本模块的常量（LimitsView），运行期不能成环
    from pmstudio.application.dto import DocumentView

#: 一次评测最多几个模拟使用者（成本上界：3 人 × 2 次重装 = 第六本账 ≤6 次调用）。
MAX_SIM_REVIEW_PERSONAS = 3

#: 一次评测最多查几个功能（报文本来就支持列表，两种选法同价）。
MAX_SIM_REVIEW_FEATURES = 2

#: 每份草案最多跑几趟评测（成功的才算；失败不计数、可重跑）。
MAX_SIM_REVIEWS_PER_DRAFT = 1

#: 每个 persona 最多几次调用（1 次业务 + 1 次带指引重装）。
MAX_SIM_REVIEW_ATTEMPTS = 2

#: 每次运行最多几条减建议（超出的进摘要，不静默丢）。
MAX_SIM_REVIEW_PROPOSALS = 3


@dataclass(frozen=True, slots=True)
class ReviewAnchors:
    """文档索引：筛与规则共用的一份只读地图。

    全部按 `entry_id` 字符串寻址（账本编号是另一条线，这里说的是文档条目）。
    """

    entry_text: dict[str, str]
    entry_target: dict[str, Target]
    entry_feature: dict[str, str | None]
    entry_order: dict[str, int]
    feature_order: tuple[str, ...]
    feature_fields: dict[str, frozenset[str]]
    acceptance: dict[str, tuple[str, ...]]
    priority_entry: dict[str, str]


def document_anchors(view: "DocumentView") -> ReviewAnchors:
    """把文档视图走成索引。走的是 `DocumentView` 的结构（不解析文本，不读库）。"""

    entry_text: dict[str, str] = {}
    entry_target: dict[str, Target] = {}
    entry_feature: dict[str, str | None] = {}
    entry_order: dict[str, int] = {}
    feature_order: list[str] = []
    feature_fields: dict[str, set[str]] = {}
    acceptance: dict[str, list[str]] = {}
    priority_entry: dict[str, str] = {}

    def walk(block, *, section_id: str, inherited_slug: str | None) -> None:  # type: ignore[no-untyped-def]
        slug = block.feature_slug or inherited_slug
        field = block.field
        if slug is not None and slug not in feature_fields:
            feature_order.append(slug)
            feature_fields[slug] = set()
        if slug is not None and field is not None:
            feature_fields[slug].add(field)
        target = _target_of(section_id, slug, field)
        for entry in block.entries:
            eid = str(entry.entry_id)
            entry_text[eid] = entry.text
            entry_target[eid] = target
            entry_feature[eid] = slug
            entry_order[eid] = len(entry_order)
            if slug is not None and field == "acceptance":
                acceptance.setdefault(slug, []).append(eid)
            if slug is not None and field == "priority" and slug not in priority_entry:
                priority_entry[slug] = eid
        for child in block.children:
            walk(child, section_id=section_id, inherited_slug=slug)

    for section in view.sections:
        section_id = str(section.section_id)
        for block in section.blocks:
            walk(block, section_id=section_id, inherited_slug=None)
    return ReviewAnchors(
        entry_text=entry_text,
        entry_target=entry_target,
        entry_feature=entry_feature,
        entry_order=entry_order,
        feature_order=tuple(feature_order),
        feature_fields={key: frozenset(value) for key, value in feature_fields.items()},
        acceptance={key: tuple(value) for key, value in acceptance.items()},
        priority_entry=priority_entry,
    )


def _target_of(section_id: str, slug: str | None, field: str | None) -> Target:
    if slug is not None and field is not None:
        return Target.body(section_id, feature_slug=slug, field=field)
    if slug is not None:
        return Target.body(section_id, feature_slug=slug)
    if field is not None:
        return Target.body(section_id, field=field)
    return Target.body(section_id)


# ── 筛 ──────────────────────────────────────────────────────────────────────


def review_issues(
    review: dict, *, anchors: ReviewAnchors, requested: tuple[str, ...]
) -> tuple[str, ...]:
    """一个 persona 报告的机械问题清单（空 = 干净）。只报问题，怎么处置是调用方的事。

    只扫**模型自撰字段**（note / action / need）：文档引文（quote / step_text）豁免——
    验收标准里的"60 秒"之类数字要能原样抄进来。
    """

    issues: list[str] = []
    reported: list[str] = []
    for feature in review["features"]:
        slug = feature["feature_slug"]
        if slug in reported:
            issues.append(f"功能 {slug} 出现了两份报告（接受层只会取第一份，另一份白写）")
            continue
        reported.append(slug)
        if slug not in requested:
            issues.append(f"报告了没被点名的功能：{slug}（只许走查被点名的）")
            continue
        issues.extend(_feature_issues(feature, slug=slug, anchors=anchors))
    for slug in requested:
        if slug not in reported:
            issues.append(f"被点名的功能 {slug} 没有被走查——每个功能都要一份报告")
    return tuple(issues)


def _feature_issues(feature: dict, *, slug: str, anchors: ReviewAnchors) -> list[str]:
    from pmstudio.application.sim_inquiry import narrow_offense

    issues: list[str] = []
    walkthrough = feature["walkthrough"]
    frictions = feature["frictions"]
    friction_ids = {item["local_id"] for item in frictions}

    if feature["started"]:
        if not walkthrough:
            issues.append(f"功能 {slug}：started=true 但一条走查步骤都没有")
    elif not any(item["kind"] == "cannot_start" for item in frictions):
        issues.append(
            f"功能 {slug}：started=false 必须有一条 kind=cannot_start 的摩擦（说清缺什么）"
        )

    for item in frictions:
        local_id = item["local_id"]
        entry_id = item["entry_id"]
        field = item["field"]
        if item["kind"] == "cannot_start" and feature["started"]:
            issues.append(f"功能 {slug} 摩擦 {local_id}：只有 started=false 才用 cannot_start")
        if entry_id:
            if entry_id not in anchors.entry_text:
                issues.append(f"功能 {slug} 摩擦 {local_id}：指向不存在的条目 {entry_id}")
            else:
                owner = anchors.entry_feature.get(entry_id)
                if owner not in (None, slug):
                    issues.append(
                        f"功能 {slug} 摩擦 {local_id}："
                        f"指到了别的功能的内容（{entry_id} 属于 {owner}）"
                    )
        elif field:
            if field not in anchors.feature_fields.get(slug, frozenset()):
                issues.append(f"功能 {slug} 摩擦 {local_id}：字段 {field} 不存在")
        if bool(item["quote"].strip()) == item["not_documented"]:
            issues.append(
                f"功能 {slug} 摩擦 {local_id}：quote 与 not_documented 必须二选一"
            )
        hit = narrow_offense(f"功能 {slug} 摩擦 {local_id}", item["note"])
        if hit:
            issues.append(hit)

    for step in walkthrough:
        if step["outcome"] != "ok" and not step["need"].strip():
            issues.append(
                f"功能 {slug} 第 {step['step_index']} 步："
                f"outcome={step['outcome']} 必须写 need（缺什么）"
            )
        for where, text in (
            (f"功能 {slug} 第 {step['step_index']} 步（action）", step["action"]),
            (f"功能 {slug} 第 {step['step_index']} 步（need）", step["need"]),
        ):
            hit = narrow_offense(where, text)
            if hit:
                issues.append(hit)

    expected = set(anchors.acceptance.get(slug, ()))
    got = [item["entry_id"] for item in feature["acceptance_checks"]]
    for missing in sorted(expected - set(got)):
        issues.append(f"功能 {slug}：验收条目 {missing} 没有被核对（每条都要给状态）")
    for extra in sorted(set(got) - expected):
        issues.append(f"功能 {slug}：核对了不属于该功能的验收条目 {extra}")

    for ref in feature["verdict_because"]:
        if ref not in friction_ids:
            issues.append(f"功能 {slug}：verdict_because 指向不存在的摩擦 {ref}")
    if feature["verdict"] in ("friction", "blocked") and not feature["verdict_because"]:
        issues.append(f"功能 {slug}：verdict={feature['verdict']} 必须给 verdict_because")
    if feature["verdict"] == "smooth" and feature["verdict_because"]:
        issues.append(f"功能 {slug}：verdict=smooth 不许带 verdict_because")
    return issues


# ── 规则：从事实长出"减"建议 ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SimCitation:
    """一条要被发号的模型产出（摩擦点 / 被标矛盾的验收核查）。

    `key` 是人可读的引用键；`external_ref` 由 `review_source_ref` 拼——
    同一文件里两个函数，改格式只改一处。
    """

    persona_id: str
    feature_slug: str
    local_id: str
    text: str

    @property
    def key(self) -> str:
        return f"{self.persona_id}@{self.feature_slug}#{self.local_id}"


@dataclass(frozen=True, slots=True)
class SimProposalDraft:
    """减建议草案：编号还没发（`ref_keys` 由调用方换成账本编号后再落成领域提案）。"""

    proposal_id: str
    entry_id: str
    op: WriteOp
    target: Target
    text: str
    rationale: str
    ref_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RulesOutcome:
    proposals: tuple[SimProposalDraft, ...] = ()
    citations: tuple[SimCitation, ...] = ()
    advisories: tuple[str, ...] = ()


def review_source_ref(round_id: str, citation: SimCitation) -> str:
    """账本 `external_ref`：`simulation:review:{round}#{persona}@{feature}#{local_id}`。"""

    return f"simulation:review:{round_id}#{citation.key}"


def evaluate_rules(
    reviews: Sequence[dict], *, anchors: ReviewAnchors, requested: tuple[str, ...]
) -> RulesOutcome:
    """R1 删条目 / R2 降级 / R3 建议复核 + 上限。输入是**已过筛**的报告（顺序 = 勾选顺序）。"""

    total = len(reviews)
    citations: dict[str, SimCitation] = {}
    advisories: list[str] = []

    def cite(item: SimCitation) -> str:
        citations.setdefault(item.key, item)
        return item.key

    # R1：同一条目被 ≥2 个不同 persona 标（dead_end 摩擦或 contradicted 验收核查）
    flags: dict[str, dict[str, list[SimCitation]]] = {}
    for review in reviews:
        for feature in review["features"]:
            slug = feature["feature_slug"]
            for friction in feature["frictions"]:
                if friction["kind"] == "dead_end" and friction["entry_id"]:
                    flags.setdefault(friction["entry_id"], {}).setdefault(
                        review["persona_id"], []
                    ).append(
                        SimCitation(
                            persona_id=review["persona_id"],
                            feature_slug=slug,
                            local_id=friction["local_id"],
                            text=friction["note"],
                        )
                    )
            for check in feature["acceptance_checks"]:
                if check["status"] == "contradicted":
                    flags.setdefault(check["entry_id"], {}).setdefault(
                        review["persona_id"], []
                    ).append(
                        SimCitation(
                            persona_id=review["persona_id"],
                            feature_slug=slug,
                            local_id=check["local_id"],
                            text=check["note"].strip()
                            or f"验收条目 {check['entry_id']} 被标矛盾",
                        )
                    )

    r1: list[SimProposalDraft] = []
    for entry_id in sorted(flags, key=lambda item: anchors.entry_order.get(item, 1 << 30)):
        contributors = flags[entry_id]
        if len(contributors) < 2:
            continue
        ref_keys = tuple(
            cite(item) for items in contributors.values() for item in items
        )
        r1.append(
            SimProposalDraft(
                proposal_id="",
                entry_id=entry_id,
                op=WriteOp.REMOVE,
                target=anchors.entry_target[entry_id],
                text=anchors.entry_text[entry_id],
                rationale=(
                    f"模拟评测：{len(contributors)}/{total} 个模拟使用者标了这一条走不通/相矛盾"
                ),
                ref_keys=ref_keys,
            )
        )

    # R2/R3：按文档功能序逐个功能判
    r2: list[SimProposalDraft] = []
    for slug in anchors.feature_order:
        if slug not in requested:
            continue
        reports = [
            (review, feature)
            for review in reviews
            for feature in review["features"]
            if feature["feature_slug"] == slug
        ]
        if not reports:
            continue
        blocked = [review for review, feature in reports if feature["verdict"] == "blocked"]
        not_started = [review for review, feature in reports if not feature["started"]]
        triggers = {review["persona_id"]: review for review in (*blocked, *not_started)}
        if len(blocked) >= 2 or not_started:
            priority = anchors.priority_entry.get(slug)
            if priority is None:
                advisories.append(
                    f"功能 {slug} 没有优先级条目——降级建议出不了，建议人工看一眼"
                )
            else:
                ref_keys = []
                for review, feature in reports:
                    if review["persona_id"] not in triggers:
                        continue
                    representative = next(
                        (item for item in feature["frictions"] if item["blocks_flow"]),
                        feature["frictions"][0] if feature["frictions"] else None,
                    )
                    if representative is None:
                        continue
                    ref_keys.append(
                        cite(
                            SimCitation(
                                persona_id=review["persona_id"],
                                feature_slug=slug,
                                local_id=representative["local_id"],
                                text=representative["note"],
                            )
                        )
                    )
                if not ref_keys:
                    advisories.append(
                        f"功能 {slug} 的走不通报告没有可引用的摩擦点——降级建议出不了"
                    )
                else:
                    r2.append(
                        SimProposalDraft(
                            proposal_id="",
                            entry_id=priority,
                            op=WriteOp.REPLACE,
                            target=anchors.entry_target[priority],
                            text=(
                                f"待复核（模拟评测：{len(triggers)}/{total} "
                                "个模拟使用者未能走通）"
                            ),
                            rationale=f"模拟评测：功能 {slug} 多人未能走通，优先级待复核",
                            ref_keys=tuple(ref_keys),
                        )
                    )
        all_sunk = all(
            (not feature["started"]) or feature["verdict"] == "blocked"
            for _, feature in reports
        )
        if all_sunk:
            advisories.append(
                f"功能 {slug}：全体模拟使用者未能走通——建议人工复核"
                "（当前不支持自动删除整棵功能）"
            )

    specs = r1 + r2
    kept = specs[:MAX_SIM_REVIEW_PROPOSALS]
    overflow = specs[MAX_SIM_REVIEW_PROPOSALS:]
    if overflow:
        lines = "；".join(f"{item.target.label()}『{item.text[:20]}…』" for item in overflow)
        advisories.append(
            f"另有 {len(overflow)} 条减建议未进卡片"
            f"（每次最多 {MAX_SIM_REVIEW_PROPOSALS} 条）：{lines}"
        )
    proposals = tuple(
        replace(item, proposal_id=f"sim#{index}") for index, item in enumerate(kept, start=1)
    )
    return RulesOutcome(
        proposals=proposals,
        citations=tuple(citations.values()),
        advisories=tuple(advisories),
    )


__all__ = [
    "MAX_SIM_REVIEW_ATTEMPTS",
    "MAX_SIM_REVIEW_FEATURES",
    "MAX_SIM_REVIEW_PERSONAS",
    "MAX_SIM_REVIEW_PROPOSALS",
    "MAX_SIM_REVIEWS_PER_DRAFT",
    "ReviewAnchors",
    "RulesOutcome",
    "SimCitation",
    "SimProposalDraft",
    "document_anchors",
    "evaluate_rules",
    "review_issues",
    "review_source_ref",
]
