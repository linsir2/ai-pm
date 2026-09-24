"""应用层错误。领域错误在 pmstudio.domain.shared.errors，两者不许互相继承。"""

from collections.abc import Sequence


def unknown_ref_message(
    unknown: Sequence[str], *, what: str = "引用了不存在的编号"
) -> str:
    """引用了账本里没有的编号时给模型看的话。

    单独做一条是因为真模型踩过：它把席位条目地址（`seat.business_rules:O1`）当编号写进
    `evidence_refs`。重装那一次要能看懂"错在哪、该改成什么"，只报"编号不存在"它只会重犯
    （`04-未决.md` C9）。
    """

    listed = "、".join(unknown)
    if any(":" in item for item in unknown):
        return (
            f"{what}：{listed}；`seat.xxx:O1` 是席位条目地址，"
            "不是出处编号——请改引它「依据」里那些 E 编号，或者留空"
        )
    return f"{what}：{listed}"


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


class ModelCallFailed(ApplicationError):
    """网关调用本身失败（超时 / 鉴权 / 供应商错误）。"""

    def __init__(self, message: str, *, purpose: str, **details: object) -> None:
        super().__init__(message, code="model_call_failed", purpose=purpose, **details)
        self.purpose = purpose


class UnknownProject(ApplicationError):
    def __init__(self, project_id: object) -> None:
        super().__init__(
            f"项目不存在：{project_id}", code="unknown_project", project_id=str(project_id)
        )


class UnknownRound(ApplicationError):
    def __init__(self, round_id: object) -> None:
        super().__init__(
            f"找不到这一轮：{round_id}", code="unknown_round", round_id=str(round_id)
        )


class RoundBusy(ApplicationError):
    """同一个 round 上已经有一个动作在跑。

    单用户不代表不会有并发：双击"继续/重试"、开两个标签页、前端自动重发都会。
    真出现过一次：两个重试同时跑，第二个记失败时 step 已经是 failed，
    续跑点被写成 failed，这一轮从此恢复不了（`04-未决.md` C9）。
    """

    def __init__(self, round_id: object) -> None:
        super().__init__(
            "这一轮正在处理中：同一个动作别点第二次，等它跑完",
            code="round_busy",
            round_id=str(round_id),
        )
