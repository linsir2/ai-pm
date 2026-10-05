"""每条守卫的**机制**都必须能抓住它声称要抓的违规。

## 为什么要有这个文件（2026-10-04 的教训）

审计时做了一次变异测试：注入 20 个真实违规，看 39 条守卫抓不抓得住。**18/20**，
两条漏的都在同一条守卫上——`test_harness_never_imports_an_application_layer`：

```python
# 写坏的判据
if f"{PACKAGE}.{layer}" in imported          # 集合成员 = 精确相等
                                             # imported 里是 "pmstudio.application.ports"
                                             # 而这里在比 "pmstudio.application" → 永远 False
```

它**永远不可能触发**，却一直报绿。**报绿的假守卫比没有守卫更坏**——它把"没检查"
伪装成"检查过"。而它能潜伏这么久，正是因为"守卫抓不抓得住"这件事**从来没被测过**：
守卫自己的测试测的是"当前状态干不干净"，不是"我这条判据有效吗"。

## 这个文件测的是什么

测**机制**（`guards.py` 里的函数），不测 `src/` 的当前状态。理由：

- 状态守卫的职责是"现在没问题"，它的失败方式是"有人引入了违规"；
- 而**判据本身的正确性**只能靠"给它一个违规输入，看它报不报"来测——
  这必须用**合成源码**，不能去改 `src/`（改 `src/` 的测试一旦崩在中间就留下残局，
  我审计时就踩过：`git checkout` 路径写错，变异残留在 5 个文件里）。

所以分工是：**这里的用例证明判据有效；真实守卫证明当前状态干净。** 两者合起来才完整。

## 还有一个容易漏的失败模式：判据对了但守卫没调用它

判据抽到 `guards.py` 之后，仍有人可能让守卫绕过它自己写一遍判据。所以最后一条
`test_every_guard_uses_the_shared_mechanisms` 直接扫守卫源码，确认它们真的调了共享函数。
"""

from pathlib import Path

from tests.architecture.guards import (
    await_inside_uow,
    code_identifiers,
    external_imports,
    harness_layer_violations,
    imported_layers,
    product_vocabulary_hits,
    runtime_modules,
    unregistered_packages,
)

ARCH = Path(__file__).parent


# ── 每条机制：给它一个违规，必须报 ────────────────────────────────────────────

#: (机制名, 违规输入, 违规说明)。这些是**合成源码**，不碰 `src/`。
MECHANISM_CASES = [
    ("external_imports", "import requests\n", "domain 引入外部包"),
    ("external_imports", "import httpx\n", "domain 引入外部包（另一个）"),
    ("foreign_context_imports", "from pmstudio.domain.round.round import DroppedItem\n",
     "上下文之间互相 import"),
    ("uses_symbol", "from x import DocumentWriter\n", "确认之外用了文档写入能力"),
    ("uses_symbol", "DocumentWriter.append(x)\n", "用属性方式调用写入能力"),
    ("forbidden_names", "return FakeModel()\n", "生产路径出现假模型"),
    ("forbidden_names", "StubGateway()\n", "生产路径出现替身"),
    ("imported_layers", "from pmstudio.infrastructure.sqlite import database\n", "layer 向外依赖"),
    ("imported_layers", "import pmstudio.harness.errors\n", "领域层反向依赖内层运行时"),
]


def test_every_mechanism_flags_the_violations_it_claims_to_catch():
    """逐条跑：机制名 → (违规源码, 说明)。报不出就是机制坏了。"""

    import tests.architecture.guards as guards

    failures: list[str] = []
    for name, source, why in MECHANISM_CASES:
        mechanism = getattr(guards, name)
        if name == "foreign_context_imports":
            hit = bool(mechanism(source, own="pmstudio.domain.authoring"))
        elif name == "uses_symbol":
            hit = bool(mechanism(source, "DocumentWriter"))
        else:
            hit = bool(mechanism(source))
        if not hit:
            failures.append(f"{name}: 没能抓住「{why}」→ {source.strip()!r}")

    assert failures == [], "这些机制对自己的违规无反应：\n  " + "\n  ".join(failures)


def test_type_checking_imports_counted_or_not_depends_on_the_mechanism():
    """**两个机制对 `TYPE_CHECKING` 的口径故意不同**，别把它们当成同一个。

    - `runtime_modules()` / `imported_layers()`（分层与 harness 守卫用）：**不算**。
      运行期不执行的东西不该判成"这条依赖方向错了"。
    - `external_imports()`（domain 守卫用）：**算**。domain 的规矩是"零外部依赖"，
      类型标注里引一个外部库也是依赖——真到运行期有人把它变成真 import 时，
      那才是真正该红的时候，而那时守卫已经在扫了。
    """

    source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import requests\n"

    assert runtime_modules(source) == {"typing"}, "运行期模块不该含 TYPE_CHECKING 里的"
    assert external_imports(source) == {"requests"}, "domain 的零外部依赖连类型标注也算"


