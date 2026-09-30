"""内存假实现。**只许用在测试里**：生产路径由守卫禁止出现 fake/mock/stub 标识符。"""

from copy import deepcopy
from typing import Any

from pmstudio.application.errors import UnknownProject, UnknownRound
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import ProjectId, RoundId


class _InMemoryRepository:
    def __init__(self, name: str) -> None:
        self._name = name
        self._items: dict[Any, Any] = {}
        self.saves = 0

    def __len__(self) -> int:
        return len(self._items)

    def snapshot(self) -> dict[Any, Any]:
        return deepcopy(self._items)

    def restore(self, snapshot: dict[Any, Any]) -> None:
        self._items = deepcopy(snapshot)


class InMemoryDocumentRepository(_InMemoryRepository):
    def __init__(self) -> None:
        super().__init__("document")

    def seed(self, document: Document) -> None:
        self._items[document.project_id] = deepcopy(document)

    def get(self, project_id: ProjectId) -> Document:
        document = self._items.get(project_id)
        if document is None:
            raise UnknownProject(project_id)
        return deepcopy(document)

    def save(self, document: Document) -> None:
        self.saves += 1
        self._items[document.project_id] = deepcopy(document)


class InMemoryRoundRepository(_InMemoryRepository):
    def __init__(self) -> None:
        super().__init__("round")

    def seed(self, round_: Round) -> None:
        self._items[round_.round_id] = deepcopy(round_)

    def get(self, round_id: RoundId) -> Round:
        round_ = self._items.get(round_id)
        if round_ is None:
            raise UnknownRound(round_id)
        return deepcopy(round_)

    def save(self, round_: Round) -> None:
        self.saves += 1
        self._items[round_.round_id] = deepcopy(round_)

    def all_rounds(self) -> tuple[Round, ...]:
        """按保存顺序给出全部轮次（测试替身专用：真实现是 SQL 的 `ORDER BY`）。"""

        return tuple(deepcopy(item) for item in self._items.values())


class InMemoryEvidenceRepository(_InMemoryRepository):
    def __init__(self) -> None:
        super().__init__("evidence")

    def seed(self, ledger: EvidenceLedger) -> None:
        self._items[ledger.project_id] = deepcopy(ledger)

    def get(self, project_id: ProjectId) -> EvidenceLedger:
        ledger = self._items.get(project_id)
        if ledger is None:
            raise UnknownProject(project_id)
        return deepcopy(ledger)

    def save(self, ledger: EvidenceLedger) -> None:
        self.saves += 1
        self._items[ledger.project_id] = deepcopy(ledger)


class FakeTransaction:
    """假 UnitOfWork：成功记一次提交；抛异常就把仓储恢复成进入事务前的样子。"""

    def __init__(self, *repositories: _InMemoryRepository) -> None:
        self._repositories = repositories
        self._snapshot: list[dict[Any, Any]] = []
        self._depth = 0
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self) -> "FakeTransaction":
        # 可嵌套，语义与 SqliteUnitOfWork 一致：只有最外层那一次算一次事务。
        if self._depth == 0:
            self._snapshot = [repository.snapshot() for repository in self._repositories]
        self._depth += 1
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self._depth -= 1
        if self._depth > 0:
            return False
        if exc_type is None:
            self.commits += 1
        else:
            for repository, snapshot in zip(self._repositories, self._snapshot, strict=True):
                repository.restore(snapshot)
            self.rollbacks += 1
        self._snapshot = []
        return False


class FailingRoundWriter:
    """写入轮状态时会炸的假实现，用来验证事务性。"""

    def __init__(self) -> None:
        self.saves = 0

    def save(self, round_: Round) -> None:
        self.saves += 1
        raise RuntimeError("模拟落库失败")
