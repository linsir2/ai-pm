"""13 章的"这一章是干什么的"——界面的章节头显示它（`04-未决.md` F2）。

真相源只有这里一份：后端 `SectionSpec.purpose` → `GET /schema` → 前端章节头。
"""

from pmstudio.domain.shared.sections import SECTION_BY_ID, SECTIONS


def test_every_section_says_what_it_is_for():
    assert len(SECTIONS) == 13
    assert all(spec.purpose.strip() for spec in SECTIONS)
    # 说明不是把标题抄一遍
    assert all(spec.purpose != spec.title for spec in SECTIONS)
    assert all(len(spec.purpose) >= 10 for spec in SECTIONS)


def test_section_purposes_are_strings_on_the_spec():
    assert SECTION_BY_ID["s5"].purpose.startswith("每个功能一棵子树")
    assert SECTION_BY_ID["s13"].purpose.startswith("不用手写")
