"""起后端：一条命令，不需要先 export 一堆环境变量。

它做的事只有三件，别的都在 `bootstrap/` 里：

1. 读 `backend/.env`（没有就用当前 shell 的环境变量）；
2. 把 `backend/src` 放进模块搜索路径（省掉 `PYTHONPATH=src`）；
3. 起 uvicorn，并在起之前把"用的哪个模型、库在哪、key 有没有"打出来——
   缺配置时后端必须起不来（`ConfigError`），这里是让那句话在起服务前就被人看见。

用法：
    python3 scripts/dev.py                 # 8000，带热重载
    python3 scripts/dev.py --port 8010
    python3 scripts/dev.py --no-reload
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC = BACKEND_ROOT / "src"
ENV_FILE = BACKEND_ROOT / ".env"

REQUIRED = ("PMSTUDIO_MODEL", "PMSTUDIO_API_KEY")


def load_env_file(path: Path) -> int:
    """极简 .env 读取：只认 KEY=VALUE 与 # 注释，已存在的环境变量优先。

    不引 python-dotenv：多一个依赖换这点功能不划算，而且这里的语义要一眼看得懂。
    """

    if not path.is_file():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == "" or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key == "" or key in os.environ:
            continue
        os.environ[key] = value
        loaded += 1
    return loaded


def mask(value: str) -> str:
    if value == "":
        return "(没填)"
    return f"{value[:6]}…{value[-4:]}" if len(value) > 12 else "…"


#: **每个 worker 的固定 fd 开销**（本机核验出来的清单，不是估的）：
#: 3 标准流 + 3 asyncio（epoll + self-pipe 两端）+ 1 uvicorn 监听 socket
#: + **3 SQLite**（库文件 + `-wal` + `-shm`，`PRAGMA journal_mode=WAL` 开着）
#: + 1 事件扇出 reader + 1 事件扇出 publisher = 12。
#:
#: **SQLite 那 3 个最容易漏**——`ls /proc/<pid>/fd` 能直接看到三个。
#: 改前这个提示只算 `workers × 200`（SSE 连接数），漏掉了这一整块。
FIXED_FDS_PER_WORKER = 12


def fd_budget_for(*, workers: int, redis_pool: int, connections_per_worker: int) -> int:
    """多 worker 跑起来大约要占多少 fd（上限估算）。

    `redis_pool` 是 `PMSTUDIO_REDIS_MAX_CONNECTIONS`——连接池是**懒增长**的，
    最坏情况就是那个上界，而它是**主导项**（默认 256，远大于连接数）。
    """

    if workers <= 0:
        return 0
    return workers * (FIXED_FDS_PER_WORKER + redis_pool + connections_per_worker)


def fd_pressure(*, soft_limit: int, workers: int, redis_pool: int) -> bool:
    """软限够不够？

    **只按"保证占用"判**（固定开销 + 连接池），**不含同时在飞连接**——
    那个数只有运行时才知道，启动期猜它等于编一个假的阈值出来。

    `workers <= 0` 一律判无压力：那是配置错误，会由 `_workers()` 自己拦下，
    **不该用 fd 压力这个理由去报一个配置错误**。
    """

    if workers <= 0:
        return False
    return soft_limit < workers * (FIXED_FDS_PER_WORKER + redis_pool)


def _raise_fd_limit() -> int:
    """**自己把软限抬到硬限**——照 nginx 的做法（它 fork 之后对每个 worker
    调 `setrlimit(RLIMIT_NOFILE, ...)`）。

    不需要 root：只要不超过硬限就能抬。而"软限不够"这个失败模式在很多机器上
    根本不存在（本机实测 soft == hard == 1048576），所以**不能靠它拦**——
    nginx 的源码行为也是**警告不失败**，硬失败只会把问题提前、什么也没修好。

    返回抬完之后的软限。
    """

    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < hard:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
            return hard
        except (ValueError, OSError):
            return soft  # 抬不动就用原来的，别因此起不来
    return soft


def _workers() -> int:
    """读 `PMSTUDIO_WORKERS`（高并发 P4）。配不了 0 或非整数 → 退出码 2，
    与缺配置的处理一致：**看起来在配、其实配了个没用的值**最危险。"""

    raw = os.environ.get("PMSTUDIO_WORKERS", "").strip()
    if not raw:
        return 1
    try:
        value = int(raw)
    except ValueError:
        print(f"PMSTUDIO_WORKERS 要是整数，现在是 {raw!r}", file=sys.stderr)
        raise SystemExit(2) from None
    if value < 1:
        print(f"PMSTUDIO_WORKERS 必须 ≥1，现在是 {value}", file=sys.stderr)
        raise SystemExit(2)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="起 PM Studio 后端")
    parser.add_argument("--port", type=int, default=None, help="端口，默认读 PMSTUDIO_PORT 或 8000")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-reload", action="store_true", help="关掉热重载")
    args = parser.parse_args()

    loaded = load_env_file(ENV_FILE)

    # **尽早**把 `src` 放进模块搜索路径：下面那行 MCP 状态要 import
    # `pmstudio.bootstrap.settings`（配置写错了得在起服务前说清楚），
    # 而 `import uvicorn` 也要它。放在这里，两处都成立。
    sys.path.insert(0, str(SRC))

    venv = BACKEND_ROOT / ".venv"
    if venv.is_dir() and Path(sys.prefix) != venv:
        print(
            f"注意：现在用的是 {sys.executable}，不是仓库自己的 {venv}/bin/python。\n"
            "      想用虚拟环境就跑：uv run python scripts/dev.py",
            file=sys.stderr,
        )

    missing = [name for name in REQUIRED if os.environ.get(name, "").strip() == ""]
    if missing:
        print(f"缺配置：{'、'.join(missing)}", file=sys.stderr)
        print(f"填进 {ENV_FILE}（模板见 .env.example），或用环境变量传。", file=sys.stderr)
        return 2

    db_env = os.environ.get("PMSTUDIO_DB", "").strip()
    db_path = Path(db_env) if db_env else Path.home() / ".pmstudio" / "pmstudio.db"
    if not db_path.is_absolute():
        db_path = BACKEND_ROOT / db_path

    port = args.port or int(os.environ.get("PMSTUDIO_PORT", "8000"))

    print(f"配置来源：{ENV_FILE if loaded else '当前环境变量'}")
    print(f"模型：{os.environ['PMSTUDIO_MODEL']} · key：{mask(os.environ['PMSTUDIO_API_KEY'])}")
    print(f"网关：{os.environ.get('PMSTUDIO_API_BASE') or '(官方默认)'}")
    timeout = os.environ.get("PMSTUDIO_MODEL_TIMEOUT", "").strip() or "90"
    print(f"超时：{timeout} 秒/次（一次业务调用最多重试 3 次）")
    print(f"库：{db_path}{'（已存在）' if db_path.exists() else '（还没有，首次写入时建）'}")
    # 留样默认关（模型与工具同一开关）：开着才会写 `model_calls` / `tool_calls`。
    # **起之前就说清楚**：管理后台「用量与失败率」恒为 0 时，先回来对一眼这行。
    trace_model = os.environ.get("PMSTUDIO_TRACE_MODEL", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    print(f"留样：{'开' if trace_model else '关'}（PMSTUDIO_TRACE_MODEL）")
    redis_url = os.environ.get("PMSTUDIO_REDIS_URL", "").strip()
    if redis_url:
        print(f"登录限流：Redis 共享计数（{redis_url}）——多 worker 合起来算一份")
    else:
        print("登录限流：**进程内计数**（没配 PMSTUDIO_REDIS_URL）")
        print("  ⚠ 多 worker 下每个进程各有一份，等于锁不住；上多 worker 前先配 Redis")
    print("事件：进程内总线（单 worker，不跨进程扇出）")
    # 向量检索的模型源：下不动会静默退化成纯 FTS5，所以**起之前就说清楚**，
    # 免得上线后只发现"搜不到换个说法的内容"却不知道模型压根没下下来。
    endpoint = os.environ.get("HF_ENDPOINT", "").strip()
    no_xet = os.environ.get("HF_HUB_DISABLE_XET", "").strip().upper() in {
        "1", "TRUE", "YES", "ON",
    }
    if endpoint:
        print(f"向量模型源：镜像 {endpoint}{'（已禁 Xet）' if no_xet else ''}")
    else:
        print("向量模型源：huggingface.co 默认源——连不上就退回纯 FTS5（无语义召回）")
    # MCP 是**只读旁路**（`04-未决.md` 的裁决）：不进主闭环、不进 API 层、不写库，
    # 目前只有 `scripts/mcp_probe.py` 真的去连它。所以这里报的是**配置状态**，
    # 不是连接状态——把"配了没配、配了哪几个"打出来，配置写错（JSON 不合法、
    # server 重名）由 `Settings.from_env()` 判，早说早好。
    try:
        from pmstudio.bootstrap.settings import Settings

        configured = Settings.from_env().mcp_servers
    except Exception as error:  # noqa: BLE001 —— 配置类错误在这里说清，别等 probe
        print(f"MCP：配置有问题——{type(error).__name__}: {error}", file=sys.stderr)
    else:
        if not configured:
            print("MCP：没配（PMSTUDIO_MCP_SERVERS 留空 = 不启用）")
        else:
            names = "、".join(str(item.get("name")) for item in configured)
            print(
                f"MCP：配了 {len(configured)} 个 server（{names}）"
                "——由 scripts/mcp_probe.py 连接（只读旁路，不进主闭环）"
            )
    print(f"起在 http://{args.host}:{port} · 健康检查 /api/health")

    import uvicorn  # 放在 sys.path 之后导入，免得 src 还没进路径

    # **多 worker 的三条约束（P4 之后的状态）**：
    #  · 进程内 SSE 总线各自为政 → **P4 已解决**（配 Redis 走跨进程扇出）；
    #  · 内存登录限流不共享 → **P3 已解决**（配 Redis 共享计数）；
    #  · SQLite 单文件写 → **仍按单进程设计**，但实测 8 进程 × 150 INSERT 零争用
    #    （WAL + timeout=10），且它只串行化**写**，读与 SSE 不受影响。
    # 所以多 worker 现在**可以开**，但要配上 Redis；没配就多 worker 等于把限流与
    # 扇出静默退化回进程内——上面那行状态提示已经写明了。
    workers = _workers()
    if workers > 1 and not args.no_reload:
        print(
            "多 worker 与热重载互斥（uvicorn 的 --workers 不能与 --reload 同用）：\n"
            "  · 要多 worker → 加 --no-reload\n"
            "  · 要热重载 → 设 PMSTUDIO_WORKERS=1",
            file=sys.stderr,
        )
        return 2
    # fd 护栏：先自己抬软限（nginx 的做法），再判够不够。
    # **警告而不是退出码 2**——见 `_raise_fd_limit` 的文档串。
    import resource as _resource

    redis_pool = int(os.environ.get("PMSTUDIO_REDIS_MAX_CONNECTIONS", "256") or 256)
    if redis_pool < 0:
        redis_pool = 0
    soft = _raise_fd_limit()
    _, hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
    print(f"文件描述符：软限 {soft}（硬限 {hard}，已尝试抬到硬限）")
    if fd_pressure(soft_limit=soft, workers=workers, redis_pool=redis_pool):
        # 每 worker 的连接数是**运行时才知道**的，这里只把公式打出来让人自己代入。
        print(
            f"  ⚠ 可能不够：{workers} 个 worker × ("
            f"固定 {FIXED_FDS_PER_WORKER} + 登录限流池 ≤{redis_pool} + 每 worker 的在飞连接)"
        )
        print(
            "  改法：systemd 单元里加 LimitNOFILE=，或 ulimit -n。"
            "不抬就够跑 200 连接 × 多 worker 的话，新连接会拿到 "
            "\"Too many open files\"。"
        )
    else:
        used = workers * (FIXED_FDS_PER_WORKER + redis_pool)
        print(f"  够：固定开销与 Redis 连接池已占 {used} 个，"
              "加上你预期的每 worker 在飞连接数即可。")

    uvicorn.run(
        "pmstudio.bootstrap.container:build_app",
        factory=True,
        workers=workers,
        # **必须给**：SSE 是长期未完成的响应，uvicorn 关停时会等它们——
        # 不设超时就是无限等，`Ctrl-C` 之后进程永远退不出（uvicorn #2635）。
        timeout_graceful_shutdown=5,
        host=args.host,
        port=port,
        reload=not args.no_reload,
        reload_dirs=[str(SRC)] if not args.no_reload else None,
        # **登录限流按源 IP 计数，源 IP 来自 `X-Forwarded-For` 才拿得到真 IP**
        #（`api/auth.py` 用 `request.client.host`）。Caddy 后面不开这个，
        #所有请求的源 IP 都是 Caddy 的 →「每 IP 限流」退化成「全站限流」，
        # 正常用户会互相锁死。
        # `forwarded_allow_ips` 只信本机反代（默认 `127.0.0.1`）：**无条件信任
        # 转发头等于让任何人伪造源 IP**，那限流就形同虚设。
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("PMSTUDIO_FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
