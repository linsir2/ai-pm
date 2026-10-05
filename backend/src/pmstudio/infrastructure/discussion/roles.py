"""讨论固定角色：三个写死在一个 TOML 里的身份（M9-T2）。

席位分文件（10 个独立演进），这里三个固定角色同进退 → 单文件三表。
`role_id` 命名空间 `dlg.*`，与席位 `seat.*` 不重叠（同线程不变角，"请席位进场"＝另开一条）。
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

#: 角色表。**定义在它所指向的资源旁边**（与 `harness.prompt.PROMPTS_DIR` 同一个理由）。
ROLES_PATH = Path(__file__).with_name("roles.toml")


@dataclass(frozen=True, slots=True)
class DiscussionRole:
    role_id: str
    title: str
    focus: str


def load_discussion_roles(path: Path = ROLES_PATH) -> tuple[DiscussionRole, ...]:
    """读 `roles.toml` 的 `[[roles]]` 三表；顺序即开聊卡上的三选顺序。

    **只有 id / title / focus 三个键**：角色怎么说话由提示词决定
    （`harness/prompts/discussion_*.md`），表里再加"配置"就没人消费了。
    """

    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(
        DiscussionRole(
            role_id=item["id"],
            title=item["title"],
            focus=item["focus"],
        )
        for item in data.get("roles", ())
    )


def discussion_role_by_id(
    roles: tuple[DiscussionRole, ...], role_id: str
) -> DiscussionRole:
    """按 id 取角色；没有 → `KeyError`（调用方转译成 `unknown_role`）。"""

    for role in roles:
        if role.role_id == role_id:
            return role
    raise KeyError(role_id)
