"""内存假实现。**只许用在测试里**：生产路径由守卫禁止出现 fake/mock/stub 标识符。"""

from copy import deepcopy
from dataclasses import replace
from typing import Any

from pmstudio.application.dto import (
    MaterialChunk,
    MaterialPacket,
    material_summary_view,
)
from pmstudio.application.errors import UnknownExploration, UnknownProject, UnknownRound
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.exploration.exploration import Exploration
from pmstudio.domain.memory.digest import RoundDigest
from pmstudio.domain.memory.inspiration import InspirationItem
from pmstudio.domain.memory.memory import ProjectMemory
from pmstudio.domain.memory.observations import RoundObservation
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import ExplorationId, ProjectId, RoundId


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


class InMemoryExplorationRepository(_InMemoryRepository):
    def __init__(self) -> None:
        super().__init__("exploration")

    def seed(self, exploration: Exploration) -> None:
        self._items[exploration.exploration_id] = deepcopy(exploration)

    def get(self, exploration_id: ExplorationId) -> Exploration:
        item = self._items.get(exploration_id)
        if item is None:
            raise UnknownExploration(exploration_id)
        return deepcopy(item)

    def save(self, exploration: Exploration) -> None:
        self.saves += 1
        self._items[exploration.exploration_id] = deepcopy(exploration)

    def list_for_project(
        self, project_id: ProjectId, *, limit: int = 20
    ) -> tuple[Exploration, ...]:
        """后写的在前（真实现按 `created_at DESC`；测试里时钟常停在同一刻）。"""

        items = [
            item
            for item in self._items.values()
            if str(item.project_id) == str(project_id)
        ]
        return tuple(deepcopy(item) for item in items[::-1][:limit])


class InMemoryMaterialStore:
    """材料仓储替身：与 SQLite 实现同一套口径（盖章、去重、列表只回摘要）。"""

    def __init__(self) -> None:
        self._packets: dict[str, MaterialPacket] = {}
        self._sha: dict[tuple[str, str], str] = {}
        self._raw: dict[str, bytes] = {}
        self._next = 0

    def seed(self, packet) -> None:  # type: ignore[no-untyped-def]
        """直接塞一份已经盖好章的材料（测试里最常用的注入口）。"""

        self._packets[packet.material_id] = packet
        self._raw[packet.material_id] = b"seeded"

    def put(self, *, project_id, parsed, sha256, content, now):  # type: ignore[no-untyped-def]
        known = self._sha.get((str(project_id), sha256))
        if known is not None:
            return self._packets[known]
        self._next += 1
        material_id = f"m{self._next}"
        stamped = replace(
            parsed,
            chunks=tuple(
                MaterialChunk(
                    chunk_id=f"{material_id}#c{chunk.ordinal:02d}",
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    heading_path=chunk.heading_path,
                    anchor=chunk.anchor,
                )
                for chunk in parsed.chunks
            ),
        )
        packet = MaterialPacket(
            material_id=material_id,
            project_id=str(project_id),
            parsed=stamped,
            created_at=now,
        )
        self._packets[material_id] = packet
        self._sha[(str(project_id), sha256)] = material_id
        self._raw[material_id] = content
        return packet

    def get(self, material_id: str):  # type: ignore[no-untyped-def]
        return self._packets.get(material_id)

    def raw_bytes(self, material_id: str) -> bytes | None:
        return self._raw.get(material_id)

    def chunk_count(self, material_id: str) -> int:
        packet = self._packets.get(material_id)
        return 0 if packet is None else len(packet.parsed.chunks)

    def list_for_project(self, project_id, *, current):  # type: ignore[no-untyped-def]
        return tuple(
            material_summary_view(packet, current=current)
            for packet in self._packets.values()
            if packet.project_id == str(project_id)
        )

    def count_for_project(self, project_id) -> int:  # type: ignore[no-untyped-def]
        return sum(1 for packet in self._packets.values() if packet.project_id == str(project_id))

    def find_by_sha(self, project_id, sha256: str):  # type: ignore[no-untyped-def]
        material_id = self._sha.get((str(project_id), sha256))
        return None if material_id is None else self._packets.get(material_id)

    def delete(self, material_id: str) -> None:
        self._packets.pop(material_id, None)
        self._raw.pop(material_id, None)

    def replace_chunks(self, material_id: str, parsed):  # type: ignore[no-untyped-def]
        """就地换块（与 SQLite 实现同口径：id / 去重键 / 原始字节都不动）。"""

        known = self._packets.get(material_id)
        if known is None:
            raise KeyError(material_id)
        stamped = replace(
            parsed,
            chunks=tuple(
                MaterialChunk(
                    chunk_id=f"{material_id}#c{chunk.ordinal:02d}",
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    heading_path=chunk.heading_path,
                    anchor=chunk.anchor,
                )
                for chunk in parsed.chunks
            ),
        )
        updated = MaterialPacket(
            material_id=material_id,
            project_id=known.project_id,
            parsed=stamped,
            created_at=known.created_at,
        )
        self._packets[material_id] = updated
        return updated

    def refresh(self, material_id: str, *, parsed, content: bytes):  # type: ignore[no-untyped-def]
        """就地刷新（与 SQLite 实现同口径：id / 去重键 / `created_at` 都不动，
        原始字节、块、解析器身份一起换）——用途是"同一个网址再抓一次"。"""

        known = self._packets.get(material_id)
        if known is None:
            raise KeyError(material_id)
        stamped = replace(
            parsed,
            chunks=tuple(
                MaterialChunk(
                    chunk_id=f"{material_id}#c{chunk.ordinal:02d}",
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    heading_path=chunk.heading_path,
                    anchor=chunk.anchor,
                )
                for chunk in parsed.chunks
            ),
            bytes_len=len(content),
        )
        updated = MaterialPacket(
            material_id=material_id,
            project_id=known.project_id,
            parsed=stamped,
            created_at=known.created_at,
        )
        self._packets[material_id] = updated
        self._raw[material_id] = content
        return updated


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


