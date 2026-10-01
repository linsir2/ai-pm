"""智能体之间唯一的通信形状。它同时做三件事：生成喂给模型的 `response_schema`、
在提示词里内联展示、解析模型回复——所以它是 JSON Schema 的唯一真相源。"""

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import UnionType
from typing import Literal, Union, get_args, get_origin

from pydantic import BaseModel, Field, ValidationError

from pmstudio.application.errors import PacketParseError
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import FeatureSlug, SectionId
from pmstudio.domain.shared.sections import FEATURE_HOME, section_slot
from pmstudio.domain.shared.target import Target

Label = Literal["fact", "need", "goal", "constraint", "preference", "guess", "decision"]
Zone = Literal["body", "preference"]


class AssumptionDraft(BaseModel):
    failure_consequence: str
    verification: str


class ClaimDraft(BaseModel):
    text: str
    raw_span: str = Field(
        description=(
            "逐字摘自用户输入、且连续出现的一段原话（用来跳回原文）："
            "不许改写、不许用省略号把两段拼成一条，说明性文字（如「未提及」「隐含于上下文」）"
            "不是原话，不许填在这里"
        )
    )
    label: Label
    zone: Zone = "body"
    section_id: str | None = None
    feature_slug: str | None = None
    field: str | None = None
    assumption: AssumptionDraft | None = None
    decided_by: str | None = None


class SeatPickDraft(BaseModel):
    """Analyzer 挑的席位：只能从目录里挑，并且必须说清为什么这一轮需要它。"""

    seat_id: str = Field(description="只能取上面席位目录里出现过的 id")
    why: str = Field(description="一句话：读完这段输入，为什么这个专业面要表态")


class WebQueryDraft(BaseModel):
    """Analyzer 提名的检索词（工具层 C-2b）：**只提名，不联网**。

    为什么把上限写在描述里、而不是 `max_length=3`：写死了就是"给了 4 条 → 整包重装一次"，
    而仓库对"模型多写了一条"的既定口径是**丢那一条 + 留痕**（见席位提名与互斥对子）。
    """

    query: str = Field(description="要拿去搜的一句话（像人问的那样，别堆关键词）")
    why: str = Field(
        description=(
            "为什么这一轮需要**外部客观事实**——文档里没有、用户也没说过的那种"
            "（行业标准、法规、第三方接口的既有做法）"
        )
    )


class ContradictionDraft(BaseModel):
    """一对**立场互斥**的主张。写的是**第几条**（从 1 开始），不是 `c1`。

    为什么用序号：`c1/c2` 是我们按 claims 数组下标发的，提示词里从没出现过——
    让模型写 id 等于让它猜。序号 → 主张 id 的映射由解析那一层做，只有那一处。
    """

    claim_a: int = Field(description="第几条主张（从 1 开始，按 claims 数组的顺序）")
    claim_b: int = Field(description="另一条主张的序号（从 1 开始）")
    why: str = Field(
        description=(
            "一句话说清这两条为什么**立场互斥**（例如「记录不许改」 vs 「点错了要能改」）。"
            "只是「还没定」「还没想清楚」不算互斥，别写在这里"
        )
    )


class ClaimsPacket(BaseModel):
    basis: str = Field(description="你读了哪些章节与编号才切出这些主张")
    #: 12 → 20（P21 F16-a）：真跑里一份"把前面几章补齐"的长输入被切出 17 条主张，
    #: 12 条上限直接把整轮判死（起轮 502，且**没有 `/retry` 可点**）。
    #: 20 条装得下长输入；再超就是模型失准，走 `parse_packet_trimmed` 截断 + 留痕，一样不判死。
    claims: list[ClaimDraft] = Field(min_length=1, max_length=20)
    seats: list[SeatPickDraft] = Field(
        min_length=1,
        max_length=7,
        description="这一轮该请哪些席位（从目录里挑，一般 2–6 个）",
    )
    contradictions: list[ContradictionDraft] = Field(
        default_factory=list,
        max_length=5,
        description="本轮输入内部**立场互斥**的主张对子；没有就给空数组",
    )
    web_queries: list[WebQueryDraft] = Field(
        default_factory=list,
        description=(
            "最多 3 条。**只在你真觉得需要外部客观事实时给**；"
            "用户已经说过的 / 文档或材料里已有的 / 产品取舍与偏好 —— 一律给空数组。"
            "没有就给空数组。（这里只是提名，用户会在复述卡上勾选要不要真的联网）"
        ),
    )


