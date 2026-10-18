"""工具目录（2026-10-04 · M5 从 `application/ports.py` + `infrastructure/tools/catalog.py` 搬来）。

三个东西合成一个文件，因为它们本来是一件事：
`ToolDefinition`（数据）+ `ToolCatalog`（协议）+ `TomlToolCatalog`（唯一的实现）。

**这个文件不许有业务词**——`harness/tools/registry.py` 在架构守卫的
`PRODUCT_VOCABULARY` 检查范围内（`tool_id` / `requires` / `max_queries` 都不在表里）。

**还有 8 个字段被砍掉**（`risk_level` / `side_effect` / `idempotent` / `approval_policy` /
`required_permissions` / `filesystem_access` / `max_retries` / `timeout_seconds`）：它们在
`03-案例与验收.md` 里零定义、全仓零命中、且**没有任何消费者**——填它们只能靠编，编完没人读。
`network_access` 已于 2026-10-18 从这张名单里移出（复述卡的授权披露是它的第一个消费者）。
字段准入的道理与这次补记**以 `registry.py` 的模块 docstring 为唯一真相**，这里不复述。
"""

import pytest

from pmstudio.harness.tools.registry import (
    DEFINITIONS_DIR,
    TomlToolCatalog,
    ToolCatalog,
    ToolDefinition,
)

__all__ = [
    "DEFINITIONS_DIR",
    "TomlToolCatalog",
    "ToolCatalog",
    "ToolDefinition",
]


def test_catalog_reads_toml_and_filters_by_required_keys():
    """`requires` 决定"没配密钥时它到底出不出现"。

    口径（P17 §9·9）：**没配密钥 = 这个工具不可用**（复述卡不出现那一栏），
    不是"假成功"。
    """

    catalog = TomlToolCatalog(DEFINITIONS_DIR)

    assert {item.tool_id for item in catalog.all()} == {"tool.attachment", "tool.web_search"}
    assert "tool.web_search" not in {item.tool_id for item in catalog.available(has=frozenset())}


def test_a_configured_key_makes_the_tool_appear():
    """配了密钥 → 联网搜索那栏出现。这是"有就显、没有就消失"的正向那一半。"""

    catalog = TomlToolCatalog(DEFINITIONS_DIR)

    available = {item.tool_id for item in catalog.available(has=frozenset({"tavily_api_key"}))}

    assert available == {"tool.attachment", "tool.web_search"}


def test_the_limits_really_come_from_the_toml():
    """**TOML ↔ 代码的联结点**：`max_queries` / `max_results` 必须真的读进来。

    为什么单独钉这一条：M5 把 `DEFAULT_WEB_QUERY_LIMIT=3` / `DEFAULT_WEB_LIMIT=5`
    两个兜底常量删了（它们与 TOML 里的 `max_queries = 3` / `max_results = 5` 数值重复，
    改一处就漂）。删掉之后，"目录声明的值"就成了唯一真相——这条测试证明它真的到了。
    搬错字段名（比如把 `max_queries` 写成 `max_query`）时它会红，而 `available()` 那几条不会。
    """

    catalog = TomlToolCatalog(DEFINITIONS_DIR)
    tools = {item.tool_id: item for item in catalog.all()}

    assert tools["tool.web_search"].max_queries == 3
    assert tools["tool.web_search"].max_results == 5


def test_a_tool_without_limits_declares_zero_not_a_fallback():
    """`attachment` 不是联网工具，没有查询上限的概念 → 目录不声明 → 读到 0。

    0 的含义是"没有上限可用"，调用方必须自己判；**不许**在这里塞一个兜底数字，
    那是 M5 删掉的那两个常量的老毛病（同一个值写在 TOML 与 Python 两处）。
    """

    catalog = TomlToolCatalog(DEFINITIONS_DIR)
    tools = {item.tool_id: item for item in catalog.all()}

    assert tools["tool.attachment"].max_queries == 0
    assert tools["tool.attachment"].max_results == 0


def test_a_tool_with_no_external_dependency_is_always_available():
    """上传的材料不需要任何外部依赖 → `requires` 为空 → 永远可用。"""

    catalog = TomlToolCatalog(DEFINITIONS_DIR)
    tools = {item.tool_id: item for item in catalog.all()}

    assert tools["tool.attachment"].requires == ()


