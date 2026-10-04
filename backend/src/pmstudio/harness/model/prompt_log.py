"""提示词留样的截断策略（`02` §5.3）。

**为什么它属于 harness 而不是留在 SQLite 那份文件里**：这是一条**模型层策略**——
"提示词要存原文，但原文有字符预算，超了留哪一段"。存进哪个库是持久化的事，
与"留哪一段"无关。策略跟着模型层走，实现它的仓库引用它，而不是反过来。

**为什么头尾各半**（2026-09-29 改）：原来只留头部，被切掉的正好是末尾的硬规矩与
输出 schema——那正是复盘"模型为什么这么答"时最想看的一段。

算法与两个常量自 `infrastructure/sqlite/model_calls.py` 原样搬来，**数值与切法一字未改**。
"""

PROMPT_LIMIT = 20_000
"""提示词留样的字符预算。**超了就头尾各半保留**（2026-09-29 改）。"""

TRUNCATION_MARK = "\n…（略去 {count} 字符）…\n"


def keep_head_and_tail(prompt: str) -> str:
    """超预算就**头尾各半**：末尾的硬规矩与输出 schema 比中间那段更值得留。"""

    if len(prompt) <= PROMPT_LIMIT:
        return prompt
    half = PROMPT_LIMIT // 2
    dropped = len(prompt) - PROMPT_LIMIT
    return prompt[:half] + TRUNCATION_MARK.format(count=dropped) + prompt[-half:]
