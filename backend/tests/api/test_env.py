"""密钥通道：`backend/.env` 必须真的被读进环境。

`.env.example` 承诺"复制成 `backend/.env` 填密钥"，而 `python-dotenv` 原来是
**声明了没消费者**的依赖——这条承诺从来跑不通。这组用例把它变成可验证的事。

放在 `tests/api/`：唯一的调用点是 HTTP 入口的启动（`web_api/app.py` 的 `lifespan`），
也就是 `web_api` 这一层。
"""

import os
from pathlib import Path

from web_api.env import load_env_file


def test_env_file_is_loaded_into_the_environment(tmp_path: Path, monkeypatch) -> None:
    """`.env` 里的键进了环境——这就是"填上密钥就能真跑"的那一步。"""
    monkeypatch.delenv("PMSTUDIO_TEST_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("PMSTUDIO_TEST_KEY=sk-from-file\n", encoding="utf-8")

    assert load_env_file(env_file) is True
    assert os.environ["PMSTUDIO_TEST_KEY"] == "sk-from-file"


def test_existing_variable_wins_over_the_file(tmp_path: Path, monkeypatch) -> None:
    """已有环境变量优先——CI / 容器里 export 的值不该被本地文件悄悄顶掉。"""
    monkeypatch.setenv("PMSTUDIO_TEST_KEY", "sk-from-shell")
    env_file = tmp_path / ".env"
    env_file.write_text("PMSTUDIO_TEST_KEY=sk-from-file\n", encoding="utf-8")

    load_env_file(env_file)

    assert os.environ["PMSTUDIO_TEST_KEY"] == "sk-from-shell"


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """没有 `.env` 是正常状态（无密钥路径靠 FakeHarness），不是错误。"""
    assert load_env_file(tmp_path / "nope.env") is False


def test_default_path_is_backend_dot_env() -> None:
    """默认读的是 `backend/.env`——`.env.example` 里那句"复制成 backend/.env"指的就是它。"""
    from web_api.env import DEFAULT_ENV_FILE

    assert DEFAULT_ENV_FILE.name == ".env"
    assert DEFAULT_ENV_FILE.parent.name == "backend"