def test_all_returns_the_same_cached_tuple_and_sharing_it_is_safe():
    """`all()` 缓存解析结果，重复调用返回**同一个元组对象**。

    共用同一个对象安全，依据是两层不可变：元组本身不可变，元素是 frozen dataclass。
    所以调用方拿到的任何一份都不可能被改坏，不需要"保险"每次新建一个。

    这条同时钉住"缓存的是元组本身"这个事实——将来若有人改成每次新建元组，它会红，
    并被提醒去说清为什么（目前没有理由）。
    """

    catalog = TomlToolCatalog(DEFINITIONS_DIR)
    first, second = catalog.all(), catalog.all()

    assert first == second
    assert first is second
    # 不可变正是共用安全的根据，所以把这条也钉住：元素确实改不动
    with pytest.raises(AttributeError):  # FrozenInstanceError 是 AttributeError 的子类
        first[0].title = "改过了"  # type: ignore[misc]


def test_the_definitions_directory_is_next_to_the_module_that_reads_it():
    """**路径跟随资源**：目录由模块自己算，不许调用方各自重算。

    为什么这条必须钉死（2026-10-04 M5 的教训）：搬目录**不产生任何 import 错误**——
    `from … import X` 在文件还在时照样通过，只在**读 `.toml` / `.md` 时**才炸。
    而当时有 5 处各自重算同一路径（`container.py`、`test_llm.py`、`test_analyzer.py`、
    `test_catalog.py`、`test_seats_catalog.py`），搬完只会红在其中一处，
    剩下几处**静默地指向一个不存在的目录**。

    所以路径常量定义在**它所指向的资源旁边**，调用方直接 import，不许拼。
    """

    from pathlib import Path

    import pmstudio.harness.tools.registry as registry

    assert DEFINITIONS_DIR == Path(registry.__file__).parent / "definitions"
    assert DEFINITIONS_DIR.is_dir(), f"{DEFINITIONS_DIR} 不存在：搬目录时漏了数据文件"
    assert sorted(path.name for path in DEFINITIONS_DIR.glob("*.toml")) == [
        "attachment.toml",
        "web_search.toml",
    ]


def test_the_catalog_protocol_is_structural_not_nominal():
    """`TomlToolCatalog` 满足 `ToolCatalog` 协议——换实现不用改调用方。"""

    catalog = TomlToolCatalog(DEFINITIONS_DIR)

    assert isinstance(catalog, ToolCatalog)


def test_tool_definitions_are_frozen():
    """声明是**配置**：运行时改它就等于悄悄改了产品行为，不许。"""

    definition = TomlToolCatalog(DEFINITIONS_DIR).all()[0]

    try:
        definition.title = "改过了"  # type: ignore[misc]
    except Exception as error:
        assert type(error).__name__ in ("FrozenInstanceError", "AttributeError")
    else:
        raise AssertionError("ToolDefinition 竟然可以被改")


def test_network_access_comes_from_the_toml(tmp_path):
    """R：工具会不会把数据发出本机，写在它的 TOML 里（不许在 Python 里猜）。"""
    (tmp_path / "a.toml").write_text(
        'tool_id = "tool.a"\ntitle = "A"\nkind = "a"\nnetwork_access = true\n',
        encoding="utf-8",
    )

    assert TomlToolCatalog(tmp_path).all()[0].network_access is True


def test_network_access_defaults_to_false(tmp_path):
    """R：缺省 = 不碰外网（新工具默认不外发，保守的那一侧）。"""
    (tmp_path / "b.toml").write_text(
        'tool_id = "tool.b"\ntitle = "B"\nkind = "b"\n', encoding="utf-8"
    )

    assert TomlToolCatalog(tmp_path).all()[0].network_access is False


def test_the_real_catalog_agrees_with_reality():
    """R：真目录里联网工具声明为真、本地工具声明为假——TOML 写歪要当场红。"""
    by_id = {item.tool_id: item for item in TomlToolCatalog(DEFINITIONS_DIR).all()}

    assert by_id["tool.web_search"].network_access is True
    assert by_id["tool.attachment"].network_access is False


def test_the_field_set_is_deliberate():
    """守卫：字段集合钉死——**加字段必须是一次自觉的改动**。

    `registry.py` 的 docstring 明写"只加**有消费者**的字段"（曾一次拒掉 9 个）。
    这条把那句人话变成机器判据：谁要加字段，就得先在这里解释它的消费者是谁。
    """
    from dataclasses import fields

    assert {f.name for f in fields(ToolDefinition)} == {
        "tool_id", "title", "kind", "requires",
        "max_queries", "max_results", "network_access",
    }
