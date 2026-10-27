"""兼容路径：向量索引已迁到 `infrastructure.postgres.vector_index`。

T5 后 `embedding` 是 `material_chunks` 上的 `vector(512)` 列（pgvector），
不再有 vec0 影子表。T10 删除整个 `infrastructure/sqlite/`。
"""

from pmstudio.infrastructure.postgres.vector_index import (
    DIMENSIONS,
    EMBED_PROFILE,
    HNSW_INDEX,
    MODEL_NAME,
    PROFILE_LOCK_KEY,
    SqliteVectorIndex,
    blocks_within_budget,
    ensure_vector_profile,
    vector_char_budget,
)

__all__ = [
    "DIMENSIONS",
    "EMBED_PROFILE",
    "HNSW_INDEX",
    "MODEL_NAME",
    "PROFILE_LOCK_KEY",
    "SqliteVectorIndex",
    "blocks_within_budget",
    "ensure_vector_profile",
    "vector_char_budget",
]
