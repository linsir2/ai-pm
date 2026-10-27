"""兼容路径：材料仓储与检索已迁到 `infrastructure.postgres.materials`。

T5 后的实现只认 PostgreSQL（generated tsvector / pgvector）。T10 删除整个
`infrastructure/sqlite/`。
"""

from pmstudio.infrastructure.postgres.materials import (
    _FTS_CANDIDATES,
    _KNN_SCAN_LIMIT,
    _MATERIAL_SLOTS,
    COVERAGE_FULL,
    COVERAGE_MAJORITY,
    FUSION_ALPHA,
    SqliteFtsRetriever,
    SqliteHybridRetriever,
    SqliteMaterialStore,
    dedup_by_material,
    reciprocal_rank_fusion,
)

__all__ = [
    "COVERAGE_FULL",
    "COVERAGE_MAJORITY",
    "FUSION_ALPHA",
    "SqliteFtsRetriever",
    "SqliteHybridRetriever",
    "SqliteMaterialStore",
    "_FTS_CANDIDATES",
    "_KNN_SCAN_LIMIT",
    "_MATERIAL_SLOTS",
    "dedup_by_material",
    "reciprocal_rank_fusion",
]
