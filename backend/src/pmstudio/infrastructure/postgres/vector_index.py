"""材料向量索引（检索阶段 2）：向量就存在 `material_chunks.embedding`。

`vector(512)` 列 + HNSW（`vector_cosine_ops`）。写入**不许**落在任何写事务里
（ONNX 推理 ~20ms/块，持锁是基线写操作的百倍）：`write()` 自己开一小段事务。
`drop()` 只把 `embedding` 置 NULL——块与正文仍在，检索退回 FTS 不影响材料可用。

profile 记在 `vector_index_meta(id=1, profile, dimensions, model)`；维度不是 512
时明确失败（在线 `ALTER COLUMN TYPE` 不在本任务做）；profile 变更时在事务级
advisory lock 下清空 `embedding` 并重建 HNSW；清空后的补算由 `reembed_missing()`
按批完成（只写向量，不动块正文）。
"""

import logging
from contextlib import suppress

from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.errors import ProgrammingError

from pmstudio.infrastructure.postgres.embedding import (
    DIMENSIONS,
    EMBED_PROFILE,
    MODEL_NAME,
)
from pmstudio.infrastructure.postgres.tokenize import index_text
from pmstudio.infrastructure.tools.limits import WEB_VECTOR_CHARS

logger = logging.getLogger(__name__)

#: 事务级 advisory lock：向量 profile 的清空/重建只能有一方在做。
PROFILE_LOCK_KEY = 0x504D535456454354

#: 重嵌入锁：profile 变更清空后，维护任务按批补算；同一时刻只让一方扫。
REEMBED_LOCK_KEY = 0x504D53545245454D

#: 一次维护最多补算多少份材料；下一趟继续（不把一次 job 拖成无限长）。
REEMBED_BATCH_SIZE = 20

#: HNSW 索引名（0001 迁移建）。
HNSW_INDEX = "idx_material_chunks_embedding"


