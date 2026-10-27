"""`PMSTUDIO_MCP_SERVERS` 的解析测试。

**为什么单独一个文件**：MCP 配置是**可选能力**的配置——
它缺了不该起不来（与 `PMSTUDIO_MODEL` 缺了就起不来形成对照），
但**写错了必须在起服务前就说清楚**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pmstudio.bootstrap.settings import ConfigError, Settings

BACKEND = Path(__file__).resolve().parents[2]


def _env(**extra: str) -> dict[str, str]:
    base = {
        "PMSTUDIO_MODEL": "openai/x",
        "PMSTUDIO_API_KEY": "k",
        "PMSTUDIO_DATABASE_URL": "postgresql://app@pgbouncer/pmstudio",
    }
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


def test_a_duplicate_server_name_is_rejected() -> None:
    """同名 server 在**配置解析**阶段就报错（B5）。

    工具名带来源前缀（`mcp__<server>__<tool>`），两个同名 server 会产出**同名工具**——
    重复的 function name 让整个请求 400，而运行时路由只会命中后写的那一个（静默）。
    配置期拦住比运行时猜强。
    """

    with pytest.raises(ConfigError):
        Settings.from_env(
            _env(
                PMSTUDIO_MCP_SERVERS=(
                    '[{"name": "dup", "transport": "http", "url": "http://a/mcp"},'
                    ' {"name": "dup", "transport": "http", "url": "http://b/mcp"}]'
                )
            )
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


def test_the_working_directory_is_passed_through() -> None:
    """`cwd` 要透传到配置里（C4）：stdio server 不再依赖**进程**的 CWD。

    不给 `cwd` 时子进程继承本进程的 CWD——配置示例里写的是相对路径
    （`scripts/mcp_demo_server.py`），只有 CWD 恰好是 `backend/` 时才成立。
    """

    settings = Settings.from_env(
        _env(PMSTUDIO_MCP_SERVERS='[{"name": "fs", "command": "npx", "cwd": "/srv/app"}]')
    )
    assert settings.mcp_servers[0]["cwd"] == "/srv/app"

    default = Settings.from_env(
        _env(PMSTUDIO_MCP_SERVERS='[{"name": "fs", "command": "npx"}]')
    )
    assert default.mcp_servers[0]["cwd"] == ""


# —— R（回归）：现有配置一行没变 ——


def test_the_existing_settings_behaviour_is_unchanged() -> None:
    """没有 MCP 时，`Settings` 的其它字段一个都不受影响。"""

    settings = Settings.from_env(_env(PMSTUDIO_MODEL_TIMEOUT="90"))
    assert settings.model == "openai/x"
    assert settings.api_key == "k"
    assert settings.model_timeout == 90


def test_the_env_template_documents_the_only_consumer() -> None:
    """`.env.example` 必须写明 `PMSTUDIO_MCP_SERVERS` 的**当前消费者**（C2）。

    不写的话，配了 MCP 的人会以为主服务真的连上了——而实际上只有
    `scripts/mcp_probe.py` 读它。**配置看起来生效了、其实没有**是最难查的那类问题。
    """

    template = BACKEND / ".env.example"
    assert template.is_file(), "缺 .env.example"
    text = template.read_text(encoding="utf-8")
    assert "PMSTUDIO_MCP_SERVERS" in text
    assert "scripts/mcp_probe.py" in text, (
        ".env.example 没写明 PMSTUDIO_MCP_SERVERS 当前只有 scripts/mcp_probe.py 读"
    )
