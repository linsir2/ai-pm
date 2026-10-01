"""材料仓储：**唯一盖章点**（material_id / project_id 只在这里生成）。

设计与取舍（`docs/plans/2026-09-30-P17-工具层.md`）：

- **原始字节与解析结果一起存**：换解析器时靠它重解析，用户不用重传；
- **同项目内按 sha256 去重**：同一份文件传两次不会存两遍（也就不占两次提示词预算）；
- **块 id 在盖章时改写**（`local-text#c00` → `m-xxxx#c00`）：解析器不知道 id，
  改写只发生在这一处，别处不再拼；
- **列表只回摘要**：块正文一个字都不进列表（要正文只有一个地方——本轮带进去的块）。
"""

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime

from pmstudio.application.dto import (
    MATERIAL_SCHEMA_VERSION,
    MaterialChunk,
    MaterialHit,
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
        self, project_id: ProjectId, *, current: frozenset[tuple[str, str]]
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
                current=current,
                chunk_count=int(row["chunk_count"]),
                chars=int(row["chars"]),
            )
            for row in rows
        )

    def count_for_project(self, project_id: ProjectId) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM materials WHERE project_id = ?", (str(project_id),)
        ).fetchone()
        return int(row["total"])

    def find_by_sha(self, project_id: ProjectId, sha256: str) -> MaterialPacket | None:
        return self._find_by_sha(project_id, sha256)

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


class SqliteLikeRetriever:
    """材料检索 v1：`LIKE` 子串 + 命中窗口。**中文可靠、零索引、零迁移。**

    口径（`docs/plans/2026-10-01-P18-C2c1-材料找得到.md` §3）：

    ① **块级**返回——材料卡、编号、锚点全是块级的，命中"一份材料"说不清是哪一句；
    ② 空查询**回空**（绝不"不填就是全量"，与 F6 同一条规矩）；`%` / `_` / `\\` 按**字面**匹配
       （`ESCAPE '\\'`，否则用户搜 "100%" 会命中一片）；
    ③ 只在这个项目里找（`WHERE m.project_id = ?`）——串味会让用户看到别家的材料；
    ④ 排序 = 命中次数 ↓ → 命中位置靠前 → 块更短（并列按 `chunk_id` 稳定）。
       `score` 与这个顺序**同向**，它的消费者就是 F6 的展示顺序（`search_hits` 不重排）。

    什么时候该升级：真跑量出"整句子串命中率不够"时，换 `FTS5(trigram)` / 向量——
    **只换这个类**，`MaterialRetriever` 的调用方一行不改。
    """

    #: 片段窗口：命中词前后各留多少字（列表里一行放不下整块）
    EXCERPT_WINDOW = 40

    _SQL = """
        SELECT c.chunk_id, c.material_id, c.text, c.anchor, c.ordinal,
               m.title, m.source_ref
        FROM material_chunks c
        JOIN materials m ON m.material_id = c.material_id
        WHERE m.project_id = ? AND c.text LIKE ? ESCAPE '\\'
        ORDER BY c.material_id, c.ordinal
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def search(
        self, *, project_id: ProjectId, query: str, limit: int = 5
    ) -> tuple[MaterialHit, ...]:
        needle = query.strip()
        if needle == "" or limit <= 0:
            return ()
        rows = self._connection.execute(
            self._SQL, (str(project_id), f"%{_like_literal(needle)}%")
        ).fetchall()
        scored = sorted(
            (_score(row, needle) for row in rows),
            key=lambda item: (-item.occurrences, item.first_at, len(item.text), item.chunk_id),
        )
        return tuple(item.hit for item in scored[:limit])


@dataclass(frozen=True, slots=True)
class _Scored:
    """排序用的中间量：`occurrences / first_at` 只服务于顺序，不进任何投影。"""

    occurrences: int
    first_at: int
    text: str
    chunk_id: str
    hit: MaterialHit


def _like_literal(needle: str) -> str:
    """把 `LIKE` 的通配符转义掉——用户搜的是**字面量**，不是模式。"""

    return needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _score(row: sqlite3.Row, needle: str) -> _Scored:
    """一行命中 → 排序中间量 + 投影。分数与顺序同向（次数为主，位置与长度次之）。"""

    text = str(row["text"])
    folded = text.casefold()
    target = needle.casefold()
    occurrences = folded.count(target)
    first_at = folded.find(target)
    score = occurrences - (max(first_at, 0) / 10_000) - (len(text) / 1_000_000)
    return _Scored(
        occurrences=occurrences,
        first_at=max(first_at, 0),
        text=text,
        chunk_id=str(row["chunk_id"]),
        hit=MaterialHit(
            material_id=str(row["material_id"]),
            chunk_id=str(row["chunk_id"]),
            score=score,
            excerpt=_excerpt(text, target),
            anchor=str(row["anchor"]),
            source_ref=str(row["source_ref"]),
            title=str(row["title"]),
        ),
    )


def _excerpt(text: str, needle: str) -> str:
    """命中词前后各 `EXCERPT_WINDOW` 字；命中词本身一定在窗口里。"""

    at = text.casefold().find(needle)
    if at < 0:  # 理论上进不来（SQL 已经筛过）；真进来就老老实实给开头
        return text[: SqliteLikeRetriever.EXCERPT_WINDOW * 2]
    start = max(0, at - SqliteLikeRetriever.EXCERPT_WINDOW)
    end = min(len(text), at + len(needle) + SqliteLikeRetriever.EXCERPT_WINDOW)
    head = "…" if start > 0 else ""
    tail = "…" if end < len(text) else ""
    return f"{head}{text[start:end]}{tail}"