def vector_char_budget(origin: str) -> int | None:
    """一份材料**最多索引多少字的正文**。`None` = 不限。

    规则只有一条：**联网检索结果（`web_search`）限 `WEB_VECTOR_CHARS` 字**。
    上传（`upload`）与抓网页（`web_fetch`）不限——它们是用户精挑要精读的材料。

    研究来源（`web_research`）不在这张表里：它们**不参与任何检索**（见
    `materials._NON_SEARCHABLE`），所以既不写向量，也不进 `reembed_missing`——
    给一份没人会查的材料算向量只是纯浪费。

    **为什么是「字」不是「块」**：块数是实现细节，块宽一改，"前 N 块"覆盖的**字数**
    就跟着腰斩（批 10：块宽 400 → 200，联网材料的语义覆盖从 41% 掉到 27%）。
    详见 `limits.WEB_VECTOR_CHARS` 的注释。
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


def _raw_connection(connection):  # type: ignore[no-untyped-def]
    return getattr(connection, "connection", connection)


def _register_vector(connection) -> object:  # type: ignore[no-untyped-def]
    raw = _raw_connection(connection)
    if getattr(raw, "_pmstudio_vector_registered", False):
        return raw
    try:
        register_vector(raw)
    except ProgrammingError:
        pass
    else:
        raw._pmstudio_vector_registered = True
    return raw


def _in_transaction(connection) -> bool:  # type: ignore[no-untyped-def]
    depth = getattr(connection, "transaction_depth", None)
    if isinstance(depth, int):
        return depth > 0
    try:
        from psycopg.pq import TransactionStatus

        return connection.info.transaction_status != TransactionStatus.IDLE
    except Exception:  # noqa: BLE001 —— 状态不可读时按事务内处理
        return True


class SqliteVectorIndex:
    """向量索引的 PostgreSQL 实现：读块 → **自然语言**输入（`index_text`）→ 嵌 → 存。"""

    def __init__(self, connection, embedder) -> None:  # type: ignore[no-untyped-def]
        self._connection = connection
        self._embedder = embedder

    def write(self, material_id: str, *, max_blocks: int | None = None) -> None:
        """给一份材料的块算向量并落库。失败只留 warning（降级不判死）。

        `max_blocks` 显式给上限时按它走（**块数**，只给测试与量化脚本用）；
        不给则按材料来源走 `vector_char_budget`（联网材料限**字数**）。
        """
        try:
            rows = self._connection.execute(
                "SELECT c.chunk_id, c.text, c.heading_path, c.ordinal,"
                " m.title AS title, m.origin AS origin"
                " FROM material_chunks c JOIN materials m"
                " ON m.material_id = c.material_id"
                " WHERE c.material_id = %s ORDER BY c.ordinal",
                (material_id,),
            ).fetchall()
            if not rows:
                return
            if max_blocks is not None:
                rows = rows[:max_blocks]
            else:
                char_budget = vector_char_budget(str(rows[0]["origin"]))
                rows = rows[: blocks_within_budget(rows, char_budget)]
            # 喂 embedder 的是**自然语言**，不是分词串（口径见 `index_text`）。
            texts = [
                index_text(
                    title=row["title"],
                    heading_path=row["heading_path"],
                    text=row["text"],
                )
                for row in rows
            ]
            vectors = self._embedder.embed_many(texts)
            self._write_rows(
                [(row["chunk_id"], vector) for row, vector in zip(rows, vectors, strict=True)]
            )
        except Exception as error:  # noqa: BLE001 —— 向量失败不许炸上传/检索主路径
            logger.warning("向量索引写入失败（已降级为纯词法）：%s", error)

    def _write_rows(self, pairs) -> None:
        _register_vector(self._connection)
        owned = not _in_transaction(self._connection)
        if owned:
            self._connection.execute("BEGIN")
        try:
            for chunk_id, vector in pairs:
                values = list(vector)
                if len(values) != DIMENSIONS:
                    raise ValueError(
                        f"向量维度 {len(values)} ≠ {DIMENSIONS}，"
                        "不能写进 vector(512)"
                    )
                self._connection.execute(
                    "UPDATE material_chunks SET embedding = %s WHERE chunk_id = %s",
                    (Vector(values), chunk_id),
                )
            if owned:
                self._connection.execute("COMMIT")
        except Exception:
            if owned:
                with suppress(Exception):
                    self._connection.execute("ROLLBACK")
            raise

    def drop(self, material_id: str) -> None:
        """把一份材料名下全部块的 `embedding` 置 NULL（块与正文保留）。"""
        self._connection.execute(
            "UPDATE material_chunks SET embedding = NULL WHERE material_id = %s",
            (material_id,),
        )

    def reembed_missing(self, *, max_materials: int = REEMBED_BATCH_SIZE) -> int:
        """补算「所有块都没有向量」的材料；返回本次处理份数。

        - **判据是 all-null**：联网材料按字数预算只嵌前 N 字，尾部块**故意**没有向量；
          只看单块 NULL 会把它们反复重算，所以要求 `count(embedding) = 0`。
        - **session advisory lock**：拿不到就跳过，不排队等（维护 job 可重跑）。
        - **只写 embedding**：不碰块正文/编号，已被引用的材料也安全。
        - 每批最多 `max_materials` 份；`write` 内部按来源预算与批次原样复用。
        """
        if max_materials <= 0:
            return 0
        raw = _raw_connection(self._connection)
        locked = raw.execute(
            "SELECT pg_try_advisory_lock(%s) AS locked", (REEMBED_LOCK_KEY,)
        ).fetchone()
        if not locked or not locked["locked"]:
            logger.info("向量重嵌入：已有实例在跑，本次跳过")
            return 0
        try:
            rows = self._connection.execute(
                # 研究来源排除在外：它们不进检索池，补算等于白算
                # （`materials._NON_SEARCHABLE` 是同一条口径的另一半）。
                "SELECT c.material_id FROM material_chunks c"
                " JOIN materials m ON m.material_id = c.material_id"
                " WHERE m.origin <> 'web_research'"
                " GROUP BY c.material_id"
                " HAVING count(*) > 0 AND count(c.embedding) = 0"
                " ORDER BY c.material_id LIMIT %s",
                (max_materials,),
            ).fetchall()
            for row in rows:
                self.write(str(row["material_id"]))
            if rows:
                logger.info("向量重嵌入：补算 %d 份材料", len(rows))
            return len(rows)
        finally:
            with suppress(Exception):
                raw.execute(
                    "SELECT pg_advisory_unlock(%s)", (REEMBED_LOCK_KEY,)
                )


def read_vector_profile(connection) -> dict | None:  # type: ignore[no-untyped-def]
    row = connection.execute(
        "SELECT profile, dimensions, model FROM vector_index_meta WHERE id = 1"
    ).fetchone()
    return None if row is None else dict(row)


def ensure_vector_profile(
    connection,  # type: ignore[no-untyped-def]
    *,
    profile: str = EMBED_PROFILE,
    dimensions: int = DIMENSIONS,
    model: str = MODEL_NAME,
) -> bool:
    """把 `vector_index_meta` 收敛到给定 profile（单行 upsert）。

    返回是否发生了变更。**维度不是 512 直接 `ValueError`**：本任务不做在线
    `ALTER COLUMN TYPE vector(N)`，也不静默清空。profile/model/维度任一变化 =
    旧向量口径不同 → 在 advisory lock 下清空 `embedding` 并重建 HNSW；清空后由
    维护任务的 `reembed_missing()` 补算。
    """
    if dimensions != DIMENSIONS:
        raise ValueError(
            f"向量 profile 维度 {dimensions} ≠ {DIMENSIONS}；"
            "改维度需要显式迁移（ALTER COLUMN TYPE + 重建 HNSW），不在启动路径里做"
        )

    owned = not _in_transaction(connection)
    if owned:
        connection.execute("BEGIN")
    try:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (PROFILE_LOCK_KEY,))
        row = connection.execute(
            "SELECT profile, dimensions, model FROM vector_index_meta"
            " WHERE id = 1 FOR UPDATE"
        ).fetchone()
        changed = row is None or (
            row["profile"] != profile
            or int(row["dimensions"]) != dimensions
            or row["model"] != model
        )
        if row is not None and changed:
            logger.warning(
                "向量 profile 变了（%s/%s → %s/%s），清空 embedding 并重建 HNSW",
                row["profile"],
                row["model"],
                profile,
                model,
            )
            connection.execute("UPDATE material_chunks SET embedding = NULL")
            connection.execute(f"REINDEX INDEX {HNSW_INDEX}")
        connection.execute(
            """
            INSERT INTO vector_index_meta (id, profile, dimensions, model)
            VALUES (1, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
               SET profile = EXCLUDED.profile,
                   dimensions = EXCLUDED.dimensions,
                   model = EXCLUDED.model
            """,
            (profile, dimensions, model),
        )
        if owned:
            connection.execute("COMMIT")
    except Exception:
        if owned:
            with suppress(Exception):
                connection.execute("ROLLBACK")
        raise
    return changed
