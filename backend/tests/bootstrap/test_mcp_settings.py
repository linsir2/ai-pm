"""`PMSTUDIO_MCP_SERVERS` 的解析测试。

**为什么单独一个文件**：MCP 配置是**可选能力**的配置——
它缺了不该起不来（与 `PMSTUDIO_MODEL` 缺了就起不来形成对照），
但**写错了必须在起服务前就说清楚**。
"""

from __future__ import annotations

import pytest

from pmstudio.bootstrap.settings import ConfigError, Settings


def _env(**extra: str) -> dict[str, str]:
    base = {"PMSTUDIO_MODEL": "openai/x", "PMSTUDIO_API_KEY": "k"}
    base.update(extra)
    return base


# —— R（需求）：缺了不是错 ——


def test_missing_mcp_config_is_not_an_error() -> None:
    """没配 MCP → 正常起来，只是没有 MCP 工具。"""

    settings = Settings.from_env(_env())
    assert settings.mcp_servers == ()


def test_empty_mcp_config_is_not_an_error() -> None:
    settings = Settings.from_env(_env(PMSTUDIO_MCP_SERVERS=""))
    assert settings.mcp_servers == ()


# —— R（需求）：写错了要说清楚 ——


def test_malformed_mcp_config_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(_env(PMSTUDIO_MCP_SERVERS="not json"))


def test_mcp_config_must_be_an_array() -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(_env(PMSTUDIO_MCP_SERVERS='{"name": "x"}'))


def test_an_mcp_entry_without_a_name_is_rejected() -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(_env(PMSTUDIO_MCP_SERVERS='[{"transport": "stdio"}]'))


def test_an_mcp_entry_with_bad_args_is_rejected() -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(
            _env(PMSTUDIO_MCP_SERVERS='[{"name": "x", "args": "not-an-array"}]')
        )


# —— I（实现）：解析 ——


def test_mcp_servers_are_parsed_into_specs() -> None:
    settings = Settings.from_env(
        _env(
            PMSTUDIO_MCP_SERVERS=(
                '[{"name": "fs", "transport": "stdio", "command": "npx", "args": ["-y", "srv"]},'
                ' {"name": "web", "transport": "http", "url": "http://127.0.0.1:9000/mcp"}]'
            )
        )
    )

    assert len(settings.mcp_servers) == 2
    first = settings.mcp_servers[0]
    assert first["name"] == "fs"
    assert first["transport"] == "stdio"
    assert first["command"] == "npx"
    assert first["args"] == ["-y", "srv"]
    second = settings.mcp_servers[1]
    assert second["transport"] == "http"
    assert second["url"] == "http://127.0.0.1:9000/mcp"


def test_the_transport_defaults_to_stdio() -> None:
    settings = Settings.from_env(
        _env(PMSTUDIO_MCP_SERVERS='[{"name": "fs", "command": "npx"}]')
    )
    assert settings.mcp_servers[0]["transport"] == "stdio"


# —— R（回归）：现有配置一行没变 ——


def test_the_existing_settings_behaviour_is_unchanged() -> None:
    """没有 MCP 时，`Settings` 的其它字段一个都不受影响。"""

    settings = Settings.from_env(_env(PMSTUDIO_MODEL_TIMEOUT="90"))
    assert settings.model == "openai/x"
    assert settings.api_key == "k"
    assert settings.model_timeout == 90
