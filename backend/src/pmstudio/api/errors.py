"""领域/应用错误 → HTTP 状态码。只有这一处做这个映射。"""

from pmstudio.domain.shared.errors import DomainError, ShapeError
from pmstudio.harness.errors import ApplicationError, ModelCallFailed, PacketParseError

CODE_STATUS = {
    "unknown_project": 404,
    "unknown_round": 404,
    # 读一个不存在的版本 / 编号是"资源不在"，不是形状错误
    "unknown_version": 404,
    "unknown_evidence": 404,
    # 记忆：删一条不在记忆里的东西 = "资源不在"（M6）
    "unknown_memory": 404,
    # 材料层的"资源不在"与"状态不允许"（P19）
    "unknown_material": 404,
    "material_frozen": 409,
    "model_call_failed": 502,
    "model_packet_unparsable": 502,
    "round_busy": 409,
    # 探索（M8）：找不到 / 同一项目已经有一趟在跑 / 用户指的条目不存在
    "unknown_exploration": 404,
    "unknown_entry": 404,
    "exploration_busy": 409,
    # 讨论（M9）：找不到线程 / 开聊时点的角色不存在；closed/full 走 invariant: 自动 409
    "unknown_discussion": 404,
    "unknown_role": 404,
    # 讨论 panel（M18）：同一线程已经有一轮在跑
    "discussion_busy": 409,
    # 模板库（`02` §11.0.68）：给错模板 id = "资源不在"，与材料同档
    "unknown_template": 404,
    # 内置模板锁死（`02` §11.0.70）：不是"资源不在"，是"这个资源不给动"
    "template_builtin_protected": 409,
    # 工具层（C-2）：材料读不了 = 422（是内容问题，不是服务器故障）；太大 = 413
    "unsupported_material": 422,
    "material_too_large": 413,
    # 工具调用失败 = 502（与模型调用失败同一档：上游不通）
    "tool_call_failed": 502,
    # "这个地址不能用"（不是网页 / 内网 / 没协议）= 422：是你给的东西不行，不是服务器故障
    "fetch_refused": 422,
    # 认证与账号（多用户改造）：没登录 / 没权限 / 重名 / 指不到用户
    "unauthenticated": 401,
    "forbidden": 403,
    "username_taken": 409,
    "unknown_user": 404,
    # 仓储拿不到 owner 上下文 = 程序员错误（漏设 scope），刻意 500 级，绝不退成 404
    "missing_owner_scope": 500,
}


def status_for(error: DomainError | ApplicationError) -> int:
    if error.code in CODE_STATUS:
        return CODE_STATUS[error.code]
    if isinstance(error, (PacketParseError, ModelCallFailed)):
        return 502
    if isinstance(error, ShapeError):
        return 422
    if isinstance(error, DomainError) and error.code.startswith("invariant:"):
        return 409
    return 500


def body_for(error: DomainError | ApplicationError) -> dict[str, object]:
    return {"code": error.code, "message": error.message, "details": error.details}
