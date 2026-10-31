"""输出归一化引擎：把"模型回包"变成"能校验的形状"，并把丢掉的每一条都报回去。

搬自 `application/agents/schemas.py`（2026-10-04）。原来它与 17 个产品报文类混在一个文件里，
而算法本身只依赖两个东西：**报文的 pydantic 字段表** + **调用方告诉它哪些键算哪一类容器**。
所以这两样被抽成 `NormalizeRules` 由 App 注入，引擎本身对产品一无所知。

**三档口径**（真跑实测攒出来的，别当过度设计看）：

- `parse_strict`：超限也判死，但把"给了几条/上限几条"塞进 `details`，重装指引要靠它说人话；
- `parse_trimmed`：只有当**所有**报错都是 `too_long` 时才截断，别的错照旧抛——"错就是错"；
- `parse_lenient`：多摘一条"容器里根本不是对象的元素"（真跑 F17：占位符字符串把整轮打掉）。

三条共同口径（`02` §11.0.9）：① 列表字段收 `null` → `[]`；② `list[str]` 收字符串 → 单元素列表；
③ 非 Optional + 有默认值的字段收 `null` → 用默认值（**不猜内容**）。
黑名单字段例外——用默认值会**悄悄改语义**（`op` / `entry_id` / `form` 决定写改删与有没有编号）。

**截断只保留模型给出的前 N 条**：顺序是它自己排的优先级，我们不替它重排。
**丢掉的每一条都必须报回调用方**（`Overflow` / `Discarded`），一条都不许默默丢。
"""

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import UnionType
from typing import Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from pmstudio.harness.errors import PacketParseError

#: 顶层数组的元素 → 容器的签名表形状：(容器名, (键集合, …))。顺序本身是判据的一部分。
Signatures = Mapping[type[BaseModel], tuple[tuple[str, tuple[frozenset[str], ...]], ...]]


