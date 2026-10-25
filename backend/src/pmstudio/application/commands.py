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
    #: 这一轮从过目台带进来的调研产物（`"x-xxxx#H1"`）；空 = 不是从探索来的（M8）
    seed_ids: tuple[str, ...] = ()
    seats: tuple[str, ...] = ()
    #: 用户在本轮复述卡上勾中的联网检索词；空 = 不联网（默认）
    web_queries: tuple[str, ...] = ()
    #: 逐条批准/拒绝的联网申请 id（`w1`…）；只用于 `decide_web_request`
    request_id: str = ""
    #: `approve` | `reject`；只用于 `decide_web_request`
    decision: str = ""
    #: 开场卡上的自动联网开关（只自动批准 seat/integrator 的后续申请）
    allow_web: bool = False
    #: 开场卡上逐条批准的新载体（与旧 `web_queries` 取并集、幂等）
    approved_request_ids: tuple[str, ...] = ()
    claim_edits: tuple[ClaimEdit, ...] = ()
    answers: tuple[AnswerInput, ...] = ()
    skipped: tuple[str, ...] = ()
    form_id: str = ""
    accepted: tuple[str, ...] = ()
    #: 用户在草案卡上勾中的**记忆提议** id（`mem#1`…）；空 = 这一轮不写记忆（M6）
    accepted_memory: tuple[str, ...] = ()
    #: 模拟评测（M11）：选中的模拟使用者与要查的功能（不碰 step 的轮内动作）
    persona_ids: tuple[str, ...] = ()
    feature_slugs: tuple[str, ...] = ()
    #: 「带进本轮」（P1.3）：材料 id + 要带的块（一次动作的参数，不是聚合字段）
    material_id: str = ""
    chunk_ids: tuple[str, ...] = ()
