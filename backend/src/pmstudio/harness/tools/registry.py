"""工具目录：从 TOML 读（席位目录的同一套做法，不再发明第二种）。

`available(has=…)` 是"没配密钥就不出现"的落点——**不是**无密钥降级分支：
工具缺配置就该说缺，而不是假装搜过了（P17 §9·9）。

2026-10-04（M5）从两处合来：`application/ports.py` 的 `ToolDefinition` + `ToolCatalog`
协议，和 `infrastructure/tools/catalog.py` 的 TOML 实现。它们本来是一件事，
拆在两层只让 import 绕一圈。逻辑一字未改。

**为什么不补 `risk_level` / `approval_policy` / `network_access` 等 9 个字段**：
它们在 `03-案例与验收.md` 里零定义、全仓零命中、且**没有任何消费者**（`use_cases.py`
只读 `tool_id` / `requires` / `max_queries` / `max_results`）。填它们只能靠编，编完没人读——
那是为别人的 checklist 建字段。真有消费方时（要审批、要权限、要超时）再按设计补。
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

#: 工具声明目录。**定义在它所指向的资源旁边**，而不是让 `container.py` 去拼路径：
#: 搬目录不产生 import 错误，只在读 `.toml` 时炸——而调用方各自重算路径时，
#: 只有碰巧用到的那一处会红，其余静默指向空目录。
#: （M5 之前有 5 处各自重算，见 `tests/harness/test_registry.py` 那条路径守卫。）
DEFINITIONS_DIR = Path(__file__).parent / "definitions"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """一个工具的声明（从 TOML 读）。`requires` 是**配置项名**：没配就没这个工具。

    `max_queries` / `max_results` 读到 0 的含义是"**没有上限可用**"，调用方必须自己判。
    这里刻意**不填兜底数字**：同一个值写在 TOML 与 Python 两处，改一处就漂
    （M5 删掉了 `use_cases.py` 里的 `DEFAULT_WEB_QUERY_LIMIT=3` / `DEFAULT_WEB_LIMIT=5`）。
    """

    tool_id: str
    title: str
    kind: str
    requires: tuple[str, ...] = ()
    max_queries: int = 0
    max_results: int = 0


@runtime_checkable
class ToolCatalog(Protocol):
    def all(self) -> tuple[ToolDefinition, ...]: ...
    def available(self, *, has: frozenset[str]) -> tuple[ToolDefinition, ...]: ...


class TomlToolCatalog:
    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._cache: tuple[ToolDefinition, ...] | None = None

    def all(self) -> tuple[ToolDefinition, ...]:
        if self._cache is None:
            self._cache = tuple(
                self._read(path) for path in sorted(self._directory.glob("*.toml"))
            )
        return self._cache

    def available(self, *, has: frozenset[str]) -> tuple[ToolDefinition, ...]:
        return tuple(item for item in self.all() if set(item.requires).issubset(has))

    def _read(self, path: Path) -> ToolDefinition:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return ToolDefinition(
            tool_id=str(data["tool_id"]),
            title=str(data["title"]),
            kind=str(data["kind"]),
            requires=tuple(str(item) for item in data.get("requires", ())),
            max_queries=int(data.get("max_queries", 0)),
            max_results=int(data.get("max_results", 0)),
        )
