"""把用例装起来的内存夹具：真领域 + 真用例 + 假端口（模型、事件、时钟、id）。

只活在测试里。它的存在是为了让每个用例的测试只写"这一步做了什么"。
"""

from collections.abc import Mapping, Sequence

from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.integrator import Integrator
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.dto import ProjectSummaryView
from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.shared.ids import DocumentId, ProjectId
from tests.support.builders import PROJECT, make_document, make_seat
from tests.support.doubles import (
    FixedClock,
    RecordingPrompts,
    RecordingPublisher,
    ScriptedModelGateway,
    SeqIds,
    StaticSeatCatalog,
    StaticWorkspaceIndex,
)
from tests.support.fakes import (
    FakeTransaction,
    InMemoryDocumentRepository,
    InMemoryEvidenceRepository,
    InMemoryRoundRepository,
)


class InMemoryProjectFactory:
    """只造空骨架（与 SqliteProjectFactory 同语义），供 API 测试用。

    注意：它直接 `seed` 跳过了事务——**只许在测试里这么干**；真实现走 SqliteUnitOfWork。
    """

    def __init__(self, documents, evidences, index, ids) -> None:  # type: ignore[no-untyped-def]
        self._documents = documents
        self._evidences = evidences
        self._index = index
        self._ids = ids

    def create(self, *, project_id, title, now):  # type: ignore[no-untyped-def]
        document_id = DocumentId(self._ids.new("doc"))
        self._documents.seed(
            Document.new(
                document_id=document_id, project_id=project_id, title=title, now=now
            )
        )
        self._evidences.seed(EvidenceLedger.empty(project_id))
        self._index.set_summary(
            ProjectSummaryView(
                project_id=str(project_id), title=title, version_no=1, updated_at=now
            )
        )
        return document_id


