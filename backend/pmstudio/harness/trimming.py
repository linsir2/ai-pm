"""M24 预算与裁剪 —— 只管"能带多少"，不做语义判断。

预算 = `模型窗口 − 输出预留 − 系统开销`：后两项来自项目配置（C16），第一项来自模型条目（C10）。
调用方不传预算（§5.3 的签名里没有这个参数）——"带多少"是 Harness 自己的事。

排序复用 `invariants.order_context_blocks`：**"越大越先保留、同值按 `(source, ref)`"只有一处出处**，
这里不另立一套排序规则。
"""

from collections.abc import Sequence
from math import ceil

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import RegistryKind
from pmstudio.contracts.interfaces.registry import RegistryPort
from pmstudio.contracts.invariants import order_context_blocks
from pmstudio.contracts.models.registry import ModelBody
from pmstudio.contracts.models.results import TrimResult
from pmstudio.contracts.skeleton.board import ContextBlock
from pmstudio.storage.ledger import Ledger

# 宽字符的起点：CJK 与全角标点都在这之后。
_WIDE_CHAR_FLOOR = 0x2E80


def estimate_tokens(text: str) -> int:
    """估算一段文本要多少个 token。

    **这是显式的启发式，不是真分词**：

    - 真分词要按模型定（DeepSeek 与 GPT 的 BPE 不一样），而模型到 R1.3 才接；
    - 口径偏保守——宽字符（中文/全角）按 1 字 1 token，真实 BPE 对常见中文约 0.6–1 token/字；
      其余按 4 字符 1 token。宁可少带一点，也不能"以为没超、其实超了"。

    换真分词器时**只改这一个函数**（唯一的调用点是 M24）。
    """
    wide = sum(1 for char in text if ord(char) >= _WIDE_CHAR_FLOOR)
    narrow = len(text) - wide
    return wide + ceil(narrow / 4)


class BudgetResolver:
    """把预算算出来：模型窗口 − 输出预留 − 系统开销。"""

    def __init__(self, registry: RegistryPort, ledger: Ledger) -> None:
        self._registry = registry
        self._ledger = ledger

    async def budget_for(self, project_id: str, model_ref: str) -> int:
        model = await self._registry.resolve(RegistryKind.MODEL, model_ref)
        window = ModelBody.model_validate(model.content).context_window_tokens

        project = self._ledger.read_project(project_id)
        if project is None:
            raise ContractViolation(f"项目 {project_id} 不存在，算不出预算")
        return (
            window
            - project.config.output_reserve_tokens
            - project.config.system_overhead_tokens
        )


class Trimmer:
    """`HarnessPort.trim` 的实现（M24）。"""

    def __init__(self, resolver: BudgetResolver) -> None:
        self._resolver = resolver

    async def trim(
        self,
        blocks: Sequence[ContextBlock],
        project_id: str,
        model_ref: str,
    ) -> TrimResult:
        budget = await self._resolver.budget_for(project_id, model_ref)

        kept: list[ContextBlock] = []
        dropped: list[ContextBlock] = []
        used = 0
        for block in order_context_blocks(blocks):
            cost = estimate_tokens(block.content)
            # 第一块永远留下：裁到空上下文比超一点预算更糟（复述只能靠猜）
            if kept and used + cost > budget:
                dropped.append(block)
                continue
            kept.append(block)
            used += cost
        return TrimResult(blocks=tuple(kept), dropped=tuple(dropped))
