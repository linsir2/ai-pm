"""三固定角色表（M9-T2）：只有 id / title / focus 三个字段。

为什么只有三个：`stance` 与 `openers` 在生产路径上**零消费者**（没有任何代码读它们），
而留在表里就是"看着有配置、其实没人用"——第二真相源的温床。真正决定角色说话方式的
是提示词（`harness/prompts/discussion_*.md`），不是这张表。
"""

import dataclasses

import pytest

from pmstudio.infrastructure.discussion.roles import (
    ROLES_PATH,
    DiscussionRole,
    discussion_role_by_id,
    load_discussion_roles,
)


def test_role_table_has_exactly_three_fields():
    """删掉 `stance` / `openers`：字段表就是这三个（多一个就说明又有人加了配置）。"""

    assert [item.name for item in dataclasses.fields(DiscussionRole)] == [
        "role_id",
        "title",
        "focus",
    ]


def test_role_table_carries_no_unused_keys():
    """`roles.toml` 里也不许再留 `stance` / `openers` 两行。"""

    text = ROLES_PATH.read_text(encoding="utf-8")

    assert "stance" not in text
    assert "openers" not in text


def test_loader_reads_the_three_roles_in_order():
    roles = load_discussion_roles()

    assert [item.role_id for item in roles] == [
        "dlg.challenger",
        "dlg.user_voice",
        "dlg.analogist",
    ]
    assert all(item.title.strip() and item.focus.strip() for item in roles)


def test_lookup_by_id_and_rejects_unknown():
    roles = load_discussion_roles()

    assert discussion_role_by_id(roles, "dlg.analogist").title == "类比者"
    with pytest.raises(KeyError):
        discussion_role_by_id(roles, "dlg.nobody")