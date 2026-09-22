import pytest

from pmstudio.bootstrap.settings import ConfigError, Settings


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
