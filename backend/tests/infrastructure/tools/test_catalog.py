"""工具目录：一行一个工具，`requires` 决定"没配密钥时它到底出不出现"。

口径（P17 §9·9）：**没配密钥 = 这个工具不可用**（复述卡不出现那一栏），不是"假成功"。
"""


from pmstudio.bootstrap.settings import Settings
from pmstudio.harness.tools.registry import DEFINITIONS_DIR, TomlToolCatalog

#: 工具声明目录**从它所属的模块引**（M5 起）。原先这里独立重算了一遍路径，
#: 搬目录时只有这一处会红，其余按旧路径算的调用方会静默指向空目录。
TOOLS_DIR = DEFINITIONS_DIR


def test_catalog_reads_toml_and_filters_by_required_keys():
    catalog = TomlToolCatalog(TOOLS_DIR)

    assert {item.tool_id for item in catalog.all()} == {"tool.attachment", "tool.web_search"}
    assert "tool.web_search" not in {item.tool_id for item in catalog.available(has=frozenset())}
    assert "tool.web_search" in {
        item.tool_id for item in catalog.available(has=frozenset({"tavily_api_key"}))
    }

    web = next(item for item in catalog.all() if item.tool_id == "tool.web_search")
    assert (web.max_queries, web.max_results) == (3, 5)
    assert web.title == "联网搜索（Tavily）"


def test_attachment_tool_never_needs_a_key():
    catalog = TomlToolCatalog(TOOLS_DIR)

    assert "tool.attachment" in {item.tool_id for item in catalog.available(has=frozenset())}


def test_settings_reads_the_tavily_key_and_tolerates_its_absence():
    base = {"PMSTUDIO_MODEL": "openai/x", "PMSTUDIO_API_KEY": "k"}

    assert Settings.from_env(base).tavily_api_key is None
    settings = Settings.from_env({**base, "PMSTUDIO_TAVILY_API_KEY": " tvly-x "})
    assert settings.tavily_api_key == "tvly-x"


def test_settings_expose_which_tools_are_available():
    base = {"PMSTUDIO_MODEL": "openai/x", "PMSTUDIO_API_KEY": "k"}

    assert Settings.from_env(base).available_tool_keys() == frozenset()
    settings = Settings.from_env({**base, "PMSTUDIO_TAVILY_API_KEY": "tvly-x"})
    assert settings.available_tool_keys() == frozenset({"tavily_api_key"})
