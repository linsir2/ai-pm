"""运行时错误：机制在 harness，词汇在 application。

为什么错误表要拆开：`application/errors.py` 名义上是"应用层错误"，实际是**全项目错误总表**
（`infrastructure/tools/*`、`sqlite/repositories.py`、`graph.py` 都 import 它）。
其中五个类只因为"模型会胡说八道"或"外部调用会失败"而存在，搬进 harness 之后
`litellm_gateway.py` 才不必反向依赖 application——依赖方向必须单向。

留在 application 的是**有业务含义**的那些：`UnknownProject` / `UnknownRound` /
`MaterialFrozen`（举的是 E7 改指）/ `RoundBusy`，以及 `unknown_ref_message`
（席位条目地址 vs 提案编号 vs E 编号，纯业务口径）。

**`code` 字符串一个字都不许改**：`api/errors.py` 的 CODE_STATUS 表按字符串映射 HTTP 状态码。
"""


class ApplicationError(Exception):
    def __init__(self, message: str, *, code: str = "application_error", **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details


class PacketParseError(ApplicationError):
    """模型返回的报文过不了 schema 或语义校验——失败必须是失败，不许静默降级。"""

    def __init__(self, message: str, *, purpose: str, **details: object) -> None:
        super().__init__(message, code="model_packet_unparsable", purpose=purpose, **details)
        self.purpose = purpose


class ToolCallFailed(ApplicationError):
    """工具（联网检索等）调用失败：超时 / 鉴权 / 供应商错误 / 回包不是 JSON。

    与 `ModelCallFailed` 分开：工具失败**不判死整轮**（留痕继续跑），模型失败才走重试那条路。
    """

    def __init__(
        self, message: str, *, tool_id: str, status: int | None = None, **details: object
    ) -> None:
        super().__init__(
            message, code="tool_call_failed", tool_id=tool_id, status=status, **details
        )
        self.tool_id = tool_id
        self.status = status


class FetchRefused(ApplicationError):
    """这个地址**不能用**：不是 http/https、带账号密码、内网地址、返回的不是网页、页面太大。

    与 `ToolCallFailed` 分开：那是"抓的时候出了问题"（超时 / 上游 5xx，502），
    这是"你给的东西本身不行"（422）——界面照样只显示 message，但码不同，复盘时一眼分得清。
    """

    def __init__(self, message: str, *, tool_id: str = "tool.web_fetch", **details: object) -> None:
        super().__init__(message, code="fetch_refused", tool_id=tool_id, **details)




class ModelCallFailed(ApplicationError):
    """网关调用本身失败（超时 / 鉴权 / 供应商错误）。

    `hint` 只给**模型自己能修**的那两种失败（空回复 / 不是 JSON）：重试时它会被递成
    `validation_error`，省掉一次盲调。供应商那类（超时 / 5xx / 鉴权）不给——递了没用。
    """

    def __init__(
        self, message: str, *, purpose: str, hint: str | None = None, **details: object
    ) -> None:
        super().__init__(message, code="model_call_failed", purpose=purpose, **details)
        self.purpose = purpose
        self.hint = hint


class ModelShed(ModelCallFailed):
    """**过载保护主动拒绝了这次调用**（熔断开闸 / 隔离舱满 / 重试预算耗尽）。

    和 `ModelCallFailed` 分开是有意的：那个说"上游真的失败了"（502），
    这个说"**我们没让它发出去**"——上游可能完全健康，是我们自己判断现在接不了。
    语义上属于 503 而不是 502：客户端应该**过一会儿再来**，不是重试上游。
    """

    def __init__(
        self, message: str, *, purpose: str, retry_after: int = 30, **details: object
    ) -> None:
        super().__init__(message, purpose=purpose, **details)
        self.code = "model_shed"
        #: 告诉客户端**多久之后再试**（秒）。给一个数而不是让人猜：
        #: 熔断的恢复窗口是配置值，客户端不知道就该硬重试，把我们刚卸掉的负载又打回来。
        self.retry_after = retry_after


class RateLimited(ApplicationError):
    """**请求太快被闸门挡下**（429）。

    与 `ModelShed`（503）同一个形状：都在"我们主动拒绝"这一侧，都带
    `retry_after` 让客户端知道等多久。区别是那个说的是模型通道，
    这个说的是整条 API 的入口速率。

    **刻意不复用 401**：登录失败锁返回的是 401（要挡用户名枚举，不能改），
    而尝试超限说的是速率、不是凭证——用 429 才不会把两件事混成一个码。
    """

    def __init__(self, message: str, *, retry_after: int, **details: object) -> None:
        super().__init__(message, code="rate_limited", **details)
        #: 秒。`0` 或负数等于没告诉客户端任何事，它只会硬重试。
        self.retry_after = retry_after


class DatabaseBusy(ApplicationError):
    """**数据库写锁等不到**（多 worker 下的常态之一）——503 + `Retry-After`。

    与 `ModelShed` 同一形状：都是"我们主动拒绝、过一会儿再来"，都带
    `retry_after`。区别只在位置：那个说的是模型通道，这个说的是存储。

    **为什么不能是 500**：等锁超时是**暂时性**的——持锁者是个短事务，多半早就
    结束了。500 传达的信息是"你自己或数据有问题"，于是客户端重试也重试
    （还是 500）。`Retry-After` 才是这个场景的标准答案。

    `retry_after` 取 3 秒：业务写已经等锁耗掉 10 秒（`database.py` 的
    `timeout=10.0`），说明持锁者多半已经结束；再让客户端等 10 秒过于悲观，
    而不给数等于让它硬重试——那会制造重试风暴。**刻意做成常量而不是配置**：
    这是一个"客户端该等多久"的提示，而我们并不真知道竞争写要多久，
    做成旋钮等于让人瞎调。
    """

    def __init__(self, message: str, *, retry_after: int = 3, **details: object) -> None:
        super().__init__(message, code="database_busy", **details)
        self.retry_after = retry_after


class StorageFull(ApplicationError):
    """**磁盘满了**——RFC 4918 的 507 `Insufficient Storage`。

    既不是 `DatabaseBusy`（等锁，会自己解开）也不是代码 bug（库没坏、数据没丢），
    是**容量耗尽**：应用好着，只是没地方写了。

    ## 为什么不复用 503

    判据只有一个：**`Retry-After` 那个头兑现得起吗？**

    - 写锁：持锁者是短事务，多半早就结束了 → 我们**兑现得起** `Retry-After: 3`。
    - 磁盘满：得等**有人在删文件** → **兑现不起**。给 `Retry-After: 3`
      等于教客户端在一个永远不会好的结果上重试，把满盘打得更死。

    RFC 4918 §11.5 就是为这件事定义的码，它明确表达"不是暂时的，别重试"。

    ## 为什么不复用 500

    磁盘满是**运维事件**，不是代码 bug。回 500 会污染错误告警（真事故里
    "磁盘满"和"我们写错了"混在同一堆 500 里，前者该叫人腾空间、后者该叫人
    改代码），也与 P5b 刚定的"容量问题回 503 而不是 500"原则自相矛盾。

    **刻意没有 `retry_after` 字段**：与 `DatabaseBusy` 刻意区分开，
    让"这个错误该不该教客户端重试"在类型上就分得清——不是靠调用点记得判断。
    """

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message, code="storage_full", **details)
        #: **永远是 `None`**：兑现不起的等待不叫 `Retry-After`，叫骗人。
        self.retry_after: int | None = None
