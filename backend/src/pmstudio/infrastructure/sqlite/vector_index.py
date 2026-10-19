"""材料向量索引（检索阶段 2）：与块的 FTS 索引**同库同表族、不同表**。

`material_chunks_vec`（vec0 虚拟表）只存 `chunk_id → embedding`，标题/正文不重复存——
读回时按 `chunk_id` 连回 `material_chunks`。写入**不许**落在任何写事务里
（ONNX 推理 ~20ms/块，持锁是基线写操作的百倍）：`write()` 自己开一小段事务。
"""

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)


class SqliteVectorIndex:
    """向量索引的 SQLite 实现：读块 → 同一个 `index_source` 分词输入 → 嵌 → 存。"""

    def __init__(self, connection: sqlite3.Connection, embedder) -> None:
        self._connection = connection
        self._embedder = embedder

    def write(self, material_id: str, *, owner=None) -> None:
        """给一份材料的所有块算向量并落库。失败只留 warning（降级不判死）。"""
        from pmstudio.infrastructure.sqlite.tokenize import index_source

        try:
            rows = self._connection.execute(
                "SELECT c.chunk_id, c.text, c.heading_path, m.title AS title"
                " FROM material_chunks c JOIN materials m"
                " ON m.material_id = c.material_id"
                " WHERE c.material_id = ?",
                (material_id,),
            ).fetchall()
            if not rows:
                return
            texts = [
                index_source(
                    title=row["title"],
                    heading_path=json.loads(row["heading_path"] or "[]"),
                    text=row["text"],
                )
                for row in rows
            ]
            vectors = self._embedder.embed_many(texts)
            self._write_rows(
                [(row["chunk_id"], vector) for row, vector in zip(rows, vectors, strict=True)]
            )
        except Exception as error:  # noqa: BLE001 —— 向量失败不许炸上传/检索主路径
            logger.warning("向量索引写入失败（已降级为纯 FTS5）：%s", error)

    def _write_rows(self, pairs) -> None:
        from sqlite_vec import serialize_float32

        began = not self._connection.in_transaction
        if began:
            self._connection.execute("BEGIN IMMEDIATE")
        try:
            for chunk_id, vector in pairs:
                self._connection.execute(
                    "INSERT OR REPLACE INTO material_chunks_vec (chunk_id, embedding)"
                    " VALUES (?, ?)",
                    (chunk_id, serialize_float32(list(vector))),
                )
            if began:
                self._connection.execute("COMMIT")
        except Exception:
            if began:
                self._connection.execute("ROLLBACK")
            raise

    def drop(self, material_id: str, *, owner=None) -> None:
        """删一份材料名下的全部向量（与块删除同事务：调用方在删块的事务里调）。

        vec 表只有 `chunk_id` 主键：按"属于这份材料的块"删。
        """
        self._connection.execute(
            "DELETE FROM material_chunks_vec WHERE chunk_id IN"
            " (SELECT chunk_id FROM material_chunks WHERE material_id = ?)",
            (material_id,),
        )
