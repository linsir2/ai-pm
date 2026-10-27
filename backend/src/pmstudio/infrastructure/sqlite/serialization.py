"""兼容路径：序列化实现已迁到 `infrastructure.postgres.serialization`。

T4 只搬路径；JSON 负载内部的 ISO 表示未变。T10 删除整个 `infrastructure/sqlite/`。
"""

from pmstudio.infrastructure.postgres.serialization import *  # noqa: F403
from pmstudio.infrastructure.postgres.serialization import _dropped_from_payload  # noqa: F401
