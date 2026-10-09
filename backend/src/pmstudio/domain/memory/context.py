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
    top_decisions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    #: 上面两槽**未截断时的总数**（0 = 未统计）。渲染时用来照实说"只带了前 N 条"。
    decisions_total: int = 0
    open_questions_total: int = 0
