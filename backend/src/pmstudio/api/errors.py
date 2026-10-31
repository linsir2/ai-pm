"""领域/应用错误 → HTTP 状态码。只有这一处做这个映射。"""

from pmstudio.domain.shared.errors import DomainError, ShapeError
from pmstudio.harness.errors import (
    ApplicationError,
    DatabaseBusy,
    DatabaseUnavailable,
    ModelCallFailed,
    PacketParseError,
    StorageFull,
)

CODE_STATUS = {
    "draft_mismatch": 409,
    "baseline_missing": 409,
    "proposal_stale": 409,
    "batch_conflict": 409,
    "unknown_project": 404,
    "document_version_conflict": 409,
    "unknown_round": 404,
    # 读一个不存在的版本 / 编号是"资源不在"，不是形状错误
    "unknown_version": 404,
    "unknown_evidence": 404,
    # 记忆：删一条不在记忆里的东西 = "资源不在"（M6）
    "unknown_memory": 404,
    # 材料层的"资源不在"与"状态不允许"（P19）
    "unknown_material": 404,
    "material_frozen": 409,
    # 带料：状态不允许 = 409；联网材料走别的路 = 422（你给的东西不对）
    "round_not_attachable": 409,
    "web_material_not_attachable": 422,
    # 管理动作落在 admin 头上（含自封）：请求本身合法、状态不允许 → 409。
    # 用 400 是"请求有问题"，用 403 是"你没权限"——都不是这两件事。
    "admin_protected": 409,
    # 用户跳过一个本趟已交回的席位：同上，"合法但状态不允许"。
    "seat_already_reviewed": 409,
    # 磁盘满（RFC 4918 §11.5）：不是暂时的——别给 `Retry-After`，
    # 重试要等**有人腾磁盘**，而客户端等不了那件事发生。
    "storage_full": 507,
    "model_call_failed": 502,
    "model_packet_unparsable": 502,
    "round_busy": 409,
    # 租约（P3b）：fencing 失败或心跳失租都是"同一个资源被别人接管了"——409。
    "lease_lost": 409,
    "round_lease_lost": 409,
    "exploration_lease_lost": 409,
    # 联网申请决策（P3a）：申请不在这一轮 = 404；已决策 / 轮终态 / 名额满 = 409
    "unknown_web_request": 404,
    "web_request_not_pending": 409,
    "web_execution_limit_reached": 409,
    # 超额已批准：先逐条恢复（执行 / 放弃）再继续；恢复动作本身不适用时也是 409
    "web_request_recovery_required": 409,
    "web_request_not_recoverable": 409,
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
    # 过载保护主动卸载（熔断开闸 / 隔离舱满 / 重试预算耗尽）= 503，不是 502：
    # 上游可能好着，是我们没让它发出去。客户端该"过会儿再来"，不是重试上游。
    "model_shed": 503,
    # 数据库写锁等不到 = 503。**不是 500**：等锁超时是暂时性的（持锁者是个短事务），
    # 500 会让客户端以为是自己或数据坏了，于是重试也重试。
    "database_busy": 503,
    "database_unavailable": 503,
    # 速率闸（登录尝试超限）= 429。**刻意不并进 401**：401 已被登录失败锁占用，
    # 且并进去会让"凭证不对"和"你太快了"变成同一个码——客户端没法决定是该
    # 换密码还是该等 Retry-After。
    "rate_limited": 429,
    # 仓储拿不到 owner 上下文 = 程序员错误（漏设 scope），刻意 500 级，绝不退成 404
    "missing_owner_scope": 500,
}


#: 锁等待 / 序列化失败 / 死锁 / 语句取消：都是"短事务竞争者"造成的暂时失败。
_RETRYABLE_SQLSTATES = frozenset({"55P03", "40001", "40P01", "57014"})
#: 连接类（08 类）与运维主动断开。`PgConnection` 已包一层；这里兜住未包装的路径。
_CONNECTION_SQLSTATES = frozenset({"08001", "08003", "08006", "57P01", "57P02", "57P03"})


def error_for_sqlstate(error: BaseException) -> ApplicationError | None:
    """PostgreSQL SQLSTATE → 应用错误。**只认 SQLSTATE，不解析文本**。

    - 55P03/40001/40P01/57014 → 503 + Retry-After（等一会儿重试）
    - 08 类 / 57P01-03 → 503 database_unavailable
    - 25P02（事务已中止）→ 503：调用方应重试整笔；UoW 负责回滚
    - 53100 → 507 storage_full（重试无用，要人腾空间）
    - 其它 → `None`（交给 500，不拿文本猜）
    """

    state = getattr(error, "sqlstate", None)
    if not isinstance(state, str):
        return None
    if state == "53100":
        return StorageFull(
            "磁盘空间不足，这次改动没能保存——请腾出空间后重试。"
            "已经确认写进去的内容不受影响。"
        )
    if state in _RETRYABLE_SQLSTATES:
        return DatabaseBusy("数据库正忙，请稍后重试", retry_after=3)
    if state in _CONNECTION_SQLSTATES or state.startswith("08"):
        return DatabaseUnavailable("数据库连接不可用，请稍后重试", retry_after=3)
    if state == "25P02":
        return DatabaseUnavailable(
            "事务已中止并回滚；请重试整笔操作", retry_after=3
        )
    return None


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
