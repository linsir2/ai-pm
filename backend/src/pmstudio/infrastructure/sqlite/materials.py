"""材料仓储：**唯一盖章点**（material_id / project_id 只在这里生成）。

设计与取舍（`docs/plans/2026-09-30-P17-工具层.md`）：

- **原始字节与解析结果一起存**：换解析器时靠它重解析，用户不用重传；
- **同项目内按 sha256 去重**：同一份文件传两次不会存两遍（也就不占两次提示词预算）；
- **块 id 在盖章时改写**（`local-text#c00` → `m-xxxx#c00`）：解析器不知道 id，
  改写只发生在这一处，别处不再拼；
- **列表只回摘要**：块正文一个字都不进列表（要正文只有一个地方——本轮带进去的块）。
"""

import json
from dataclasses import dataclass, replace
from datetime import datetime

from pmstudio.application.dto import (
    MATERIAL_SCHEMA_VERSION,
    MaterialChunk,
    MaterialPacket,
    MaterialSummaryView,
    ParsedMaterial,
    material_summary_view,
)
from pmstudio.application.ports import IdGenerator
from pmstudio.domain.shared.ids import ProjectId


@dataclass(frozen=True, slots=True)
class _Row:
    material_id: str
    project_id: str
    origin: str
    title: str
    source_ref: str
    media_type: str
    bytes_len: int
    parser_id: str
    parser_version: str
    schema_version: int
    truncated: int
    warnings: str
    created_at: str


class SqliteMaterialStore:
    def __init__(self, connection, *, ids: IdGenerator) -> None:  # type: ignore[no-untyped-def]
        self._connection = connection
        self._ids = ids

    # ── 写 ────────────────────────────────────────────────────────────────

    def put(
        self,
        *,
        project_id: ProjectId,
        parsed: ParsedMaterial,
        sha256: str,
        content: bytes,
        now: datetime,
    ) -> MaterialPacket:
        existing = self._find_by_sha(project_id, sha256)
        if existing is not None:
            return existing
        material_id = self._ids.new("m")
        stamped = _restamp(parsed, material_id)
        self._connection.execute(
            """
            INSERT INTO materials (material_id, project_id, origin, title, source_ref,
                media_type, bytes_len, sha256, parser_id, parser_version, schema_version,
                truncated, warnings, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                material_id,
                str(project_id),
                stamped.origin,
                stamped.title,
                stamped.source_ref,
                stamped.media_type,
                stamped.bytes_len,
                sha256,
                stamped.parser_id,
                stamped.parser_version,
                MATERIAL_SCHEMA_VERSION,
                int(stamped.truncated),
                json.dumps(list(stamped.warnings), ensure_ascii=False),
                content,
                now.isoformat(),
            ),
        )
        for chunk in stamped.chunks:
            self._connection.execute(
                """
                INSERT INTO material_chunks
                    (chunk_id, material_id, ordinal, text, heading_path, anchor)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.chunk_id,
                    material_id,
                    chunk.ordinal,
                    chunk.text,
                    json.dumps(list(chunk.heading_path), ensure_ascii=False),
                    chunk.anchor,
                ),
            )
        return MaterialPacket(
            material_id=material_id,
            project_id=str(project_id),
            parsed=stamped,
            created_at=now,
            schema_version=MATERIAL_SCHEMA_VERSION,
        )

    def delete(self, material_id: str) -> None:
        self._connection.execute(
            "DELETE FROM material_chunks WHERE material_id = ?", (material_id,)
        )
        self._connection.execute("DELETE FROM materials WHERE material_id = ?", (material_id,))

    # ── 读 ────────────────────────────────────────────────────────────────

    def get(self, material_id: str) -> MaterialPacket | None:
        row = self._connection.execute(
            "SELECT * FROM materials WHERE material_id = ?", (material_id,)
        ).fetchone()
        return None if row is None else self._packet(row)

    def raw_bytes(self, material_id: str) -> bytes | None:
        row = self._connection.execute(
            "SELECT content FROM materials WHERE material_id = ?", (material_id,)
        ).fetchone()
        return None if row is None else bytes(row["content"])

    def chunk_count(self, material_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM material_chunks WHERE material_id = ?", (material_id,)
        ).fetchone()
        return int(row["total"])

    def list_for_project(
        self, project_id: ProjectId, *, parser_id: str, parser_version: str
    ) -> tuple[MaterialSummaryView, ...]:
        rows = self._connection.execute(
            """
            SELECT m.*, (SELECT COUNT(*) FROM material_chunks c WHERE c.material_id = m.material_id)
                AS chunk_count,
                   (SELECT COALESCE(SUM(LENGTH(c.text)), 0) FROM material_chunks c
                    WHERE c.material_id = m.material_id) AS chars
            FROM materials m
            WHERE m.project_id = ?
            ORDER BY m.created_at DESC, m.material_id DESC
            """,
            (str(project_id),),
        ).fetchall()
        return tuple(
            material_summary_view(
                self._packet(row, chunks=()),
                parser_id=parser_id,
                parser_version=parser_version,
                chunk_count=int(row["chunk_count"]),
                chars=int(row["chars"]),
            )
            for row in rows
        )

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _find_by_sha(self, project_id: ProjectId, sha256: str) -> MaterialPacket | None:
        row = self._connection.execute(
            "SELECT * FROM materials WHERE project_id = ? AND sha256 = ?",
            (str(project_id), sha256),
        ).fetchone()
        return None if row is None else self._packet(row)

    def _packet(self, row, *, chunks: tuple[MaterialChunk, ...] | None = None):  # type: ignore[no-untyped-def]
        if chunks is None:
            chunks = tuple(
                MaterialChunk(
                    chunk_id=item["chunk_id"],
                    ordinal=int(item["ordinal"]),
                    text=item["text"],
                    heading_path=tuple(json.loads(item["heading_path"])),
                    anchor=item["anchor"],
                )
                for item in self._connection.execute(
                    "SELECT * FROM material_chunks WHERE material_id = ? ORDER BY ordinal",
                    (row["material_id"],),
                ).fetchall()
            )
        parsed = ParsedMaterial(
            origin=row["origin"],
            title=row["title"],
            source_ref=row["source_ref"],
            media_type=row["media_type"],
            bytes_len=int(row["bytes_len"]),
            chunks=chunks,
            parser_id=row["parser_id"],
            parser_version=row["parser_version"],
            truncated=bool(row["truncated"]),
            warnings=tuple(json.loads(row["warnings"])),
        )
        return MaterialPacket(
            material_id=row["material_id"],
            project_id=row["project_id"],
            parsed=parsed,
            created_at=datetime.fromisoformat(row["created_at"]),
            schema_version=int(row["schema_version"]),
        )


def _restamp(parsed: ParsedMaterial, material_id: str) -> ParsedMaterial:
    """把块 id 的前缀换成 material_id（解析器用 `parser_id#cNN` 占位）。"""

    return replace(
        parsed,
        chunks=tuple(
            replace(chunk, chunk_id=f"{material_id}#c{chunk.ordinal:02d}")
            for chunk in parsed.chunks
        ),
    )
