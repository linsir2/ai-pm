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
import shutil
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


def test_the_backend_dockerfile_skips_uv_sync_at_runtime() -> None:
    """`uv run` 默认每次起都全量 sync —— 镜像里的 `.venv` 是 `--no-dev` 的，
    它会把 dev 包（ruff/playwright，几十 MB）再下一遍才起服务。
    构建时依赖已锁死（`uv sync --frozen`），运行时不许再碰网关。"""

    cmds = _directives(_text(BACKEND_DOCKERFILE), "CMD")
    assert any("--no-sync" in line for line in cmds)


def test_the_backend_dockerfile_does_not_hardcode_a_sqlite_path() -> None:
    """backend 只认 `PMSTUDIO_DATABASE_URL`；镜像里再写 `PMSTUDIO_DB` 就是双真相源。"""

    envs = _directives(_text(BACKEND_DOCKERFILE), "ENV")
    assert not any("PMSTUDIO_DB=" in line for line in envs)


# —— R（需求）：compose ——


def test_the_compose_file_sets_the_forwarded_allow_ips() -> None:
    """跨容器反代不给这个 → 「每 IP 限流」退化成「全站限流」，正常用户互相锁死。"""

    text = _text(COMPOSE)
    assert "PMSTUDIO_FORWARDED_ALLOW_IPS" in text, (
        "跨容器反代必须给 PMSTUDIO_FORWARDED_ALLOW_IPS（见 scripts/dev.py 的注释）"
    )


def _service_block(text: str, name: str) -> str:
    """取 compose 里某个服务的文本块（遇到顶格 `volumes:` 或下一个服务结束）。"""

    pattern = (
        rf"^  {re.escape(name)}:\n"
        r"(.*?)(?=^  [a-zA-Z0-9_-]+:\n|^volumes:\n|\Z)"
    )
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    assert match is not None, f"compose 缺服务：{name}"
    return match.group(0)


def test_the_compose_file_pins_the_postgres_and_pgbouncer_images() -> None:
    text = _text(COMPOSE)
    db = _service_block(text, "db")
    pgbouncer = _service_block(text, "pgbouncer")
    assert "pgvector/pgvector:0.8.7-pg18-trixie@sha256:" in db
    assert "edoburu/pgbouncer:v1.26.1-p0@sha256:" in pgbouncer


def test_pgbouncer_uses_transaction_mode_without_prepared_statements() -> None:
    block = _service_block(_text(COMPOSE), "pgbouncer")
    assert re.search(r"POOL_MODE:\s*transaction", block)
    assert re.search(r"MAX_PREPARED_STATEMENTS:\s*\"0\"", block)
    assert re.search(r"MAX_CLIENT_CONN:\s*\"64\"", block)
    assert re.search(r"DEFAULT_POOL_SIZE:\s*\"10\"", block)
    assert not re.search(r"^\s+ports:", block, re.MULTILINE), "PgBouncer admin 不能发布端口"
    assert not re.search(r"^\s+ports:", _service_block(_text(COMPOSE), "db"), re.MULTILINE)


def test_db_and_pgbouncer_have_healthchecks() -> None:
    text = _text(COMPOSE)
    for name in ("db", "pgbouncer"):
        block = _service_block(text, name)
        assert "healthcheck:" in block, f"{name} 缺 healthcheck"
        assert "pg_isready" in block, f"{name} 的 healthcheck 要真的探库"


def test_backend_connects_through_pgbouncer_only() -> None:
    block = _service_block(_text(COMPOSE), "backend")
    # 只看生效的键，不看解释性注释。
    code = "\n".join(line.split("#", 1)[0] for line in block.splitlines())
    assert "PMSTUDIO_DATABASE_URL" in code
    assert "pgbouncer:6432" in code
    assert "PMSTUDIO_DIRECT_DATABASE_URL" not in code, "backend 不许拿直连 URL"


def test_backend_waits_for_db_and_pgbouncer() -> None:
    block = _service_block(_text(COMPOSE), "backend")
    assert re.search(r"db:\s*\n\s+condition:\s*service_healthy", block)
    assert re.search(r"pgbouncer:\s*\n\s+condition:\s*service_healthy", block)


def test_the_compose_file_proxies_to_the_service_name_not_localhost() -> None:
    """容器里的 127.0.0.1 是 Caddy 自己 —— 反代目标必须是服务名。"""

    text = _text(COMPOSE)
    assert "PMSTUDIO_API_TARGET" in text
    assert "backend:8000" in text, "反代目标应该是服务名 backend:8000"


