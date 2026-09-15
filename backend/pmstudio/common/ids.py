"""标识生成端口。

规格（directory.md §3.3）：`<类型前缀>_<UTC 时间 YYYYMMDDTHHMMSS>_<8 位随机十六进制>`。
只有一个来源——引用靠 id 定位（I21），两层各生成一套 id 的话引用立刻对不上。
"""

import secrets
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

# 前缀词表：与 directory.md §3.3 一一对应。
PROJECT = "prj"
DOCUMENT = "doc"
BLOCK = "blk"
VERSION = "ver"
ROUND = "rnd"
CARD_GROUP = "grp"
CARD = "crd"
PROPOSAL = "prp"
OPTION = "opt"
MEMORY = "mry"
MATERIAL = "mat"
IDEA = "ide"
MESSAGE = "msg"
SUMMARY = "sum"
TRACE = "trc"
REGISTRY = "reg"
PACKET = "pkt"
CLAIM = "clm"

ALL_PREFIXES = frozenset(
    {
        PROJECT,
        DOCUMENT,
        BLOCK,
        VERSION,
        ROUND,
        CARD_GROUP,
        CARD,
        PROPOSAL,
        OPTION,
        MEMORY,
        MATERIAL,
        IDEA,
        MESSAGE,
        SUMMARY,
        TRACE,
        REGISTRY,
        PACKET,
        CLAIM,
    }
)


@runtime_checkable
class IdGenerator(Protocol):
    """按前缀造一个新 id。"""

    def new_id(self, prefix: str) -> str: ...


class TimestampIdGenerator:
    """真实现：UTC 时间前缀（可读、可排序）+ 随机后缀（同一秒内不撞）。"""

    def new_id(self, prefix: str) -> str:
        if prefix not in ALL_PREFIXES:
            raise ValueError(f"未知的 id 前缀：{prefix}")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        return f"{prefix}_{stamp}_{secrets.token_hex(4)}"
