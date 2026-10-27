import pytest

from pmstudio.bootstrap.settings import (
    DEFAULT_MODEL_TIMEOUT,
    AppSettings,
    BootstrapSettings,
    ConfigError,
    OpsSettings,
)

APP_URL = "postgresql://app:secret@pgbouncer:6432/pmstudio"
DIRECT_URL = "postgresql://owner:secret@db:5432/pmstudio"


def app_env(**extra):
    return {
        "PMSTUDIO_MODEL": "openai/gpt-4o-mini",
        "PMSTUDIO_API_KEY": "sk-test",
        "PMSTUDIO_DATABASE_URL": APP_URL,
        **extra,
    }


def test_app_settings_read_database_url_model_and_key():
    settings = AppSettings.from_env(
        app_env(
            PMSTUDIO_API_BASE="https://example.invalid/v1",
        )
    )
    assert settings.database_url == APP_URL
    assert settings.model == "openai/gpt-4o-mini"
    assert settings.api_key == "sk-test"
    assert settings.api_base == "https://example.invalid/v1"


def test_app_settings_has_no_sqlite_path():
    settings = AppSettings.from_env(app_env())
    assert not hasattr(settings, "db_path")


def test_app_settings_missing_database_url_is_a_hard_error():
    with pytest.raises(ConfigError, match="PMSTUDIO_DATABASE_URL"):
        AppSettings.from_env(
            {"PMSTUDIO_MODEL": "m", "PMSTUDIO_API_KEY": "k"}
        )


def test_app_settings_bad_missing_model_is_a_hard_error():
    """没有模型配置就直接失败——生产路径不提供"用假模型"的降级分支。"""

    with pytest.raises(ConfigError, match="PMSTUDIO_MODEL"):
        AppSettings.from_env(
            {"PMSTUDIO_API_KEY": "k", "PMSTUDIO_DATABASE_URL": APP_URL}
        )


def test_app_settings_bad_missing_key_is_a_hard_error():
    with pytest.raises(ConfigError, match="PMSTUDIO_API_KEY"):
        AppSettings.from_env(
            {"PMSTUDIO_MODEL": "m", "PMSTUDIO_DATABASE_URL": APP_URL}
        )


def test_app_settings_rejects_old_pmstudio_db_instead_of_falling_back():
    with pytest.raises(ConfigError, match="PMSTUDIO_DATABASE_URL"):
        AppSettings.from_env(
            {
                "PMSTUDIO_MODEL": "m",
                "PMSTUDIO_API_KEY": "k",
                "PMSTUDIO_DB": "data/pmstudio.db",
            }
        )


def test_app_settings_model_timeout_defaults_and_reads_env():
    """超时是配置项：不填有默认值，填了要生效，填错要当场报错（不做静默兜底）。"""

    assert AppSettings.from_env(app_env()).model_timeout == DEFAULT_MODEL_TIMEOUT
    assert (
        AppSettings.from_env(app_env(PMSTUDIO_MODEL_TIMEOUT="12.5")).model_timeout
        == 12.5
    )
    with pytest.raises(ConfigError, match="PMSTUDIO_MODEL_TIMEOUT"):
        AppSettings.from_env(app_env(PMSTUDIO_MODEL_TIMEOUT="abc"))
    with pytest.raises(ConfigError, match="PMSTUDIO_MODEL_TIMEOUT"):
        AppSettings.from_env(app_env(PMSTUDIO_MODEL_TIMEOUT="0"))


def test_app_settings_model_params_reads_json():
    """供应商特有参数（如 DashScope 关思考）走配置，不进代码。"""

    settings = AppSettings.from_env(
        app_env(PMSTUDIO_MODEL_PARAMS='{"extra_body": {"enable_thinking": false}}')
    )

    assert settings.model_params == {"extra_body": {"enable_thinking": False}}
    assert AppSettings.from_env(app_env()).model_params == {}


def test_app_settings_model_params_strips_shell_quotes():
    """`.env.example` 要求 JSON 值带单引号（`source` 才吃得下，learnings L19），
    但 docker `--env-file` / compose `env_file` 不剥引号（原样注入）。
    配置解析在这里统一剥一层——原生（dev.py 已剥过）是 no-op，容器里是救命。
    """

    settings = AppSettings.from_env(
        app_env(PMSTUDIO_MODEL_PARAMS='\'{"extra_body": {"enable_thinking": false}}\'')
    )

    assert settings.model_params == {"extra_body": {"enable_thinking": False}}


