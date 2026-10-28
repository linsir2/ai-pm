"""P（生产·真跑）：真 PostgreSQL + 词法 fallback + 真 `StartRound`。

只验**上下文形状**（哪些块进了这一轮、预算守不守得住），**不烧真模型答题**——
正确率的对照实验在 `experiment-v5`。这里用 `SqliteHybridRetriever(connection, None)`：
没有 embedder，向量分支必然失败并退回纯词法检索——**不是真 hybrid**（真 hybrid 见
`test_round_context_pool_hybrid_e2e.py`）。

**为什么不放 `tests/application/`**：那里的 harness 材料仓是内存的、检索器是替身；
真检索器返回的 `material_id` 在内存仓里查不到 → 自动材料会被全部过滤，
用例会**绿着但什么都没测到**（v4 评审 C.1 的原话）。
"""

import hashlib

from pmstudio.application import context
from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.use_cases import SECTION_OVERHEAD, StartRound
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.shared.ids import UserId
from pmstudio.domain.shared.template import standard_template
from pmstudio.infrastructure.postgres.materials import SqliteHybridRetriever, SqliteMaterialStore
from pmstudio.infrastructure.postgres.owner_scope import set_owner_scope
from pmstudio.infrastructure.postgres.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.postgres.repositories import (
    SqliteDiscussionRepository,
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteExplorationRepository,
    SqliteRoundRepository,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.support.builders import NOW, PROJECT
from tests.support.doubles import FixedClock, RecordingPublisher
from tests.support.postgres import open_direct

OWNER = UserId("u-convergence")
INPUT = "导出权限怎么定"


class _UnusedAnalyzer:
    """`create()` 不调分析者（那是 `analyze` 那一步的事）：这里只满足构造签名。"""


def put(store: SqliteMaterialStore, text: str, title: str):
    """把一份材料落到真库（真 `put`：块与 FTS 索引都由实现写）。"""

    parsed = ParsedMaterial(
        origin="upload",
        title=title,
        source_ref=f"attachment:{title}",
        media_type="text/markdown",
        bytes_len=len(text.encode("utf-8")),
        chunks=(
            MaterialChunk(
                chunk_id="c00",
                ordinal=0,
                text=text,
                heading_path=(),
                anchor="第 1 行",
            ),
        ),
        parser_id="local-text",
        parser_version="v1",
    )
    content = text.encode("utf-8")
    return store.put(
        project_id=PROJECT,
        parsed=parsed,
        sha256=hashlib.sha256(content).hexdigest(),
        content=content,
        now=NOW,
    )


def selection_cost(store: SqliteMaterialStore, round_) -> int:
    total = 0
    first_seen: set[str] = set()
    for ref in round_.materials:
        packet = store.get(ref.material_id)
        chunk = next(c for c in packet.parsed.chunks if c.chunk_id == ref.chunk_id)
        first = ref.material_id not in first_seen
        first_seen.add(ref.material_id)
        total += context.material_block_cost(packet, chunk, first_of_material=first)
    return total


async def test_a_real_round_converges_from_the_whole_library(tmp_path):
    """P：用户只勾 1 份，真检索器从整个库里把相关材料带进这一轮，且预算守得住。

    改前这条路**不存在**（库不可达 → 只用手勾的那份）；改后它必须成立：
    ① 上下文里出现**用户没勾**的材料，且块都发到号；
    ② 选块合计 + 收尾预留 ≤ 一个预算。
    """

    set_owner_scope(OWNER)
    connection = open_direct()
    ids = UuidIdGenerator()
    store = SqliteMaterialStore(connection, ids=ids)
    documents = SqliteDocumentRepository(connection)
    rounds = SqliteRoundRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT, title="收敛核验", now=NOW, template=standard_template()
    )

    pinned = put(store, "目标：这一轮做个导出。", "目标.md")
    gold = put(store, "导出权限怎么定：按角色分三级，管理员可导出全量。", "权限设计.md")
    for index in range(6):
        put(store, f"无关内容第 {index} 段，讲的是颜色与字体。", f"杂项{index}.md")

    start = StartRound(
        documents=documents,
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        uow=PgUnitOfWork(connection),
        analyzer=_UnusedAnalyzer(),  # type: ignore[arg-type]
        assigner=EvidenceAssigner(),
        checker=ReadinessChecker(),
        clock=FixedClock(),
        ids=ids,
        events=RecordingPublisher(),
        materials=store,
        explorations=SqliteExplorationRepository(connection),
        discussions=SqliteDiscussionRepository(connection),
        # **真**检索器：词法路走真 PG（没有 embedder → 它内部退回纯词法检索）。
        retriever=SqliteHybridRetriever(connection, None),
    )

    round_id = await start.create(
        project_id=PROJECT, user_input=INPUT, material_ids=[pinned.material_id]
    )

    round_ = rounds.get(round_id)
    selected = {ref.material_id for ref in round_.materials}
    assert pinned.material_id in selected, "手勾的那份一定在"
    assert gold.material_id in selected, (
        f"真检索器该把 gold 材料带进来（实际：{selected}）"
    )
    eids = [str(ref.eid) for ref in round_.materials]
    assert len(set(eids)) == len(eids), "每块一个号"

    # **渲染**出来的那一段才是模型真读到的：量它，而不是只量成本（尺子可能又是假的）。
    by_material: dict[str, dict[str, str]] = {}
    for ref in round_.materials:
        by_material.setdefault(ref.material_id, {})[ref.chunk_id] = str(ref.eid)
    rendered = context.render_materials(
        [(store.get(material_id), chunk_eids) for material_id, chunk_eids in by_material.items()]
    )
    assert len(rendered.text) <= context.MATERIAL_BUDGET, (
        f"渲染 {len(rendered.text)} 字 > 预算 {context.MATERIAL_BUDGET} 字"
    )
    # 成本尺子也要与渲染对得上（上界），否则下一轮又会静默超
    assert selection_cost(store, round_) + SECTION_OVERHEAD <= context.MATERIAL_BUDGET


