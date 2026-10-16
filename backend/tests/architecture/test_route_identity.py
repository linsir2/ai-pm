"""业务端点必须带身份（多用户改造 Task 6）。

## 为什么要有这条

T6 给 `api/routes.py` 的每个业务端点加了 `user: CurrentUser`（`CurrentUser =
Annotated[UserView, Depends(require_user)]`）。这条依赖做两件事：验身份（401）**和**
把当前用户设进 owner 上下文——漏挂一个端点，那个端点的内容读/写就会抛
`missing_owner_scope`（500），或者（更坏）在别的上下文里读到不属于自己的东西。

靠人逐个核对 50+ 个签名会漏。**把"每个业务路由都有身份"变成一条可执行的守卫**：
从 `routes.py` 的 AST 里找出所有 `@router.<method>(...)` 处理函数，逐个查签名里有没有
身份参数，缺了就点名。守卫自己能红——`test_the_checker_flags_a_route_without_identity`
用合成源码钉住"判据有效"（= `tests/architecture/test_guard_coverage.py` 的那条纪律）。

## 豁免

只有**健康检查** `/health`：探活不该要 session（Caddy / 监控要能在未登录时打）。
清单写死在这里；加一条豁免必须改这个常量，评审时一眼可见。

`api/auth.py` 的公开端点（register / login）**不在 `routes.py`**，故天然不被扫——
那正是"公开端点放别处"这个结构本身的价值。
"""

import ast

from tests.architecture.guards import SRC

ROUTES = SRC / "pmstudio" / "api" / "routes.py"

#: 无身份豁免（路径）。理由：探活。auth.py 的公开端点不在本文件，故不出现在这里。
IDENTITY_EXEMPT: frozenset[str] = frozenset({"/health"})

_ROUTE_METHODS = frozenset({"get", "post", "put", "delete", "patch"})
_IDENTITY_ALIAS = "CurrentUser"


def _route_path(decorator: ast.expr) -> str | None:
    """`@router.get("/x", ...)` → `"/x"`；不是路由装饰器就 `None`。"""

    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
        return None
    target = decorator.func
    if not (isinstance(target.value, ast.Name) and target.value.id == "router"):
        return None
    if target.attr not in _ROUTE_METHODS or not decorator.args:
        return None
    first = decorator.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _annotation_is_identity(annotation: ast.expr | None) -> bool:
    """这个参数注解是不是身份依赖：别名 `CurrentUser` 或 `Annotated[..., Depends(...)]`。"""

    if isinstance(annotation, ast.Name):
        return annotation.id == _IDENTITY_ALIAS
    if (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "Annotated"
    ):
        return any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Depends"
            for node in ast.walk(annotation)
        )
    return False


def _has_identity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _annotation_is_identity(arg.annotation)
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        if arg.annotation is not None
    )


def route_paths(source: str) -> dict[str, str]:
    """`routes.py` 源码 → `{路径: 处理函数名}`（所有业务路由，含豁免的）。"""

    found: dict[str, str] = {}
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            path = _route_path(decorator)
            if path is not None:
                found[path] = node.name
                break
    return found


def routes_missing_identity(source: str) -> list[tuple[str, str]]:
    """`routes.py` 里缺身份参数的业务路由 `[(path, func_name), …]`（避开豁免清单）。"""

    missing: list[tuple[str, str]] = []
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        path = next(
            (p for p in (_route_path(d) for d in node.decorator_list) if p is not None),
            None,
        )
        if path is None or path in IDENTITY_EXEMPT:
            continue
        if not _has_identity(node):
            missing.append((path, node.name))
    return missing


# ── 判据有效性（合成源码；证明守卫抓得住）────────────────────────────────────


def test_the_checker_flags_a_route_without_identity():
    source = (
        "from fastapi import APIRouter, Request\n"
        "router = APIRouter()\n"
        '@router.get("/things")\n'
        "async def list_things(request: Request) -> None: ...\n"
    )
    assert routes_missing_identity(source) == [("/things", "list_things")]


def test_the_checker_accepts_the_alias_and_a_depends():
    source = (
        "from typing import Annotated\n"
        "from fastapi import APIRouter, Depends, Request\n"
        "router = APIRouter()\n"
        "CurrentUser = Annotated[object, Depends(lambda: None)]\n"
        '@router.get("/a")\n'
        "async def a(request: Request, user: CurrentUser) -> None: ...\n"
        '@router.post("/b")\n'
        "async def b(request: Request, actor: Annotated[object, Depends(lambda: None)])"
        " -> None: ...\n"
    )
    assert routes_missing_identity(source) == []


def test_the_checker_skips_the_exempt_path():
    source = (
        "from fastapi import APIRouter, Request\n"
        "router = APIRouter()\n"
        '@router.get("/health")\n'
        "async def health(request: Request) -> None: ...\n"
    )
    assert routes_missing_identity(source) == []


# ── 真实状态：每个业务端点都有身份 ───────────────────────────────────────────


def test_every_route_in_routes_py_has_an_identity():
    source = ROUTES.read_text(encoding="utf-8")
    assert routes_missing_identity(source) == []


def test_the_scanner_actually_looks_at_the_routes():
    """不给"扫到 0 条也报绿"留缝：真文件里的关键路径必须在扫描结果里。"""

    paths = route_paths(ROUTES.read_text(encoding="utf-8"))
    assert "/health" in paths
    assert "/events" in paths
    assert "/projects" in paths
    assert len(paths) > 40, f"只扫到 {len(paths)} 条路由，像是解析器坏了"
