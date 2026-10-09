"""L3 灵感档案：InspirationItem 数据类型 + InspirationOrigin 枚举。"""

import pytest

from pmstudio.domain.memory.inspiration import InspirationItem, InspirationOrigin


class TestInspirationOrigin:
    """InspirationOrigin: 四种来源。"""

    def test_values(self):
        assert InspirationOrigin.DISCUSSION == "discussion"
        assert InspirationOrigin.EXPLORATION == "exploration"
        assert InspirationOrigin.SIM_INQUIRY == "sim_inquiry"
        assert InspirationOrigin.SIM_REVIEW == "sim_review"


class TestInspirationItem:
    """InspirationItem: 不可变数据类。"""

    def test_frozen(self):
        item = InspirationItem(
            item_id="ins-1",
            project_id="p1",
            origin=InspirationOrigin.DISCUSSION,
            statement="团长流失率15%",
        )
        with pytest.raises(AttributeError):
            item.statement = "新内容"

    def test_default_fields(self):
        item = InspirationItem(
            item_id="ins-1",
            project_id="p1",
            origin=InspirationOrigin.DISCUSSION,
            statement="团长流失率15%",
        )
        assert item.context is None

    def test_with_context(self):
        item = InspirationItem(
            item_id="ins-1",
            project_id="p1",
            origin=InspirationOrigin.EXPLORATION,
            statement="用户最在意配送时效",
            context="如果用户频繁比价则推翻",
        )
        assert item.context == "如果用户频繁比价则推翻"
