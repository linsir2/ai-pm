import pytest

from pmstudio.bootstrap.settings import DEFAULT_MODEL_TIMEOUT, ConfigError, Settings


def test_settings_read_model_key_and_db_path(tmp_path):
    settings = Settings.from_env(
        {
            "PMSTUDIO_MODEL": "openai/gpt-4o-mini",
            "PMSTUDIO_API_KEY": "sk-test",
            "PMSTUDIO_API_BASE": "https://example.invalid/v1",
            "PMSTUDIO_DB": str(tmp_path / "pmstudio.db"),
        },
        default_db=tmp_path / "default.db",
    )
    assert settings.model == "openai/gpt-4o-mini"
    assert settings.api_key == "sk-test"
    assert settings.api_base == "https://example.invalid/v1"
    assert settings.db_path == tmp_path / "pmstudio.db"


def test_settings_default_db_path_is_used(tmp_path):
    settings = Settings.from_env(
        {"PMSTUDIO_MODEL": "m", "PMSTUDIO_API_KEY": "k"}, default_db=tmp_path / "d.db"
    )
    assert settings.db_path == tmp_path / "d.db"


def test_settings_bad_missing_model_is_a_hard_error(tmp_path):
    """没有模型配置就直接失败——生产路径不提供"用假模型"的降级分支。"""

    with pytest.raises(ConfigError, match="PMSTUDIO_MODEL"):
        Settings.from_env({"PMSTUDIO_API_KEY": "k"}, default_db=tmp_path / "d.db")


def test_settings_bad_missing_key_is_a_hard_error(tmp_path):
    with pytest.raises(ConfigError, match="PMSTUDIO_API_KEY"):
        Settings.from_env({"PMSTUDIO_MODEL": "m"}, default_db=tmp_path / "d.db")


def test_settings_model_timeout_defaults_and_reads_env(tmp_path):
    """超时是配置项：不填有默认值，填了要生效，填错要当场报错（不做静默兜底）。"""

    base = {"PMSTUDIO_MODEL": "m", "PMSTUDIO_API_KEY": "k"}

    assert (
        Settings.from_env(base, default_db=tmp_path / "d.db").model_timeout
        == DEFAULT_MODEL_TIMEOUT
    )
    assert (
        Settings.from_env(
            {**base, "PMSTUDIO_MODEL_TIMEOUT": "12.5"}, default_db=tmp_path / "d.db"
        ).model_timeout
        == 12.5
    )
    with pytest.raises(ConfigError, match="PMSTUDIO_MODEL_TIMEOUT"):
        Settings.from_env(
            {**base, "PMSTUDIO_MODEL_TIMEOUT": "abc"}, default_db=tmp_path / "d.db"
        )
    with pytest.raises(ConfigError, match="PMSTUDIO_MODEL_TIMEOUT"):
        Settings.from_env(
            {**base, "PMSTUDIO_MODEL_TIMEOUT": "0"}, default_db=tmp_path / "d.db"
        )


def test_settings_model_params_reads_json(tmp_path):
    """供应商特有参数（如 DashScope 关思考）走配置，不进代码。"""

    settings = Settings.from_env(
        {
            "PMSTUDIO_MODEL": "m",
            "PMSTUDIO_API_KEY": "k",
            "PMSTUDIO_MODEL_PARAMS": '{"extra_body": {"enable_thinking": false}}',
        },
        default_db=tmp_path / "d.db",
    )

    assert settings.model_params == {"extra_body": {"enable_thinking": False}}
    assert Settings.from_env(
        {"PMSTUDIO_MODEL": "m", "PMSTUDIO_API_KEY": "k"}, default_db=tmp_path / "d.db"
    ).model_params == {}


def test_settings_model_params_strips_shell_quotes(tmp_path):
    """`.env.example` 要求 JSON 值带单引号（`source` 才吃得下，learnings L19），
    但 docker `--env-file` / compose `env_file` 不剥引号（原样注入）。
    配置解析在这里统一剥一层——原生（dev.py 已剥过）是 no-op，容器里是救命。
    """

    settings = Settings.from_env(
        {
            "PMSTUDIO_MODEL": "m",
            "PMSTUDIO_API_KEY": "k",
            "PMSTUDIO_MODEL_PARAMS": '\'{"extra_body": {"enable_thinking": false}}\'',
        },
        default_db=tmp_path / "d.db",
    )

    assert settings.model_params == {"extra_body": {"enable_thinking": False}}


def test_settings_model_params_bad_json_is_a_hard_error(tmp_path):
    with pytest.raises(ConfigError, match="PMSTUDIO_MODEL_PARAMS"):
        Settings.from_env(
            {
                "PMSTUDIO_MODEL": "m",
                "PMSTUDIO_API_KEY": "k",
                "PMSTUDIO_MODEL_PARAMS": "{不是 JSON}",
            },
            default_db=tmp_path / "d.db",
        )
    with pytest.raises(ConfigError, match="PMSTUDIO_MODEL_PARAMS"):
        Settings.from_env(
            {
                "PMSTUDIO_MODEL": "m",
                "PMSTUDIO_API_KEY": "k",
                "PMSTUDIO_MODEL_PARAMS": "[1, 2]",
            },
            default_db=tmp_path / "d.db",
        )
