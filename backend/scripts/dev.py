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


def main() -> int:
    parser = argparse.ArgumentParser(description="起 PM Studio 后端")
    parser.add_argument("--port", type=int, default=None, help="端口，默认读 PMSTUDIO_PORT 或 8000")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-reload", action="store_true", help="关掉热重载")
    args = parser.parse_args()

    loaded = load_env_file(ENV_FILE)

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
    print(f"起在 http://{args.host}:{port} · 健康检查 /api/health")

    sys.path.insert(0, str(SRC))
    import uvicorn  # 放在 sys.path 之后导入，免得 src 还没进路径

    # **单 worker 是正确性约束，不是性能取舍**（多用户改造 §0.9）：owner 是每个请求现装的
    # contextvar（不跨请求），多 worker 本身不会因此串味。真风险有两个：**进程内 SSE 总线**
    # 各自为政——某项目的事件只推给"连在这个 worker 上的"浏览器，连在另一个 worker 上就丢；
    # 内存登录限流的状态也不共享，等于锁不住。SQLite 单文件写同样按单进程设计。
    # **不要加 `--workers`。**
    uvicorn.run(
        "pmstudio.bootstrap.container:build_app",
        factory=True,
        host=args.host,
        port=port,
        reload=not args.no_reload,
        reload_dirs=[str(SRC)] if not args.no_reload else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
