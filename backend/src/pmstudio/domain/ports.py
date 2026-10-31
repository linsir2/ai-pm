"""领域端口：只声明能力，不给实现。实现全在 infrastructure（P2）。"""

from types import TracebackType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pmstudio.domain.shared.ids import (
    DiscussionId,
    ExplorationId,
    ProjectId,
    RoundId,
    UserId,
)

if TYPE_CHECKING:
    from pmstudio.domain.authoring.document import Document
    from pmstudio.domain.discussion.thread import Discussion
    from pmstudio.domain.exploration.exploration import Exploration
    from pmstudio.domain.memory.memory import ProjectMemory
    from pmstudio.domain.provenance.ledger import EvidenceLedger
    from pmstudio.domain.round.round import Round


@runtime_checkable
class DocumentReader(Protocol):
    def get(self, project_id: ProjectId) -> "Document": ...

    def get_for_update(
        self, project_id: ProjectId, owner: UserId | None = None
    ) -> "Document":
        """UoW 内先锁文档行再读正文与版本；锁持续到外层事务结束。"""

        ...


@runtime_checkable
class DocumentWriter(Protocol):
    """写文档的能力被单独拆出来：编排层与界面只拿得到 Reader。"""

    def save(self, document: "Document") -> None: ...


@runtime_checkable
class RoundReader(Protocol):
    def get(self, round_id: RoundId) -> "Round": ...

    def get_for_update(
        self, round_id: RoundId, owner: UserId | None = None
    ) -> "Round":
        """UoW 内锁定 round 行再读；所有 round 读改写都先走这里。"""

        ...


@runtime_checkable
class RoundWriter(Protocol):
    def save(self, round_: "Round") -> None: ...


@runtime_checkable
class ExplorationReader(Protocol):
    def get(self, exploration_id: ExplorationId) -> "Exploration": ...

    def list_for_project(
        self, project_id: ProjectId, *, limit: int = 20
    ) -> tuple["Exploration", ...]: ...

    def list_running(
        self, project_id: ProjectId, *, owner: UserId | None = None
    ) -> tuple["Exploration", ...]:
        """这一项目里所有 `running` 行（**不截断**：僵壳清理不能只看最近 20 条）。"""

        ...


@runtime_checkable
class ExplorationWriter(Protocol):
    def save(self, exploration: "Exploration") -> None: ...


@runtime_checkable
class DiscussionReader(Protocol):
    def get(self, discussion_id: DiscussionId) -> "Discussion": ...

    def list_for_project(
        self, project_id: ProjectId, *, limit: int = 20
    ) -> tuple["Discussion", ...]: ...


@runtime_checkable
class DiscussionWriter(Protocol):
    def save(self, discussion: "Discussion") -> None: ...


@runtime_checkable
class EvidenceReader(Protocol):
    def get(self, project_id: ProjectId) -> "EvidenceLedger": ...

    def get_for_update(
        self, project_id: ProjectId, owner: UserId | None = None
    ) -> "EvidenceLedger":
        """发 E 的 UoW 首次读取账本就锁行，不能读旧对象后只在 save 时锁。"""

        ...


@runtime_checkable
class EvidenceWriter(Protocol):
    def save(self, ledger: "EvidenceLedger") -> None: ...


@runtime_checkable
class MemoryReader(Protocol):
    def get(self, project_id: ProjectId) -> "ProjectMemory": ...


@runtime_checkable
class MemoryWriter(Protocol):
    """写记忆的能力（M6）。与 `DocumentWriter` 同一个纪律：**只有确认路径能拿到它**。"""

    def save(self, memory: "ProjectMemory") -> None: ...


@runtime_checkable
class ObservationRepository(Protocol):
    """L2 轮次观察的存储（读 + 写）。"""

    def save(self, observation) -> None: ...
    def save_batch(self, observations) -> None: ...
    def recent(self, project_id: str, limit: int | None = None): ...


@runtime_checkable
class DigestRepository(Protocol):
    """L4 轮次摘要的存储（读 + 写）。"""

    def save(self, digest) -> None: ...
    def recent(self, project_id: str, limit: int | None = None): ...


@runtime_checkable
class InspirationRepository(Protocol):
    """L3 灵感档案的存储（读 + 写）。"""

    def save(self, item) -> None: ...
    def save_batch(self, items) -> None: ...
    def recent(self, project_id: str, limit: int | None = None): ...


@runtime_checkable
class IdSource(Protocol):
    """造一个新 id（记忆条目用）。与 `application.ports.IdGenerator` 结构相同。"""

    def new(self, prefix: str) -> str: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """一个用例一个事务：正常退出即提交，抛异常即回滚。"""

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