def test_app_settings_model_params_bad_json_is_a_hard_error():
    with pytest.raises(ConfigError, match="PMSTUDIO_MODEL_PARAMS"):
        AppSettings.from_env(app_env(PMSTUDIO_MODEL_PARAMS="{不是 JSON}"))
    with pytest.raises(ConfigError, match="PMSTUDIO_MODEL_PARAMS"):
        AppSettings.from_env(app_env(PMSTUDIO_MODEL_PARAMS="[1, 2]"))


def test_app_settings_do_not_own_first_admin_seed():
    settings = AppSettings.from_env(
        app_env(
            PMSTUDIO_FIRST_ADMIN_USER="root",
            PMSTUDIO_FIRST_ADMIN_PASSWORD="secret",
        )
    )
    assert not hasattr(settings, "first_admin_username")
    assert not hasattr(settings, "first_admin_password")


def test_ops_settings_only_need_direct_url():
    settings = OpsSettings.from_env({"PMSTUDIO_DIRECT_DATABASE_URL": DIRECT_URL})
    assert settings.direct_database_url == DIRECT_URL
    assert settings.first_admin_username is None
    assert settings.first_admin_password is None


def test_ops_settings_carry_first_admin_seed():
    settings = OpsSettings.from_env(
        {
            "PMSTUDIO_DIRECT_DATABASE_URL": DIRECT_URL,
            "PMSTUDIO_FIRST_ADMIN_USER": "root",
            "PMSTUDIO_FIRST_ADMIN_PASSWORD": "root-s3cret",
        }
    )
    assert settings.first_admin_username == "root"
    assert settings.first_admin_password == "root-s3cret"


def test_ops_settings_missing_direct_url_is_a_hard_error():
    with pytest.raises(ConfigError, match="PMSTUDIO_DIRECT_DATABASE_URL"):
        OpsSettings.from_env({"PMSTUDIO_DATABASE_URL": APP_URL})


def test_ops_settings_do_not_read_the_app_url():
    """两边不互相推导：只有 app URL 时 OpsSettings 必须失败。"""

    with pytest.raises(ConfigError, match="PMSTUDIO_DIRECT_DATABASE_URL"):
        OpsSettings.from_env({"PMSTUDIO_DATABASE_URL": APP_URL})


def test_app_settings_do_not_read_the_direct_url():
    """backend 不认 direct URL：只有 direct 时 AppSettings 必须失败。"""

    with pytest.raises(ConfigError, match="PMSTUDIO_DATABASE_URL"):
        AppSettings.from_env(
            {
                "PMSTUDIO_MODEL": "m",
                "PMSTUDIO_API_KEY": "k",
                "PMSTUDIO_DIRECT_DATABASE_URL": DIRECT_URL,
            }
        )


def test_bootstrap_settings_read_url_passwords_and_auth_file():
    settings = BootstrapSettings.from_env(
        {
            "PMSTUDIO_BOOTSTRAP_DATABASE_URL": "postgresql://postgres@db:5432/pmstudio",
            "PMSTUDIO_APP_PASSWORD": "app-pass",
            "PMSTUDIO_OWNER_PASSWORD": "owner-pass",
            "PMSTUDIO_PGBOUNCER_AUTH_FILE": "/run/pgbouncer/userlist.txt",
            "PMSTUDIO_PGBOUNCER_AUTH_UID": "70",
        }
    )
    assert settings.bootstrap_database_url.startswith("postgresql://postgres@")
    assert settings.app_password == "app-pass"
    assert settings.owner_password == "owner-pass"
    assert settings.auth_file == "/run/pgbouncer/userlist.txt"
    assert settings.auth_uid == 70


def test_bootstrap_settings_require_both_role_passwords():
    for missing in ("PMSTUDIO_APP_PASSWORD", "PMSTUDIO_OWNER_PASSWORD"):
        env = {
            "PMSTUDIO_BOOTSTRAP_DATABASE_URL": "postgresql://postgres@db:5432/pmstudio",
            "PMSTUDIO_APP_PASSWORD": "app-pass",
            "PMSTUDIO_OWNER_PASSWORD": "owner-pass",
        }
        env.pop(missing)
        with pytest.raises(ConfigError, match=missing):
            BootstrapSettings.from_env(env)


def test_bootstrap_settings_do_not_read_ops_url():
    with pytest.raises(ConfigError, match="PMSTUDIO_BOOTSTRAP_DATABASE_URL"):
        BootstrapSettings.from_env(
            {
                "PMSTUDIO_DIRECT_DATABASE_URL": DIRECT_URL,
                "PMSTUDIO_APP_PASSWORD": "a",
                "PMSTUDIO_OWNER_PASSWORD": "o",
            }
        )
