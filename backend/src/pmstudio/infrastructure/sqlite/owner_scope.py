"""环境 owner 上下文（多用户改造 Task 5 / D2）。

为什么不是"每个读方法都必须显式传 owner"：那样要打断 160+ 处调用点，还会把身份
捅进 `domain/services/committer.py`（plan §0 明令 **domain 零改动**）。D2 把 owner 做成
**可从环境取**的上下文值：仓储方法可以显式传（`*, owner=`），没传就取这里的 contextvar。

- **读写都必须有归属**：读按 owner 过滤（`AND owner_id = ?`），写按 owner 盖章（`owner_id`）。
- **两个都没有 = 程序员漏传**：抛 `ApplicationError(code="missing_owner_scope")`（500 级），
  绝不静默当成"无主"，也绝不退化成 404（那是"资源不在"的语义）。
- `contextvar` 而非全局变量：单事件循环里按 async 任务隔离（plan §0.9：单 worker、全 async）。
  生产由 `require_user`（Task 6）在每个请求里 `set_owner_scope`；测试由 conftest 装默认值。
"""

from contextvars import ContextVar, Token

from pmstudio.application.errors import ApplicationError
from pmstudio.domain.shared.ids import UserId

_current: ContextVar[UserId | None] = ContextVar("pmstudio_owner_scope", default=None)


def set_owner_scope(owner: UserId) -> Token[UserId | None]:
    """把当前上下文（async 任务）的 owner 设成 `owner`，返回可还原的 token。"""

    return _current.set(owner)


def current_owner_scope() -> UserId | None:
    """当前上下文的 owner；没设过就是 `None`。"""

    return _current.get()


def clear_owner_scope(token: Token[UserId | None] | None = None) -> None:
    """还原上下文：给了 `set_owner_scope` 的 token 就还原到之前的值，否则清空。"""

    if token is None:
        _current.set(None)
    else:
        _current.reset(token)


def require_owner(owner: UserId | None) -> UserId:
    """显式 owner 优先；没传就取环境；两个都没有 → 抛 `missing_owner_scope`。"""

    resolved = owner if owner else current_owner_scope()
    if not resolved:
        raise ApplicationError(
            "内容仓储的读/写缺少 owner：显式传 owner，或先 set_owner_scope",
            code="missing_owner_scope",
        )
    return resolved


def optional_owner(owner: UserId | None) -> UserId | None:
    """同 `require_owner`，但**允许没有**：拿不到就返回 `None` 而不是抛。

    只给"查询本身就要包含全局（无主）行"的读用——目前只有 `templates.list()`：
    builtin 模板的 `owner_id` 恒为 NULL、出厂即人人可见，而 `build_container`
    在组装期就要读它一次，那会儿还没有任何身份（`require_user` 只在请求里设 scope）。
    调用方**必须**按 `None` 与非 `None` 分两条 SQL，别拿 `None` 去拼 `owner_id = ?`。
    """

    return owner if owner else current_owner_scope()
