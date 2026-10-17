"""材料检索的中文分词（检索 P1）：**索引侧与查询侧必须调同一份**（对称是命门）。

jieba 是唯一"纯 Python、可 pip 装、离线、词典内置"的中文分词；选型与实验见
`docs/plans/2026-10-17-检索升级-方案.md`。SQLite FTS5 内置的 tokenizer 都不切中文
（`unicode61` 把连续中文当**一个** token），所以词切在应用层做、结果写进 FTS。

**查询用 OR**（不是 FTS5 默认的隐式 AND）：jieba 分词**上下文相关**——同一串字在片段里
与在全文里可能切成不同 token，AND 会因"某个 token 不在全文 token 集里"整条不命中。
本地实验（46 条真中文语料）：AND recall@5 39/45、**OR 45/45**。
"""

import re
from collections.abc import Sequence

import jieba

#: 别在 stderr 刷 "Building prefix dict…"（进程启动时由组装点预热一次）。
jieba.setLogLevel(60)

#: 至少含一个"词字符"（字母/数字/下划线/汉字）的 token 才算数——纯标点（`"`、`·`、`_`…）
#: 丢掉：既不携带检索信号，`"` 还会把 FTS5 的引号语法搞崩。
_WORDY = re.compile(r"[\w\u4e00-\u9fff]")


def _cut(text: str) -> list[str]:
    return list(jieba.cut_for_search(text))


def query_tokens(query: str) -> list[str]:
    """查询侧分词后的有效 token（纯标点丢）。空/纯标点 → `[]`。"""
    return [token for token in _cut(query) if _WORDY.search(token)]


def index_tokens(text: str) -> str:
    """索引侧：分词后空格连接（FTS5 按空格切，token 已是词）。"""
    return " ".join(token for token in _cut(text) if _WORDY.search(token))


def match_expr(query: str) -> str:
    """查询侧 MATCH 表达式：每个 token 加双引号（当字面串）→ `OR` 连接。

    空/纯标点回 `""`——调用方据此回空，**不把空串塞进 MATCH**（FTS5 对空查询报错）。
    """
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in query_tokens(query))


def index_source(*, title: str, heading_path: Sequence[str], text: str) -> str:
    """一块的索引源：**材料标题 + 祖先标题 + 正文**（写路径与回填**共用这一份**）。

    为什么加标题：材料标题（文件名 / 页标题）**不在任何块正文里**，搜它只能靠这一份。
    为什么加祖先标题：长节被切成多块时，**后几块的正文里没有节名**（只在 `heading_path`），
    不补就搜不到（真数据坐实：3 块只有第 1 块正文带节名）。
    """
    return index_tokens(" ".join((title, *heading_path, text)))


def warm() -> None:
    """把 jieba 词典**提前加载**（首次 `cut` 要 ~0.6s——别落在首个请求里）。

    只是调 jieba 自己的初始化，**不自己实现加载逻辑**。组装点调一次即可（幂等）。
    """

    jieba.initialize()
