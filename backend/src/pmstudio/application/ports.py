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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

from pmstudio.domain.shared.ids import (
    DiscussionId,
    DocumentId,
    ExplorationId,
    ProjectId,
    RoundId,
    UserId,
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

    与 `MaterialParser` / `MaterialStore` 同一套路子：实现换过两版，**这个协议没变**，
    调用方一行没改（换实现只改 `bootstrap/container.py` 一行）：

    实现 `SqliteFtsRetriever`：**SQLite FTS5 + jieba 预分词 + OR**（词级、词序无关、
    容忍停用词）。它替换掉了更早的整句 `LIKE` 版（`SqliteLikeRetriever`，2026-10-17 删除）；
    升级前后对照见 `docs/plans/2026-10-17-检索升级验收.md`。

    再往上的升级位（向量 / 混合）仍在同一个协议后面。

    它只读：给 F6 全局搜索用（`SearchProject`），"按主张取块"是 C-2c-3 的事。
    """

    def search(
        self, *, project_id: ProjectId, query: str, limit: int = 5
    ) -> tuple["MaterialHit", ...]: ...


@runtime_checkable
class MaterialVectorIndex(Protocol):
    """材料块的**向量**索引（检索阶段 2）：与 `MaterialStore` 同库同文件、不同表。

    为什么是独立协议而不是塞进 `MaterialStore`：写向量要跑 ONNX 推理，
    **不许**落在仓储的写事务里（持锁时长是基线写操作的百倍，见批 5 检索升级计划）。
    所以向量由调用方在**主事务提交后**另起小事务写；崩溃窗口由 `migrate` 的幂等回填兜底。
    """

    def write(self, material_id: str, *, owner: UserId | None = None) -> None: ...
    def drop(self, material_id: str, *, owner: UserId | None = None) -> None: ...


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


@dataclass(frozen=True, slots=True)
class TemplateEntry:
    """模板 + 库内元数据（`02` §11.0.70）。

    `builtin` 是**仓储的账**（哪些是出厂的），所以不进 `DocumentTemplate`——那份定义
    一旦被拷进项目的行内快照，就带着一个毫无意义的 `builtin=False`。读库的两条路都
    返回它：界面靠这个标记决定给不给编辑/删除按钮（`06` §2.3）。
    """

    template: DocumentTemplate
    builtin: bool


@runtime_checkable
class TemplateRepository(Protocol):
    """模板库表：定义时来源。只存取，不校验（入库前调用方已 `validate` 过）。

    `delete` 对不存在的 id 抛 `UnknownTemplate`——"删一个不在库里的东西"和"读一个不在
    库里的东西"是同一件事（404），不是静默成功。
    """

    def save(self, template: DocumentTemplate, *, builtin: bool) -> None: ...
    def list(self) -> tuple[TemplateEntry, ...]: ...
    def get(self, template_id: str) -> TemplateEntry: ...
    def delete(self, template_id: str) -> None: ...


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


@dataclass(frozen=True, slots=True)
class UserRecord:
    """一行用户（仓储的读投影，不是领域聚合）。

    `status` 是字符串（`pending` / `active` / `banned`），审批与封禁只改它——
    身份进引擎只以 `UserId` 值对象，所以没有对应的 domain 类型。
    """

    user_id: UserId
    username: str
    password_hash: str
    status: str
    role: str
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """一行登录会话。`token_hash` 是 `hashlib.sha256` 摘要——明文永不在这里。"""

    session_id: str
    user_id: UserId
    token_hash: str
    expires_at: datetime
    created_at: datetime
    last_seen_at: datetime
    ip: str
    user_agent: str
    revoked_at: datetime | None


@runtime_checkable
class AdminStatsStore(Protocol):
    """管理端那一屏的数据源：每个用户一行的活动读模型（`/api/auth/stats`）。

    **只读、且必须是"每用户一行"**：这一层存在的意义就是让界面拿到管理员真正会问的
    几件事——谁在用、用了多少、失败多不多、最近还来不来。扇出的 join 会把计数放大，
    实现里一律用相关子查询（见 `SqliteAdminStatsRepository` 的注释）。
    """

    def rows(self, *, now: str) -> Sequence[object]: ...


class UserStore(Protocol):
    """用户表：注册只造 pending；审批 / 封禁走 `set_status`。不碰密码（Task 1 负责）。"""

    def register(
        self, *, username: str, password_hash: str, role: str = "user"
    ) -> UserId: ...
    def get_by_name(self, username: str) -> UserRecord | None: ...
    def get_by_id(self, user_id: UserId) -> UserRecord | None: ...
    def list_all(self) -> tuple[UserRecord, ...]: ...
    def set_status(
        self,
        user_id: UserId,
        status: str,
        *,
        approved_by: str | None = None,
        approved_at: datetime | None = None,
    ) -> None: ...
    def list_pending(self) -> tuple[UserRecord, ...]: ...


@runtime_checkable
class UsageStore(Protocol):
    """管理端的用量读模型（Task 8 / Ruling 7）：按 owner 聚合其轮次的 `model_calls` 行数。

    `model_calls` 不加 owner 列（`§0.3`），实现以 join 取（`round_id → rounds.owner_id`）。
    只保证"有调用的 owner"在键里；没有调用的用户由用例侧补 0。
    """

    def count_model_calls_by_owner(self) -> dict[UserId, int]: ...


@runtime_checkable
class SessionStore(Protocol):
    """会话表：**只认 token 的 sha256 摘要**，明文由调用方在算好 cookie 值时算掉。

    这样"明文永不入库"是结构保证：仓储根本没有收明文的入口。
    `get_by_token_hash` 不判过期（过期由调用方按 `expires_at` 判），只把已吊销的挡掉。
    """

    def create(
        self,
        *,
        user_id: UserId,
        token_hash: str,
        expires_at: datetime,
        now: datetime,
        ip: str = "",
        user_agent: str = "",
    ) -> SessionRecord: ...
    def get_by_token_hash(self, token_hash: str) -> SessionRecord | None: ...
    def touch(
        self, token_hash: str, *, now: datetime, expires_at: datetime
    ) -> None: ...
    def revoke_by_token_hash(self, token_hash: str) -> None: ...
    def revoke_user(self, user_id: UserId) -> None: ...


class LoginVerdict(NamedTuple):
    """限流判定结果。`retry_after` 是**客户端该等多久**（秒），不是"建议值"。"""

    limited: bool
    retry_after: int


@runtime_checkable
class LoginGuard(Protocol):
    """登录限流闸。**三个维度共用一个接口**，因为它们要回答的是同一个问题。

    - **失败锁** `(ip, username)`：`locked` / `record_failure` / `clear`。
      窗口内失败到阈值即锁；登录成功要 `clear`（否则手滑三次就永久锁死）。
    - **尝试维度** `(ip)` 与 `(username)`：`consume_attempt`。
      每次尝试都扣一个令牌（**不论成败**），挡的是"单 IP 喷很多账号"
      与"很多 IP 撞一个账号"——这两种攻击在失败锁上永远不触发。

    **方法一律 async、且不接受 `now`**：Redis 那边用服务端时钟，
    内存那边用自己的时钟，把时间来源藏进实现，才不会让调用方时刻对表。
    """

    async def locked(self, *, ip: str, username: str) -> LoginVerdict: ...
    async def record_failure(self, *, ip: str, username: str) -> None: ...
    async def clear(self, *, ip: str, username: str) -> None: ...
    async def consume_attempt(self, *, ip: str, username: str) -> LoginVerdict: ...
