"""L1 项目上下文：纯数据类型，无跨 context 导入。

构建逻辑在 `application/context.py`（应用层可以跨 context 导入）。
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """L1: 项目上下文 — 每轮都注入的紧凑信息。"""

    domain: str = ""
    target_users: str = ""
    core_value: str = ""
    key_constraints: tuple[str, ...] = ()
    sections_covered: tuple[tuple[str, str], ...] = ()
    features_defined: tuple[str, ...] = ()
    total_confirmed_entries: int = 0
    top_decisions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
