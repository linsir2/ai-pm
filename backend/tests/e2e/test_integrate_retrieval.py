"""P2.6：整合失败重试的**真实上下文链路**（真 SQLite + 真 FTS + 真 `IntegrateRound`）。

这一条钉的是"解析失败可以重装，但检索只查一次、编号不许变"：

① 第一趟回坏 JSON → 第二趟在同一份上下文上重装，integration 恰好调用 2 次；
② INTEGRATE trace 只有 1 条、检索器只查 1 次（重装不重查）；
③ 重试前后 `Round.materials` 的 E 号集合**逐字不变**（重装不重新发号）；
④ 第二趟的提示词里带着第一趟的 `validation_error`（不是盲调）。

另有一条网关类失败：`ModelCallFailed` 是终态，`step=failed`、原因可见、**不重试**。
"""

import hashlib
import json

import pytest

from pmstudio.application.agents.integrator import Integrator
from pmstudio.application.agents.memory_curator import MemoryCurator
from pmstudio.application.dto import MaterialChunk, ParsedMaterial, round_view
from pmstudio.application.round_context import RoundContextPreparer
from pmstudio.application.use_cases import IntegrateRound
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.round.retrieval import RetrievalStage
from pmstudio.domain.round.round import MaterialRef, Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.domain.shared.template import standard_template
from pmstudio.harness.errors import ModelCallFailed
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.materials import (
    SqliteFtsRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteMemoryRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_seat
from tests.support.doubles import (
    FixedClock,
    RecordingPrompts,
    RecordingPublisher,
    ScriptedModelGateway,
    StaticSeatCatalog,
)
from tests.support.leases import bind_round_lease

MATERIAL_TEXT = "导出权限按角色控制，管理员才能导出全量。"

INTEGRATION_REPLY = json.dumps(
    {
        "basis": "读了文档与 E1",
        "questions": [
            {
                "kind": "gap",
                "title": "单次导出的最大行数",
                "why": "量级决定同步还是异步",
                "consequence_if_skipped": "按同步实现",
                "target": {"zone": "body", "section_id": "s8"},
                "type": "single",
                "options": [
                    {"option_id": "a", "text": "少于 1 万"},
                    {"option_id": "b", "text": "1 万以上"},
                ],
                "evidence_refs": ["E1"],
                "asked_by": [SEAT],
            }
        ],
        "list_items": [],
        "proposals": [],
        "dropped_opinions": [],
    },
    ensure_ascii=False,
)


class _CountingRetriever:
    """真 FTS 外面只套一个计数器：检索口径仍是 `SqliteFtsRetriever` 那一份。"""

    def __init__(self, inner: SqliteFtsRetriever) -> None:
        self.inner = inner
        self.calls = 0

    def search(self, *, project_id, query, limit=5):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self.inner.search(project_id=project_id, query=query, limit=limit)


def _setup(tmp_path, gateway: ScriptedModelGateway):  # type: ignore[no-untyped-def]
    connection = connect(tmp_path / "integrate_retry.db")
    ids = UuidIdGenerator()
    store = SqliteMaterialStore(connection, ids=ids)
    documents = SqliteDocumentRepository(connection)
    rounds = SqliteRoundRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    memory = SqliteMemoryRepository(connection)
    index = SqliteWorkspaceIndex(connection)
    uow = SqliteUnitOfWork(connection)
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT,
        title="整合重试 e2e",
        now=NOW,
        template=standard_template(),
    )

    parsed = ParsedMaterial(
        origin="upload",
        title="权限设计.md",
        source_ref="attachment:权限设计.md",
        media_type="text/markdown",
        bytes_len=len(MATERIAL_TEXT.encode("utf-8")),
        chunks=(
            MaterialChunk(
                chunk_id="c00",
                ordinal=0,
                text=MATERIAL_TEXT,
                heading_path=(),
                anchor="第 1 行",
            ),
        ),
        parser_id="local-text",
        parser_version="v1",
    )
    content = MATERIAL_TEXT.encode("utf-8")
    packet = store.put(
        project_id=PROJECT,
        parsed=parsed,
        sha256=hashlib.sha256(content).hexdigest(),
        content=content,
        now=NOW,
    )
    chunk_id = packet.parsed.chunks[0].chunk_id

    ledger = evidences.get(PROJECT)
    ledger.record(
        kind=EvidenceKind.EXTERNAL,
        text=MATERIAL_TEXT,
        source=EvidenceSource(external_ref=f"attachment:权限设计.md#{chunk_id}"),
        now=NOW,
        eid=EvidenceId("E1"),
    )
    evidences.save(ledger)

    round_ = Round(
        round_id=ROUND_ID,
        project_id=PROJECT,
        user_input="导出权限怎么定",
        created_at=NOW,
        step=Step.SEATS_RUNNING,
        waiting_on=WaitingOn.NONE,
        seats=(SEAT,),
        active_seats=(SEAT,),
        seat_round_count=1,
        materials=(
            MaterialRef(material_id=packet.material_id, chunk_id=chunk_id, eid=EvidenceId("E1")),
        ),
    )
    rounds.save(round_)
    bind_round_lease(connection, ROUND_ID)

    retriever = _CountingRetriever(SqliteFtsRetriever(connection))
    clock = FixedClock()
    prompts = RecordingPrompts()
    catalog = StaticSeatCatalog((make_seat(),))
    integrate = IntegrateRound(
        rounds=rounds,
        round_writer=rounds,
        documents=documents,
        evidence=evidences,
        evidence_writer=evidences,
        uow=uow,
        integrator=Integrator(model=gateway, prompts=prompts),
        catalog=catalog,
        index=index,
        checker=ReadinessChecker(),
        clock=clock,
        ids=ids,
        events=RecordingPublisher(),
        materials=store,
        curator=MemoryCurator(model=gateway, prompts=prompts),
        memory=memory,
        preparer=RoundContextPreparer(
            rounds=rounds,
            round_writer=rounds,
            evidence=evidences,
            evidence_writer=evidences,
            materials=store,
            retriever=retriever,
            uow=uow,
            clock=clock,
        ),
    )
    return connection, rounds, retriever, integrate, catalog


