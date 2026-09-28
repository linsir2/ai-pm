"""答案轮缩编：下一趟只请"与这批题有关"的席位（`04` C8 / `02` §7.1）。

纯函数：不碰聚合、不碰 IO、不调模型。名单永远在**用户编制**之内（R7 不许破），
命中为空就全跑——**宁多勿缺**：多跑一个席位是钱，漏一个面是缺陷。

两条命中路径：

1. **归因**：这道题是哪些席位提出来的（`question.asked_by`，装配层算的，不信模型自填）；
   席位意见本身没有 target，所以"上一趟提过这个面"只能从题上来。
2. **映射**：席位 TOML 里写的 `sections` / `fields` 命中这道题的 target（`01` F16）。
"""

from collections.abc import Sequence

from pmstudio.application.ports import SeatCatalog, SeatDefinition
from pmstudio.domain.round.questionnaire import Questionnaire
from pmstudio.domain.shared.ids import QuestionId
from pmstudio.domain.shared.target import Target


def narrow_seats(
    *,
    seats: Sequence[str],
    catalog: SeatCatalog,
    questionnaire: Questionnaire,
    touched: Sequence[QuestionId],
) -> tuple[str, ...]:
    """下一趟要跑的席位；`seats` 是用户编制（结果一定是它的子集，按原顺序）。"""

    touched_set = set(touched)
    questions = [
        question
        for question in questionnaire.questions
        if question.question_id in touched_set
    ]
    if not questions:
        return tuple(seats)

    targets = [question.target for question in questions]
    by_attribution = {seat for question in questions for seat in question.asked_by}
    by_mapping = {
        seat.seat_id
        for seat in catalog.all()
        if any(_covers(seat, target) for target in targets)
    }

    narrowed = tuple(
        seat for seat in seats if seat in by_attribution or seat in by_mapping
    )
    return narrowed or tuple(seats)


def _covers(seat: SeatDefinition, target: Target) -> bool:
    if target.section_id is None:  # 偏好区：没有任何席位的映射管它
        return False
    if str(target.section_id) in seat.sections:
        return True
    return target.field is not None and target.field in seat.fields
