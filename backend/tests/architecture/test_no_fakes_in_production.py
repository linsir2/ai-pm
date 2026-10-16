"""生产路径不许出现假实现；测试侧的替身必须**逐个登记**。

## 为什么后两条是这次补的（2026-10-04 审计发现）

`forbidden_names` 一直**只扫 `src/`**。而测试侧的替身对它是隐形的，于是这条不变式
有一个静默失效的路径：

1. 某个测试为了方便，让一个组件可替换（比如把 `MaterialStore` 换成内存实现）；
2. 生产装配层跟着 import 了测试模块——**这在运行期是成立的**，因为
   `pyproject.toml` 的 `pythonpath = ["src", "."]` 把项目根也放进了导入路径；
3. 于是一个"单用户本地应用"里跑着一套假实现，而**没有任何测试会红**。

第 2 步是真正危险的那一步，所以 `test_production_code_never_imports_the_tests_package`
单独立一条钉它。

而第 1 步本身不是问题（测试就该有替身），问题是它**不可见**。所以
`test_every_test_side_fake_is_registered` 要求每个替身都在
`ALLOWED_TEST_FAKES` 里登记一句理由——登记是一次显式决定，不登记是隐形的事实。
"""

from tests.architecture.guards import (
    SRC,
    forbidden_names,
    iter_sources,
    runtime_modules,
)

TESTS = SRC.parent / "tests"

#: 测试侧**故意**存在的替身 → 它为什么该留在测试里。
#:
#: 这张表与 `test_harness_imports.py` 的 `ALLOWED_PRODUCT_WORDS` 是同一个模式：
#: **显式欠账**，每条都要写清为什么。新增一条等于公开承认"这里需要一个替身"，
#: 评审时一眼可见；而不登记就意味着守卫会红，藏不住。
ALLOWED_TEST_FAKES: dict[str, str] = {
    "FakeTransaction": (
        "内存 UnitOfWork：测领域服务与整条闭环时不碰 SQLite（真库留给仓储自己的测试）。"
        "它实现的是 `pmstudio.domain.services` 用的那个协议，不是业务组件。"
    ),
    "MockTransport": (
        "**httpx 自带的**测试传输层，不是我们的替身：它让抓取器 / Tavily 客户端跑"
        "**真代码路径**（真护栏、真 bs4、真重试、真 JSON 解析），只是不出网。"
        "这正是 `test_fetch.py` 那六个护栏测试能测到真逻辑的原因。"
    ),
    "StubStream": (
        "收集发出的事件的假 SSE 流：`api/test_sse.py` 要断言发了什么、顺序如何，"
        "而挂真总线会把测试变成并发测试。"
    ),
    "_Fake": (
        "`runtime_checkable` 的协议是**结构化**的，要断言它就得有一个最小实现体。"
        "这里断言的是：任何有 `complete` 的东西都算网关，故意写得极简。"
    ),
    "fake_sleep": (
        "注入的 sleeper：退避时刻（1s / 3s）是**要被断言的行为**，真睡会让测试慢到"
        "不可用。生产侧的 `asyncio.sleep` 从不被它替换（M4b 起那是构造参数）。"
    ),
    "fake": (
        "`test_gateway_resilience.py` 里替换 `acompletion` 的桩：韧性这几条断言的是"
        "**网关有没有真的走熔断/隔离舱/预算**（比如\"闸门开了之后一个上游包都不发\"），"
        "要数请求次数就不能不发真请求——真打上游既慢又不确定。"
        "被验的代码路径（`LiteLLMGateway._call`）是**真的**。"
    ),
    "_fake_response": (
        "上行那个桩 `fake` 的回包形状：litellm 只需要 `choices[0].message.content`，"
        "而完整响应对象有一堆与断言无关的字段。"
    ),
    "use_fake_model": (
        "`test_container.py` 在断言**守卫本身**：生产容器**没有**这个开关。"
        "这条断言必须提到这个名字，提到它才是那条断言。"
    ),
    "test_guard_flags_fake_names": (
        "这条守卫自己的单测：它要拿一个含 fake 的名字去试判据，"
        "所以它自己必然含这个词。"
    ),
    "test_production_code_has_no_fake_implementations": (
        "同上——守卫自己那条断言的名字里就带 fake。"
    ),
    "test_every_test_side_fake_is_registered": "同上：这条断言自己的名字。",
    "ALLOWED_TEST_FAKES": (
        "**这条守卫自己**的常量名。它必然要提到它禁的东西——"
        "所以守卫文件本身也会被自己扫到。"
        "这里选择登记而不是给它开特例：特例是藏人的地方，登记是明示的。"
    ),
    "test_container_has_no_fake_model_switch": (
        "它在断言生产容器**没有**这个开关——提到它是那条断言本身的一部分。"
    ),
}


def test_guard_flags_fake_names():
    assert forbidden_names("def make_fake_gateway():\n    ...\n") == {"make_fake_gateway"}


def test_production_code_has_no_fake_implementations():
    offenders = {
        path.relative_to(SRC).as_posix(): sorted(names)
        for path, source in iter_sources(SRC)
        if (names := forbidden_names(source))
    }
    assert offenders == {}


def test_production_code_never_imports_the_tests_package():
    """**`src/` 里不许 import 任何 `tests.*` 的东西**——这是那个静默失效的关键一步。

    它能发生，是因为 `pyproject.toml` 的 `pythonpath = ["src", "."]` 把项目根
    也放进了导入路径，所以 `from tests.support.fakes import FakeTransaction`
    在运行期是**成立的**，不会报 ImportError。
    """

    offenders: dict[str, list[str]] = {}
    for path, source in iter_sources(SRC):
        leaked = sorted(
            module for module in runtime_modules(source) if module.split(".")[0] == "tests"
        )
        if leaked:
            offenders[path.relative_to(SRC).as_posix()] = leaked

    assert offenders == {}, (
        f"生产代码 import 了测试模块：{offenders}。"
        "测试替身漏进生产路径时不会报 ImportError（项目根在 pythonpath 上），"
        "所以必须靠这条"
    )


def test_every_test_side_fake_is_registered():
    """测试侧的每个替身都要在 `ALLOWED_TEST_FAKES` 里登记一句理由。

    查两个方向：
    - 有替身没登记 → 红（那是隐形的既成事实）；
    - 登记了但已经不存在 → 红（否则这张表自己腐烂，而腐烂的表没人会去看）。
    """

    found: dict[str, set[str]] = {}
    for path, source in iter_sources(TESTS):
        names = forbidden_names(source)
        if names:
            found[path.relative_to(TESTS).as_posix()] = names

    everything = set().union(*found.values()) if found else set()

    unregistered = sorted(everything - set(ALLOWED_TEST_FAKES))
    assert unregistered == [], (
        f"这些测试替身没有登记理由：{unregistered}（出现在 "
        f"{sorted(k for k, v in found.items() if v & set(unregistered))}）"
    )

    stale = sorted(set(ALLOWED_TEST_FAKES) - everything)
    assert stale == [], f"ALLOWED_TEST_FAKES 里这些替身已经不存在了，请删掉条目：{stale}"

    for name, why in ALLOWED_TEST_FAKES.items():
        assert why.strip(), f"{name} 必须写清它为什么该留在测试里"
