"""种子数据：启动时把 `backend/seeds/*.json` 灌进注册中心。

**`seeds/` 只是初始数据，不是运行时真相源**——模板 schema 的归属是注册中心（DECISIONS v0.5）。

文件格式就是一条 `RegistryEntry` 的 JSON（`id` / `kind` / `name` / `content` / `owner`）。
**种子条目的 id 由数据文件给定**，不用 `<前缀>_<时间>_<随机>` 那套：`Document.template_id`
会长期存着它，每次启动换一个 id 就等于把已建文档指向一个不存在的模板（I21）。
用户 / 系统新建的条目才走 `IdGenerator`——那条路今天还没有（等 R6 的复利闭环）。
"""

import json
from pathlib import Path

from pmstudio.contracts.interfaces.registry import RegistryPort
from pmstudio.contracts.models.registry import RegistryEntry


def load_entries(directory: Path) -> tuple[RegistryEntry, ...]:
    """读目录下所有 `*.json`，一个文件一条条目。按文件名排序，装载顺序可复现。"""
    return tuple(
        RegistryEntry.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.json"))
    )


async def seed(registry: RegistryPort, directory: Path) -> tuple[str, ...]:
    """把种子登记进注册中心，返回登记进去的 id（启动日志与测试都用它）。

    走的是 `register` 的正门——种子和其他条目受同一套校验，坏掉的种子在启动时就炸。
    """
    registered: list[str] = []
    for entry in load_entries(directory):
        registered.append(await registry.register(entry))
    return tuple(registered)
