"""应用端口：只声明能力，实现在 infrastructure，由 bootstrap 注入。

**模型调用契约不在这里**：请求 / 回复 / 网关协议 / 留样协议 / 提示词库协议已经搬进
`pmstudio.harness.model.contracts`（2026-10-04）——它们只讲"怎么跟模型说话"，
和 `SeatDefinition` / `MaterialStore` 这些业务端口不是一类东西，混在一个文件里
会让"模型能力"没有住处，`litellm_gateway.py` 也只好反向依赖 application。

**工具声明也不在这里**（2026-10-04 · M5）：`ToolDefinition` + `ToolCatalog` +
TOML 实现搬进 `pmstudio.harness.tools.registry`——"一个工具有哪些字段、没配密钥
要不要出现"是控制平面的事，与产品做什么无关。留在外层会让 `application` 知道
"工具怎么从 TOML 读"，那是装配细节。

留在这里的都是**产品侧**能力：席位编制、材料库、全局检索投影、项目工厂、轮的编排与取消。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pmstudio.domain.shared.ids import (
    DiscussionId,
    DocumentId,
    ExplorationId,
    ProjectId,
    RoundId,
)
from pmstudio.domain.shared.template import DocumentTemplate

if TYPE_CHECKING:
    from pmstudio.application.commands import RoundCommand
    from pmstudio.application.dto import (
        ExplorationView,
        MaterialHit,
        MaterialPacket,
        MaterialSummaryView,
        OpinionSourceView,
        ParsedMaterial,
        ProjectSummaryView,
        RoundHeadView,
        RoundOutcomeView,
        RoundView,
        ThreadView,
    )
    from pmstudio.domain.exploration.exploration import ExplorationScope


@dataclass(frozen=True, slots=True)
class SeatDefinition:
    seat_id: str
    title: str
    focus: str
    trigger_keywords: tuple[str, ...] = ()
    always_on: bool = False
    #: 这个席位负责哪几章 / 哪些功能字段（`01` F16）。缩编与"这一趟请了谁"都用它。
    sections: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class IdGenerator(Protocol):
    def new(self, prefix: str) -> str: ...


@runtime_checkable
class SeatCatalog(Protocol):
    def all(self) -> tuple[SeatDefinition, ...]: ...
    def nominate(self, *, user_input: str) -> tuple[SeatDefinition, ...]: ...
    def by_id(self, seat_id: str) -> SeatDefinition: ...


@runtime_checkable
class MaterialParser(Protocol):
    """把上传的字节变成**解析草稿**。抽不出文字抛 `UnsupportedMaterial`，不返回空包。"""

    def parse(self, *, filename: str, media_type: str, data: bytes) -> "ParsedMaterial": ...
    def identities(self) -> frozenset[tuple[str, str]]:
        """我能产出的 `(parser_id, parser_version)` 集合——判断"这份材料是不是旧解析器读的"。"""

        ...


@runtime_checkable
class WebSearchGateway(Protocol):
    """一次查询 → 若干**解析草稿**（每条结果一个，含 URL 与摘要块）。

    与 `MaterialParser` 同一个形状：**只有 `MaterialStore.put` 能盖章**（material_id/project_id），
    所以网关不产 `MaterialPacket`——它连"这是哪个项目"都不知道。
    """

    async def search(self, *, query: str, limit: int) -> tuple["ParsedMaterial", ...]: ...


@runtime_checkable
class WebFetcher(Protocol):
    """按网址抓**一份**网页 → `FetchedPage`（解析草稿 + 原始字节）。

    与 `WebSearchGateway` 的区别：这是**用户点名的一个页面**，所以要抓正文、块级
    （搜回来的只是线索，300 字摘要就够）。抓不到就抛 `ToolCallFailed`——不吞、不假装抓到。
    """

    async def fetch(self, *, url: str) -> "FetchedPage": ...


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """一次抓取的产出：**原始字节 + 媒体类型 + 最终地址 + 标题**。

    刻意**不含**解析结果（`ParsedMaterial`）：解析是 `MaterialParser` 的活，不属于
    "按网址抓一个页面"这个动作。旧版让 `HttpxWebFetcher` 自己调 `html_text`，后果是
    同一份 HTML 有两个解析调用点，而 `extract` 里现成的 PDF / Office 分派抓取侧一种都
    用不上（用户点一个 PDF 链接只能先下载再上传）。职责切干净之后：
    fetcher 管网络与护栏，parser 管字节→文字，用例管出处。

    为什么要带原始字节：材料表存的是"原始字节 + 解析结果"（换解析器时要能重解析，
    用户不用重抓）。字节只在这条**端口 → 用例 → 存储**的路上走，绝不进任何 API 投影。
    """

    content: bytes
    media_type: str
    final_url: str
    title: str | None = None


@runtime_checkable
class MaterialStore(Protocol):
    """材料的唯一入口/出口。**只有这里能盖章**（material_id / project_id）。"""

    def put(
        self,
        *,
        project_id: ProjectId,
        parsed: "ParsedMaterial",
        sha256: str,
        content: bytes,
        now: datetime,
    ) -> "MaterialPacket": ...
    def get(self, material_id: str) -> "MaterialPacket | None": ...
    def raw_bytes(self, material_id: str) -> bytes | None: ...
    def list_for_project(
        self, project_id: ProjectId, *, current: frozenset[tuple[str, str]]
    ) -> tuple["MaterialSummaryView", ...]: ...
    def count_for_project(self, project_id: ProjectId) -> int: ...
    def find_by_sha(self, project_id: ProjectId, sha256: str) -> "MaterialPacket | None": ...
    def chunk_count(self, material_id: str) -> int: ...
    def delete(self, material_id: str) -> None:
        """删掉这份材料（块随 `ON DELETE CASCADE` 一起走，原始字节也不留）。

        **谁决定能不能删**：用例（`DeleteMaterial`）——被引用过的材料不许删（编号不能悬空）。
        仓储只负责"删干净"，不做判据。
        """

        ...

    def replace_chunks(self, material_id: str, parsed: "ParsedMaterial") -> "MaterialPacket":
        """**就地**换掉这份材料的块与解析器身份（`P19` 的重解析）。

        只用于"没被任何一轮引用过"的材料：`material_id`、去重键、原始字节、标题都不动，
        只把块按新解析器重建，并更新 `parser_id / parser_version / schema_version /
        truncated / warnings`。**别拿它改被引用过的材料**——那会让编号改指。
        """

        ...


    def refresh(
        self, material_id: str, *, parsed: "ParsedMaterial", content: bytes
    ) -> "MaterialPacket":
        """**就地**刷新一份材料：原始字节、块、解析器身份、截断与告警一起换（`P27`）。

        只给"没被任何一轮引用过"的材料用（用例那边判，`P19` 的同一道边）——
        它的用途是"同一个网址再抓一次"，网页变了就把这份更新到最新；
        被引用过的材料改内容会改指编号，只能走"另出一份"。
        `material_id`、去重键、`created_at` 都不动。
        """

        ...


@runtime_checkable
class MaterialRetriever(Protocol):
    """在**已经存下来**的材料块里找东西：块级返回，带锚点与分数——调用方不看实现。

    与 `MaterialParser` / `MaterialStore` 同一套路子：v1 是 SQLite `LIKE`（中文子串可靠、
    零索引、零迁移），升级位（`FTS5(trigram)` / 向量）在**同一个协议**后面，
    调用方一行不改（换实现只改 `bootstrap/container.py` 一行）。

    它只读：给 F6 全局搜索用（`SearchProject`），"按主张取块"是 C-2c-3 的事。
    """

    def search(
        self, *, project_id: ProjectId, query: str, limit: int = 5
    ) -> tuple["MaterialHit", ...]: ...


@runtime_checkable
class WorkspaceIndex(Protocol):
    """只读查询投影：项目摘要、项目列表、某项目最新一轮。

    它不是第二份真相源——数据仍来自 documents / rounds 两张表，这里只是查询入口，
    因为 `DocumentReader` / `RoundReader` 是聚合入口，不该长出"列全部项目"这种查询。
    """

    def summary(self, project_id: ProjectId) -> "ProjectSummaryView": ...
    def list_summaries(self) -> tuple["ProjectSummaryView", ...]: ...
    def latest_round_id(self, project_id: ProjectId) -> RoundId | None: ...
    def recent_rounds(
        self, project_id: ProjectId, *, limit: int = 5
    ) -> tuple["RoundOutcomeView", ...]: ...
    def list_rounds(
        self, project_id: ProjectId, *, limit: int = 20
    ) -> tuple["RoundHeadView", ...]: ...
    def recent_opinions(
        self, project_id: ProjectId, *, limit_rounds: int = 10
    ) -> tuple["OpinionSourceView", ...]: ...
    def material_references(
        self, project_id: ProjectId
    ) -> Mapping[str, tuple[str, ...]]:
        """这份材料被**哪些编号**引用过：`{material_id: (E4, E9)}`（P19）。

        它是"编号发出即冻结"的唯一判据（真相源是轮次 payload 的 `materials[]`）：
        空 = 没被引用过 = 可以删、可以就地重解析。**只读**，不建表、不缓存。
        """

        ...


@runtime_checkable
class ProjectFactory(Protocol):
    """建项目：造一份空骨架文档 + 空账本并落库（骨架形状由传进来的模板定）。

    它与 `DocumentWriter` 是两回事：它**只能造空文档**，改已有文档仍然只有
    `ProposalCommitter` 一条路（"AI 不写文档"这条不变式靠这个划分保住）。
    """

    def create(
        self, *, project_id: ProjectId, title: str, now: datetime, template: DocumentTemplate
    ) -> DocumentId: ...


@runtime_checkable
class TemplateRepository(Protocol):
    """模板库表：定义时来源。只存取，不校验（入库前调用方已 `validate` 过）。"""

    def save(self, template: DocumentTemplate, *, builtin: bool) -> None: ...
    def list(self) -> tuple[DocumentTemplate, ...]: ...
    def get(self, template_id: str) -> DocumentTemplate: ...


@runtime_checkable
class RoundOrchestrator(Protocol):
    """图的入口（实现是 infrastructure 里的 `RoundRunner`）。

    `api` 只认这个协议，所以它不必 import infrastructure——分层守卫因此能保持全绿。
    """

    async def run(self, command: "RoundCommand") -> "RoundView": ...


@runtime_checkable
class RoundCanceller(Protocol):
    """把正在跑的那一轮停下来（实现是 infrastructure 里的 `RoundRunner`）。

    只有"放弃这一轮"用它。它不是写能力——取消只负责让正在等模型的那次调用停下来；
    磁盘上的状态由 `AbandonRound` 写（写法只有一处）。
    """

    async def cancel(self, round_id: RoundId) -> None: ...


@runtime_checkable
class ExplorationOrchestrator(Protocol):
    """探索的入口（实现是 infrastructure 里的 `ExplorationRunner`，M8）。

    与 `RoundOrchestrator` 同一个理由：`api` 只认协议，不 import infrastructure。
    一个动作 = 一次请求内跑完（与轮次同款：长请求 + SSE 事件推进度）。
    """

    async def start(
        self, *, project_id: ProjectId, scope: "ExplorationScope"
    ) -> "ExplorationView": ...

    async def retry(self, *, exploration_id: str) -> "ExplorationView": ...


@runtime_checkable
class ExplorationCanceller(Protocol):
    """把正在跑的探索停下来（实现是 `ExplorationRunner`）。

    与 `RoundCanceller` 同一条纪律：先写状态、再取消；取消不写任何业务状态。
    """

    async def cancel(self, exploration_id: ExplorationId) -> None: ...


@runtime_checkable
class DiscussionRunner(Protocol):
    """panel 轮次的执行器（实现是 infrastructure 里的 `DiscussionRunner`，M18）。

    `api` 只认这个协议。与探索/轮次 runner 同形：串行 + 任务注册 + 取消。
    取消只让在途调用停下来，**不写状态**（线程还是 open，已落库的 turn 照留）——
    讨论 abort 没有"先写状态"的那一半（与 AbandonRound/AbandonExploration 不同）。
    """

    async def run_round(self, *, discussion_id: DiscussionId) -> "ThreadView": ...

    async def abort(self, *, discussion_id: DiscussionId) -> None: ...
