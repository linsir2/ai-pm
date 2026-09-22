"""领域端口：只声明能力，不给实现。实现全在 infrastructure（P2）。"""

from types import TracebackType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pmstudio.domain.shared.ids import ProjectId, RoundId

if TYPE_CHECKING:
    from pmstudio.domain.authoring.document import Document
    from pmstudio.domain.provenance.ledger import EvidenceLedger
    from pmstudio.domain.round.round import Round


@runtime_checkable
class DocumentReader(Protocol):
    def get(self, project_id: ProjectId) -> "Document": ...


@runtime_checkable
class DocumentWriter(Protocol):
    """写文档的能力被单独拆出来：编排层与界面只拿得到 Reader。"""

    def save(self, document: "Document") -> None: ...


@runtime_checkable
class RoundReader(Protocol):
    def get(self, round_id: RoundId) -> "Round": ...


@runtime_checkable
class RoundWriter(Protocol):
    def save(self, round_: "Round") -> None: ...


@runtime_checkable
class EvidenceReader(Protocol):
    def get(self, project_id: ProjectId) -> "EvidenceLedger": ...


@runtime_checkable
class EvidenceWriter(Protocol):
    def save(self, ledger: "EvidenceLedger") -> None: ...


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
