"""兼容路径：向量模型适配已迁到 `infrastructure.postgres.embedding`。

T5 只搬路径；fastembed / token 窗口 / `EMBED_PROFILE` 口径不变。T10 删除整个
`infrastructure/sqlite/`。
"""

from pmstudio.infrastructure.postgres.embedding import (
    DIMENSIONS,
    EMBED_INPUT_MAX_TOKENS,
    EMBED_PROFILE,
    EMBED_TOKEN_MARGIN,
    MODEL_MAX_TOKENS,
    MODEL_NAME,
    Embedder,
    FastEmbedEmbedder,
    fit_to_window,
)

__all__ = [
    "DIMENSIONS",
    "EMBED_INPUT_MAX_TOKENS",
    "EMBED_PROFILE",
    "EMBED_TOKEN_MARGIN",
    "MODEL_MAX_TOKENS",
    "MODEL_NAME",
    "Embedder",
    "FastEmbedEmbedder",
    "fit_to_window",
]
