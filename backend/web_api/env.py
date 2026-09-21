"""密钥通道：把 `backend/.env` 读进进程环境。

`python-dotenv` 从 R1 起就是声明依赖，但全仓**没有一处调用它**——`.env.example` 承诺的
"复制成 `backend/.env` 填密钥"因此从来没成立过。这里是它的唯一消费者。

三条口径：

- **不覆盖已有的环境变量**：CI / 容器里 `export` 的值优先，与 dotenv 的默认行为一致；
- **文件不在不是错误**：没配 `.env` 是正常状态（无密钥那条路由 `FakeHarness`，见 PRD §12 / P0）；
- **不承载任何业务**：它只把键读进环境，读哪个变量名由 C10 `ModelBody.api_key_env` 决定。
"""

from pathlib import Path

from dotenv import load_dotenv

# `backend/.env`（与 `seeds/`、`pyproject.toml` 同级）。`.gitignore` 已忽略它。
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_env_file(path: Path | None = None) -> bool:
    """把 `path`（默认 `backend/.env`）里的键读进环境；返回是否真的读了。"""
    env_file = path or DEFAULT_ENV_FILE
    if not env_file.is_file():
        return False
    load_dotenv(env_file, override=False)
    return True
