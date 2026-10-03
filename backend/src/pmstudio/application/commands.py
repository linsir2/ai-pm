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
    #: 这一轮带进来的材料（用户在上传框里挑的）；空 = 不带材料
    material_ids: tuple[str, ...] = ()
    seats: tuple[str, ...] = ()
    #: 用户在本轮复述卡上勾中的联网检索词；空 = 不联网（默认）
    web_queries: tuple[str, ...] = ()
    claim_edits: tuple[ClaimEdit, ...] = ()
    answers: tuple[AnswerInput, ...] = ()
    skipped: tuple[str, ...] = ()
    form_id: str = ""
    accepted: tuple[str, ...] = ()
    #: 用户在草案卡上勾中的**记忆提议** id（`mem#1`…）；空 = 这一轮不写记忆（M6）
    accepted_memory: tuple[str, ...] = ()
