"""七种标签。标签决定处理方式：偏好的去处是偏好区，猜测要升格为假设。"""

from enum import StrEnum


class Zone(StrEnum):
    BODY = "body"
    PREFERENCE = "preference"


class ClaimLabel(StrEnum):
    FACT = "fact"
    NEED = "need"
    GOAL = "goal"
    CONSTRAINT = "constraint"
    PREFERENCE = "preference"
    GUESS = "guess"
    DECISION = "decision"


PREFERENCE_LABELS = frozenset({ClaimLabel.PREFERENCE})


def zone_for(label: ClaimLabel) -> Zone:
    return Zone.PREFERENCE if label in PREFERENCE_LABELS else Zone.BODY