class OpinionDraft(BaseModel):
    local_id: str = Field(description="本席位内唯一，如 O1")
    form: Literal["question", "judgment"]
    about: str
    claim: str
    because: str
    evidence_refs: list[str] = Field(default_factory=list, description="已存在的编号，如 E4")
    basis_note: str | None = Field(default=None, description="没有可引用编号时说明为什么")


class SeatPacket(BaseModel):
    basis: str
    #: 上限跟着提示词走：`seat_review.md` 允许"最多 5 条判断"，加上疑问一共 12 条。
    #: 真跑实测：5 判断 + 5 疑问 = 10 条，旧的 8 条上限直接把整轮打死（报告 F4）。
    opinions: list[OpinionDraft] = Field(default_factory=list, max_length=12)


class TargetDraft(BaseModel):
    zone: Zone = "body"
    section_id: str | None = Field(
        default=None, description="章节 id，如 s5；写功能字段时必须是 s5"
    )
    feature_slug: str | None = Field(
        default=None,
        description="功能标识，如 order-export；**给了 field 就必须给 feature_slug**",
    )
    field: str | None = Field(
        default=None,
        description=(
            "功能字段名，只能取功能字段表里的名字（如 acceptance / exceptions / one_liner）；"
            "必须与 feature_slug 一起给；章节级内容不要给 field"
        ),
    )


class OptionDraft(BaseModel):
    option_id: str
    text: str


class QuestionDraft(BaseModel):
    # 必填（2026-09-29 P11 C3）：默认值会把"忘了写"和"判断成 gap"混成一种——
    # 真跑 `edge-contradiction` 的 15 道题里 8 道没写 kind，全被默认值吞成 gap。
    kind: Literal["gap", "conflict", "decision"]
    title: str
    why: str
    consequence_if_skipped: str
    target: TargetDraft
    type: Literal["single", "multi", "text"] = "single"
    options: list[OptionDraft] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    from_opinions: list[str] = Field(
        default_factory=list,
        description=(
            "这道题来自哪几条席位意见（席位条目地址，如 seat.business_rules:O1）。"
            "这只是归因，不是出处编号；出处编号写在 evidence_refs 里"
        ),
    )


class ListItemDraft(BaseModel):
    text: str
    target: TargetDraft
    reason: str


class ProposalDraft(BaseModel):
    op: Literal["add", "replace", "remove"] = "add"
    target: TargetDraft
    text: str = Field(
        description=(
            "这一条要写进文档的内容。**add / replace 必须有内容**；"
            "**remove 可以留空**（要撤下哪一条看 entry_id）"
        )
    )
    origin_type: Literal["user", "derived", "proposed", "system_default"] = "derived"
    evidence_refs: list[str] = Field(min_length=1)
    rationale: str | None = None
    kind: str | None = Field(
        default=None, description="章节级内容才给：text/acceptance/nfr/bullet_list/…"
    )
    entry_id: str | None = Field(
        default=None, description="replace/remove 必填，用既有条目清单里的 id"
    )


class DroppedOpinionDraft(BaseModel):
    """模型自己申报的丢弃：一条都不许默默丢掉（`04-未决.md` C5 后半）。"""

    handle: str = Field(min_length=1, description="席位条目地址，如 seat.business_rules:O1")
    reason: str = Field(min_length=1, description="一句话：为什么这条意见没有进卡片")


class DispositionsPacket(BaseModel):
    """归因补交代那条**小调用**的唯一回答形状（`02` §11.0.38）。

    只有 `dropped_opinions` 一个字段，而且只回答"我点名的那些里，哪些是你没采用的"。
    为什么不让它回"我用了"：那样等于**让模型自己把"没人交代"这条指标清零**，
    而 `04-未决.md` C5 早就否掉了"赌模型老实交代"这条设计。**它没列的就仍算没交代**，
    沉默还是沉默——这条指标才不会被自报抹平。
    """

    dropped_opinions: list[DroppedOpinionDraft] = Field(
        default_factory=list,
        max_length=84,
        description=(
            "上面点名的意见里，你**没采用**的那些：handle 照抄 + 一句话原因"
            "（重复 / 无依据 / 与本轮无关 / 已经落到别处…）。**一条都不许漏**"
        ),
    )


