"""造领域对象的测试构造器。只造对象，不做断言。"""

from dataclasses import replace
from datetime import UTC, datetime

from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Claim, Contradiction
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import (
    DocumentId,
    EntryId,
    EvidenceId,
    FeatureSlug,
    ProjectId,
    RoundId,
)
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.template import standard_template
from pmstudio.domain.shared.template_adapter import TemplateAdapter, adapt
from pmstudio.domain.shared.writing import BlockKind, WriteOp

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
PROJECT = ProjectId("p1")
ROUND_ID = RoundId("r1")
SEAT = "seat.data_scale"


def make_document(*, title: str = "订单中台") -> Document:
    return Document.new(document_id=DocumentId("doc1"), project_id=PROJECT, title=title, now=NOW)


def make_material_packet(
    *texts: str,
    title: str = "需求.md",
    parser_id: str = "local-text",
    material_id: str = "m1",
    origin: str = "upload",
    source_ref: str | None = None,
):
    """一份已盖章的材料（工具层 C-2）。测试里最常用的材料夹具。"""

    from pmstudio.application.dto import MaterialChunk, MaterialPacket, ParsedMaterial

    chunks = tuple(
        MaterialChunk(
            chunk_id=f"{material_id}#c{index:02d}",
            ordinal=index,
            text=text,
            heading_path=(),
            anchor=f"第 {index + 1} 行",
        )
        for index, text in enumerate(texts)
    )
    return MaterialPacket(
        material_id=material_id,
        project_id=str(PROJECT),
        parsed=ParsedMaterial(
            origin=origin,
            title=title,
            source_ref=source_ref or f"attachment:{title}",
            media_type="text/markdown",
            bytes_len=100,
            chunks=chunks,
            parser_id=parser_id,
            parser_version="v1",
        ),
        created_at=NOW,
    )


def make_web_packet(
    *,
    url: str = "https://example.com/rbac",
    text: str = "行业通行做法是 RBAC：角色绑权限，用户绑角色。",
    title: str = "权限模型综述",
):
    """一条 Tavily 结果的**解析草稿**（工具层 C-2b）。

    形状与 `TavilySearchGateway._packet` 对齐：单块、锚点是"网页正文"、
    正文由夹具自己截断（见下面的 `snippet`）。**没有 material_id**——那要等存储层盖章。
    """

    from pmstudio.application.dto import NO_SOURCE_REF, MaterialChunk, ParsedMaterial
    from pmstudio.infrastructure.tools.tavily import PARSER_VERSION

    # 夹具自己的截断长度（**与生产无关**：生产的联网截断在 `tools/tavily.py` 那条路）
    snippet = text[:300]
    return ParsedMaterial(
        origin="web_search",
        title=title,
        # 与网关同口径：没有链接时用占位符（**不是空串**）——
        # 空串会让去重键认不出"这条没有出处"，两条无链接结果会共用一把键。
        source_ref=url or NO_SOURCE_REF,
        media_type="text/html",
        bytes_len=len(text.encode("utf-8")),
        chunks=(
            MaterialChunk(
                chunk_id="tavily#c00",
                ordinal=0,
                text=snippet,
                heading_path=(),
                anchor="网页正文",
            ),
        ),
        parser_id="tavily",
        # **跟随生产常量**（2026-10-20 批 6c）：联网材料的入库口径换过档位
        # （query 片段 → 整页），写死一个版本号就会漂。
        parser_version=PARSER_VERSION,
        truncated=len(text) > len(snippet),
        warnings=("来自检索：这一轮的检索词",),
    )


def make_claim(
    *,
    text: str = "要做一个订单导出功能",
    raw_span: str = "想做个订单导出的功能",
    label: ClaimLabel = ClaimLabel.NEED,
    target: Target | None = None,
    eid: EvidenceId | None = None,
) -> Claim:
    return Claim(
        claim_id="c1",
        text=text,
        label=label,
        target=target or Target.body("s5"),
        raw_span=raw_span,
        eid=eid,
    )


def make_round(*, seats: tuple[str, ...] = (SEAT,)) -> Round:
    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="想做个订单导出的功能", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    round_.submit_opening(seats=seats)
    return round_


def make_contradiction(
    *, why: str = "一个说不能改，一个说要能改，两侧立场互斥"
) -> Contradiction:
    return Contradiction(claim_id_a="c1", claim_id_b="c2", why=why)


def make_round_with_contradictions() -> Round:
    """一轮：主张 c1（不许改）与 c2（点错了要改）被判成互斥对子。

    真跑里就是这么出现的（P11：`q1 kind=conflict … E7 与 E8 冲突`）。
    """

    round_ = Round.start(
        round_id=ROUND_ID,
        project_id=PROJECT,
        user_input="点检记录不许改；点错了要能改",
        now=NOW,
    )
    round_.attach_claims(
        (
            make_claim(text="点检记录不能改", raw_span="点检记录不许改", eid=EvidenceId("E1")),
            replace(
                make_claim(text="点错了要能改", raw_span="点错了要能改", eid=EvidenceId("E2")),
                claim_id="c2",
            ),
        )
    )
    round_.submit_opening(seats=(SEAT,))
    round_.attach_contradictions((make_contradiction(),))
    return round_


def make_seat(
    seat_id: str = SEAT,
    *,
    title: str = "数据规模与性能",
    sections: tuple[str, ...] = (),
    fields: tuple[str, ...] = (),
) -> SeatDefinition:
    return SeatDefinition(
        seat_id=seat_id,
        title=title,
        focus="量级、性能上限与同步异步的分界",
        sections=sections,
        fields=fields,
    )


#: 测试里绝大多数 target 都按 standard 说（这些用例测的是既有行为，不是模板变体）。
#: 短名是刻意的：`Target.feature_field(a, b, home=STD.feature_home)` 要塞进 100 列。
#: 需要别的模板的用例自己 `adapt(...)`。
STD: TemplateAdapter = adapt(standard_template())


def document_with_feature(*, one_liner: str = "把订单导出成表格") -> Document:
    document = make_document()
    document.apply(
        [
            WriteInstruction(
                instruction_id="i1",
                entry_id=EntryId("en-bootstrap"),
                op=WriteOp.ADD,
                target=Target.feature_field(
                    FeatureSlug("order-export"), "one_liner", home=STD.feature_home
                ),
                text=one_liner,
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            )
        ],
        now=NOW,
        summary="干跑",
        authorized_new_features=frozenset({FeatureSlug("order-export")}),
    )
    return document


def seed_user_input(ledger: EvidenceLedger, claims: tuple[Claim, ...]) -> tuple[Claim, ...]:
    """把主张写进账本并盖编号——等价于 EvidenceAssigner.assign_claims 的结果。"""

    assigned: list[Claim] = []
    for claim in claims:
        eid = ledger.record(
            kind=EvidenceKind.USER_INPUT,
            text=claim.text,
            source=EvidenceSource(round_id=ROUND_ID, raw_span=claim.raw_span),
            now=NOW,
        )
        assigned.append(replace(claim, eid=eid))
    return tuple(assigned)
