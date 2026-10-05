"""把用例装起来的内存夹具：真领域 + 真用例 + 假端口（模型、事件、时钟、id）。

只活在测试里。它的存在是为了让每个用例的测试只写"这一步做了什么"。
"""

from collections.abc import Mapping, Sequence

from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.inquirer import Inquirer
from pmstudio.application.agents.integrator import Integrator
from pmstudio.application.agents.memory_curator import MemoryCurator
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.dto import ProjectSummaryView
from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.shared.ids import DocumentId, ProjectId
from pmstudio.harness.model.contracts import ModelGateway
from tests.support.builders import PROJECT, make_document, make_seat
from tests.support.doubles import (
    FixedClock,
    RecordingMaterialRetriever,
    RecordingPrompts,
    RecordingPublisher,
    RecordingWebFetch,
    RecordingWebSearch,
    ScriptedModelGateway,
    SeqIds,
    StaticSeatCatalog,
    StaticWorkspaceIndex,
)
from tests.support.fakes import (
    FakeTransaction,
    InMemoryDocumentRepository,
    InMemoryEvidenceRepository,
    InMemoryExplorationRepository,
    InMemoryMaterialStore,
    InMemoryMemoryRepository,
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


class _RoundHeadIndex:
    """`ListRounds` 在测试里的查询源：把内存仓储里的轮次投影成 `RoundHeadView`。

    与真实现（`SqliteWorkspaceIndex.list_rounds`）同一条口径：最新在前、只读头部几个键。
    """

    def __init__(self, rounds) -> None:  # type: ignore[no-untyped-def]
        self._rounds = rounds

    def list_rounds(self, project_id, *, limit: int = 20):  # type: ignore[no-untyped-def]
        from pmstudio.application.dto import round_head_view
        from pmstudio.infrastructure.sqlite.serialization import round_to_payload

        rounds = [item for item in self._rounds.all_rounds() if item.project_id == project_id]
        rounds.sort(key=lambda item: (item.created_at, str(item.round_id)), reverse=True)
        return tuple(
            round_head_view(
                round_id=str(item.round_id),
                step=str(item.step),
                created_at=item.created_at,
                payload=round_to_payload(item),
            )
            for item in rounds[:limit]
        )


class _RoundMaterialIndex:
    """材料引用在测试里的查询源：把内存仓储里的轮次投影成 `{material_id: (eid, …)}`。

    与真实现（`SqliteWorkspaceIndex.material_references` 扫 `rounds.payload`）同一条口径：
    只看 `Round.materials`，不限轮数，不带任何写能力。
    """

    def __init__(self, rounds) -> None:  # type: ignore[no-untyped-def]
        self._rounds = rounds

    def material_references(self, project_id):  # type: ignore[no-untyped-def]
        found: dict[str, list[str]] = {}
        for item in self._rounds.all_rounds():
            if item.project_id != project_id:
                continue
            for ref in item.materials:
                found.setdefault(ref.material_id, []).append(str(ref.eid))
        return {material_id: tuple(eids) for material_id, eids in found.items()}


class Harness:
    def __init__(
        self,
        *,
        replies: Mapping[str, str | Sequence[str]] | None = None,
        seat_replies: Mapping[str, str | Sequence[str]] | None = None,
        seats: Sequence[SeatDefinition] | None = None,
        gateway: ModelGateway | None = None,
        available_tool_keys: frozenset[str] = frozenset({"tavily_api_key"}),
        web_search: RecordingWebSearch | None = None,
        fetcher: RecordingWebFetch | None = None,
        material_retriever: RecordingMaterialRetriever | None = None,
    ) -> None:
        self.documents = InMemoryDocumentRepository()
        self.rounds = InMemoryRoundRepository()
        self.evidences = InMemoryEvidenceRepository()
        self.explorations = InMemoryExplorationRepository()
        self.materials = InMemoryMaterialStore()
        self.memory = InMemoryMemoryRepository()
        # 真解析器（不是替身）：上传路径在测试里也走真 pdfminer / bs4
        from pmstudio.infrastructure.tools.extract import LocalMaterialParser

        self.parser = LocalMaterialParser()
        self.uow = FakeTransaction(
            self.documents, self.rounds, self.evidences, self.explorations, self.memory
        )
        # gateway 是给"要卡住/要乱来"的测试用的注入口；平时用脚本化网关
        self.gateway = gateway or ScriptedModelGateway(
            replies=replies or {}, seat_replies=seat_replies or {}
        )
        self.prompts = RecordingPrompts()
        self.clock = FixedClock()
        self.ids = SeqIds()
        self.events = RecordingPublisher()
        self.index = StaticWorkspaceIndex()
        self.material_index = _RoundMaterialIndex(self.rounds)
        self.seats = StaticSeatCatalog(tuple(seats) if seats else (make_seat(),))
        # 工具目录用**真实现**（读真 TOML）：判据 `requires` / `max_queries` 是数据，
        # 用替身就测不出".toml 写错了"这件事（与材料解析器同一个理由）。
        from pmstudio.harness.tools.registry import DEFINITIONS_DIR, TomlToolCatalog

        self.tools = TomlToolCatalog(DEFINITIONS_DIR)
        self.available_tool_keys = available_tool_keys
        self.web_search = web_search or RecordingWebSearch()
        self.fetcher = fetcher or RecordingWebFetch()
        self.material_retriever = material_retriever or RecordingMaterialRetriever()
        self.analyzer = ClaimAnalyzer(
            model=self.gateway, prompts=self.prompts, catalog=self.seats
        )
        self.reviewer = SeatReviewer(model=self.gateway, prompts=self.prompts)
        self.inquirer = Inquirer(model=self.gateway, prompts=self.prompts)
        self.integrator = Integrator(model=self.gateway, prompts=self.prompts)
        self.curator = MemoryCurator(model=self.gateway, prompts=self.prompts)
        self.assigner = EvidenceAssigner()
        self.readiness = ReadinessChecker()
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
            checker=self.readiness,
            clock=self.clock,
            ids=self.ids,
            events=self.events,
            materials=self.materials,
            explorations=self.explorations,
        )

    # —— 探索（M8）——
    def run_inquiry(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import RunInquiry

        return RunInquiry(
            documents=self.documents,
            explorations=self.explorations,
            exploration_writer=self.explorations,
            evidence=self.evidences,
            memory=self.memory,
            uow=self.uow,
            inquirer=self.inquirer,
            checker=self.readiness,
            clock=self.clock,
            ids=self.ids,
            events=self.events,
        )

    def retry_exploration(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import RetryExploration

        return RetryExploration(
            explorations=self.explorations,
            exploration_writer=self.explorations,
            uow=self.uow,
            clock=self.clock,
            events=self.events,
        )

    def exploration_runner(self):  # type: ignore[no-untyped-def]
        """**缓存同一个实例**（与 `runner()` 同一个理由：取消要能找到那个任务）。"""

        from pmstudio.infrastructure.graph.exploration_runner import ExplorationRunner

        if not hasattr(self, "_exploration_runner"):
            self._exploration_runner = ExplorationRunner(
                run_inquiry=self.run_inquiry(),
                retry_exploration=self.retry_exploration(),
                explorations=self.explorations,
            )
        return self._exploration_runner

    def abandon_exploration(self, *, canceller):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import AbandonExploration

        return AbandonExploration(
            explorations=self.explorations,
            exploration_writer=self.explorations,
            uow=self.uow,
            clock=self.clock,
            events=self.events,
            canceller=canceller,
        )

    def keep_candidate(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import KeepCandidate

        return KeepCandidate(
            explorations=self.explorations,
            exploration_writer=self.explorations,
            materials=self.materials,
            uow=self.uow,
            clock=self.clock,
        )

    def dismiss_candidate(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import DismissCandidate

        return DismissCandidate(
            explorations=self.explorations,
            exploration_writer=self.explorations,
            uow=self.uow,
        )

    def list_explorations(self, *, limit: int | None = None):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import ListExplorations

        if limit is None:
            return ListExplorations(explorations=self.explorations)
        return ListExplorations(explorations=self.explorations, limit=limit)

    def submit_opening(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import SubmitOpening

        return SubmitOpening(
            rounds=self.rounds,
            round_writer=self.rounds,
            catalog=self.seats,
            tools=self.tools,
            available_tool_keys=self.available_tool_keys,
            web_search=self.web_search,
            evidence=self.evidences,
            evidence_writer=self.evidences,
            materials=self.materials,
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
            checker=self.readiness,
            clock=self.clock,
            events=self.events,
            materials=self.materials,
            memory=self.memory,
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
            index=self.index,
            checker=self.readiness,
            clock=self.clock,
            ids=self.ids,
            events=self.events,
            materials=self.materials,
            curator=self.curator,
            memory=self.memory,
        )

    def submit_answers(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import SubmitAnswers

        return SubmitAnswers(
            rounds=self.rounds,
            round_writer=self.rounds,
            evidence=self.evidences,
            evidence_writer=self.evidences,
            catalog=self.seats,
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
            memory=self.memory,
            memory_writer=self.memory,
            ids=self.ids,
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

    def dig_round(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import DigRound

        return DigRound(
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

        return GetRound(
            rounds=self.rounds,
            evidence=self.evidences,
            catalog=self.seats,
            tools=self.tools,
            available_tool_keys=self.available_tool_keys,
            materials=self.materials,
        )

    def get_versions(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetVersions

        return GetVersions(documents=self.documents)

    def get_version_diff(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetVersionDiff

        return GetVersionDiff(documents=self.documents)

    def get_evidence(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetEvidence

        return GetEvidence(documents=self.documents, evidence=self.evidences)

    def rollback_version(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import RollbackVersion

        return RollbackVersion(
            committer=self.committer(),
            documents=self.documents,
            clock=self.clock,
            events=self.events,
        )

    def export_markdown(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import ExportMarkdown

        return ExportMarkdown(
            documents=self.documents,
            evidence=self.evidences,
            checker=self.readiness,
            clock=self.clock,
        )

    def get_readiness(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetReadiness

        return GetReadiness(documents=self.documents, checker=self.readiness)

    def get_schema(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetSchema

        return GetSchema()

    def list_rounds(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import ListRounds

        return ListRounds(index=_RoundHeadIndex(self.rounds))

    def search(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import SearchProject

        return SearchProject(
            documents=self.documents,
            evidence=self.evidences,
            index=self.index,
            materials=self.material_retriever,
        )

    def upload_material(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import UploadMaterial

        return UploadMaterial(
            documents=self.documents,
            materials=self.materials,
            parser=self.parser,
            clock=self.clock,
            uow=self.uow,
        )

    def list_materials(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import ListMaterials

        return ListMaterials(
            materials=self.materials, parser=self.parser, index=self.material_index
        )

    def delete_material(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import DeleteMaterial

        return DeleteMaterial(
            materials=self.materials,
            parser=self.parser,
            index=self.material_index,
            uow=self.uow,
        )

    def reparse_material(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import ReparseMaterial

        return ReparseMaterial(
            materials=self.materials,
            parser=self.parser,
            index=self.material_index,
            uow=self.uow,
            clock=self.clock,
        )

    def fetch_material(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import FetchMaterial

        return FetchMaterial(
            documents=self.documents,
            materials=self.materials,
            parser=self.parser,
            fetcher=self.fetcher,
            index=self.material_index,
            clock=self.clock,
            uow=self.uow,
        )

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
            tools=self.tools,
            available_tool_keys=self.available_tool_keys,
            checker=self.readiness,
            materials=self.materials,
            memory=self.memory,
        )

    def list_projects(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import ListProjects

        return ListProjects(index=self.index)

    def runner(self):  # type: ignore[no-untyped-def]
        """**缓存同一个实例**：`run()` 记下"这一轮在跑的任务"，`cancel()` 要能找到它。

        每次 new 一个 runner，取消就落空了——真实现（`build_container`）本来就只造一个。
        """

        from pmstudio.application.use_cases import GetRound
        from pmstudio.infrastructure.graph.graph import RoundRunner

        if not hasattr(self, "_runner"):
            self._runner = RoundRunner(
                start_round=self.start_round(),
                submit_opening=self.submit_opening(),
                run_seat_round=self.run_seat_round(),
                integrate_round=self.integrate_round(),
                submit_answers=self.submit_answers(),
                confirm_draft=self.confirm_draft(),
                retry_round=self.retry_round(),
                dig_round=self.dig_round(),
                get_round=GetRound(
                    rounds=self.rounds,
                    evidence=self.evidences,
                    catalog=self.seats,
                    tools=self.tools,
                    available_tool_keys=self.available_tool_keys,
                    materials=self.materials,
                ),
            )
        return self._runner

    def abandon_round(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import AbandonRound

        return AbandonRound(
            rounds=self.rounds,
            round_writer=self.rounds,
            uow=self.uow,
            clock=self.clock,
            events=self.events,
            canceller=self.runner(),
        )

    def api_services(self, *, model_name: str):  # type: ignore[no-untyped-def]
        from pmstudio.api.app import ApiServices

        return ApiServices(
            runner=self.runner(),
            dig_round=self.dig_round(),
            abandon_round=self.abandon_round(),
            exploration_runner=self.exploration_runner(),
            abandon_exploration=self.abandon_exploration(
                canceller=self.exploration_runner()
            ),
            keep_candidate=self.keep_candidate(),
            dismiss_candidate=self.dismiss_candidate(),
            list_explorations=self.list_explorations(),
            create_project=self.create_project(),
            workspace=self.get_workspace(),
            list_projects=self.list_projects(),
            get_round=self.get_round(),
            get_schema=self.get_schema(),
            list_rounds=self.list_rounds(),
            search=self.search(),
            upload_material=self.upload_material(),
            list_materials=self.list_materials(),
            fetch_material=self.fetch_material(),
            delete_material=self.delete_material(),
            reparse_material=self.reparse_material(),
            get_document=self.get_document(),
            get_readiness=self.get_readiness(),
            export_markdown=self.export_markdown(),
            edit_entry=self.edit_entry(),
            get_versions=self.get_versions(),
            get_version_diff=self.get_version_diff(),
            rollback_version=self.rollback_version(),
            get_evidence=self.get_evidence(),
            forget_memory=self.forget_memory(),
            stream=self.events,
            model_name=model_name,
        )

    def forget_memory(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import ForgetMemory

        return ForgetMemory(
            memory=self.memory,
            memory_writer=self.memory,
            uow=self.uow,
            clock=self.clock,
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
