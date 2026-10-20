"""联网内容的清洗归一（批 6a Task 3）：Tavily 的 `content` → 干净文本。

**为什么要有这一层**：Tavily 返回的 `content` 实测（2026-10-19 真跑）带三类噪音——
① 它自己的省略标记 `[...]`（每条 2 次）、② 法条原文排版用的全角空格 `　`、
③ Markdown 表格的**分隔行**（`| --- | --- |`，骨架不是内容）。
这些进了 FTS 索引会稀释词面匹配，进了向量会白占 token（512 的上限很紧）。

**不洗什么**（有意）：
- **Markdown 标题符**（`## `）**保留**——它既是内容的一部分，又被 `chunk_text`
  认成标题从而生成 `heading_path`（章节链进索引源，检索时"只在这一节出现的内容"
  才搜得到）。洗掉它等于扔掉一段检索信号。
- **表格的内容行**保留——那是有信息的；只剥分隔行。
- **代码块围栏**（``` ）保留——那是内容的结构。

纯函数、无外部依赖、无副作用：清洗要**可重放**（回填与重解析都会再跑一遍）。
"""

from __future__ import annotations

import re

#: Tavily 自己的省略标记（实测每条 content 里 2 次）。不是内容。
_ELLIPSIS = re.compile(r"\[\.\.\.\]|\[…]")

#: 全角空格（U+3000）——法条原文排版用它（`第五十六条　个人信息保护影响评估`）。
#: 它会打断 2-gram 滑窗与 jieba 分词（两边的切法不一致，词面匹配就漏）。
_FULLWIDTH_SPACE = "　"

#: 行内连续空白（2 个以上）折成一个。网页正文里的连续空格是排版产物。
_MULTI_SPACE = re.compile(r"[ \t]{2,}")

#: 连续 3 个以上换行折成 2 个（保留段落间隔，去掉多余空行——切块靠空行分段）。
_MULTI_NEWLINE = re.compile(r"\n{3,}")

#: Markdown 表格的**分隔行**：整行只有 `|`、`-`、`:`、空格，且至少有一个 `-`。
#: 形如 `| --- | --- |` 或 `|---|---|`。表头与数据行**不是**这个形状，要留。
_TABLE_SEPARATOR = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")

#: 整行**只有一个** markdown 链接或图片 → 导航/装饰行。
#: 实测（Tavily `raw_content`，2026-10-20）：18.7% 的行是这种——面包屑、logo、
#: 按钮、产品目录。剔掉后总字数降 20%，**检索命中不降反升**（32/32 vs 31/31）：
#: 这些行既不是内容，又会在向量里稀释页面的主题。
#: **不命中** `- [标题](url)`（有列表符号 → 参考资料列表要留）、
#: **不命中** `[a](1)[b](2)`（一行多个 → 正文里的行内链接）。
_LINK_ONLY = re.compile(r"^!?\[[^\]]*\]\([^)]*\)$")


def _is_link_only(line: str) -> bool:
    """这一行是不是「整行只有一个 markdown 链接/图片」。"""
    return bool(_LINK_ONLY.match(line.strip()))


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    if "-" not in stripped:
        return False
    return bool(_TABLE_SEPARATOR.match(line))


def normalize_web_text(text: str) -> str:
    """Tavily 的 `content` → 可切块、可进 FTS、向量不失真的干净文本。

    顺序有讲究：**先**剔导航行与省略标记、全角空格、剥表格分隔行，**后**做空白归一
    （归一会改行数，所以放最后）。
    """
    if not text:
        return ""
    cleaned = "\n".join(line for line in text.split("\n") if not _is_link_only(line))
    cleaned = _ELLIPSIS.sub("", cleaned)
    cleaned = cleaned.replace(_FULLWIDTH_SPACE, " ")
    lines = [line for line in cleaned.split("\n") if not _is_table_separator(line)]
    cleaned = "\n".join(line.rstrip() for line in lines)
    cleaned = _MULTI_SPACE.sub(" ", cleaned)
    cleaned = _MULTI_NEWLINE.sub("\n\n", cleaned)
    return cleaned.strip()
