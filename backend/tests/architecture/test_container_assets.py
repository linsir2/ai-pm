"""容器与 CI 资产的守卫。

**为什么要有这些守卫**：Docker 与 CI 的失败模式大多是**静默**的——
容器起来了但外部连不上（`--host` 还是 127.0.0.1）、提示词目录漏 COPY
（每轮拿空提示词调模型而不报错）、跨容器反代没给 `FORWARDED_ALLOW_IPS`
（限流退化成全站限流，正常用户互相锁死）、CI 没排 slow（去下 90MB 模型）。
这些**没有一条会自己报错**，所以要用机器钉住。

⚠️ **判据只看"真正的指令"，不看注释**：文件里的注释会解释"为什么不用 alpine"，
按全文匹配就会把那条解释当成违规。守卫要盯的是**实际生效的那一行**。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND_DOCKERFILE = ROOT / "backend" / "Dockerfile"
FRONTEND_DOCKERFILE = ROOT / "frontend" / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DEV_STACK = ROOT / "scripts" / "dev_stack.sh"
CADDYFILE = ROOT / "Caddyfile"


def _text(path: Path) -> str:
    assert path.is_file(), f"{path} 不存在"
    return path.read_text(encoding="utf-8")


def _logical_lines(text: str) -> list[str]:
    """把 Dockerfile 的**续行**接成一条逻辑指令。

    `ENV A=1 \\\n    B=2` 是**一条**指令（两个键）。不接起来的话
    第二个键就看不见——守卫会漏判。
    """

    out: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not buffer:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            buffer = stripped
        else:
            buffer += " " + line.strip()
        if buffer.endswith("\\"):
            buffer = buffer[:-1].rstrip()
            continue
        out.append(buffer)
        buffer = ""
    if buffer:
        out.append(buffer)
    return out


def _directives(text: str, keyword: str) -> list[str]:
    """只取真正的指令行（去掉注释与空行，并接好续行）。"""

    return [
        line for line in _logical_lines(text) if line.upper().startswith(keyword.upper())
    ]


# —— R（需求）：后端镜像 ——


def test_the_backend_dockerfile_copies_the_whole_source_tree() -> None:
    """**漏 COPY 资源目录 = 静默空提示词**（每轮拿空提示词调模型且不报错）。"""

    copies = _directives(_text(BACKEND_DOCKERFILE), "COPY")
    assert any(line.replace(" ", "") == "COPYsrc./src" for line in copies), (
        "必须整树 COPY src/ ——提示词(.md) / 席位与工具声明(.toml) / 模板 / schema.sql "
        "都是运行时读的资源，只 COPY .py 会让它们静默消失"
    )


def test_the_backend_dockerfile_binds_all_interfaces() -> None:
    """`dev.py` 的 `--host` 默认是 127.0.0.1 —— 容器里**连不上而且没有日志**。"""

    cmds = _directives(_text(BACKEND_DOCKERFILE), "CMD")
    assert cmds, "后端镜像必须有 CMD"
    assert any("--host" in line and "0.0.0.0" in line for line in cmds), (
        "容器里必须 --host 0.0.0.0"
    )


def test_the_backend_dockerfile_does_not_use_alpine() -> None:
    """`onnxruntime`（fastembed 的传递依赖）要 glibc —— alpine 装不上。"""

    froms = _directives(_text(BACKEND_DOCKERFILE), "FROM")
    assert froms, "后端镜像必须有 FROM"
    for line in froms:
        assert "alpine" not in line.lower(), (
            f"后端镜像不能用 alpine（onnxruntime 不支持 musl）：{line}"
        )


def test_the_backend_dockerfile_disables_reload() -> None:
    """热重载会挂文件监听，且与多 worker 互斥。"""

    cmds = _directives(_text(BACKEND_DOCKERFILE), "CMD")
    assert any("--no-reload" in line for line in cmds)


def test_the_backend_dockerfile_uses_an_absolute_db_path() -> None:
    """相对路径按 `backend/` 解析（`scripts/dev.py`），容器里会落到别处。"""

    envs = _directives(_text(BACKEND_DOCKERFILE), "ENV")
    assert any("PMSTUDIO_DB=/data/" in line for line in envs)


# —— R（需求）：compose ——


def test_the_compose_file_sets_the_forwarded_allow_ips() -> None:
    """跨容器反代不给这个 → 「每 IP 限流」退化成「全站限流」，正常用户互相锁死。"""

    text = _text(COMPOSE)
    assert "PMSTUDIO_FORWARDED_ALLOW_IPS" in text, (
        "跨容器反代必须给 PMSTUDIO_FORWARDED_ALLOW_IPS（见 scripts/dev.py 的注释）"
    )


def test_the_compose_file_proxies_to_the_service_name_not_localhost() -> None:
    """容器里的 127.0.0.1 是 Caddy 自己 —— 反代目标必须是服务名。"""

    text = _text(COMPOSE)
    assert "PMSTUDIO_API_TARGET" in text
    assert "backend:8000" in text, "反代目标应该是服务名 backend:8000"


def test_the_compose_file_parses() -> None:
    """compose 语法必须是合法的（这一条**本机就能跑**，不需要 Docker 守护进程）。"""

    result = subprocess.run(
        ["docker", "compose", "config"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 and (
        "not found" in (result.stderr or "").lower()
        or "unknown command" in (result.stderr or "").lower()
    ):
        pytest.skip("本机没有 docker compose")
    assert result.returncode == 0, result.stderr


# —— R（需求）：Caddyfile ——


def test_the_caddyfile_proxies_to_a_configurable_target() -> None:
    """同机与 compose 两种形态**共用一份** Caddyfile —— 硬编码 127.0.0.1 会让 compose 502。"""

    text = _text(CADDYFILE)
    assert "PMSTUDIO_API_TARGET" in text
    assert "PMSTUDIO_WEB_ROOT" in text


# —— R（需求）：原生等价脚本 ——


def test_the_dev_stack_script_uses_the_same_env_keys_as_compose() -> None:
    """compose 与脚本用同一套键名 —— 那是两者"等价"的根据。

    只比 **compose 里显式写的那些**（`environment:` 段）。
    `env_file` 里的那些（`PMSTUDIO_MODEL` / `PMSTUDIO_API_KEY` …）由
    `backend/.env` 提供，两边读的是**同一个文件**，不必在脚本里重写一遍。
    """

    compose_text = _text(COMPOSE)
    # 只取 environment 段里的键（不含 env_file 段）。
    environment_block = compose_text.split("environment:", 1)[-1]
    environment_block = environment_block.split("\n    depends_on:", 1)[0]
    compose_keys = set(re.findall(r"PMSTUDIO_[A-Z_]+", environment_block))

    script_keys = set(re.findall(r"PMSTUDIO_[A-Z_]+", _text(DEV_STACK)))
    missing = compose_keys - script_keys
    assert not missing, f"dev_stack.sh 没覆盖 compose 的这些键：{sorted(missing)}"


def test_the_dev_stack_script_reads_the_same_env_file_as_compose() -> None:
    """两边读**同一个** `.env` —— 否则"等价"是假的。"""

    assert "backend/.env" in _text(DEV_STACK)
    assert "backend/.env" in _text(COMPOSE)


# —— R（需求）：CI ——


def test_the_ci_workflow_uses_the_backend_working_directory() -> None:
    """pytest 的 `pythonpath = ["src", "."]` 依赖 cwd 是 `backend/` —— 不对就 ImportError。"""

    text = _text(CI_WORKFLOW)
    assert "working-directory: backend" in text


def test_the_ci_workflow_excludes_slow_tests() -> None:
    """**必须显式排 slow**：`markers` 只注册标记，pytest 默认**会跑** slow 用例
    → CI 去下 90MB 模型 + 调真模型 API（慢、贵、还因网络抖而红）。"""

    text = _text(CI_WORKFLOW)
    assert re.search(r'-m\s+"not slow"', text), 'CI 必须显式加 -m "not slow"'


def test_the_ci_workflow_runs_ruff_and_pytest() -> None:
    text = _text(CI_WORKFLOW)
    assert "ruff check" in text
    assert "pytest" in text


def test_the_ci_workflow_runs_frontend_typecheck_and_tests() -> None:
    text = _text(CI_WORKFLOW)
    assert "tsc --noEmit" in text
    assert "vitest run" in text


def test_the_ci_workflow_pins_a_python_with_sqlite_extension_support() -> None:
    """**用 setup-python 的解释器，不用 uv 自己下载的那个**。

    有些 Python 构建**没有** `sqlite3.Connection.enable_load_extension`
    （`sqlite-vec` 靠它加载扩展）→ 扩展装不上 → 检索静默降级成纯 FTS5
    → 一批向量相关的用例红（本机实测就是如此：uv 管的 3.13 构建没有，
    发行版的 python3.12 有）。
    """

    text = _text(CI_WORKFLOW)
    assert "actions/setup-python" in text, "CI 必须显式用 setup-python 装解释器"
    assert "UV_PYTHON" in text, "要显式钉住 uv 用哪个解释器"