class InMemoryMemoryRepository(_InMemoryRepository):
    """跨轮记忆替身（M6）：与 SQLite 实现同一口径——没写过 = 空记忆，覆写整份。"""

    def __init__(self) -> None:
        super().__init__("memory")

    def seed(self, memory: ProjectMemory) -> None:
        self._items[memory.project_id] = deepcopy(memory)

    def get(self, project_id: ProjectId) -> ProjectMemory:
        stored = self._items.get(project_id)
        if stored is None:
            return ProjectMemory.empty(project_id)
        return deepcopy(stored)

    def save(self, memory: ProjectMemory) -> None:
        self.saves += 1
        self._items[memory.project_id] = deepcopy(memory)


class InMemoryObservationRepository:
    """L2 观察替身：内存存储，用于测试。"""

    def __init__(self) -> None:
        self._items: dict[str, RoundObservation] = {}

    def save(self, observation: RoundObservation) -> None:
        self._items[observation.obs_id] = observation

    def save_batch(self, observations) -> None:
        for obs in observations:
            self.save(obs)

    def get(self, obs_id: str):
        return self._items.get(obs_id)

    def search(self, project_id: str, query: str, limit: int = 10):
        results = [
            obs for obs in self._items.values()
            if obs.project_id == project_id and (query in obs.title or query in obs.detail)
        ]
        return tuple(results[:limit])

    def recent(self, project_id: str, limit: int = 5):
        """最新观察，同 source_ref 只保留最新一条。"""
        items = [obs for obs in self._items.values() if obs.project_id == project_id]
        items.sort(key=lambda o: o.obs_id, reverse=True)
        seen: dict[str, RoundObservation] = {}
        for obs in items:
            if obs.source_ref not in seen:
                seen[obs.source_ref] = obs
        deduped = sorted(seen.values(), key=lambda o: o.obs_id, reverse=True)[:limit]
        return tuple(deduped)


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


class InMemoryDigestRepository:
    """L4 轮次摘要替身：内存存储，用于测试。"""

    def __init__(self) -> None:
        self._items: dict[str, RoundDigest] = {}

    def save(self, digest: RoundDigest) -> None:
        self._items[digest.round_id] = digest

    def get(self, round_id: str):
        return self._items.get(round_id)

    def recent(self, project_id: str, limit: int = 3):
        items = [d for d in self._items.values() if d.project_id == project_id]
        items.sort(key=lambda d: d.round_id, reverse=True)
        return tuple(items[:limit])


class InMemoryInspirationRepository:
    """L3 灵感档案替身：内存存储，用于测试。"""

    def __init__(self) -> None:
        self._items: dict[str, InspirationItem] = {}

    def save(self, item: InspirationItem) -> None:
        self._items[item.item_id] = item

    def save_batch(self, items) -> None:
        for item in items:
            self.save(item)

    def get(self, item_id: str):
        return self._items.get(item_id)

    def search(self, project_id: str, query: str, limit: int = 10):
        results = [
            item for item in self._items.values()
            if item.project_id == project_id and (query in item.statement or (item.context and query in item.context))
        ]
        return tuple(results[:limit])

    def recent(self, project_id: str, limit: int = 5):
        items = [i for i in self._items.values() if i.project_id == project_id]
        items.sort(key=lambda i: i.item_id, reverse=True)
        return tuple(items[:limit])