def _prompt_of(request) -> str:  # type: ignore[no-untyped-def]
    return "\n".join(message.content for message in request.messages)


async def test_a_bad_integration_packet_is_repaired_without_re_retrieving_or_re_numbering(
    tmp_path,
):
    gateway = ScriptedModelGateway(replies={"integration": ["这不是 JSON", INTEGRATION_REPLY]})
    connection, rounds, retriever, integrate, _ = _setup(tmp_path, gateway)
    try:
        before = rounds.get(ROUND_ID)
        before_eids = {str(ref.eid) for ref in before.materials}
        assert before_eids == {"E1"}

        await integrate(round_id=ROUND_ID)

        after = rounds.get(ROUND_ID)
        assert after.step is not Step.FAILED
        assert after.step is Step.AWAITING_ANSWERS
        assert after.failure is None

        purpose_counts = [request.purpose for request in gateway.requests]
        assert purpose_counts.count("integration") == 2, "解析失败该重装一次（且只一次）"

        traces = after.retrievals
        assert len(traces) == 1, "重装不重查：INTEGRATE trace 只许一条"
        assert traces[0].stage is RetrievalStage.INTEGRATE
        assert retriever.calls == 1, "同一份上下文只检索一次"

        assert {str(ref.eid) for ref in after.materials} == before_eids, (
            "重试前后 E 号集合必须逐字不变"
        )

        first, second = [
            request for request in gateway.requests if request.purpose == "integration"
        ]
        first_prompt = _prompt_of(first)
        second_prompt = _prompt_of(second)
        assert "上一次的报文没通过校验" not in first_prompt
        assert "上一次的报文没通过校验" in second_prompt, "第二次请求必须带着第一次的报错"
        assert "模型返回的不是合法 JSON" in second_prompt
    finally:
        connection.close()


async def test_a_model_call_failure_is_terminal_and_is_not_retried(tmp_path):
    gateway = ScriptedModelGateway(replies={})
    connection, rounds, retriever, integrate, catalog = _setup(tmp_path, gateway)
    try:
        with pytest.raises(ModelCallFailed):
            await integrate(round_id=ROUND_ID)

        after = rounds.get(ROUND_ID)
        assert after.step is Step.FAILED
        assert after.failure is not None
        assert "脚本里没有 integration 的回复" in after.failure.reason
        assert after.failure.at_step is Step.SEATS_RUNNING
        assert [request.purpose for request in gateway.requests].count("integration") == 1, (
            "网关类失败是终态，不重试"
        )
        assert retriever.calls == 1
        assert round_view(after, catalog=catalog).failure_reason == after.failure.reason
    finally:
        connection.close()
