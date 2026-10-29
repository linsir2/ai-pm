"""应用层错误。领域错误在 pmstudio.domain.shared.errors，两者不许互相继承。

**运行时错误不在这里**：`ApplicationError` 基类与 `PacketParseError` / `ToolCallFailed` /
`FetchRefused` / `ModelCallFailed` 已搬进 `pmstudio.harness.errors`（2026-10-04）——
它们只因为"模型会胡说八道"或"外部调用会失败"而存在。搬走之后
`litellm_gateway.py` 与 `tools/*.py` 才不必反向依赖 application。

留在这里的都是**有业务含义**的：`UnknownProject` / `UnknownRound` / `UnknownEvidenceId` /
`UnknownMaterial` / `MaterialFrozen` / `RoundBusy` / `UnsupportedMaterial` /
`MaterialTooLarge` / `SeatAlreadyReviewed`，以及 `unknown_ref_message`
（席位条目地址 vs 提案编号 vs E 编号）。

`code` 字符串与搬移前逐字一致——`api/errors.py` 的 CODE_STATUS 表靠它映射 HTTP 状态码。
"""

from collections.abc import Sequence

from pmstudio.harness.errors import ApplicationError


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
    # 提示词里同时可见五套编号（`02` §11.0.60）：章节 `s5`、提案 `r-xxxx#01`、
    # 功能 `feature#slug`、对话轮次 `t5`、出处 `E12`。只有 E 那种能进 `based_on`。
    # 这三种（2026-10-10 讨论区真跑实测：模型依次引用过 `s2..s12`、
    # `feature#order-export`、`t5`/`t6`）错在同一个地方，所以给同一条指引——
    # **说清哪个是什么，别只说"不存在"**：重装只有一次机会，这行字是唯一一次指引。
    if any(
        item.startswith("s") and item[1:].isdigit() for item in unknown
    ) or any(item.startswith("feature#") for item in unknown) or any(
        item.startswith("t") and item[1:].isdigit() for item in unknown
    ):
        return (
            f"{what}：{listed}；`s5` 是章节号、`feature#xxx` 是功能标识、"
            "`t5` 是对话里的第几轮——都不是出处编号。"
            "出处编号只在上面那份文档清单里，形如 E12；引不到就留空"
        )
    return f"{what}：{listed}"


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
    """这个项目里没有这份材料（删/重解析/起轮带料时给错了 id）——资源不在，404。"""

    def __init__(self, material_id: object) -> None:
        super().__init__(
            f"这个项目里没有这份材料：{material_id}",
            code="unknown_material",
            material_id=str(material_id),
        )


class RoundNotAttachable(ApplicationError):
    """这一轮已经走到不能追加材料的步骤（草案卡之后、或已结束）——合法但状态不允许。"""

    def __init__(self, *, round_id: object, step: object) -> None:
        super().__init__(
            f"这一轮已经不能带料了（当前 {step}）：等下一轮再带",
            code="round_not_attachable",
            round_id=str(round_id),
            step=str(step),
        )


class WebMaterialNotAttachable(ApplicationError):
    """联网材料有另一条渲染/编号路径，不能从搜索框手动带进材料池。"""

    def __init__(self, *, material_id: object) -> None:
        super().__init__(
            "联网结果不能手动带进本轮；请用联网申请或研究入口",
            code="web_material_not_attachable",
            material_id=str(material_id),
        )


class UnknownTemplate(ApplicationError):
    """没有这个模板（创建项目/取模板时给错了 id）——资源不在，404。"""

    def __init__(self, template_id: object) -> None:
        super().__init__(
            f"没有这个模板：{template_id}",
            code="unknown_template",
            template_id=str(template_id),
        )


class BuiltinTemplateProtected(ApplicationError):
    """内置模板（standard / light）不能改也不能删——状态不允许，409（`02` §11.0.70）。

    **为什么锁**：内置那份是"回到出厂状态"的唯一路径。改了或删了，模板出问题时就没有
    基准可比了。界面上的做法是不给编辑/删除按钮（不是点了报错），这个类是给直接调接口
    的那条路兜底的。
    """

    def __init__(self, template_id: object) -> None:
        super().__init__(
            f"内置模板不能修改或删除：{template_id}",
            code="template_builtin_protected",
            template_id=str(template_id),
        )


class DigestAlreadyWritten(ApplicationError):
    """同一个轮次的摘要已经写过了（`07 §7.14`：摘要只追加，不改写）。

    **为什么不容忍覆盖**：摘要会进后面每一轮的提示词（`$digests` 带最近 3 轮）。
    回头改一个已写下的摘要，等于让"前几轮发生了什么"在读过它的那些轮次之后变样——
    那就是改历史。真走到重复写入就明确失败，不静默覆盖。
    """

    def __init__(self, round_id: str) -> None:
        super().__init__(
            f"这一轮的摘要已经写过了（{round_id}）——摘要只追加、不改写",
            code="digest_already_written",
            round_id=round_id,
        )


