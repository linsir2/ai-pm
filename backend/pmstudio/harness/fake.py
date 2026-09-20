"""Fake/Local Harness：没有 API Key 的环境也能跑通全链路（P0 可运行闭环）。

它实现 `HarnessPort` 的完整形状（complete / trim / can_use / can_enter / record_trace），
但**不碰任何网络**：

- `complete`：理解卡（无 response_format）返回固定复述文本；成稿卡（带 JSON
  schema）从 prompt 里解析"需要填充的字段"，为每个字段生成同一条假内容。
- `trim`：全部保留（不做裁剪），把预算决策留给真实 Trimmer 的测试。
- `can_use` / `can_enter`：一律放行；`record_trace`：落空操作。

用途：测试与本地 demo 注入它，跑通 M8 → M28 → M20 的完整闭环；
真实模型只在该注入真实 harness（`build_runtime` 默认）时被调用。
"""

import json
import re
from collections.abc import Sequence

from pmstudio.contracts.enums import Permission, RoundEntry
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.models.results import TrimResult
from pmstudio.contracts.models.trace import Trace
from pmstudio.contracts.skeleton.board import ContextBlock

# M28 的 prompt 里有一行"需要填充的字段：[...]"，FakeHarness 从这里知道要生成哪些字段。
_FIELDS_PATTERN = re.compile(r"需要填充的字段：\[(.*?)\]")

_DEFAULT_UNDERSTANDING_TEXT = "我理解你要细化功能清单，当前文档中该字段为空。"
_DEFAULT_FILL_CONTENT = "支持把讨论候选带进主闭环。"


class FakeHarness:
    """无网络模型替身。注入 `build_runtime(harness=...)` 即替代真实链路。"""

    def __init__(
        self,
        *,
        understanding_text: str = _DEFAULT_UNDERSTANDING_TEXT,
        fill_content: str = _DEFAULT_FILL_CONTENT,
    ) -> None:
        self._understanding_text = understanding_text
        self._fill_content = fill_content
        self.calls: list[dict] = []

    async def complete(
        self,
        model_ref: str,
        messages: Sequence[PromptMessage],
        response_format: dict | None = None,
    ) -> str:
        """按 response_format 分流：有 JSON schema = 成稿卡，否则 = 理解卡。"""
        self.calls.append({"model_ref": model_ref, "response_format": response_format})

        if response_format is not None:
            fields = self._extract_fields(messages)
            return json.dumps(
                {label: self._fill_content for label in fields},
                ensure_ascii=False,
            )

        return self._understanding_text

    async def can_use(self, role_id: str, tool_id: str) -> Permission:
        """Fake 一律放行——权限测试在真实 harness 上做。"""
        return Permission.ALLOW

    async def can_enter(self, role_id: str, entry: RoundEntry) -> Permission:
        """Fake 一律放行。"""
        return Permission.ALLOW

    async def trim(
        self,
        blocks: Sequence[ContextBlock],
        project_id: str,
        model_ref: str,
    ) -> TrimResult:
        """全保留，不做裁剪。"""
        return TrimResult(blocks=tuple(blocks), dropped=())

    async def record_trace(self, trace: Trace) -> None:
        """Fake 不落观测——M25 在真实 harness 上做。"""

    def _extract_fields(self, messages: Sequence[PromptMessage]) -> list[str]:
        """从 system prompt 里解析"需要填充的字段：['功能清单', ...]"。

        解析不到就返回空列表——M28 会因没有提案而拒绝，错误信息会暴露问题。
        """
        for message in messages:
            match = _FIELDS_PATTERN.search(message.text)
            if match is None:
                continue
            raw = match.group(1)
            labels = [
                part.strip().strip("'\"")
                for part in raw.split(",")
                if part.strip().strip("'\"")
            ]
            return labels
        return []
