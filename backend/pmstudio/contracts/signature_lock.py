"""端口签名锁：协议与实现必须形状一致，漂移即失败。

P0 的核心防御机制（docs/redesign/02-contracts.md §2.2）：
- 协议声明的每个方法，实现必须存在；
- 签名严格一致：参数名 / 顺序 / 默认值 / 种类 / async / 返回类型；
- 实现不允许「多参数」「少参数」「改默认」——任何漂移测试即红。

规则是严格相等，不是「实现 ⊇ 协议」：
D1 的教训正是「实现多 6 个 keyword-only 参数、调用方按 8 参调用」，
如果签名锁允许「实现可添加可选参数」，D1 就会被放过。
实现需要额外依赖时，应通过构造注入（如 BoardReader），而不是往方法里加参数。
"""

import inspect
import types
from collections.abc import Callable
from typing import Any, Union, get_args, get_origin, get_type_hints

from pmstudio.common.errors import ContractViolation


def _public_methods(protocol: type) -> list[str]:
    return [
        name
        for name, value in vars(protocol).items()
        if not name.startswith("_") and callable(value)
    ]


def _kind_label(kind: inspect._ParameterKind) -> str:
    return {
        inspect.Parameter.POSITIONAL_ONLY: "positional-only",
        inspect.Parameter.POSITIONAL_OR_KEYWORD: "positional-or-keyword",
        inspect.Parameter.VAR_POSITIONAL: "*args",
        inspect.Parameter.KEYWORD_ONLY: "keyword-only",
        inspect.Parameter.VAR_KEYWORD: "**kwargs",
    }.get(kind, str(kind))


def _is_async(fn: Callable[..., Any]) -> bool:
    return inspect.iscoroutinefunction(fn)


def _return_type_compatible(proto: object, impl: object) -> bool:
    """实现返回类型必须是协议返回类型的子类型（或相同）。

    支持 union 与泛型别名（`tuple[X, ...]`、`A | B | None`）的结构等价比较；
    纯 `type` 时允许协变（实现返回子类）。
    """
    if proto is impl:
        return True

    # 纯 class：允许实现返回子类（协变）
    if isinstance(proto, type) and isinstance(impl, type):
        return issubclass(impl, proto)

    # union：实现的每个分支都要能在协议分支里找到等价或子类型
    proto_args = get_args(proto)
    impl_args = get_args(impl)
    if proto_args and impl_args and get_origin(proto) in (Union, types.UnionType) and get_origin(impl) in (Union, types.UnionType):
        return all(
            any(_return_type_compatible(p, i) for p in proto_args)
            for i in impl_args
        )

    # 泛型别名：origin 相同 + 参数逐位等价（如 tuple[X, ...]）
    if proto_args and impl_args and get_origin(proto) is get_origin(impl) and len(proto_args) == len(impl_args):
        return all(
            _return_type_compatible(p, i)
            for p, i in zip(proto_args, impl_args, strict=True)
        )

    return False


def assert_port_implementation(protocol: type, implementation: type) -> None:
    """校验 implementation 与 protocol 形状一致，不一致抛 ContractViolation。"""
    missing = [
        name for name in _public_methods(protocol) if not hasattr(implementation, name)
    ]
    if missing:
        raise ContractViolation(
            f"{implementation.__name__} 缺少端口 {protocol.__name__} 声明的方法：{', '.join(missing)}"
        )

    for name in _public_methods(protocol):
        proto_fn = getattr(protocol, name)
        impl_fn = getattr(implementation, name)
        _assert_single_method(protocol, implementation, name, proto_fn, impl_fn)


def _assert_single_method(
    protocol: type,
    implementation: type,
    name: str,
    proto_fn: Callable[..., Any],
    impl_fn: Callable[..., Any],
) -> None:
    where = f"{implementation.__name__}.{name} 与端口 {protocol.__name__}.{name}"

    # async 一致性
    if _is_async(proto_fn) != _is_async(impl_fn):
        raise ContractViolation(f"{where}：async 性质不一致")

    proto_sig = inspect.signature(proto_fn)
    impl_sig = inspect.signature(impl_fn)

    proto_params = list(proto_sig.parameters.items())
    impl_params = list(impl_sig.parameters.items())

    # 数量一致（含 self）
    if len(proto_params) != len(impl_params):
        raise ContractViolation(
            f"{where}：参数数量不一致——协议 {len(proto_params) - 1} 个，"
            f"实现 {len(impl_params) - 1} 个"
        )

    for (pname, pparam), (iname, iparam) in zip(proto_params, impl_params, strict=True):
        if pname != iname:
            raise ContractViolation(
                f"{where}：第 {proto_params.index((pname, pparam))} 个参数名不一致——"
                f"协议叫 '{pname}'，实现叫 '{iname}'"
            )
        if pparam.kind is not iparam.kind:
            raise ContractViolation(
                f"{where}：参数 '{pname}' 的种类不一致——协议 {_kind_label(pparam.kind)}，"
                f"实现 {_kind_label(iparam.kind)}"
            )
        if pparam.default is not iparam.default:
            raise ContractViolation(
                f"{where}：参数 '{pname}' 的默认值不一致——协议 {pparam.default!r}，"
                f"实现 {iparam.default!r}"
            )

    # 返回类型兼容：实现返回类型必须是协议返回类型的子类型（或相同）
    try:
        proto_hints = get_type_hints(proto_fn)
        impl_hints = get_type_hints(impl_fn)
    except Exception as exc:  # pragma: no cover - 类型提示解析失败按不兼容处理
        raise ContractViolation(f"{where}：类型提示无法解析（{exc}）") from exc

    proto_return = proto_hints.get("return")
    impl_return = impl_hints.get("return")
    if proto_return is not None and impl_return is not None:
        if not _return_type_compatible(proto_return, impl_return):
            raise ContractViolation(
                f"{where}：返回类型不一致——协议 {proto_return}，实现 {impl_return}"
            )