class IntegrationPacket(BaseModel):
    questions: list[QuestionDraft] = Field(default_factory=list, max_length=12)
    list_items: list[ListItemDraft] = Field(default_factory=list, max_length=20)
    #: 一棵完整功能树 12 个字段；30 条装不下 3 棵（36），真跑里 44 条提案被整包判死（报告 F2）。
    proposals: list[ProposalDraft] = Field(default_factory=list, max_length=60)
    dropped_opinions: list[DroppedOpinionDraft] = Field(
        default_factory=list,
        #: 7 个席位 × 单席位 12 条 = 84——"一条都不许默默丢掉"要在数学上成立（报告 F11）。
        max_length=84,
        description="你没采用的席位意见：给出地址与原因；**一条都不许默默丢掉**",
    )


@dataclass(frozen=True, slots=True)
class Overflow:
    """一处容器超限：保留了前 `kept` 条，溢出 `dropped` 条。

    它只是"这一包太大了"的事实，不是错误——由调用方决定重装还是截断（`parse_packet_trimmed`）。
    """

    container: str
    kept: int
    dropped: int


#: 非对象元素的统一说法。**不复制模型写的那串**（占位符本身不进留痕）——
#: 留痕要的是"这里少了一条、为什么少"，不是把模型写的垃圾再抄一遍（learnings L20）。
NOT_AN_OBJECT = "这一条不是对象（模型写了占位符），没有内容可收"


@dataclass(frozen=True, slots=True)
class Discarded:
    """容器里被摘掉的一条：**它根本不是对象**，没有内容可判。

    与 `Overflow` 分开：超限是"太多了"，这个是"这一条不成形状"。
    两者都不是内容错——真跑里一个占位符字符串 `"O2_placeholder"` 曾把整轮打掉
    （`.codex/runs/2026-10-01/报告.md` F17）。
    """

    container: str
    index: int
    reason: str = NOT_AN_OBJECT


#: 顶层数组的元素 → 容器。**顺序本身是判据的一部分**：命中即停，先查特征最强的键。
#: 题面特征（`title` / `consequence_if_skipped`）必须排在 `evidence_refs` 前面——
#: 题目也可能带依据编号，先看题面才能把它和提案分开。
_SIGNATURES: dict[type[BaseModel], tuple[tuple[str, tuple[frozenset[str], ...]], ...]] = {
    IntegrationPacket: (
        ("dropped_opinions", (frozenset({"handle"}),)),
        ("questions", (frozenset({"title"}), frozenset({"consequence_if_skipped"}))),
        ("list_items", (frozenset({"text", "target", "reason"}),)),
        ("proposals", (frozenset({"evidence_refs"}), frozenset({"op"}))),
    ),
}


def _top_level_keys(model: type[BaseModel]) -> tuple[str, ...]:
    """这个报文顶层允许的键（形状指引要照着它说话）。"""

    return tuple(model.model_fields)


def _container_of_item(item: Mapping[str, object], *, model: type[BaseModel]) -> str | None:
    """一个元素该归到哪个容器？命中不了任何签名就返回 None——调用方判死，**不猜**。"""

    keys = set(item)
    for container, signatures in _SIGNATURES.get(model, ()):
        if any(signature <= keys for signature in signatures):
            return container
    return None


