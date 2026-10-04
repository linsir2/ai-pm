"""提示词库：从目录读 `.md`，用 `$slot` 语法填槽。少给槽位就报错，不许静默留空。

2026-10-04（M5）从 `infrastructure/llm/prompt_library.py` 搬来，逻辑一字未改。
搬的理由不是"它属于内层"，而是**它原先在 infrastructure 却实现着 harness 的协议**
（`model/contracts.py` 的 `PromptLibrary`）——实现和协议分住两层是个既有的错位。

**提示词内容（5 个 `.md`）是产品数据，不是代码**，所以留在 harness 里跟着这个模块走：
它们是"这个模块读什么"，不是"这个模块是什么"。真要把文案挪出代码库，那是另一件事
（要做的话连席位目录的 `.toml` 一起做，别只搬一半）。
"""

from pathlib import Path
from string import Template

#: 提示词目录。**定义在它所指向的资源旁边**，理由同 `tools/registry.py` 的 `DEFINITIONS_DIR`：
#: 搬目录不产生 import 错误，只在读 `.md` 时炸，而各自重算路径的调用方里
#: 只有碰巧用到的那一处会红。
PROMPTS_DIR = Path(__file__).parent / "prompts"


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
