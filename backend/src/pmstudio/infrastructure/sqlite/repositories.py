"""兼容路径：核心仓储已迁到 `infrastructure.postgres.repositories`。

T4 只改路径与驱动；类名保持不变，T10 删除整个 `infrastructure/sqlite/`。
"""

from pmstudio.infrastructure.postgres.repositories import (
    SqliteDiscussionRepository,
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteExplorationRepository,
    SqliteMemoryRepository,
    SqliteRoundRepository,
    SqliteTemplateRepository,
    SqliteWorkspaceIndex,
)

__all__ = [
    "SqliteDiscussionRepository",
    "SqliteDocumentRepository",
    "SqliteEvidenceRepository",
    "SqliteExplorationRepository",
    "SqliteMemoryRepository",
    "SqliteRoundRepository",
    "SqliteTemplateRepository",
    "SqliteWorkspaceIndex",
]
