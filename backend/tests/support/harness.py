"""把用例装起来的内存夹具：真领域 + 真用例 + 假端口（模型、事件、时钟、id）。

只活在测试里。它的存在是为了让每个用例的测试只写"这一步做了什么"。
"""

from collections.abc import Mapping, Sequence

from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.discusser import Discusser
from pmstudio.application.agents.inquirer import Inquirer
from pmstudio.application.agents.integrator import Integrator
from pmstudio.application.agents.memory_curator import MemoryCurator
from pmstudio.application.agents.persona_simulator import PersonaSimulatorAgent
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.agents.sim_inquirer import SimInquiryAgent
from pmstudio.application.dto import ProjectSummaryView
from pmstudio.application.errors import UnknownDiscussion
from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.discussion.thread import Discussion
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.shared.ids import DiscussionId, DocumentId, ProjectId
from pmstudio.harness.model.contracts import ModelGateway
from pmstudio.infrastructure.discussion.roles import DiscussionRole
from pmstudio.infrastructure.personas.catalog import PersonaDefinition
from tests.support.builders import PROJECT, make_document, make_seat
from tests.support.doubles import (
    FixedClock,
    RecordingCallLog,
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
    InMemoryDigestRepository,
    InMemoryDocumentRepository,
    InMemoryEvidenceRepository,
    InMemoryExplorationRepository,
    InMemoryInspirationRepository,
    InMemoryMaterialStore,
    InMemoryMemoryRepository,
    InMemoryObservationRepository,
    InMemoryRoundRepository,
    InMemoryTemplateRepository,
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

    def create(self, *, project_id, title, now, template=None):  # type: ignore[no-untyped-def]
        document_id = DocumentId(self._ids.new("doc"))
        self._documents.seed(
            Document.new(
                document_id=document_id,
                project_id=project_id,
                title=title,
                now=now,
                template=template,
            )
        )
        self._evidences.seed(EvidenceLedger.empty(project_id))
        self._index.set_summary(
            ProjectSummaryView(
                project_id=str(project_id),
                title=title,
                version_no=1,
                updated_at=now,
                template_id=template.template_id,
                template_title=template.title,
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
    `Round.materials` + 手勾/手动带料哨兵，不限轮数，不带任何写能力。
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
            for material_id in item.pinned_materials:
                found.setdefault(material_id, []).append("手勾")
            for chunk in item.manual_chunks:
                found.setdefault(chunk.material_id, []).append("手动带料")
        return {material_id: tuple(eids) for material_id, eids in found.items()}


class InMemoryDiscussionRepository:
    """讨论线程的内存仓储（M9-T3）：与 `InMemoryExplorationRepository` 同口径。

    后写的在前（真实现按 `created_at DESC`）；找不到抛 `UnknownDiscussion`（404）。
    定义在这里而不是 `fakes.py`：Task 3 文件清单只含 `harness.py`，不另动 fakes。
    """

    def __init__(self) -> None:
        from copy import deepcopy as _deepcopy  # noqa: F401  (局部 import 免顶层噪音)

        self._items: dict[object, Discussion] = {}
        self.saves = 0

    def seed(self, discussion: Discussion) -> None:  # type: ignore[no-untyped-def]
        from copy import deepcopy

        self._items[discussion.discussion_id] = deepcopy(discussion)

    def get(self, discussion_id: DiscussionId) -> Discussion:  # type: ignore[no-untyped-def]
        from copy import deepcopy

        item = self._items.get(discussion_id)
        if item is None:
            raise UnknownDiscussion(discussion_id)
        return deepcopy(item)

    def save(self, discussion: Discussion) -> None:  # type: ignore[no-untyped-def]
        from copy import deepcopy

        self.saves += 1
        self._items[discussion.discussion_id] = deepcopy(discussion)

    def list_for_project(
        self, project_id, *, limit: int = 20  # type: ignore[no-untyped-def]
    ):  # type: ignore[no-untyped-def]
        from copy import deepcopy

        items = [
            item for item in self._items.values() if str(item.project_id) == str(project_id)
        ]
        return tuple(deepcopy(item) for item in items[::-1][:limit])

    def snapshot(self):  # type: ignore[no-untyped-def]
        from copy import deepcopy

        return deepcopy(self._items)

    def restore(self, snapshot) -> None:  # type: ignore[no-untyped-def]
        from copy import deepcopy

        self._items = deepcopy(snapshot)


def _default_discussion_roles() -> tuple[DiscussionRole, ...]:
    """测试默认的三固定角色（与 `roles.toml` 同 id/title，零文件依赖）。"""

    return (
        DiscussionRole(
            role_id="dlg.challenger",
            title="反方",
            focus="找漏洞、要反例、泼冷水",
        ),
        DiscussionRole(
            role_id="dlg.user_voice",
            title="用户之声",
            focus="替真实用户说话：他们会怎么用、哪里会骂",
        ),
        DiscussionRole(
            role_id="dlg.analogist",
            title="类比者",
            focus="别的产品、别的领域怎么做过类似的事",
        ),
    )


def _default_personas() -> tuple[PersonaDefinition, ...]:
    """测试默认的模拟使用者（与 `personas.toml` 同 id/title，零文件依赖）。"""

    return (
        PersonaDefinition(
            persona_id="persona.first_touch",
            title="首次上手者",
            situation="第一次使用，没有背景，手上只有文档与界面文案",
            goal="独立走完一次完整任务",
            behavior="按主流程从头走，不读任何内部说明；卡一次就停",
            probes="入口、前置条件、每一步的反馈",
        ),
        PersonaDefinition(
            persona_id="persona.edge_maker",
            title="异常状态制造者",
            situation="对抗性走查：故意走空/满/重复/非法状态",
            goal="把流程搞崩，看文档有没有定义后果",
            behavior="每一步都先试边界输入；专找没有定义的异常路径",
            probes="异常与边界字段、错误提示、回滚",
        ),
        PersonaDefinition(
            persona_id="persona.bulk_operator",
            title="高频批量操作者",
            situation="每天重复操作同类对象，路径已熟悉",
            goal="少点击、可预期，别让重复劳动变慢",
            behavior="优先找筛选/批量/导出；跳过解释性文字；对超时和失败敏感",
            probes="批量入口、上限、失败重试",
        ),
    )


class Harness:
    def __init__(
        self,
        *,
        replies: Mapping[str, str | Sequence[str]] | None = None,
        seat_replies: Mapping[str, str | Sequence[str]] | None = None,
        persona_replies: Mapping[str, str | Sequence[str]] | None = None,
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
        self.discussions = InMemoryDiscussionRepository()
        self.materials = InMemoryMaterialStore()
        self.memory = InMemoryMemoryRepository()
        # 真解析器（不是替身）：上传路径在测试里也走真 pdfminer / bs4
        from pmstudio.infrastructure.tools.extract import LocalMaterialParser

        self.parser = LocalMaterialParser()
        self.uow = FakeTransaction(
            self.documents,
            self.rounds,
            self.evidences,
            self.explorations,
            self.discussions,
            self.memory,
        )
        # gateway 是给"要卡住/要乱来"的测试用的注入口；平时用脚本化网关
        self.gateway = gateway or ScriptedModelGateway(
            replies=replies or {},
            seat_replies=seat_replies or {},
            persona_replies=persona_replies or {},
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
        # 调用轨迹读模型替身（可观测性 v2）：真实现是 `SqliteRoundCallLog`（真 SQLite）。
        self.call_log = RecordingCallLog()
        #: 工具留样器：默认 None = 什么都不记（与生产 `trace_model` 默认关同一条纪律）。
        #: 用例要验留样时自己塞一个真的 `SqliteToolCallRecorder`。
        self.tool_recorder = None
        self.analyzer = ClaimAnalyzer(
            model=self.gateway, prompts=self.prompts, catalog=self.seats
        )
        self.reviewer = SeatReviewer(model=self.gateway, prompts=self.prompts)
        self.inquirer = Inquirer(model=self.gateway, prompts=self.prompts)
        self.sim_inquirer = SimInquiryAgent(model=self.gateway, prompts=self.prompts)
        self.persona_simulator = PersonaSimulatorAgent(model=self.gateway, prompts=self.prompts)
        self.personas = _default_personas()
        self.discusser = Discusser(model=self.gateway, prompts=self.prompts)
        self.discussion_roles = _default_discussion_roles()
        self.integrator = Integrator(model=self.gateway, prompts=self.prompts)
        self.curator = MemoryCurator(model=self.gateway, prompts=self.prompts)
        self.assigner = EvidenceAssigner()
        self.readiness = ReadinessChecker()
        self.factory = InMemoryProjectFactory(
            self.documents, self.evidences, self.index, self.ids
        )
        self.observations = InMemoryObservationRepository()
        self.digests = InMemoryDigestRepository()
        self.inspirations = InMemoryInspirationRepository()
        self.templates = InMemoryTemplateRepository()
        # 生产在启动时把 TOML 内置模板 seed 进库；测试镜像同一件事——
        # 否则默认 `template_id="standard"` 的创建全炸（与 `tools` 用真 TOML 同一理由）。
        from pmstudio.domain.shared.template import standard_template
        from pmstudio.infrastructure.templates.loader import TEMPLATES_DIR, load_template_file

        self.templates.save(standard_template(), builtin=True)
        self.templates.save(load_template_file(TEMPLATES_DIR / "light.toml"), builtin=True)

    def seed_project(self, *, title: str = "订单中台") -> ProjectId:
        from pmstudio.domain.shared.template import standard_template

        standard = standard_template()
        self.documents.seed(make_document(title=title))
        self.evidences.seed(EvidenceLedger.empty(PROJECT))
        self.index.set_summary(
            ProjectSummaryView(
                project_id=str(PROJECT),
                title=title,
                version_no=1,
                updated_at=self.clock.now(),
                template_id=standard.template_id,
                template_title=standard.title,
            )
        )
        return PROJECT

    def seat_titles(self) -> dict[str, str]:
        return self.seats.titles()

    # —— 用例工厂 ——
    def start_round(self, *, retriever=None):  # type: ignore[no-untyped-def]
        """建轮用例。`retriever` **默认 None**（= 只用手勾的材料，与改前一致）。

        要验"库级检索 + 收敛"的用例自己传一个检索器（`F2/F3`）——默认不给，
        是为了让既有用例的行为**不受**这条新路影响。
        """

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
            discussions=self.discussions,
            memory=self.memory,
            retriever=retriever,
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
            inspiration_repo=self.inspirations,
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

    # —— 讨论（M9-T3；照 exploration 接入点仿写）——
    def start_discussion(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import StartDiscussion

        return StartDiscussion(
            documents=self.documents,
            discussions=self.discussions,
            discussion_writer=self.discussions,
            explorations=self.explorations,
            uow=self.uow,
            clock=self.clock,
            ids=self.ids,
            events=self.events,
            catalog=self.seats,
            discussion_roles=self.discussion_roles,
        )

    def say_to_thread(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import SayToThread

        return SayToThread(
            documents=self.documents,
            discussions=self.discussions,
            discussion_writer=self.discussions,
            evidence=self.evidences,
            explorations=self.explorations,
            uow=self.uow,
            discusser=self.discusser,
            checker=self.readiness,
            clock=self.clock,
            events=self.events,
            catalog=self.seats,
            discussion_roles=self.discussion_roles,
        )

    def assemble_cards(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import AssembleCards

        return AssembleCards(
            documents=self.documents,
            discussions=self.discussions,
            discussion_writer=self.discussions,
            evidence=self.evidences,
            uow=self.uow,
            discusser=self.discusser,
            checker=self.readiness,
            clock=self.clock,
            events=self.events,
            catalog=self.seats,
            discussion_roles=self.discussion_roles,
        )

    def run_panel_round(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import RunPanelRound

        return RunPanelRound(
            documents=self.documents,
            discussions=self.discussions,
            discussion_writer=self.discussions,
            evidence=self.evidences,
            explorations=self.explorations,
            uow=self.uow,
            discusser=self.discusser,
            checker=self.readiness,
            clock=self.clock,
            events=self.events,
            catalog=self.seats,
            discussion_roles=self.discussion_roles,
        )

    def join_panel(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import JoinPanel

        return JoinPanel(
            discussions=self.discussions,
            discussion_writer=self.discussions,
            uow=self.uow,
            clock=self.clock,
            events=self.events,
            catalog=self.seats,
            discussion_roles=self.discussion_roles,
        )

    def discussion_runner(self):  # type: ignore[no-untyped-def]
        """**缓存同一个实例**（取消要能找到那个任务，照 exploration_runner）。"""

        from pmstudio.infrastructure.discussion.runner import DiscussionRunner

        if not hasattr(self, "_discussion_runner"):
            self._discussion_runner = DiscussionRunner(
                run_panel_round=self.run_panel_round(),
                discussions=self.discussions,
            )
        return self._discussion_runner

    def keep_discussion_card(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import KeepDiscussionCard

        return KeepDiscussionCard(
            discussions=self.discussions,
            discussion_writer=self.discussions,
            materials=self.materials,
            uow=self.uow,
            clock=self.clock,
            inspiration_repo=self.inspirations,
        )

    def dismiss_discussion_card(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import DismissDiscussionCard

        return DismissDiscussionCard(
            discussions=self.discussions,
            discussion_writer=self.discussions,
            uow=self.uow,
        )

    def run_sim_inquiry(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import RunSimInquiry

        return RunSimInquiry(
            rounds=self.rounds,
            round_writer=self.rounds,
            documents=self.documents,
            evidence=self.evidences,
            memory=self.memory,
            materials=self.materials,
            uow=self.uow,
            sim_inquirer=self.sim_inquirer,
            checker=self.readiness,
            clock=self.clock,
            events=self.events,
        )

    def run_sim_review(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import RunSimReview

        return RunSimReview(
            rounds=self.rounds,
            round_writer=self.rounds,
            documents=self.documents,
            evidence=self.evidences,
            evidence_writer=self.evidences,
            memory=self.memory,
            materials=self.materials,
            uow=self.uow,
            personas=self.personas,
            simulator=self.persona_simulator,
            checker=self.readiness,
            clock=self.clock,
            events=self.events,
        )

    def keep_sim_inquiry(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import KeepSimInquiry

        return KeepSimInquiry(
            rounds=self.rounds,
            round_writer=self.rounds,
            materials=self.materials,
            uow=self.uow,
            clock=self.clock,
            inspiration_repo=self.inspirations,
        )

    def close_discussion(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import CloseDiscussion

        return CloseDiscussion(
            discussions=self.discussions,
            discussion_writer=self.discussions,
            uow=self.uow,
            clock=self.clock,
            events=self.events,
        )

    def list_discussions(self, *, limit: int | None = None):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import ListDiscussions

        if limit is None:
            return ListDiscussions(discussions=self.discussions)
        return ListDiscussions(discussions=self.discussions, limit=limit)

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
            # 判"这份材料被哪些编号引用过"——用专门的那份（`StaticWorkspaceIndex`
            # 只有查询投影，没有 `material_references`；`fetch_material` 也用它）
            index=self.material_index,
            uow=self.uow,
            clock=self.clock,
            events=self.events,
            tool_recorder=self.tool_recorder,
        )

    def attach_materials(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import AttachMaterials

        return AttachMaterials(
            rounds=self.rounds,
            round_writer=self.rounds,
            materials=self.materials,
            uow=self.uow,
        )

    def run_seat_round(self, *, preparer=None):  # type: ignore[no-untyped-def]
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
            observations_repo=self.observations,
            preparer=preparer,
        )

    def integrate_round(self, *, preparer=None):  # type: ignore[no-untyped-def]
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
            observations_repo=self.observations,
            digest_repo=self.digests,
            inspiration_repo=self.inspirations,
            preparer=preparer,
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
            committer=self.committer(),
            rounds=self.rounds,
            clock=self.clock,
            events=self.events,
            digest_repo=self.digests,
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
            factory=self.factory,
            index=self.index,
            clock=self.clock,
            ids=self.ids,
            templates=self.templates,
        )

    def create_template(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import CreateTemplate

        return CreateTemplate(templates=self.templates, ids=self.ids)

    def list_templates(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import ListTemplates

        return ListTemplates(templates=self.templates)

    def get_template(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetTemplate

        return GetTemplate(templates=self.templates)

    def update_template(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import UpdateTemplate

        return UpdateTemplate(templates=self.templates)

    def delete_template(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import DeleteTemplate

        return DeleteTemplate(templates=self.templates)

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

    def get_round_calls(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetRoundCalls

        return GetRoundCalls(rounds=self.rounds, call_log=self.call_log)

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

    def get_schema(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetSchema

        return GetSchema(
            discussion_roles=self.discussion_roles,
            personas=self.personas,
            templates=tuple(entry.template for entry in self.templates.list()),
        )

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
                run_sim_inquiry=self.run_sim_inquiry(),
                run_sim_review=self.run_sim_review(),
                attach_materials=self.attach_materials(),
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

    def skip_seat(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import SkipSeat

        return SkipSeat(
            rounds=self.rounds,
            round_writer=self.rounds,
            uow=self.uow,
            events=self.events,
            clock=self.clock,
            viewer=self.get_round(),
        )

    def api_services(self, *, model_name: str):  # type: ignore[no-untyped-def]
        from pmstudio.api.app import ApiServices

        return ApiServices(
            runner=self.runner(),
            dig_round=self.dig_round(),
            abandon_round=self.abandon_round(),
            skip_seat=self.skip_seat(),
            exploration_runner=self.exploration_runner(),
            abandon_exploration=self.abandon_exploration(
                canceller=self.exploration_runner()
            ),
            keep_candidate=self.keep_candidate(),
            dismiss_candidate=self.dismiss_candidate(),
            list_explorations=self.list_explorations(),
            start_discussion=self.start_discussion(),
            say_to_thread=self.say_to_thread(),
            assemble_cards=self.assemble_cards(),
            join_panel=self.join_panel(),
            discussion_runner=self.discussion_runner(),
            keep_card=self.keep_discussion_card(),
            dismiss_card=self.dismiss_discussion_card(),
            keep_sim_inquiry=self.keep_sim_inquiry(),
            close_discussion=self.close_discussion(),
            list_discussions=self.list_discussions(),
            create_project=self.create_project(),
            create_template=self.create_template(),
            list_templates=self.list_templates(),
            get_template=self.get_template(),
            update_template=self.update_template(),
            delete_template=self.delete_template(),
            workspace=self.get_workspace(),
            list_projects=self.list_projects(),
            get_round=self.get_round(),
            get_round_calls=self.get_round_calls(),
            get_schema=self.get_schema(),
            list_rounds=self.list_rounds(),
            search=self.search(),
            upload_material=self.upload_material(),
            list_materials=self.list_materials(),
            fetch_material=self.fetch_material(),
            delete_material=self.delete_material(),
            reparse_material=self.reparse_material(),
            get_document=self.get_document(),
            export_markdown=self.export_markdown(),
            edit_entry=self.edit_entry(),
            get_versions=self.get_versions(),
            get_version_diff=self.get_version_diff(),
            rollback_version=self.rollback_version(),
            get_evidence=self.get_evidence(),
            forget_memory=self.forget_memory(),
            memory_usage=self.memory_usage(),
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

    def memory_usage(self):  # type: ignore[no-untyped-def]
        from pmstudio.application.use_cases import GetMemoryUsage

        return GetMemoryUsage(
            memory=self.memory,
            index=_RoundHeadIndex(self.rounds),
            rounds=self.rounds,
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