def test_imported_layers_matches_on_prefix_not_equality():
    """**这条是死守卫那个 bug 的正面钉死。**

    `imported_layers()` 必须按**前缀**匹配完整点分名：
    `"pmstudio.application.ports"` 要能认出它是 `application` 层。
    如果哪条守卫改成 `f"{PACKAGE}.{layer}" in imported`（集合成员 = 精确相等），
    这条会红，而那条守卫会变成永远不触发的死代码。
    """

    for module, layer in [
        ("pmstudio.application.ports", "application"),
        ("pmstudio.domain.round.round", "domain"),
        ("pmstudio.infrastructure.sqlite.database", "infrastructure"),
        ("pmstudio.harness.model.failure", "harness"),
        ("pmstudio.api.routes", "api"),
        ("pmstudio.bootstrap.container", "bootstrap"),
    ]:
        assert layer in imported_layers(f"from {module} import x\n"), module

    assert imported_layers("from stdlib import nothing\n") == set()
    assert imported_layers("import pmstudio\n") == set()


def test_harness_layer_violations_flags_outward_imports():
    """**这条是那条死守卫修好后的行为契约。**

    `harness` 里 import 应用层 / 领域层 / 基础设施 / api / bootstrap，一律违规。
    判据是 `imported_layers()`（前缀匹配），不是字符串成员判断。
    """

    for module in (
        "pmstudio.application.ports",
        "pmstudio.domain.round.round",
        "pmstudio.infrastructure.sqlite.database",
        "pmstudio.api.routes",
        "pmstudio.bootstrap.container",
    ):
        expected = module.split(".")[1]
        assert harness_layer_violations(f"from {module} import x\n") == {expected}, module

    inward = "from pmstudio.harness.errors import ModelCallFailed\n"
    assert harness_layer_violations(inward) == set(), "harness 依赖自己内部是合法的"


def test_product_vocabulary_only_looks_at_identifiers():
    """产品词只查**代码标识符**，注释与字符串不算——那里在解释为什么。"""

    whitelisted = "round_id"  # 事件信封的关联键，白名单里的那条
    source = (
        '"""这个模块处理 round_id 的投影。"""\n'
        "# round_id 只是关联键\n"
        "TEXT = 'round_id'\n"
    )

    assert product_vocabulary_hits(source, allowed=frozenset({whitelisted})) == set()

    real = "def f(round_id):\n    return round_id\n"
    assert product_vocabulary_hits(real) == {"round_id"}
    assert product_vocabulary_hits(real, allowed=frozenset({"round_id"})) == set()


def test_code_identifiers_covers_the_four_forms():
    """标识符的四个来源：变量 / 参数 / 属性 / 定义名（改漏一个就是漏检）。"""

    found = code_identifiers("x = 1\ndef f(param):\n    return obj.attr\nclass C: ...\n")

    assert {"x", "f", "param", "attr", "C"} <= found


def test_await_inside_uow_only_catches_the_named_variable():
    """约定：变量名必须叫 `uow`。别的地方不扫（否则误报一片）。"""

    assert await_inside_uow("async def f(uow):\n    with uow:\n        await g()\n") == [3]
    outside = "async def f(uow):\n    await g()\n    with uow:\n        save()\n"
    assert await_inside_uow(outside) == []
    # async for / async with 也算（它们同样会挂起）
    looping = "async def f(uow):\n    with uow:\n        async for x in y: pass\n"
    assert await_inside_uow(looping) == [3]


def test_unregistered_package_detection(tmp_path):
    """没登记的顶层包 = 分层守卫对它**静默跳过**，所以必须有东西显式失败。"""

    package = tmp_path / "pmstudio"
    # 用**真实登记过的层名**当对照，否则测的是"未登记的层名"而不是"未登记的包"
    (package / "harness").mkdir(parents=True)
    (package / "harness" / "__init__.py").write_text("", encoding="utf-8")
    (package / "rogue").mkdir()
    (package / "rogue" / "__init__.py").write_text("", encoding="utf-8")

    assert unregistered_packages(tmp_path) == {"rogue"}


# ── 判据对了，还得有人真的用它 ────────────────────────────────────────────────


def test_every_guard_uses_the_shared_mechanisms():
    """**守卫不许绕过共享判据自己写一遍**——那正是死守卫的成因。

    左边是守卫文件，右边是它必须用到的机制名。少一个（或者守卫自己重新实现了一遍
    判据），这里会红。
    """

    required = {
        "test_domain_imports.py": ("external_imports", "foreign_context_imports"),
        "test_harness_imports.py": ("harness_layer_violations", "product_vocabulary_hits"),
        "test_layers.py": ("imported_layers", "unregistered_packages"),
        "test_no_await_in_uow.py": ("await_inside_uow", "await_inside_uow_offenders"),
        "test_no_fakes_in_production.py": ("forbidden_names",),
        "test_write_path.py": ("uses_symbol",),
        "test_resource_paths.py": ("iter_sources",),
        # `test_parse_entry_points` 按 `__all__` 名单 + 调用方扫描判定，不走这些机制；
        # `test_graph_state` 用 `inspect` 直接读类属性（它守的是"恰好三个键"这种
        # 字面量事实，没有可复用的 AST 机制）。两者不进这张表。
    }

    missing: dict[str, list[str]] = {}
    for filename, mechanisms in required.items():
        source = (ARCH / filename).read_text(encoding="utf-8")
        absent = [name for name in mechanisms if name not in source]
        if absent:
            missing[filename] = absent

    assert missing == {}, f"这些守卫没在用共享机制（可能自己重写了一遍判据）：{missing}"