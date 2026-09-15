"""L0 注册中心对外暴露什么。

写入口只有这里（`register` / `update_status` / `remove`）：M30 要改 skill 状态也只能经它。
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pmstudio.contracts.enums import RegistryKind, RegistryStatus
from pmstudio.contracts.models.registry import RegistryEntry


@runtime_checkable
class RegistryPort(Protocol):
    """定义类的读与维护。权限判定不在这里（在 L6）。"""

    async def resolve(self, kind: RegistryKind, id: str) -> RegistryEntry:
        """按种类与标识取一条定义。找不到要抛错，不返回 None 让调用方猜。"""
        ...

    async def list(self, kind: RegistryKind, tags: Sequence[str] = ()) -> tuple[RegistryEntry, ...]:
        """按种类与标签查。M10 就是靠 `tags` 挑工具的。"""
        ...

    async def register(self, entry: RegistryEntry) -> str:
        """注册一条定义，返回 id。校验（三件套、白名单）由 M27 做，形状在 C10 的模型里。"""
        ...

    async def update_status(self, id: str, status: RegistryStatus) -> None:
        """改状态。改完发 `registry.updated`。"""
        ...

    async def remove(self, id: str) -> None:
        """用户删掉 skill / 角色 / prompt。删了就删了，不软删除。"""
        ...
