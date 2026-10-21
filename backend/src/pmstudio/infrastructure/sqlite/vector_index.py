"""材料向量索引（检索阶段 2）：与块的 FTS 索引**同库同表族、不同表**。

`material_chunks_vec`（vec0 虚拟表）只存 `chunk_id → embedding`，标题/正文不重复存——
读回时按 `chunk_id` 连回 `material_chunks`。写入**不许**落在任何写事务里
（ONNX 推理 ~20ms/块，持锁是基线写操作的百倍）：`write()` 自己开一小段事务。
"""

import json
import logging
import sqlite3

from pmstudio.infrastructure.tools.limits import WEB_VECTOR_CHARS

logger = logging.getLogger(__name__)


def vector_char_budget(origin: str) -> int | None:
    """一份材料**最多索引多少字的正文**。`None` = 不限。

    规则只有一条：**联网材料（`web_search`）限 `WEB_VECTOR_CHARS` 字**。
    上传（`upload`）与抓网页（`web_fetch`）不限——它们是用户精挑要精读的材料。

    **为什么是「字」不是「块」**：块数是实现细节，块宽一改，"前 N 块"覆盖的**字数**
    就跟着腰斩（批 10：块宽 400 → 200，联网材料的语义覆盖从 41% 掉到 27%）。
    详见 `limits.WEB_VECTOR_CHARS` 的注释。

    **为什么写入与回填必须共用这一个函数**：回填（`database._backfill_chunk_vectors`）
    是在启动时补"缺向量的块"，若它不认这条规则，就会把预算外的块全补上——
    预算当场形同虚设，而**症状要等几十秒后的向量推理才显形**，极难归因。
    """
    if origin == "web_search":
        return WEB_VECTOR_CHARS
    return None


def blocks_within_budget(rows, char_budget: int | None) -> int:
    """前多少个块落在字数预算内。`rows` 须**按 ordinal 排好**，元素要有 `text`。

    判据是**累计字数**：`rows[:k]` 的字数和 ≤ 预算的最大 `k`。
    `char_budget is None` → 全要（返回 `len(rows)`）。

    标题与章节链**不另计**：它们是每块都带的上下文，按块重复计会让预算失真；
    只计正文是**偏保守**的一侧（实际嵌进去的字更多）。
    """
    if char_budget is None:
        return len(rows)
    used = 0
    count = 0
    for row in rows:
        size = len(str(row["text"]))
        if used + size > char_budget:
            break
        used += size
        count += 1
    return count


class SqliteVectorIndex:
    """向量索引的 SQLite 实现：读块 → **自然语言**输入（`index_text`）→ 嵌 → 存。"""

    def __init__(self, connection: sqlite3.Connection, embedder) -> None:
        self._connection = connection
        self._embedder = embedder

    def write(self, material_id: str, *, max_blocks: int | None = None) -> None:
        """给一份材料的块算向量并落库。失败只留 warning（降级不判死）。

        `max_blocks` 显式给上限时按它走（**块数**，只给测试与量化脚本用）；
        不给则按材料来源走 `vector_char_budget`（联网材料限**字数**——见那个函数的说明）。
        """
        from pmstudio.infrastructure.sqlite.tokenize import index_text

        try:
            rows = self._connection.execute(
                "SELECT c.chunk_id, c.text, c.heading_path, c.ordinal,"
                " m.title AS title, m.origin AS origin"
                " FROM material_chunks c JOIN materials m"
                " ON m.material_id = c.material_id"
                " WHERE c.material_id = ? ORDER BY c.ordinal",
                (material_id,),
            ).fetchall()
            if not rows:
                return
            if max_blocks is not None:
                rows = rows[:max_blocks]
            else:
                char_budget = vector_char_budget(str(rows[0]["origin"]))
                rows = rows[: blocks_within_budget(rows, char_budget)]
            # 喂 embedder 的是**自然语言**，不是分词串（口径见
            # `tokenize.index_text` 的文档：2026-10-19 实测改述类 recall@5
            # 85% → 95%，假 embedder 对照组不变 → 是口径在削弱真模型）。
            texts = [
                index_text(
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

    def drop(self, material_id: str) -> None:
        """删一份材料名下的全部向量（与块删除同事务：调用方在删块的事务里调）。

        vec 表只有 `chunk_id` 主键：按"属于这份材料的块"删。
        """
        self._connection.execute(
            "DELETE FROM material_chunks_vec WHERE chunk_id IN"
            " (SELECT chunk_id FROM material_chunks WHERE material_id = ?)",
            (material_id,),
        )