def test_the_compose_file_parses() -> None:
    """compose 语法必须是合法的（这一条**本机就能跑**，不需要 Docker 守护进程）。

    **skip 只许在"本机没有 docker"这一种情况下发生**（A5）：
    - 有 docker 但 compose 插件没装 → `docker compose` 会报 `is not a docker command`，skip；
    - 别的任何失败（env_file 不存在、语法错、镜像构建不了）→ **红**。
      原来这里按 `"not found" in stderr` 判断，于是"env file ... not found"
      也会被当成"没 docker"而 skip——**真问题被吞掉**。
    """

    if shutil.which("docker") is None:
        pytest.skip("本机没有 docker")
    result = subprocess.run(
        ["docker", "compose", "config"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # 两种文案都表示"compose 插件不在"：
    #   老 CLI：`docker: 'compose' is not a docker command`
    #   新 CLI：`docker: unknown command: docker compose`
    # 只认前者会让后者变成红灯——而它的语义确实是"本机没插件"，该 skip。
    missing_plugin = (
        "is not a docker command" in (result.stderr or "")
        or "unknown command: docker compose" in (result.stderr or "")
    )
    if result.returncode != 0 and missing_plugin:
        pytest.skip("本机没有 docker compose 插件")
    assert result.returncode == 0, result.stderr


# —— R（需求）：Caddyfile ——


def test_the_caddyfile_proxies_to_a_configurable_target() -> None:
    """同机与 compose 两种形态**共用一份** Caddyfile —— 硬编码 127.0.0.1 会让 compose 502。"""

    text = _text(CADDYFILE)
    assert "PMSTUDIO_API_TARGET" in text
    assert "PMSTUDIO_WEB_ROOT" in text


def test_the_caddyfile_site_address_has_a_real_source() -> None:
    """站点地址的占位符必须**有真实来源**（A3）。

    占位符没有来源时，Caddyfile 渲染出**空串** → Caddy 起不来（或监听在错的地方），
    而 `docker compose up` 本身**不报错**——这是最典型的静默失败。
    """

    match = re.search(r"^\{(\$[A-Z_]+):", _text(CADDYFILE), re.MULTILINE)
    assert match, "Caddyfile 的站点地址占位符缺默认值"
    variable = match.group(1)[1:]  # 去掉 `$`
    assert re.search(rf"^\s+{variable}:", _text(COMPOSE), re.MULTILINE), (
        f"Caddyfile 的站点地址读 {variable}，但 compose 没注入它"
    )


def test_the_caddyfile_placeholders_are_not_nested() -> None:
    """占位符**不嵌套**（A3/C7.11）：Caddy 的环境变量替换是**单遍扫描**，
    默认值里再写一个 `{$...}` 不会被二次展开——会原样留在配置里，**启动即挂**。
    """

    for placeholder in re.findall(r"\{\$[^}]*\}", _text(CADDYFILE)):
        assert "{$" not in placeholder[1:-1], f"嵌套占位符：{placeholder}"


def test_the_local_caddyfile_does_not_redirect_to_https() -> None:
    """本地形态不许把 80 的请求重定向到 https（README 说"打开 http://localhost"）。

    站点地址以 `http://` 开头，Caddy 就**不会**对这个站点自动签发 HTTPS。
    """

    match = re.search(r"^\{\$[A-Z_]+:([^}]*)\}", _text(CADDYFILE), re.MULTILINE)
    assert match, "站点地址缺默认值"
    assert match.group(1).startswith("http://"), (
        f"本地默认站点地址不该带 https（那会把 80 的请求 301 到 443）：{match.group(1)}"
    )


# —— R（需求）：.dockerignore ——


def test_the_dockerignore_excludes_secrets_and_data() -> None:
    """仓库根 `.dockerignore` 必须挡住密钥与数据（A2）。

    **前端 build context 是仓库根**，没有它 `COPY frontend/ ./` 会把
    `backend/.env`（含模型 key）与整个 SQLite 库一起送进上下文——
    前端镜像层里就带上了密钥与用户数据，而它没有任何理由拥有它们。
    """

    path = ROOT / ".dockerignore"
    assert path.is_file(), "仓库根缺 .dockerignore（前端 context 是仓库根 → 密钥会进镜像）"
    text = path.read_text(encoding="utf-8")
    for entry in (".git", "**/node_modules", "**/.venv", "backend/.env", "backend/data"):
        assert entry in text, f".dockerignore 缺条目：{entry}"


def test_the_backend_dockerignore_excludes_secrets_data_and_venv() -> None:
    """后端 context 是 `backend/`，根上的 `.dockerignore` 管不到它。

    没有这一份，`backend/.env`（真 key）、`backend/data`（sqlite 库）、
    `.venv` 每次都被送进构建上下文（`COPY` 只取 src/scripts 所以进不了镜像层，
    但上下文白白变大，且 key 离镜像越远越好）。
    """

    path = ROOT / "backend" / ".dockerignore"
    assert path.is_file(), "backend 缺 .dockerignore（context 是 backend/ → 根那份管不到）"
    text = path.read_text(encoding="utf-8")
    for entry in (".env", "data", ".venv"):
        assert entry in text, f"backend/.dockerignore 缺条目：{entry}"
    assert ".env.example" not in text, (
        "不能把 .env.example 也挡掉——CI 靠 `cp .env.example .env` 造占位"
        "（占位本身被 `.env` 那条挡掉，进不了镜像，只是要文件在）"
    )


def test_the_fastembed_cache_volume_matches_its_env() -> None:
    """fastembed 的缓存目录必须**就是**挂卷的那个目录（C5）。

    fastembed 的默认目录是 `${TMPDIR}/fastembed_cache`（可用 `FASTEMBED_CACHE_PATH`
    覆盖）。compose 用环境变量覆盖它——**两者对不上**时，模型缓存落在容器的
    `/tmp` 里、**不挂卷**，于是每次重建容器都要重下 90MB 模型（静默变慢）。
    """

    compose_text = _text(COMPOSE)
    match = re.search(r"FASTEMBED_CACHE_PATH:\s*\"?([^\"\s]+)", compose_text)
    assert match, "compose 必须显式给 FASTEMBED_CACHE_PATH（否则缓存不挂卷）"
    cache_path = match.group(1)
    assert re.search(rf"-\s*\S+:{re.escape(cache_path)}\s*$", compose_text, re.MULTILINE), (
        f"FASTEMBED_CACHE_PATH={cache_path} 没有对应的挂卷"
    )


# —— R（需求）：前端镜像 ——


def test_the_frontend_dockerfile_copies_paths_inside_the_context() -> None:
    """前端 build context 是**仓库根**（A1）：COPY 的源必须带 `frontend/` 前缀，
    且**不能** COPY context 之外的东西（`COPY ../Caddyfile` 会直接构建失败）。
    """

    copies = _directives(_text(FRONTEND_DOCKERFILE), "COPY")
    assert any(line.replace(" ", "").startswith("COPYfrontend/package.json") for line in copies), (
        "前端镜像必须从 context 内 COPY frontend/package.json"
    )
    assert any(line.replace(" ", "") == "COPYfrontend/./" for line in copies), (
        "前端镜像必须 COPY 整个 frontend/"
    )
    assert any(line.replace(" ", "") == "COPYCaddyfile/etc/caddy/Caddyfile" for line in copies), (
        "Caddyfile 在 context 根上，要从那里 COPY"
    )
    for line in copies:
        source = line.split()[1]
        assert not source.startswith(".."), (
            f"COPY 不能指向 build context 之外：{line}（Docker 不允许 `COPY ../`）"
        )


def test_the_frontend_waits_for_a_healthy_backend() -> None:
    """前端必须等后端**真的健康**再收流量（C7.10）。

    否则 Caddy 反代到一个还没起来的后端 → 用户看到 502，而不是"页面开了但点什么都失败"。
    这也让 backend 的 healthcheck **有了消费者**（否则它是个没有消费者的装饰）。
    """

    frontend = _text(COMPOSE).split("frontend:", 1)[-1]
    assert re.search(r"backend:\s*\n\s*condition:\s*service_healthy", frontend), (
        "前端必须 depends_on backend: condition: service_healthy"
    )


# —— R（需求）：原生等价脚本 ——


def _compose_environment_keys(compose_text: str) -> set[str]:
    """compose 里**所有** `environment:` 段的键（两个服务都要）。

    只取第一个 `environment:` 段是不够的——**前端服务**的键
    （`PMSTUDIO_API_TARGET` / `PMSTUDIO_WEB_ROOT` / `PUBLIC_DOMAIN`）会整个漏掉，
    于是"compose 与脚本等价"根本没比到它们。
    """

    keys: set[str] = set()
    in_environment = False
    for line in compose_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= 4:
            # 服务名（indent 2）或服务的其它键（indent 4）——都结束 environment 段。
            in_environment = stripped == "environment:"
            continue
        if in_environment:
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*):", stripped)
            if match:
                keys.add(match.group(1))
    return {key for key in keys if key.startswith(("PMSTUDIO_", "FASTEMBED_", "PUBLIC_"))}


def test_the_dev_stack_script_uses_the_same_env_keys_as_compose() -> None:
    """compose 与脚本用同一套键名 —— 那是两者"等价"的根据。

    **双向**比（C7.5）：只比"compose 有、脚本没有"是不够的——那会漏掉
    "脚本有、compose 没有"的键，而那种键往往是**本机形态独有**的（比如 vite 的端口），
    说不清就会在换形态时炸。所以两个方向都要求**显式登记例外**，例外得写清为什么。

    只比 `environment:` 段里的键：`env_file` 里的那些
    （`PMSTUDIO_MODEL` / `PMSTUDIO_API_KEY` …）由 `backend/.env` 提供，
    两边读的是**同一个文件**，不必在脚本里重写一遍。
    """

    compose_keys = _compose_environment_keys(_text(COMPOSE))

    #: 脚本里**真的被赋值**的键（`export PMSTUDIO_X=` / `PMSTUDIO_X=`）。
    #: 注释里提到不算——提到是解释，赋值才是事实。
    script_keys = set(re.findall(r"\b(PMSTUDIO_[A-Z_]+)\s*=", _text(DEV_STACK)))

    #: compose 独有、脚本**故意**没有的键 —— 写清为什么（不是"漏了"）。
    #: · `PMSTUDIO_WEB_ROOT`：compose 的前端是 Caddy，要一个静态根目录；
    #:   本机跑的是 vite dev server，它自己伺服，不需要这个变量。
    #: · `FASTEMBED_CACHE_PATH`：compose 要把模型缓存指到**挂卷**的目录
    #:   （容器重建不丢）；本机的缓存落在 fastembed 默认的 `${TMPDIR}/fastembed_cache`。
    #: · `PUBLIC_DOMAIN`：compose 的 Caddy 站点地址；本机没有 Caddy。
    compose_only = {
        "PMSTUDIO_WEB_ROOT",
        "FASTEMBED_CACHE_PATH",
        "PUBLIC_DOMAIN",
        # backend 容器里连的是 compose 服务名；原生 dev_stack 的 PG/PgBouncer
        # 启动顺序归 T7，T1 不在这里造一个假的本机 URL。
        "PMSTUDIO_DATABASE_URL",
    }
    #: 脚本独有、compose **故意**没有的键 —— 同理。
    #: `PMSTUDIO_WEB_PORT`：compose 的前端是 Caddy（端口由镜像 `EXPOSE 80` 固定）；
    #: 本机是 vite dev server，端口要能挑（默认 5173）。
    script_only = {
        "PMSTUDIO_WEB_PORT",
        # 原生 dev_stack 仍在 SQLite 上跑（T1 不删旧运行时）；PG/PgBouncer 的
        # 启动序列与 URL 归 T7。这里不把旧键伪装成"compose 也有"。
        "PMSTUDIO_DB",
    }

    missing = compose_keys - script_keys - compose_only
    assert not missing, f"dev_stack.sh 没覆盖 compose 的这些键：{sorted(missing)}"
    extra = script_keys - compose_keys - script_only
    assert not extra, f"dev_stack.sh 有 compose 没有的未登记键：{sorted(extra)}"


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


def test_the_ci_workflow_builds_the_images() -> None:
    """CI 必须**真的构建镜像**（C6.1）。

    否则 Dockerfile 里的错（COPY 路径不对、缺依赖、alpine 装不上 onnxruntime）
    要等到**部署时**才炸——而那时已经不在 CI 的保护范围内了。
    """

    text = _text(CI_WORKFLOW)
    assert "docker compose build" in text or "docker build" in text, (
        "CI 必须有一个步骤真的构建镜像"
    )


def test_the_ci_parity_step_provides_model_config() -> None:
    """接口零差那一步必须给模型配置（A4）。

    `AppSettings.from_env()` 缺 key 或模型名会**直接起不来**（`ConfigError`）——
    不给的话这一步在 CI 上必红（假值即可，它不调模型）。
    """

    text = _text(CI_WORKFLOW)
    steps = re.split(r"\n\s*-\s+name:", text)
    parity = next((step for step in steps if "check_api_parity" in step), "")
    assert parity, "CI 里没有接口零差这一步"
    assert "PMSTUDIO_MODEL" in parity, "接口零差步骤缺 PMSTUDIO_MODEL"
    assert "PMSTUDIO_API_KEY" in parity, "接口零差步骤缺 PMSTUDIO_API_KEY"
