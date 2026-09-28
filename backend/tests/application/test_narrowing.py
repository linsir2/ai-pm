"""缩编规则（`04` C8 / `02` §7.1）：

    下一趟 = (① 上一趟提过这些面的席位 ∪ ② 目录映射命中这些面的席位) ∩ 用户编制

① 用 `question.asked_by`——席位意见本身没有 target，归因是装配层算在题上的；
命中为空 → 全跑（宁多勿缺：多跑一个席位是钱，漏一个面是缺陷）。
"""

from pmstudio.application.narrowing import narrow_seats
from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.round.questionnaire import Question, Questionnaire, QuestionType
from pmstudio.domain.shared.ids import FormId, QuestionId
from pmstudio.domain.shared.target import Target
from tests.support.builders import ROUND_ID
from tests.support.doubles import StaticSeatCatalog


def catalog_of(**seats) -> StaticSeatCatalog:  # type: ignore[no-untyped-def]
    """`a=("s8",)` → 造成席位 `seat.a`（sections=("s8",)）；值给 dict 时可以带 fields。"""

    built = []
    for name, value in seats.items():
        sections = value if isinstance(value, tuple) else tuple(value["sections"])
        fields = () if isinstance(value, tuple) else tuple(value.get("fields", ()))
        built.append(
            SeatDefinition(
                seat_id=f"seat.{name}", title=name, focus="", sections=sections, fields=fields
            )
        )
    return StaticSeatCatalog(tuple(built))


def questionnaire_with(
    *, asked_by: tuple[str, ...], target: Target, question_id: str = "q1"
) -> Questionnaire:
    return Questionnaire(
        form_id=FormId("f1"),
        round_id=ROUND_ID,
        questions=(
            Question(
                question_id=QuestionId(question_id),
                target=target,
                type=QuestionType.TEXT,
                title="这面怎么定？",
                why="缺一个只有你能给的信息",
                consequence_if_skipped="按默认处理",
                asked_by=asked_by,
            ),
        ),
    )


def test_seat_that_asked_the_question_stays():
    result = narrow_seats(
        seats=("seat.a", "seat.b"),
        catalog=catalog_of(a=(), b=()),
        questionnaire=questionnaire_with(asked_by=("seat.a",), target=Target.body("s8")),
        touched=(QuestionId("q1"),),
    )

    assert result == ("seat.a",)


def test_seat_whose_mapping_hits_the_target_joins_in():
    result = narrow_seats(
        seats=("seat.a", "seat.b"),
        catalog=catalog_of(a=(), b=("s8",)),
        questionnaire=questionnaire_with(asked_by=(), target=Target.body("s8")),
        touched=(QuestionId("q1"),),
    )

    assert result == ("seat.b",)


def test_field_mapping_hits_too():
    result = narrow_seats(
        seats=("seat.a", "seat.c"),
        catalog=catalog_of(a=("s1",), c={"sections": ("s5",), "fields": ("acceptance",)}),
        questionnaire=questionnaire_with(
            asked_by=(), target=Target.feature_field("order-export", "acceptance")
        ),
        touched=(QuestionId("q1"),),
    )

    assert result == ("seat.c",)


def test_nothing_hits_falls_back_to_the_whole_roster():
    result = narrow_seats(
        seats=("seat.a", "seat.b"),
        catalog=catalog_of(a=(), b=()),
        questionnaire=questionnaire_with(asked_by=(), target=Target.body("s1")),
        touched=(QuestionId("q1"),),
    )

    assert result == ("seat.a", "seat.b")


def test_only_touched_questions_count():
    """没答也没跳过的题不算（用户只答了一半时，另一半不该把它的席位拽进来）。"""

    questionnaire = Questionnaire(
        form_id=FormId("f1"),
        round_id=ROUND_ID,
        questions=(
            Question(
                question_id=QuestionId("q1"),
                target=Target.body("s8"),
                type=QuestionType.TEXT,
                title="量级？",
                why="分界",
                consequence_if_skipped="按同步",
                asked_by=("seat.a",),
            ),
            Question(
                question_id=QuestionId("q2"),
                target=Target.body("s2"),
                type=QuestionType.TEXT,
                title="目标？",
                why="目标要能验证",
                consequence_if_skipped="留空",
                asked_by=("seat.b",),
            ),
        ),
    )

    result = narrow_seats(
        seats=("seat.a", "seat.b"),
        catalog=catalog_of(a=("s8",), b=("s2",)),
        questionnaire=questionnaire,
        touched=(QuestionId("q1"),),  # 只碰了 q1
    )

    assert result == ("seat.a",)


def test_result_never_leaves_the_roster():
    result = narrow_seats(
        seats=("seat.a",),
        catalog=catalog_of(a=(), b=("s8",)),
        questionnaire=questionnaire_with(asked_by=("seat.b",), target=Target.body("s8")),
        touched=(QuestionId("q1"),),
    )

    assert result == ("seat.a",)  # seat.b 不在编制里，进不来


def test_no_touched_questions_keeps_the_whole_roster():
    result = narrow_seats(
        seats=("seat.a", "seat.b"),
        catalog=catalog_of(a=(), b=()),
        questionnaire=questionnaire_with(asked_by=(), target=Target.body("s8")),
        touched=(),
    )

    assert result == ("seat.a", "seat.b")
