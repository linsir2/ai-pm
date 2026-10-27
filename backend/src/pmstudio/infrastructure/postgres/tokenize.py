"""材料检索的中文分词（检索 P1）：**索引侧与查询侧必须调同一份**（对称是命门）。

jieba 是唯一"纯 Python、可 pip 装、离线、词典内置"的中文分词；选型与实验见
`docs/plans/2026-10-17-检索升级-方案.md`。PostgreSQL 的 `simple` 配置不切中文，
所以词切在应用层做，索引串写进 `material_chunks.tokens`，库里的 `search_tsv`
再按空格把它转成 tsvector。

**查询用 OR**（不是隐式 AND）：jieba 分词**上下文相关**——同一串字在片段里
与在全文里可能切成不同 token，AND 会因"某个 token 不在全文 token 集里"整条不命中。
本地实验（46 条真中文语料）：AND recall@5 39/45、**OR 45/45**。

`match_expr` 生成 PostgreSQL `to_tsquery('simple', ...)` 的输入：每个 token 加单引号
（字面量），并把 tsquery 的运算符与引号本身拆掉——它们在引号内仍会被解释成
运算符（`'c:d'` 会变成 `c <-> d`），不处理就会改变语义甚至抛语法错。
"""

import re
from collections.abc import Sequence

import jieba

#: 别在 stderr 刷 "Building prefix dict…"（进程启动时由组装点预热一次）。
jieba.setLogLevel(60)

#: 至少含一个"词字符"（字母/数字/下划线/汉字）的 token 才算数——纯标点（`"`、`·`、`_`…）
#: 丢掉：既不携带检索信号，还会在 tsquery 里变成运算符。
_WORDY = re.compile(r"[\w\u4e00-\u9fff]")

#: tsquery 的运算符与引号。**在单引号里也仍然生效**（实测 `'c:d'` → `c <-> d`），
#: 所以先按它们切开，再把每个词片单独加引号。
_TSQUERY_SPECIAL = re.compile(r"[&|!():*'\\]+")


def _cut(text: str) -> list[str]:
    return list(jieba.cut_for_search(text))


def query_tokens(query: str) -> list[str]:
    """查询侧分词后的有效 token（纯标点丢）。空/纯标点 → `[]`。"""
    return [token for token in _cut(query) if _WORDY.search(token)]


def index_tokens(text: str) -> str:
    """索引侧：分词后空格连接（`to_tsvector('simple', tokens)` 按空格切）。"""
    return " ".join(token for token in _cut(text) if _WORDY.search(token))


def _tsquery_lexemes(token: str) -> list[str]:
    """一个分词 token → tsquery 可安全引用的词片（与 simple parser 的切法一致）。"""

    return [part for part in _TSQUERY_SPECIAL.split(token) if _WORDY.search(part)]


def match_expr(query: str) -> str:
    """查询侧 tsquery 表达式：每个 token 加单引号（字面量）→ `|` 连接。

    空/纯标点回 `""`——调用方据此回空，**不把空串塞进 `to_tsquery`**
    （PostgreSQL 对空查询报语法错）。单个 token 里的运算符会被拆成多个词片。
    """

    lexemes = [
        lexeme for token in query_tokens(query) for lexeme in _tsquery_lexemes(token)
    ]
    if not lexemes:
        return ""
    return " | ".join("'" + lexeme.replace("'", "''") + "'" for lexeme in lexemes)


def index_source(*, title: str, heading_path: Sequence[str], text: str) -> str:
    """一块的**词法**索引源：材料标题 + 祖先标题 + 正文 → 分词串（写路径与回填**共用这一份**）。

    为什么加标题：材料标题（文件名 / 页标题）**不在任何块正文里**，搜它只能靠这一份。
    为什么加祖先标题：长节被切成多块时，**后几块的正文里没有节名**（只在 `heading_path`），
    不补就搜不到（真数据坐实：3 块只有第 1 块正文带节名）。
    """
    return index_tokens(" ".join((title, *heading_path, text)))


def index_text(*, title: str, heading_path: Sequence[str], text: str) -> str:
    """一块的**向量**输入：材料标题 + 祖先标题 + 正文——**原样，不分词**。

    ## 为什么与 `index_source` 分开（2026-10-19 实测定的）

    两条路要的**不是同一种东西**：词法索引要的是**词袋**（分词串），向量模型要的是
    **自然语言**。把 jieba 分词后的空格串喂给 bge，等于把自然语言退化成词袋——
    它的语义优势被扔掉一半。实测（批 6a，46 条真观察 + 101 条生成语料）：

    | 口径 | 逐字子串 | 同词换序 | 同义改述（recall@5） |
    |---|---|---|---|
    | 分词串（改前） | 34/40 | 35/40 | **34/40（85%）** |
    | 自然语言（改后） | 35/40 | 35/40 | **38/40（95%）** |

    同期"假 embedder"对照组几乎不变（1/2）——变好的是**口径**在削弱真模型，
    不是噪声。**两侧形态一致**（索引侧自然语言、查询侧原始 query）也是业界做法。

    组成与 `index_source` 相同（同样的标题 + 章节链 + 正文），只是**不过分词**。
    """
    return " ".join((title, *heading_path, text))


def warm() -> None:
    """把 jieba 词典**提前加载**（首次 `cut` 要 ~0.6s——别落在首个请求里）。

    只是调 jieba 自己的初始化，**不自己实现加载逻辑**。组装点调一次即可（幂等）。
    """

    jieba.initialize()
