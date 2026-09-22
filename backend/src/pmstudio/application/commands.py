"""一次用户动作的命令（编排层的输入）。

它属于应用层：`api` 构造它、`infrastructure/graph` 消费它——两边都只依赖 application，
所以分层守卫（api 不许 import infrastructure）能保持全绿。
"""

from dataclasses import dataclass

from pmstudio.application.use_cases import AnswerInput, ClaimEdit


@dataclass(frozen=True, slots=True)
class RoundCommand:
    kind: str  # start | submit_opening | submit_answers | confirm | retry
    project_id: str | None = None
    round_id: str | None = None
    user_input: str = ""
    seats: tuple[str, ...] = ()
    claim_edits: tuple[ClaimEdit, ...] = ()
    answers: tuple[AnswerInput, ...] = ()
    skipped: tuple[str, ...] = ()
    form_id: str = ""
    accepted: tuple[str, ...] = ()
