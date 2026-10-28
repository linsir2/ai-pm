"""材料索引源的拼装：标题 + 祖先标题 + 正文——**原样，不分词**。

词法侧把这份文本写进 `material_chunks.search_text`；pg_search 的 jieba 分词器
在数据库里由 `pmstudio_tokens` 统一产出 `search_tokens` 与查询 token（索引与
查询同一份口径）。向量侧也吃这份自然语言，不喂词袋。

## 为什么不在应用层分词（2026-10-28）

旧版在应用层预分词并写词袋列，索引与查询各切一次，口径容易漂移；现在词法分词
只发生在 pg_search 一处，应用层只负责拼原文。
"""

from collections.abc import Sequence


def index_text(*, title: str, heading_path: Sequence[str], text: str) -> str:
    """一块的索引源（词法与向量共用）：材料标题 + 祖先标题 + 正文。

    为什么加标题：材料标题（文件名 / 页标题）**不在任何块正文里**，搜它只能靠这一份。
    为什么加祖先标题：长节被切成多块时，**后几块的正文里没有节名**（只在 `heading_path`），
    不补就搜不到（真数据坐实：3 块只有第 1 块正文带节名）。

    词法侧由 pg_search 在库里分词；向量侧喂 embedder 的也是这份**自然语言**——
    分词串会把语义模型的一半优势扔掉（2026-10-19 实测：改述召回 85% → 95%）。
    """
    return " ".join((title, *heading_path, text))
