"""提示词库：从目录读 .md，用 `$slot` 语法填槽。少给槽位就报错，不许静默留空。"""

from pathlib import Path
from string import Template


class FilePromptLibrary:
    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._cache: dict[str, str] = {}

    def render(self, name: str, **slots: str) -> str:
        path = self._directory / f"{name}.md"
        if name not in self._cache:
            if not path.is_file():
                raise FileNotFoundError(path)
            self._cache[name] = path.read_text(encoding="utf-8")
        return Template(self._cache[name]).substitute(**slots)