async def test_a_real_round_caps_a_huge_pinned_block_and_keeps_the_auto_segment(tmp_path):
    """P：真 PostgreSQL + 真词法检索——SEED 先检索自动段，再按同一个 split 选块。

    合成单块 3000 字手勾装不进手勾 cap（60%），自动段 700 字装得进余量：
    手勾不该靠地板复活，自动段必须进得来，两者合计 + 收尾 ≤ 4000。
    """

    set_owner_scope(OWNER)
    connection = open_direct()
    ids = UuidIdGenerator()
    store = SqliteMaterialStore(connection, ids=ids)
    documents = SqliteDocumentRepository(connection)
    rounds = SqliteRoundRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT, title="切分核验", now=NOW, template=standard_template()
    )

    pinned = put(store, "字" * 3000, "超大手勾块.md")
    auto = put(store, "导出权限怎么定：" + "细" * 700, "自动材料.md")

    start = StartRound(
        documents=documents,
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        uow=PgUnitOfWork(connection),
        analyzer=_UnusedAnalyzer(),  # type: ignore[arg-type]
        assigner=EvidenceAssigner(),
        checker=ReadinessChecker(),
        clock=FixedClock(),
        ids=ids,
        events=RecordingPublisher(),
        materials=store,
        explorations=SqliteExplorationRepository(connection),
        discussions=SqliteDiscussionRepository(connection),
        retriever=SqliteHybridRetriever(connection, None),
    )

    round_id = await start.create(
        project_id=PROJECT, user_input=INPUT, material_ids=[pinned.material_id]
    )

    round_ = rounds.get(round_id)
    selected = {ref.material_id for ref in round_.materials}
    assert auto.material_id in selected, (
        f"自动段该进得来（实际：{selected}）"
    )
    assert pinned.material_id not in selected, "超出手勾 cap 的单块不许靠地板复活"
    assert selection_cost(store, round_) + SECTION_OVERHEAD <= context.MATERIAL_BUDGET
