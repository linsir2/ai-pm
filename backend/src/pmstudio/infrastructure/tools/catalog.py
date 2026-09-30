"""工具目录：从 TOML 读（席位目录的同一套做法，不再发明第二种）。

`available(has=…)` 是"没配密钥就不出现"的落点——**不是**无密钥降级分支：
工具缺配置就该说缺，而不是假装搜过了（P17 §9·9）。
"""

import tomllib
from pathlib import Path

from pmstudio.application.ports import ToolDefinition


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
        return tuple(
            item for item in self.all() if set(item.requires).issubset(has)
        )

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