class MaterialFrozen(ApplicationError):
    """这份材料已经发过编号：**编号发出即冻结**（`P19`）。

    编号指向它里面的块——删了 `E7` 会指向空气；就地改形状会让 `E7` 改指别的内容。
    所以被引用过的材料只能**留着**（要用新解析器读，走"另出一份"那条路）。
    """

    def __init__(
        self,
        material_id: object,
        *,
        eids: tuple[str, ...],
        action: str,
        reason: str | None = None,
    ) -> None:
        numbers = tuple(
            eid
            for eid in eids
            if len(eid) > 1 and eid.startswith("E") and eid[1:].isdigit()
        )
        if reason is not None:
            # 调用方知道得更具体（例：研究来源一律冻结，与"有没有被引用"无关）
            message = f"这份材料不能{action}：{reason}"
        elif numbers:
            message = (
                f"这份材料已经发过编号 {'、'.join(numbers)}，不能{action}"
                "——编号指向它里面的块，不能回收、也不能改指。"
            )
        else:
            message = (
                f"这份材料已被本轮引用（手勾/手动带料），不能{action}"
                "——它可能还没投影、没有编号，但用户已经明确要它。"
            )
        super().__init__(
            message,
            code="material_frozen",
            material_id=str(material_id),
            eids=list(numbers),
            references=list(eids),
            action=action,
            **({"reason": reason} if reason is not None else {}),
        )


class SeatAlreadyReviewed(ApplicationError):
    """**这个席位本趟已经交回了，现在跳过没有意义**——409。

    与 `admin_protected` / `material_frozen` 同一侧："请求本身合法、状态不允许"。
    用 400 是"请求有问题"，用 404 是"找不到"——都不是这两件事。
    而**静默 200 更坏**：等于告诉用户"跳过成功"，但那份意见还在整合里好好待着。

    这个错只可能来自**竞态**（交回先提交、跳过后到）：按钮只对"未交回"亮，
    点下去的那一刻还没交回，落库时才发现交回了。`SkipSeat` 在同一 uow 内
    复查当趟 review——没有这个复查，交错的两序会写出"review 落库且席位被标跳过"，
    不变式（跳过的席位零意见进整合）就破了。所以这不是 UX 选项，是正确性必需。
    """

    def __init__(self, message: str, *, seat_id: str, **details: object) -> None:
        super().__init__(message, code="seat_already_reviewed", seat_id=seat_id, **details)


class UnknownEntryId(ApplicationError):
    """用户指的那条文档内容不存在（从条目发起调研时指错了）——资源不在，404。"""

    def __init__(self, entry_id: object) -> None:
        super().__init__(
            f"文档里没有这条内容：{entry_id}",
            code="unknown_entry",
            entry_id=str(entry_id),
        )


class UnknownExploration(ApplicationError):
    """查询/操作一个不存在的探索运行——资源不在，404。"""

    def __init__(self, exploration_id: object) -> None:
        super().__init__(
            f"找不到这一趟探索：{exploration_id}",
            code="unknown_exploration",
            exploration_id=str(exploration_id),
        )


class UnknownWebRequest(ApplicationError):
    """这一轮里没有这条联网申请——资源不在，404。"""

    def __init__(self, request_id: object, *, round_id: object | None = None) -> None:
        super().__init__(
            f"这一轮没有这条联网申请：{request_id}",
            code="unknown_web_request",
            request_id=str(request_id),
            round_id=None if round_id is None else str(round_id),
        )


class WebRequestNotPending(ApplicationError):
    """这条申请已经越过 `proposed`（已批准/已拒绝/已执行），或轮次已经结束——409。

    与 `SeatAlreadyReviewed` 同一侧："请求本身合法、状态不允许"。决策只能发生一次；
    第二次点批准不能让已执行的结果再跑一遍网络。
    """

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message, code="web_request_not_pending", **details)


class WebExecutionLimitReached(ApplicationError):
    """这一轮的联网执行名额已经用满（或根本没有可用工具）——409。

    `approved/done/failed` 占名额；`proposed/rejected` 不占。上限是
    `min(tool.max_queries, MAX_WEB_EXECUTIONS)`，没配工具时生效值是 0。
    """

    def __init__(
        self,
        *,
        round_id: object,
        limit: int,
        used: int,
        tool_available: bool,
    ) -> None:
        if not tool_available:
            message = (
                "服务端没有配好联网工具（没配密钥，或工具被停用），"
                "这一轮不能批准联网申请"
            )
        else:
            message = (
                f"这一轮最多执行 {limit} 条联网申请，已有 {used} 条批准或执行记录"
                "——名额用满了，不能再批准"
            )
        super().__init__(
            message,
            code="web_execution_limit_reached",
            round_id=str(round_id),
            limit=limit,
            used=used,
            tool_available=tool_available,
        )


