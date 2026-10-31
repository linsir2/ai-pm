"""数字断言判据：一份判据，两处消费者共用（S2）。

硬数字只认"可被当成数据的量"（百分比 / 倍数 / 成数），软词只认带语境的数量话术。
反例集锁死两个曾真伤到用户的误判：`2026 成功`、`3 成本` 被 `\\d+\\s*成` 吃掉，
`按比例缩放` 被裸词"比例"吃掉。
"""

import pytest

from pmstudio.domain.shared.numeric_assertion import find_numeric_assertion

HITS = (
    "30%",
    "3 成",
    "2 倍",
    "3.5 倍",
    "占比 20%",
    "大多数用户",
    "高比例",
    "数据显示",
)

MISSES = (
    "按比例缩放",
    "缩放比例",
    "2026 成功",
    "3 成本",
    "团队成员 3 人",
    "O(1)",
    "3 类",
    "第 3 版",
)


@pytest.mark.parametrize("text", HITS)
def test_numeric_assertion_hits(text: str) -> None:
    assert find_numeric_assertion(text) is not None


@pytest.mark.parametrize("text", MISSES)
def test_numeric_assertion_does_not_hit(text: str) -> None:
    assert find_numeric_assertion(text) is None


def test_numeric_assertion_returns_the_matched_segment() -> None:
    """返回命中的那段（不是 True），供调用方定位；错误消息不回显它。"""

    assert find_numeric_assertion("约 30% 的用户会这么干") == "30%"
    assert find_numeric_assertion("大约 3.5 倍开销") == "3.5 倍"
    assert find_numeric_assertion("这是高比例场景") == "高比例"
