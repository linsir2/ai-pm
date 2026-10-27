"""兼容路径：分词已迁到 `infrastructure.postgres.tokenize`。

T5 只搬路径与查询表达式；同一份 jieba 分词与索引源不变。T10 删除整个
`infrastructure/sqlite/`。
"""

from pmstudio.infrastructure.postgres.tokenize import (
    index_source,
    index_text,
    index_tokens,
    match_expr,
    query_tokens,
    warm,
)

__all__ = [
    "index_source",
    "index_text",
    "index_tokens",
    "match_expr",
    "query_tokens",
    "warm",
]
