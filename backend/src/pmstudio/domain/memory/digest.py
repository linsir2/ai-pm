"""L4 轮次摘要：纯数据类型，无跨 context 导入。

构建逻辑在 `application/context.py`（应用层可以跨 context 导入）。
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RoundDigest:
    """L4: 轮次摘要 — 旧轮次的压缩记录。"""

    round_id: str
    project_id: str
    summary: str
    sections_touched: tuple[str, ...] = ()
    features_addressed: tuple[str, ...] = ()
    key_outcomes: tuple[str, ...] = ()
