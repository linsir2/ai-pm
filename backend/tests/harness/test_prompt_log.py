"""提示词留样的截断策略（`02` §5.3）。

**为什么它属于 harness 而不是 SQLite 那份文件里**：这是一条**模型层策略**——
"提示词要存原文，但原文有字符预算，超了怎么留"。存进哪个库是持久化的事，
与"留哪一段"无关。策略跟着模型层走，实现它的仓库也跟着引用它。

**为什么头尾各半**（2026-09-29 改）：原来只留头部，被切掉的正好是末尾的
硬规矩与输出 schema——那正是复盘"模型为什么这么答"时最想看的一段。
"""

from pmstudio.harness.model.prompt_log import (
    PROMPT_LIMIT,
    TRUNCATION_MARK,
    keep_head_and_tail,
)

__all__ = ["PROMPT_LIMIT", "TRUNCATION_MARK", "keep_head_and_tail"]


def test_under_the_limit_is_returned_untouched():
    """没超预算就**原样返回**——一个字都不许动（动了就再也复现不出来了）。"""

    prompt = "短提示词"
    assert keep_head_and_tail(prompt) == prompt


def test_exactly_at_the_limit_is_not_truncated():
    """边界：正好等于预算**不截断**（判据是 `<=`，含等于）。"""

    prompt = "x" * PROMPT_LIMIT
    assert keep_head_and_tail(prompt) == prompt


def test_empty_prompt_is_returned_untouched():
    assert keep_head_and_tail("") == ""


def test_over_the_limit_keeps_both_ends():
    """超预算：头尾各留一半。"""

    prompt = "头" * 30_000 + "尾" * 30_000
    kept = keep_head_and_tail(prompt)

    assert kept.startswith("头")
    assert kept.endswith("尾")
    # 中段被换成了标记，标记里要写清省了多少字符——否则复盘的人不知道丢了多少
    assert "略去" in kept
    assert str(len(prompt) - PROMPT_LIMIT) in kept


def test_kept_length_stays_within_budget_plus_the_mark():
    """留下来的总长 = 一半 + 标记 + 一半，不因标记本身超预算。"""

    prompt = "很长的提示词" * 5_000
    kept = keep_head_and_tail(prompt)

    assert len(kept) <= PROMPT_LIMIT + 100


def test_two_prompts_of_the_same_length_truncate_to_the_same_length():
    """同样长度 → 同样截断长度（截断只认长度，不认内容）。"""

    first = keep_head_and_tail("a" * 40_000)
    second = keep_head_and_tail("b" * 40_000)

    assert len(first) == len(second)


def test_truncating_an_already_truncated_prompt_does_not_nest_marks():
    """已经截过的再过一次，**标记不会嵌套**（不会变成"略去…略去…"）。

    注意它**不是幂等的**：第二次会再截一次，于是标记里的字符数会变小
    （第一次是 20000，第二次是 17）。这没关系——`record()` 每次都对**原始**提示词
    调用本函数，从不对已截断的结果再截；这条测的是"万一被误用也不会产出鬼东西"。
    """

    once = keep_head_and_tail("x" * 40_000)
    assert once.count("略去") == 1

    twice = keep_head_and_tail(once)
    assert twice.count("略去") == 1