def _normalize_payload(payload: object, *, model: type[BaseModel], purpose: str) -> object:
    """顶层是数组时补上外层对象壳；补不了就抛"形状错"（带指引）。

    真跑实测（留样 15 / 66）：模型把整包回成**一个数组**（元素是 QuestionDraft），
    旧写法直接 `model_type` 判死整轮。数组是**漏了外壳**，不是内容错——
    只要每个元素能用签名唯一归位，就机械补壳；有元素归不了位（或这个报文没有签名表）照旧判死。

    为什么只给 `IntegrationPacket` 开这个口子：它的四个容器都有默认值，补壳不需要造任何字段；
    `ClaimsPacket`（必填 `basis` + 至少一个席位）与 `SeatPacket`（必填 `basis`）一补就得
    **凭空造必填字段**——那是编内容，不是修形状。
    """

    if not isinstance(payload, list) or not payload:
        return payload  # 空数组不重建：空返回不等于没意见（L2），交给原路判死
    if model in _SIGNATURES:
        buckets: dict[str, list[object]] = {}
        for item in payload:
            if not isinstance(item, dict):
                break
            container = _container_of_item(item, model=model)
            if container is None:
                break
            buckets.setdefault(container, []).append(item)
        else:
            return buckets
    raise PacketParseError(
        "模型把整包返回成了数组，无法唯一归位到任何一类内容",
        purpose=purpose,
        packet_shape={"got": "array", "expected_keys": _top_level_keys(model)},
    )


#: 这几个字段**不适用**"null → 用默认值"：
#: `op` / `entry_id` 决定写/改/删哪一条；`form` 决定这条意见进不进账本（有没有编号）。
_WRITE_SEMANTICS_FIELDS = frozenset({"op", "entry_id", "form"})


def _is_model(annotation: object) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _allows_none(annotation: object) -> bool:
    return annotation is type(None) or type(None) in get_args(annotation)  # noqa: E721


def _normalize_value(
    value: object,
    *,
    annotation: object,
    container: str | None = None,
    discarded: list[Discarded] | None = None,
) -> object:
    """按注解归一化一个值：列表字段收 `null` / 单个字符串；嵌套报文递归进去。

    `discarded` 非空时才开启**摘非对象元素**（`parse_packet_lenient`）：
    那一支只在"元素根本不是对象"时丢，元素是对象但字段写错照旧原样交给 pydantic 报错。
    """

    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        inner = [item for item in get_args(annotation) if item is not type(None)]
        if value is None or len(inner) != 1:
            return value
        return _normalize_value(
            value, annotation=inner[0], container=container, discarded=discarded
        )
    if origin is list:
        args = get_args(annotation)
        item_type = args[0] if args else object
        if value is None:
            return []
        if isinstance(value, str) and item_type is str:
            return [value]
        if isinstance(value, list) and _is_model(item_type):
            kept: list[object] = []
            for position, item in enumerate(value, start=1):
                if isinstance(item, dict):
                    kept.append(
                        _normalize_model(item, model=item_type, discarded=discarded)
                    )
                elif discarded is not None:
                    discarded.append(
                        Discarded(container=container or "?", index=position)
                    )
                else:
                    kept.append(item)  # 严格路径：原样留着，让 pydantic 报 `model_type`
            return kept
        return value
    if _is_model(annotation) and isinstance(value, dict):
        return _normalize_model(value, model=annotation, discarded=discarded)
    return value


def _normalize_model(
    payload: object, *, model: type[BaseModel], discarded: list[Discarded] | None = None
) -> object:
    """按字段表过一遍：先按注解修形状，再让"非 Optional + 有默认值"的 `null` 用默认值。"""

    if not isinstance(payload, dict):
        return payload
    fixed: dict[str, object] = {}
    for name, value in payload.items():
        spec = model.model_fields.get(name)
        if spec is None:
            fixed[name] = value  # 多余的键交给 pydantic（默认忽略）
            continue
        normalized = _normalize_value(
            value, annotation=spec.annotation, container=name, discarded=discarded
        )
        if (
            normalized is None
            and name not in _WRITE_SEMANTICS_FIELDS
            and not spec.is_required()
            and spec.default is not None
            and not _allows_none(spec.annotation)
        ):
            continue  # 用 schema 的默认值
        fixed[name] = normalized
    return fixed


def _normalize_fields(
    payload: object, *, model: type[BaseModel], discarded: list[Discarded] | None = None
) -> object:
    """字段级归一化：机械可判的就地修，**内容字段与写入语义一律不动**。

    真跑实测（P7 验证留样 7）：席位把 `evidence_refs` 写成 `null` → 整席位判死。
    三档规则（`02` §11.0.9）：
    ① 列表字段收到 `null` → `[]`（空列表在本仓库有明确定义：没依据就按"它提议的"处理）；
    ② `list[str]` 收到字符串 → 单元素列表（"E4" 就是一个编号）；
    ③ 非 Optional + 有默认值的字段收到 `null` → 用默认值（"没给值"用分类默认，不猜内容）；
    黑名单（`op` / `entry_id` / `form`）例外——那三个用默认值会**悄悄改语义**。
    """

    return _normalize_model(payload, model=model, discarded=discarded)


