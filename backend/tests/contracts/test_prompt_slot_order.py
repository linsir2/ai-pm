"""提示词槽位顺序契约（`02 §11.0.55`）：**静态块在前，动态块在后**。

## 这条文件为什么存在

量出来的事实（2026-10-10，DashScope 实测）：供应商按**前缀**命中上下文缓存，
而 11 份模板的可缓存前缀短的只有 9 个字符（`seat_review`）、长的 88 个
（`analyzer`）——因为所有**只随代码变**的静态块（`$schema_json`、`$limits`、
`$labels`…）全排在动态内容之后。整份整合者提示词 20,016 字里有 8,816 字（44%）
是静态块，本该全部命中缓存，实际一个字符都命不中。

实测还确认了两条缓存机理：
- 改**开头**一个字 → 整个前缀报废（一次性重暖，之后在新前缀上恢复）；
- 只改**末尾**动态内容 → 前缀照旧命中（实测 2,816 / 3,127 = 90%）。

所以顺序是本项目唯一能自己控制的缓存杠杆。没有守卫，任何人往模板里加一段说明
都可能把新动态槽插到静态槽前面，缓存悄悄退化，而且没有任何测试会红。

## 哪些槽算静态

`schema_json` / `labels` / `sections` / `feature_fields` / `section_slots` /
`limits`——内容只随代码与文档变，同一套代码下每次调用逐字相同。

`seat_catalog` **不算**：它随本轮输入过滤（`context.render_seat_catalog`），
放进前缀反而会毁掉缓存。`validation_error` 只在重试时出现，必须排在最后。
"""

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "src" / "pmstudio" / "harness" / "prompts"

#: 只随代码变的槽（同一套代码下每次调用逐字相同）
STATIC_SLOTS = frozenset(
    {"schema_json", "labels", "sections", "feature_fields", "section_slots", "limits"}
)
#: 只在重试时出现的槽：它必须排在最后（它本身会变，且大多数调用里根本不存在）
RETRY_SLOTS = frozenset({"validation_error"})
#: 身份槽：写在开头那句"你是谁"里，**同一个实例跨轮不变**（同一个席位、同一个讨论角色），
#: 所以它们哪怕排在静态块之前也不损失缓存——同型调用之间（N 个席位并行那种）本来就共享不到。
IDENTITY_SLOTS = frozenset(
    {"seat_title", "seat_focus", "role_title", "role_focus", "persona"}
)


def _slots_of(path: Path) -> list[str]:
    return re.findall(r"\$([a-z_]+)", path.read_text(encoding="utf-8"))


def test_the_static_block_sits_at_the_head_of_every_prompt():
    """静态槽必须**连续成块**、且位于所有动态槽之前（身份槽例外）。

    要防的漂移有两种：有人往模板中间插一段新说明，把静态块冲断；有人把某节静态说明
    挪到动态内容后面。两种都会让可缓存前缀缩短，而没有任何别的测试会红。
    """

    broken = []
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        slots = _slots_of(path)
        static_at = [index for index, name in enumerate(slots) if name in STATIC_SLOTS]
        if not static_at:
            continue
        head, tail = static_at[0], static_at[-1]
        # ① 连续：静态槽之间不许夹着别的槽
        inside = [name for index, name in enumerate(slots) if head < index < tail]
        if any(name not in STATIC_SLOTS for name in inside):
            broken.append(f"{path.stem}: 静态块中间夹了 {inside}")
        # ② 在前：第一个静态槽之前，只许身份槽
        before = [
            name
            for index, name in enumerate(slots)
            if index < head and name not in IDENTITY_SLOTS
        ]
        if before:
            broken.append(f"{path.stem}: 静态块之前还有动态槽 {before}")

    assert broken == [], "这些模板的槽位顺序不对（缓存前缀会退化）：\n" + "\n".join(broken)


def test_the_retry_slot_is_the_last_one():
    broken = []
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        slots = _slots_of(path)
        if "validation_error" not in slots:
            continue
        if slots[-1] != "validation_error":
            broken.append(f"{path.stem}: 最后一个是 {slots[-1]}")

    assert broken == [], "校验错误槽必须排在最后（它只在重试时出现）：\n" + "\n".join(broken)