class WebRequestRecoveryRequired(ApplicationError):
    """轮里已有超额 `approved`：开场前必须先逐条执行或放弃——409。"""

    def __init__(
        self,
        *,
        round_id: object,
        limit: int,
        used: int,
        approved: int,
    ) -> None:
        super().__init__(
            f"这一轮已有 {used} 条联网记录，超过名额 {limit}；"
            f"请先逐条处理 {approved} 条已批准申请（执行或放弃）再开场",
            code="web_request_recovery_required",
            round_id=str(round_id),
            limit=limit,
            used=used,
            approved=approved,
        )


class WebRequestNotRecoverable(ApplicationError):
    """这条申请不在逐条恢复的适用面上（非 approved / 未超额 / 轮终态）——409。"""

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message, code="web_request_not_recoverable", **details)


class UnknownDiscussion(ApplicationError):
    """查询/操作一条不存在的讨论线程——资源不在，404。"""

    def __init__(self, discussion_id: object) -> None:
        super().__init__(
            f"找不到这条讨论：{discussion_id}",
            code="unknown_discussion",
            discussion_id=str(discussion_id),
        )


class UnknownRole(ApplicationError):
    """开聊时点的讨论角色不存在（`dlg.*` 不在三固定里，或 `seat.*` 不在注册表里）——404。"""

    def __init__(self, role_id: object) -> None:
        super().__init__(
            f"没有这个讨论角色：{role_id}",
            code="unknown_role",
            role_id=str(role_id),
        )


class ExplorationBusy(ApplicationError):
    """同一个项目已经有一趟探索在跑。

    单用户也会有并发：双击"开始调研"、开两个标签页都会撞上。与 `RoundBusy`
    同一条口径——同一时间只允许一趟，等它跑完或先放弃它。
    """

    def __init__(self, project_id: object) -> None:
        super().__init__(
            "这个项目已经有一趟调研在跑：等它跑完，或先放弃它",
            code="exploration_busy",
            project_id=str(project_id),
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


class LeaseLost(ApplicationError):
    """写库时发现租约已经不属于自己（过期被接管、被 force 抢走，或根本没拿到）。

    它是**写路径的 fencing 失败**：这次 `save` 没有落库（或在同一事务里被回滚）。
    与 `RoundBusy` 分开——那是"还没开始就被别人占着"，这是"跑到一半发现锁没了"。
    """

    def __init__(self, *, kind: object, target_id: object) -> None:
        super().__init__(
            f"租约已失效（{kind}:{target_id}），这次写入没有落库",
            code="lease_lost",
            kind=str(kind),
            target_id=str(target_id),
        )


class RoundLeaseLost(ApplicationError):
    """跑一轮的后台心跳发现租约已被接管：这次动作被取消，**不代表**轮次失败。

    与 `LeaseLost` 分开：那是 `save` 当场撞上的 fencing 失败，这是心跳先发现、
    主动把在途模型调用停下来。两者都是 409，但回调文案不同——用户要看到的是
    "另一个 worker 接手了"，不是"你的数据写坏了"。
    """

    def __init__(self, round_id: object) -> None:
        super().__init__(
            "这一轮的租约已被另一个进程接管，本次动作已停止",
            code="round_lease_lost",
            round_id=str(round_id),
        )


class ExplorationLeaseLost(ApplicationError):
    """跑一趟探索的后台心跳发现租约已被接管：这次动作被取消，**不代表**探索失败。

    与 `RoundLeaseLost` 同一条口径：`LeaseLost` 是 `save` 当场撞上的 fencing 失败，
    这是心跳先发现、主动把在途模型调用停下来。两者都是 409。
    """

    def __init__(self, exploration_id: object) -> None:
        super().__init__(
            "这一趟探索的租约已被另一个进程接管，本次动作已停止",
            code="exploration_lease_lost",
            exploration_id=str(exploration_id),
        )


class DiscussionBusy(ApplicationError):
    """同一条讨论线程已经有一轮在跑。

    与 `RoundBusy` 同一条口径：前端自动循环 + 双击"再跑一轮" + 两个标签页都会撞上。
    已落库的 turn 不动——停下来等它跑完，或调 abort 停掉它再跑。
    """

    def __init__(self, discussion_id: object) -> None:
        super().__init__(
            "这条讨论正在跑一轮：等它跑完，或先停掉它",
            code="discussion_busy",
            discussion_id=str(discussion_id),
        )