def parse_packet[T: BaseModel](text: str, model: type[T], *, purpose: str) -> T:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise PacketParseError(
            f"模型返回的不是合法 JSON：{error.msg}", purpose=purpose, offset=error.pos
        ) from error
    payload = _normalize_payload(payload, model=model, purpose=purpose)
    payload = _normalize_fields(payload, model=model)
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        details: dict[str, object] = {"errors": error.errors()}
        overflows = _overflows_of(error)
        if overflows and _only_too_long(error):
            # 严格解析也要把"给了几条、上限几条"带出去：重装指引靠它说人话（`repair_note`）。
            first = overflows[0]
            details["overflow"] = (
                first.container,
                first.kept + first.dropped,
                first.kept,
            )
        raise PacketParseError(
            "模型返回的报文过不了 schema", purpose=purpose, **details
        ) from error


def parse_packet_trimmed[T: BaseModel](
    text: str, model: type[T], *, purpose: str
) -> tuple[T, tuple[Overflow, ...]]:
    """与 `parse_packet` 同口径，**只多一条**：容器超限不再判死整包。

    规矩（真跑实测出来的，见 `.codex/runs/2026-09-28/报告.md` F2/F4/F11）：
    - 只有当**所有**报错都是 `too_long` 时才截断；出现别的错照旧抛——"错就是错"不许放松；
    - 截断保留**模型给出的前 N 条**：顺序就是它自己排的优先级，我们不替它重排；
    - 溢出条数报回调用方，由用例层写成留痕（不静默丢）。
    """

    return _validate_trimming(
        _load_normalized(text, model, purpose=purpose, discarded=None),
        model,
        purpose=purpose,
    )


def parse_packet_lenient[T: BaseModel](
    text: str, model: type[T], *, purpose: str
) -> tuple[T, tuple[Overflow, ...], tuple[Discarded, ...]]:
    """`parse_packet_trimmed` + **摘掉容器里根本不是对象的元素**（2026-10-01 审计 F17）。

    真跑现场：某个席位的 `opinions[1]` 是模型自己写的占位符字符串 `"O2_placeholder"`，
    pydantic 报 `model_type` → 不是 `too_long` → 整轮判死（用户只能点一次 `/retry` 重跑那个席位）。

    它不是内容错（那一"条"根本没有内容可判），是**形状错**——与超限同一套语义：
    丢掉那一条、把位置报回调用方写成留痕，整轮照跑。三条边界：

    1. **只摘非对象**。元素是对象、字段写错（`claim: null`）→ 照旧抛，不许被"丢那一条"掩盖；
    2. 摘空之后的校验照旧生效（主张一条都不剩 ⇒ `min_length` 拦下，整轮判死——主张是地基）；
    3. 摘掉的每一条都上报（`Discarded`），**一条都不许默默丢**。

    为什么单独开一个函数、不让 `parse_packet_trimmed` 一起变：整合者那条路还没有"把丢掉的东西
    写进留痕"的出口，让它静默摘就等于破"一条都不许默默丢掉"的规矩。整合者单独一轮再开。
    """

    discarded: list[Discarded] = []
    packet, overflow = _validate_trimming(
        _load_normalized(text, model, purpose=purpose, discarded=discarded),
        model,
        purpose=purpose,
    )
    return packet, overflow, tuple(discarded)


def _load_normalized(
    text: str, model: type[BaseModel], *, purpose: str, discarded: list[Discarded] | None
) -> object:
    """JSON → 顶层数组重建 → 字段归一化（`discarded` 非空时才摘非对象元素）。"""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise PacketParseError(
            f"模型返回的不是合法 JSON：{error.msg}", purpose=purpose, offset=error.pos
        ) from error
    payload = _normalize_payload(payload, model=model, purpose=purpose)
    return _normalize_fields(payload, model=model, discarded=discarded)


