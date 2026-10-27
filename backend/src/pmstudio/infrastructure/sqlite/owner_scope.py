"""兼容路径：owner 上下文已迁到 `infrastructure.postgres.owner_scope`。

T4 只搬路径；同一组 contextvar 与判据，T10 删除整个 `infrastructure/sqlite/`。
"""

from pmstudio.infrastructure.postgres.owner_scope import (
    clear_owner_scope,
    current_owner_scope,
    optional_owner,
    require_owner,
    set_owner_scope,
)

__all__ = [
    "clear_owner_scope",
    "current_owner_scope",
    "optional_owner",
    "require_owner",
    "set_owner_scope",
]
