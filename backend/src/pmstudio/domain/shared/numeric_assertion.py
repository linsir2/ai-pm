"""数字断言判据：一处定义，两处消费者共用。

`exploration` 的调研产物校验与 `sim_inquiry` 的机械筛必须同口径；分开写两份正则
从 2026-10-31 的真实误判来看会各自漂移（`2026 成功`/`3 成本`/`按比例缩放`）。
本模块只依赖标准库 `re`，不碰任何外部包。
"""

import re

_HARD = (
    r"(?<![\d.])\d+(?:\.\d+)?\s*"
    r"(?:%|％|倍|成(?!功|本|员|立|熟|长|年|就|为|全|果|分))"
)
_SOFT = (
    r"占比|百分比|数据显示|调查显示|大多数|多数人|"
    r"(?:高|低|大|小|相同|固定|一定|同一|不同)比例|"
    r"比例(?:为|是|达|约|超过|不足|较|很|高|低|大|小)"
)
_NUMERIC_ASSERTION = re.compile(f"(?:{_HARD})|(?:{_SOFT})")


def find_numeric_assertion(text: str) -> str | None:
    """返回命中的原文片段；没有命中返回 None。"""

    match = _NUMERIC_ASSERTION.search(text)
    return match.group(0) if match is not None else None