def _validate_trimming[T: BaseModel](
    payload: object, model: type[T], *, purpose: str
) -> tuple[T, tuple[Overflow, ...]]:
    """先严格校验；**只有当所有报错都是 `too_long` 时**才截断重试（别的错照旧抛）。"""

    try:
        return model.model_validate(payload), ()
    except ValidationError as error:
        overflows = _overflows_of(error)
        if not overflows or not _only_too_long(error):
            raise PacketParseError(
                "模型返回的报文过不了 schema", purpose=purpose, errors=error.errors()
            ) from error
        trimmed = copy.deepcopy(payload)
        for item in overflows:
            trimmed[item.container] = trimmed[item.container][: item.kept]
        try:
            return model.model_validate(trimmed), tuple(overflows)
        except ValidationError as second:
            raise PacketParseError(
                "模型返回的报文过不了 schema", purpose=purpose, errors=second.errors()
            ) from second


def _only_too_long(error: ValidationError) -> bool:
    """这一包是不是**只有**容器超限这一种毛病？其它的错不许被截断掩盖。"""

    return all(item.get("type") == "too_long" for item in error.errors())


def _overflows_of(error: ValidationError) -> list[Overflow]:
    """`too_long` 错误 → 每处"保留了前几条、溢出几条"（pydantic 的 ctx 里带着数字）。"""

    found: list[Overflow] = []
    for item in error.errors():
        if item.get("type") != "too_long":
            continue
        location = item.get("loc", ())
        context = item.get("ctx", {})
        limit = int(context.get("max_length") or 0)
        actual = int(context.get("actual_length") or len(item.get("input") or ()))
        found.append(
            Overflow(
                container=str(location[0]) if location else "?",
                kept=limit,
                dropped=max(actual - limit, 0),
            )
        )
    return found


def target_of(draft: TargetDraft) -> Target:
    """线形状 → Target。形状不对时抛领域错误；调用方负责转成 PacketParseError。"""

    if draft.zone == "preference":
        if any((draft.section_id, draft.feature_slug, draft.field)):
            raise ShapeError("偏好区的 target 不带章节、功能与字段")
        return Target.preference()
    section = SectionId(draft.section_id) if draft.section_id else FEATURE_HOME
    if draft.field is not None:
        if draft.feature_slug is not None:
            return Target.feature_field(FeatureSlug(draft.feature_slug), draft.field)
        # 没有 feature_slug 时，field 只能是**章节槽**（s8 的六块，P5a）。
        # 真模型实测：这里以前一律要求 feature_slug，于是六个槽对 AI 路径根本不可达。
        if section_slot(section, draft.field) is None:
            raise ShapeError(
                f"{section} 没有名为 {draft.field} 的章节槽；功能字段必须和 feature_slug 一起给"
            )
        return Target.body(section, field=draft.field)
    if draft.feature_slug is not None:
        return Target.body(section, feature_slug=FeatureSlug(draft.feature_slug))
    return Target.body(section)


def normalize_claim_target(draft: TargetDraft, *, label: str) -> Target:
    """主张的"归位建议"规范化：套用领域规则，而不是把模型的笔误变成整轮失败。

    规则（真模型冒烟实测出来的，见汇报）：
    - **标签优先**：`label=preference` ⇒ 偏好区（LB1 说"偏好不进正文"，标签才是权威分类，
      `zone` 只是它的复述——模型两处写不一致时以标签为准）；
    - 有 `feature_slug` ⇒ 章节必然是 s5（功能子树只长在 s5，D3）；模型写成 s12 也纠正过来；
    - 有 `field` 但没 `feature_slug` ⇒ 丢掉 field（字段必须挂在功能下，留着没意义）；

    注意这里**不丢内容**：主张文本、原话、标签都照旧，用户在开场卡上还能改去处。
    章节 id 本身不认识（比如 s99）仍然报错——那是幻觉，不能猜。
    """

    if label == "preference" or draft.zone == "preference":
        return Target.preference()
    if draft.feature_slug:
        return Target.feature_field(FeatureSlug(draft.feature_slug), draft.field) if draft.field \
            else Target.body(FEATURE_HOME, feature_slug=FeatureSlug(draft.feature_slug))
    return Target.body(SectionId(draft.section_id) if draft.section_id else FEATURE_HOME)