@dataclass(frozen=True, slots=True)
class NormalizeRules:
    """调用方告诉引擎的两件事。

    `signatures`：哪些键组合算哪一类容器（只有"容器都有默认值"的报文才需要，补壳不必造必填字段）。
    `write_semantics_fields`：**不许**用默认值兜底的字段——那三个字段用兜底会悄悄改语义。
    """

    signatures: Signatures = field(default_factory=dict)
    write_semantics_fields: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Overflow:
    """一处容器超限：保留了前 `kept` 条，溢出 `dropped` 条。

    它只是"这一包太大了"的事实，不是错误——由调用方决定重装还是截断（`parse_trimmed`）。
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


def _top_level_keys(model: type[BaseModel]) -> tuple[str, ...]:
    """这个报文顶层允许的键（形状指引要照着它说话）。"""

    return tuple(model.model_fields)


def _container_of_item(
    item: Mapping[str, object], *, model: type[BaseModel], rules: NormalizeRules
) -> str | None:
    """一个元素该归到哪个容器？命中不了任何签名就返回 None——调用方判死，**不猜**。"""

    keys = set(item)
    for container, signatures in rules.signatures.get(model, ()):
        if any(signature <= keys for signature in signatures):
            return container
    return None


def _normalize_payload(
    payload: object, *, model: type[BaseModel], purpose: str, rules: NormalizeRules
) -> object:
    """顶层是数组时补上外层对象壳；补不了就抛"形状错"（带指引）。

    真跑实测（留样 15 / 66）：模型把整包回成**一个数组**（元素是 QuestionDraft），
    旧写法直接 `model_type` 判死整轮。数组是**漏了外壳**，不是内容错——
    只要每个元素能用签名唯一归位，就机械补壳；有元素归不了位（或这个报文没有签名表）照旧判死。

    为什么只给部分报文开这个口子：它们的容器都有默认值，补壳不需要造任何字段；
    必填 `basis` 的那两个（主张包 / 席位包）一补就得**凭空造必填字段**——那是编内容，不是修形状。
    """

    if not isinstance(payload, list) or not payload:
        return payload  # 空数组不重建：空返回不等于没意见（L2），交给原路判死
    if model in rules.signatures:
        buckets: dict[str, list[object]] = {}
        for item in payload:
            if not isinstance(item, dict):
                break
            container = _container_of_item(item, model=model, rules=rules)
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


def _is_model(annotation: object) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _allows_none(annotation: object) -> bool:
    return annotation is type(None) or type(None) in get_args(annotation)  # noqa: E721


def _normalize_value(
    value: object,
    *,
    annotation: object,
    rules: NormalizeRules,
    container: str | None = None,
    discarded: list[Discarded] | None = None,
) -> object:
    """按注解归一化一个值：列表字段收 `null` / 单个字符串；嵌套报文递归进去。

    `discarded` 非空时才开启**摘非对象元素**（`parse_lenient`）：
    那一支只在"元素根本不是对象"时丢，元素是对象但字段写错照旧原样交给 pydantic 报错。
    """

    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        inner = [item for item in get_args(annotation) if item is not type(None)]
        if value is None or len(inner) != 1:
            return value
        return _normalize_value(
            value, annotation=inner[0], rules=rules, container=container, discarded=discarded
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
                        _normalize_model(
                            item, model=item_type, rules=rules, discarded=discarded
                        )
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
        return _normalize_model(value, model=annotation, rules=rules, discarded=discarded)
    return value


def _normalize_model(
    payload: object,
    *,
    model: type[BaseModel],
    rules: NormalizeRules,
    discarded: list[Discarded] | None = None,
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
            value, annotation=spec.annotation, rules=rules, container=name, discarded=discarded
        )
        if (
            normalized is None
            and name not in rules.write_semantics_fields
            and not spec.is_required()
            and spec.default is not None
            and not _allows_none(spec.annotation)
        ):
            continue  # 用 schema 的默认值
        fixed[name] = normalized
    return fixed


def _normalize_fields(
    payload: object,
    *,
    model: type[BaseModel],
    rules: NormalizeRules,
    discarded: list[Discarded] | None = None,
) -> object:
    """字段级归一化：机械可判的就地修，**内容字段与写入语义一律不动**。"""

    return _normalize_model(payload, model=model, rules=rules, discarded=discarded)


def load_shell[T: BaseModel](
    text: str, model: type[T], *, purpose: str, rules: NormalizeRules
) -> object:
    """回包原文 → **顶层数组补壳后的对象**（字段级归一化还没做）。

    为什么单独暴露：补壳会把"模型回了一个数组"这件事**改写成对象**——只有拿到补壳之后
    那个对象，才谈得上"这一包的 payload 是什么"。调用方（例如要往信封里填 payload 的
    那一层）需要的就是它，而不是原文；自己再 `json.loads` 一次会拿到**没补壳的数组**，
    于是同一个形状在两处得到两个不同的理解——真踩过：`structured.call_structured` 曾因此
    把数组当 dict 用，信封那一行直接 AttributeError，而那条路没有任何处理器接得住
    （`api/app.py` 只注册了 `DomainError` / `ApplicationError`）。

    **它不保证返回对象**：模型回一个字符串 / 数字时原样返回，交给下游判死。
    """

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise PacketParseError(
            f"模型返回的不是合法 JSON：{error.msg}", purpose=purpose, offset=error.pos
        ) from error
    return _normalize_payload(payload, model=model, purpose=purpose, rules=rules)


def parse_lenient_from[T: BaseModel](
    payload: object, model: type[T], *, purpose: str, rules: NormalizeRules
) -> tuple[T, tuple[Overflow, ...], tuple[Discarded, ...]]:
    """已补壳的 payload → 宽松档校验（截断档 + 摘非对象元素）。"""
    """`parse_trimmed` + **摘掉容器里根本不是对象的元素**（2026-10-01 审计 F17）。

    真跑现场：某个席位的 `opinions[1]` 是模型自己写的占位符字符串 `"O2_placeholder"`，
    pydantic 报 `model_type` → 不是 `too_long` → 整轮判死（用户只能点一次 `/retry` 重跑那个席位）。

    它不是内容错（那一"条"根本没有内容可判），是**形状错**——与超限同一套语义：
    丢掉那一条、把位置报回调用方写成留痕，整轮照跑。三条边界：

    1. **只摘非对象**。元素是对象、字段写错（`claim: null`）→ 照旧抛，不许被"丢那一条"掩盖；
    2. 摘空之后的校验照旧生效（主张一条都不剩 ⇒ `min_length` 拦下，整轮判死——主张是地基）；
    3. 摘掉的每一条都上报（`Discarded`），**一条都不许默默丢**。

    为什么单独开一个函数、不让 `parse_trimmed` 一起变：整合者那条路还没有"把丢掉的东西
    写进留痕"的出口，让它静默摘就等于破"一条都不许默默丢掉"的规矩。整合者单独一轮再开。
    """

    discarded: list[Discarded] = []
    packet, overflow = _validate_trimming(
        _normalize_fields(payload, model=model, rules=rules, discarded=discarded),
        model,
        purpose=purpose,
    )
    return packet, overflow, tuple(discarded)


def parse_lenient[T: BaseModel](
    text: str, model: type[T], *, purpose: str, rules: NormalizeRules
) -> tuple[T, tuple[Overflow, ...], tuple[Discarded, ...]]:
    return parse_lenient_from(
        load_shell(text, model, purpose=purpose, rules=rules),
        model,
        purpose=purpose,
        rules=rules,
    )


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
                "模型返回的报文过不了 schema",
                purpose=purpose,
                errors=error.errors(include_input=False),
            ) from error
        trimmed = copy.deepcopy(payload)
        for item in overflows:
            trimmed[item.container] = trimmed[item.container][: item.kept]
        try:
            return model.model_validate(trimmed), tuple(overflows)
        except ValidationError as second:
            raise PacketParseError(
                "模型返回的报文过不了 schema",
                purpose=purpose,
                errors=second.errors(include_input=False),
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
