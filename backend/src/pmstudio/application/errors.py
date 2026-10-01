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
    if any(item.startswith("r-") and "#" in item for item in unknown):
        return (
            f"{what}：{listed}；`r-xxxx#01` 是**提案编号**，不是出处编号——"
            "出处编号只在上面那份清单里（形如 E12）"
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


class UnsupportedMaterial(ApplicationError):
    """这份材料读不了（类型不支持，或者里面抽不出文字）。

    **明确报错，不静默存空**：抽不出文字的材料如果悄悄存成空包，后面所有人都会以为
    "这份材料读过了"（工具层 C-2 的口径之一）。
    """

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message, code="unsupported_material", **details)


class MaterialTooLarge(ApplicationError):
    """材料超过大小上限（单份 / 单项目 / 单轮）。"""

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message, code="material_too_large", **details)


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


class UnknownEvidenceId(ApplicationError):
    """查询用：这个项目里没有这个编号。

    与 `domain` 的 `UnknownEvidence` 不是一回事——那是"内容引用了不存在的编号"
    （不变量违规，409）；这里是"你要查的这个编号不存在"（资源不在，404）。
    """

    def __init__(self, eid: object) -> None:
        super().__init__(f"这个项目里没有编号 {eid}", code="unknown_evidence", eid=str(eid))


class UnknownMaterial(ApplicationError):
    """这个项目里没有这份材料（删/重解析时给错了 id）——资源不在，404。"""

    def __init__(self, material_id: object) -> None:
        super().__init__(
            f"这个项目里没有这份材料：{material_id}",
            code="unknown_material",
            material_id=str(material_id),
        )


class MaterialFrozen(ApplicationError):
    """这份材料已经发过编号：**编号发出即冻结**（`P19`）。

    编号指向它里面的块——删了 `E7` 会指向空气；就地改形状会让 `E7` 改指别的内容。
    所以被引用过的材料只能**留着**（要用新解析器读，走"另出一份"那条路）。
    """

    def __init__(self, material_id: object, *, eids: tuple[str, ...], action: str) -> None:
        listed = "、".join(eids)
        super().__init__(
            f"这份材料已经发过编号 {listed}，不能{action}"
            "——编号指向它里面的块，不能回收、也不能改指。",
            code="material_frozen",
            material_id=str(material_id),
            eids=list(eids),
            action=action,
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
