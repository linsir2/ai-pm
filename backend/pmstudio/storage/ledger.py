"""账本：项目、文档、块、版本的读写。

三条不变量在这里落实：

- I8 版本只增：`seq` 单调整数；回退**产生新版本**，不删不改历史
- I9 确认原子：一次写入要么全生效要么全不生效
- I10 冲突不静默覆盖：带 `expected_version`，对不上就拒
"""

import sqlite3
from collections.abc import Sequence

from pmstudio.common.errors import BlockNotFound, VersionConflict
from pmstudio.common.ids import VERSION, IdGenerator, TimestampIdGenerator
from pmstudio.contracts.enums import BlockOpKind, VersionTrigger
from pmstudio.contracts.models.document import (
    Block,
    BlockOp,
    Document,
    DocumentSnapshot,
    DocumentVersion,
)
from pmstudio.contracts.models.project import Project, ProjectConfig
from pmstudio.storage.db import Database


class Ledger:
    """账本的读写入口。不含业务语义。"""

    def __init__(self, db: Database, ids: IdGenerator | None = None) -> None:
        self._db = db
        self._ids = ids or TimestampIdGenerator()

    # ── 项目 ────────────────────────────────────────────────

    def create_project(self, project: Project) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO projects (project_id, name, template_id, config_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    project.project_id,
                    project.name,
                    project.template_id,
                    project.config.model_dump_json(),
                    project.created_at.isoformat(),
                ),
            )

    def read_project(self, project_id: str) -> Project | None:
        row = self._db._connection.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return None
        return Project(
            project_id=row["project_id"],
            name=row["name"],
            template_id=row["template_id"],
            config=ProjectConfig.model_validate_json(row["config_json"]),
            created_at=row["created_at"],
        )

    # ── 文档与块 ────────────────────────────────────────────

    def create_document(self, document: Document, blocks: Sequence[Block]) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO documents (doc_id, project_id, template_id) VALUES (?, ?, ?)",
                (document.doc_id, document.project_id, document.template_id),
            )
            for position, block in enumerate(blocks):
                self._insert_block(conn, document.doc_id, block, position)

    def read_document(self, doc_id: str) -> Document | None:
        row = self._db._connection.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        if row is None:
            return None
        return Document(
            doc_id=row["doc_id"],
            project_id=row["project_id"],
            template_id=row["template_id"],
        )

    def read_blocks(self, doc_id: str) -> list[Block]:
        rows = self._db._connection.execute(
            "SELECT * FROM blocks WHERE doc_id = ? ORDER BY position", (doc_id,)
        ).fetchall()
        return [self._row_to_block(row) for row in rows]

    def write_blocks(
        self,
        doc_id: str,
        ops: Sequence[BlockOp],
        trigger: VersionTrigger,
        group_id: str | None = None,
    ) -> DocumentVersion:
        """写一批块，产生一个新版本。整批要么全生效要么全不生效（I9）。"""
        with self._db.transaction() as conn:
            current = {block.block_id: block for block in self._read_blocks(conn, doc_id)}

            # 先全部校验，再动手——校验不过就一个字节都不该改。
            for op in ops:
                block = current.get(op.block_id)
                if block is None:
                    raise BlockNotFound(f"块不存在：{op.block_id}")
                if block.version != op.expected_version:
                    raise VersionConflict(
                        f"块 {op.block_id} 的版本是 {block.version}，"
                        f"但你按 {op.expected_version} 写的——有人在中间改过它"
                    )

            for op in ops:
                block = current[op.block_id]
                content = (
                    f"{block.content}\n{op.content}".strip() if op.kind is BlockOpKind.APPEND else op.content
                )
                updated = block.model_copy(update={"content": content, "version": block.version + 1})
                current[op.block_id] = updated
                self._update_block(conn, doc_id, updated)

            return self._append_version(
                conn,
                doc_id,
                snapshot=tuple(current.values()),
                trigger=trigger,
                group_id=group_id,
                target_version_id=None,
            )

    def rollback(self, doc_id: str, target_version_id: str) -> DocumentVersion:
        """回退到某一版：内容回到那一刻，**产生新版本**，不删不改历史（I8）。"""
        target = self.read_version(target_version_id)
        if target is None or target.doc_id != doc_id:
            raise VersionConflict(f"找不到要回退的版本：{target_version_id}")

        with self._db.transaction() as conn:
            current = {block.block_id: block for block in self._read_blocks(conn, doc_id)}
            wanted = {block.block_id: block for block in target.snapshot.blocks}

            # snapshot 里没有的块，说明它是那一版之后加的——回退要把它去掉。
            for block_id in current.keys() - wanted.keys():
                conn.execute("DELETE FROM blocks WHERE doc_id = ? AND block_id = ?", (doc_id, block_id))

            restored: dict[str, Block] = {}
            for position, (block_id, old) in enumerate(wanted.items()):
                previous = current.get(block_id)
                base = previous.version if previous else old.version
                restored_block = old.model_copy(update={"version": base + 1})
                restored[block_id] = restored_block
                if previous is None:
                    self._insert_block(conn, doc_id, restored_block, position)
                else:
                    self._update_block(conn, doc_id, restored_block)

            return self._append_version(
                conn,
                doc_id,
                snapshot=tuple(restored.values()),
                trigger=VersionTrigger.ROLLBACK,
                group_id=None,
                target_version_id=target_version_id,
            )

    def list_versions(self, doc_id: str) -> list[DocumentVersion]:
        rows = self._db._connection.execute(
            "SELECT * FROM document_versions WHERE doc_id = ? ORDER BY seq", (doc_id,)
        ).fetchall()
        return [self._row_to_version(row) for row in rows]

    def read_version(self, version_id: str) -> DocumentVersion | None:
        row = self._db._connection.execute(
            "SELECT * FROM document_versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        return self._row_to_version(row) if row else None

    # ── 内部 ────────────────────────────────────────────────

    def _read_blocks(self, conn: sqlite3.Connection, doc_id: str) -> list[Block]:
        rows = conn.execute("SELECT * FROM blocks WHERE doc_id = ? ORDER BY position", (doc_id,)).fetchall()
        return [self._row_to_block(row) for row in rows]

    @staticmethod
    def _row_to_block(row: sqlite3.Row) -> Block:
        return Block(
            block_id=row["block_id"],
            parent_id=row["parent_id"],
            schema_label=row["schema_label"],
            content=row["content"],
            version=row["version"],
            source_card_id=row["source_card_id"],
        )

    @staticmethod
    def _row_to_version(row: sqlite3.Row) -> DocumentVersion:
        return DocumentVersion(
            version_id=row["version_id"],
            doc_id=row["doc_id"],
            seq=row["seq"],
            snapshot=DocumentSnapshot.model_validate_json(row["snapshot_json"]),
            trigger=VersionTrigger(row["trigger"]),
            group_id=row["group_id"],
            target_version_id=row["target_version_id"],
        )

    @staticmethod
    def _insert_block(conn: sqlite3.Connection, doc_id: str, block: Block, position: int) -> None:
        conn.execute(
            "INSERT INTO blocks"
            " (doc_id, block_id, parent_id, schema_label, content, version, source_card_id, position)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                block.block_id,
                block.parent_id,
                block.schema_label,
                block.content,
                block.version,
                block.source_card_id,
                position,
            ),
        )

    @staticmethod
    def _update_block(conn: sqlite3.Connection, doc_id: str, block: Block) -> None:
        conn.execute(
            "UPDATE blocks SET content = ?, version = ?, source_card_id = ?"
            " WHERE doc_id = ? AND block_id = ?",
            (block.content, block.version, block.source_card_id, doc_id, block.block_id),
        )

    def _append_version(
        self,
        conn: sqlite3.Connection,
        doc_id: str,
        *,
        snapshot: tuple[Block, ...],
        trigger: VersionTrigger,
        group_id: str | None,
        target_version_id: str | None,
    ) -> DocumentVersion:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS last_seq FROM document_versions WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        version = DocumentVersion(
            version_id=self._ids.new_id(VERSION),
            doc_id=doc_id,
            seq=int(row["last_seq"]) + 1,
            snapshot=DocumentSnapshot(blocks=snapshot),
            trigger=trigger,
            group_id=group_id,
            target_version_id=target_version_id,
        )
        conn.execute(
            "INSERT INTO document_versions"
            " (version_id, doc_id, seq, snapshot_json, trigger, group_id, target_version_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                version.version_id,
                version.doc_id,
                version.seq,
                version.snapshot.model_dump_json(),
                version.trigger.value,
                version.group_id,
                version.target_version_id,
            ),
        )
        return version
