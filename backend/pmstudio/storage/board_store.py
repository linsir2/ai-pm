"""工作台仓库：`rounds`（永久）＋ `board_regions`（只装当前轮的四个区块）。

两条映射在这里落实：

- **`round` 区块的家就是 `rounds` 表**——轮末删区块，这一行留着（C12）。
- 其余四个区块进 `board_regions`，主键 `(round_id, region)`。

端口是 async（P3），内部是单线程 `sqlite3`。
"""

import json

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import RegionName, RoundPhase
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import REGION_VALUE_TYPES, RegionValue, RoundRegion
from pmstudio.storage.db import Database


class BoardStore:
    """黑板背下的两张表。没有 update-value、没有 delete-row 之外的入口。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ── round 区块（rounds 表） ──────────────────────────────

    async def write_round(self, region: RoundRegion) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO rounds"
                " (round_id, project_id, entry, user_input, scope_json, phase, ended_at, end_reason)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(round_id) DO UPDATE SET"
                " project_id = excluded.project_id,"
                " entry = excluded.entry,"
                " user_input = excluded.user_input,"
                " scope_json = excluded.scope_json,"
                " phase = excluded.phase,"
                " ended_at = excluded.ended_at,"
                " end_reason = excluded.end_reason",
                (
                    region.round_id,
                    region.project_id,
                    region.entry.value,
                    region.user_input,
                    region.scope.model_dump_json(),
                    region.phase.value,
                    region.ended_at.isoformat() if region.ended_at else None,
                    region.end_reason.value if region.end_reason else None,
                ),
            )

    async def read_round(self, round_id: str) -> RoundRegion | None:
        row = self._db._connection.execute("SELECT * FROM rounds WHERE round_id = ?", (round_id,)).fetchone()
        if row is None:
            return None
        return RoundRegion(
            round_id=row["round_id"],
            project_id=row["project_id"],
            entry=row["entry"],
            user_input=row["user_input"],
            scope=Scope.model_validate_json(row["scope_json"]),
            phase=row["phase"],
            ended_at=row["ended_at"],
            end_reason=row["end_reason"],
        )

    # ── 其余四个区块（board_regions 表） ─────────────────────

    async def write_region(self, round_id: str, region: RegionName, value: RegionValue) -> None:
        self._reject_round_region(region)
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO board_regions (round_id, region, payload_json) VALUES (?, ?, ?)"
                " ON CONFLICT(round_id, region) DO UPDATE SET payload_json = excluded.payload_json",
                (round_id, region.value, _dump_value(region, value)),
            )

    async def read_region(self, round_id: str, region: RegionName) -> RegionValue | None:
        self._reject_round_region(region)
        row = self._db._connection.execute(
            "SELECT payload_json FROM board_regions WHERE round_id = ? AND region = ?",
            (round_id, region.value),
        ).fetchone()
        if row is None:
            return None
        return _load_value(region, row["payload_json"])

    # ── 清理 ────────────────────────────────────────────────

    async def drop_regions(self, round_id: str) -> None:
        """轮末清理：这一轮的区块没了，`rounds` 那一行还在。"""
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM board_regions WHERE round_id = ?", (round_id,))

    async def drop_other_regions(self, round_id: str) -> None:
        """开新一轮时，把别的轮残留的区块清掉（C12）。"""
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM board_regions WHERE round_id != ?", (round_id,))

    async def unfinished_round_ids(self) -> tuple[str, ...]:
        """还没结束的轮（启动时恢复要扫它）。按落库顺序给，方便复现。"""
        finished = (RoundPhase.DONE.value, RoundPhase.FAILED.value)
        rows = self._db._connection.execute(
            "SELECT round_id FROM rounds WHERE phase NOT IN (?, ?) ORDER BY rowid",
            finished,
        ).fetchall()
        return tuple(row["round_id"] for row in rows)

    @staticmethod
    def _reject_round_region(region: RegionName) -> None:
        if region is RegionName.ROUND:
            raise ContractViolation("round 区块的家是 rounds 表，不走 board_regions")


def _dump_value(region: RegionName, value: RegionValue) -> str:
    if region is RegionName.CLAIMS:
        # claims 装的是编排层自有的对象，这里只做 JSON 化；C4 定义之后会收窄。
        return json.dumps([_as_jsonable(item) for item in value])  # type: ignore[union-attr]
    return value.model_dump_json()  # type: ignore[union-attr]


def _load_value(region: RegionName, payload_json: str) -> RegionValue:
    if region is RegionName.CLAIMS:
        return tuple(json.loads(payload_json))
    return REGION_VALUE_TYPES[region].model_validate_json(payload_json)  # type: ignore[union-attr]


def _as_jsonable(item: object) -> object:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")  # type: ignore[attr-defined]
    try:
        json.dumps(item)
    except TypeError as error:
        raise ContractViolation(f"claims 区块里的对象必须能转成 JSON，收到 {type(item).__name__}") from error
    return item
