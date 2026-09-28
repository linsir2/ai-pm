"""版本三件（`01` F1/F3、`04` D6）：列得出来、比得清楚、回得去，而且回退也走唯一写路径。"""

import pytest

from pmstudio.application.events import DOC_CHANGED
from pmstudio.domain.shared.errors import UnknownVersion
from tests.support.builders import PROJECT, document_with_feature
from tests.support.harness import Harness


@pytest.fixture()
def world():
    """一个已经有一版内容（v2）的项目。"""

    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    harness.documents.seed(document_with_feature())
    return harness


async def test_list_versions_comes_from_the_aggregate(world):
    views = await world.get_versions()(project_id=PROJECT)

    assert [view.version_no for view in views.versions] == [1, 2]
    assert views.current == 2
    assert views.versions[0].summary == "初始化 13 章骨架"
    assert views.versions[0].trigger_label == "手工编辑"
    assert views.versions[1].trigger == "confirm"
    assert views.versions[1].trigger_label == "草案确认"


async def test_diff_between_two_versions_names_the_target(world):
    view = await world.get_version_diff()(
        project_id=PROJECT, from_version_no=1, to_version_no=2
    )

    assert (view.from_version_no, view.to_version_no) == (1, 2)
    assert [(group.target_label, len(group.added)) for group in view.groups] == [
        ("5. 功能需求 · order-export · 一句话描述", 1)
    ]
    assert view.groups[0].added[0].refs == ("E1",)


async def test_diff_of_an_unknown_version_is_an_error(world):
    with pytest.raises(UnknownVersion):
        await world.get_version_diff()(
            project_id=PROJECT, from_version_no=1, to_version_no=99
        )


async def test_rollback_creates_a_new_version_and_publishes_doc_changed(world):
    result = await world.rollback_version()(project_id=PROJECT, version_no=1)

    assert result.version_no == 3
    assert result.document.sections[4].blocks == ()  # 回到空骨架（s5 一条内容都没有）
    assert result.document.version_no == 3
    assert world.events.names == [DOC_CHANGED]

    versions = await world.get_versions()(project_id=PROJECT)
    assert [view.trigger for view in versions.versions] == ["manual", "confirm", "rollback"]
    assert versions.versions[1].summary == "干跑"  # 版本只增不改：v2 的记录还在


async def test_rollback_to_an_unknown_version_is_an_error(world):
    with pytest.raises(UnknownVersion):
        await world.rollback_version()(project_id=PROJECT, version_no=42)

    assert world.documents.saves == 0  # seed 不算 save：失败不写盘