class Harness:
    def __init__(
        self,
        *,
        replies: Mapping[str, str | Sequence[str]] | None = None,
        seat_replies: Mapping[str, str | Sequence[str]] | None = None,
        seats: Sequence[SeatDefinition] | None = None,
    ) -> None:
        self.documents = InMemoryDocumentRepository()
        self.rounds = InMemoryRoundRepository()
        self.evidences = InMemoryEvidenceRepository()
        self.uow = FakeTransaction(self.documents, self.rounds, self.evidences)
        self.gateway = ScriptedModelGateway(
            replies=replies or {}, seat_replies=seat_replies or {}
        )
        self.prompts = RecordingPrompts()
        self.clock = FixedClock()
        self.ids = SeqIds()
        self.events = RecordingPublisher()
        self.index = StaticWorkspaceIndex()
        self.seats = StaticSeatCatalog(tuple(seats) if seats else (make_seat(),))
        self.analyzer = ClaimAnalyzer(
            model=self.gateway, prompts=self.prompts, catalog=self.seats
        )
        self.reviewer = SeatReviewer(model=self.gateway, prompts=self.prompts)
        self.integrator = Integrator(model=self.gateway, prompts=self.prompts)
        self.assigner = EvidenceAssigner()
        self.factory = InMemoryProjectFactory(
            self.documents, self.evidences, self.index, self.ids
        )

    def seed_project(self, *, title: str = "订单中台") -> ProjectId:
        self.documents.seed(make_document(title=title))
        self.evidences.seed(EvidenceLedger.empty(PROJECT))
        self.index.set_summary(
            ProjectSummaryView(
                project_id=str(PROJECT), title=title, version_no=1, updated_at=self.clock.now()
            )
        )
        return PROJECT

    def seat_titles(self) -> dict[str, str]:
        return self.seats.titles()

    # —— 用例工厂 ——
    def start_round(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import StartRound

        return StartRound(
            documents=self.documents,
            rounds=self.rounds,
            round_writer=self.rounds,
            evidence=self.evidences,
            evidence_writer=self.evidences,
            uow=self.uow,
            analyzer=self.analyzer,
            assigner=self.assigner,
            clock=self.clock,
            ids=self.ids,
            events=self.events,
        )

    def submit_opening(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import SubmitOpening

        return SubmitOpening(
            rounds=self.rounds,
            round_writer=self.rounds,
            uow=self.uow,
            clock=self.clock,
            events=self.events,
        )

    def run_seat_round(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import RunSeatRound

        return RunSeatRound(
            rounds=self.rounds,
            round_writer=self.rounds,
            documents=self.documents,
            evidence=self.evidences,
            evidence_writer=self.evidences,
            uow=self.uow,
            reviewer=self.reviewer,
            catalog=self.seats,
            assigner=self.assigner,
            clock=self.clock,
            events=self.events,
        )

    def integrate_round(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import IntegrateRound

        return IntegrateRound(
            rounds=self.rounds,
            round_writer=self.rounds,
            documents=self.documents,
            evidence=self.evidences,
            evidence_writer=self.evidences,
            uow=self.uow,
            integrator=self.integrator,
            catalog=self.seats,
            clock=self.clock,
            ids=self.ids,
            events=self.events,
        )

    def submit_answers(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import SubmitAnswers

        return SubmitAnswers(
            rounds=self.rounds,
            round_writer=self.rounds,
            evidence=self.evidences,
            evidence_writer=self.evidences,
            uow=self.uow,
            clock=self.clock,
            events=self.events,
        )

    def confirm_draft(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import ConfirmDraft

        return ConfirmDraft(
            committer=self.committer(), rounds=self.rounds, clock=self.clock, events=self.events
        )

    def committer(self):  # type: ignore[no-untyped-def]
        from pmstudio.domain.services.committer import ProposalCommitter
        from pmstudio.domain.services.scope_guard import ScopeGuard
        from pmstudio.domain.services.traceability import TraceabilityChecker

        return ProposalCommitter(
            documents=self.documents,
            writer=self.documents,
            rounds=self.rounds,
            round_writer=self.rounds,
            evidence=self.evidences,
            scope=ScopeGuard(),
            traceability=TraceabilityChecker(),
            uow=self.uow,
        )

    def retry_round(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import RetryRound

        return RetryRound(
            rounds=self.rounds,
            round_writer=self.rounds,
            uow=self.uow,
            clock=self.clock,
            events=self.events,
        )

    def create_project(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import CreateProject

        return CreateProject(
            factory=self.factory, index=self.index, clock=self.clock, ids=self.ids
        )

    def get_round(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetRound

        return GetRound(rounds=self.rounds, evidence=self.evidences, catalog=self.seats)

    def get_document(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetDocument

        return GetDocument(documents=self.documents)

    def get_workspace(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetWorkspace

        return GetWorkspace(
            documents=self.documents,
            rounds=self.rounds,
            evidence=self.evidences,
            index=self.index,
            catalog=self.seats,
        )

    def list_projects(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import ListProjects

        return ListProjects(index=self.index)

    def runner(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetRound
        from pmstudio.infrastructure.graph.graph import RoundRunner

        return RoundRunner(
            start_round=self.start_round(),
            submit_opening=self.submit_opening(),
            run_seat_round=self.run_seat_round(),
            integrate_round=self.integrate_round(),
            submit_answers=self.submit_answers(),
            confirm_draft=self.confirm_draft(),
            retry_round=self.retry_round(),
            get_round=GetRound(rounds=self.rounds, evidence=self.evidences, catalog=self.seats),
        )

    def api_services(self, *, model_name: str):  # type: ignore[no-untyped-def]
        from pmstudio.api.app import ApiServices

        return ApiServices(
            runner=self.runner(),
            create_project=self.create_project(),
            workspace=self.get_workspace(),
            list_projects=self.list_projects(),
            get_round=self.get_round(),
            get_document=self.get_document(),
            edit_entry=self.edit_entry(),
            stream=self.events,
            model_name=model_name,
        )

    def edit_entry(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import EditEntry

        return EditEntry(
            documents=self.documents,
            evidence=self.evidences,
            evidence_writer=self.evidences,
            committer=self.committer(),
            uow=self.uow,
            clock=self.clock,
            ids=self.ids,
            events=self.events,
        )
