"""harness 是最内层：只许依赖自己 + 白名单里的第三方。

这条守卫回答一个问题：**"模型能力"这一层有没有开始长业务词汇。**
它比"文件放对目录"更难糊弄：一旦 harness import 了 `pmstudio.application`，
依赖方向就倒过来了，整套分层声明失效。
"""

import sys

from tests.architecture.guards import (
    PACKAGE,
    SRC,
    harness_layer_violations,
    iter_sources,
    product_vocabulary_hits,
    top_level_imports,
)

HARNESS = SRC / PACKAGE / "harness"

#: 允许出现在 harness 里的第三方。两个，理由：
#: - `pydantic`：输出归一化引擎的对象就是 pydantic 模型（model_fields / annotation /
#:   model_validate / errors()），把它抽象掉是为不存在的需求建抽象。
#: - `litellm`：**供应商调用本身就是模型层的职责**（`model/providers/litellm.py`）。
#:   不把它搬进来，`ModelGateway` 就只是个协议，实现仍在仓外——"模型层可独立抽出"
#:   这个目标等于没达成。换 provider 要改 harness 是真的，但控制平面知道
#:   "调谁"跟知道"产品有哪些章节"完全是两回事。
ALLOWED_THIRD_PARTY = frozenset({"pydantic", "litellm"})

#: 标准库全集由解释器给出，不手抄一份（手抄必然漏，然后漏的那条就成了免检通道）。
_STDLIB = frozenset(sys.stdlib_module_names)

#: 显式欠账：{文件 : 词汇} → 为什么还在、什么时候消。见下面那条"只许减"的测试。
ALLOWED_PRODUCT_WORDS: dict[tuple[str, str], str] = {
    # `Event` 的关联键：这封通知属于哪一轮。信封**不按它分支、不组装它**，
    # 它只是随通知带出去的定位信息。改名会动 SSE 契约（前端 `workspace.ts` 按
    # `event.round_id` 过滤），而事件名与 payload 键这一层是不许改的——所以先登记。
    ("events/schemas.py", "round_id"): "关联键，不是产品知识；改名会破 SSE 契约",
}


def test_harness_layer_exists_and_is_not_empty():
    """空目录会让下面所有断言变成空转——先确认它真的有住户。"""

    assert HARNESS.is_dir(), f"{HARNESS} 不存在：新增最内层必须先建目录"
    assert list(iter_sources(HARNESS)), f"{HARNESS} 里没有 .py：空壳不是分层"


def test_harness_never_imports_an_application_layer():
    """harness 是最内层，**不许反向依赖**应用 / 领域 / 基础设施 / api / 装配。

    判据在 `guards.harness_layer_violations()`，它走 `imported_layers()` 的前缀匹配。
    **这里曾经是一句 `f"{PACKAGE}.{layer}" in imported`**——集合成员判断是精确相等，
    而被比的是完整点分名 `pmstudio.application.ports`，于是那个判据**永远为假**，
    这条守卫从落地起一次都没触发过，却一直报绿。

    为什么两种写法差别这么大：裸子串会把注释与 docstring 算进来（将来谁在文档里写一句
    "`RoundId` 来自 `pmstudio.domain.shared.ids`"就会红，而报错说的是"harness import 了
    application"——那不是事实，会白烧一个人一小时）；而集合成员判断则会漏掉真正的导入。
    正确写法只有一种：**AST + 前缀匹配**，也就是共享机制那个。
    它的有效性由 `guard_coverage.py::test_harness_layer_violations_flags_outward_imports` 钉住。
    """

    offenders: dict[str, list[str]] = {}
    for path, source in iter_sources(HARNESS):
        bad = sorted(harness_layer_violations(source))
        if bad:
            offenders[path.relative_to(HARNESS).as_posix()] = bad
    assert offenders == {}


def test_harness_imports_only_stdlib_and_the_whitelisted_third_party():
    """与 domain 那条守卫**故意相反**：

    - `domain` 用窄 stdlib 白名单（`guards.ALLOWED_STDLIB`）——`sqlite3` / `json` 出现在
      domain 里就是设计错误，所以要逐个列；
    - `harness` 用**任意 stdlib + 窄第三方白名单**——它本来就是运行时层，`asyncio` / `json`
      / `hashlib` 是它的工具；但第三方只许 pydantic 一个（理由见 `ALLOWED_THIRD_PARTY`）。

    判据反过来是因为要防的东西不同：domain 防"偷偷做 IO"，harness 防"长出业务依赖"。
    """

    offenders: dict[str, list[str]] = {}
    for path, source in iter_sources(HARNESS):
        outside = sorted(
            top_level_imports(source) - _STDLIB - {PACKAGE} - ALLOWED_THIRD_PARTY
        )
        if outside:
            offenders[path.relative_to(HARNESS).as_posix()] = outside
    assert offenders == {}


def test_harness_code_identifiers_carry_no_product_vocabulary():
    """`harness` 的**代码标识符**里不许出现产品词汇（注释与 docstring 不算——那里在解释为什么）。

    这条比"有没有 import 业务层"更难糊弄：依赖方向对，但变量名叫 `section_id` 的
    控制平面代码，半年后没人说得清它是控制平面还是业务。

    `ALLOWED` 是**显式欠账**，不是开口子：每一条都要写清为什么还在、什么时候消。
    """

    offenders: dict[str, list[str]] = {}
    for path, source in iter_sources(HARNESS):
        here = path.relative_to(HARNESS).as_posix()
        # 白名单必须**按文件**生效：键是 (文件, 词)，只按词放行等于把欠账扩散到全层——
        # 那道守卫唯一的职责就是回答"harness 有没有开始长业务词汇"。
        allowed = frozenset(word for (where, word) in ALLOWED_PRODUCT_WORDS if where == here)
        hits = sorted(product_vocabulary_hits(source, allowed=allowed))
        if hits:
            offenders[here] = hits
    assert offenders == {}


def test_the_allowlist_has_not_grown():
    """白名单只许减，不许增——新增一条等于给"产品词汇进控制平面"开口子。

    M4 消掉了一条：留样协议（`ModelCallRecorder.record`）的关联键从 `round_id`
    改名 `trace_id`。剩下的**只有一条**，它是关联键（随通知带出去的定位信息），
    不是"harness 按产品词分支"。
    真要新增一条，先问：它是定位信息，还是控制逻辑？后者一律不许进。
    """

    assert set(ALLOWED_PRODUCT_WORDS) == {
        ("events/schemas.py", "round_id"),
    }
    for (where, word), why in ALLOWED_PRODUCT_WORDS.items():
        assert why.strip(), f"{where} 的 {word} 必须写清为什么还在、什么时候消"

