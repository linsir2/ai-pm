"""前后端 API 对等检查（M9 真跑验收 P0-C2）：后端 OpenAPI 路径 ↔ 前端 client.ts。

判据：双向零差。任一方向多出来 = 有人加了端点没加界面（死契约），或界面调了
不存在的端点（404 炸）——两边都是"真相源漂移"。

跑法（**不碰真库**：不给 `PMSTUDIO_DB` 就用一个**用完即删**的临时库；
显式给了 `PMSTUDIO_DB` 且它指向仓库内 `backend/data` 则直接拒绝）：

```bash
cd backend && set -a && . ./.env && set +a && python3 scripts/check_api_parity.py
```

退出码非 0 = 有漂移，打印两个方向的差集。
"""

import re
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND_CLIENT = BACKEND.parents[0] / "frontend" / "src" / "api" / "client.ts"

sys.path.insert(0, str(BACKEND / "src"))

# SSE 不走 fetch（EventSource 直连），client.ts 里没有它的调用点——这里声明，不算漂移。
KNOWN_NON_FETCH = {("GET", "/events")}


#: 临时库目录（`backend_paths()` 造、`main()` 收尾时删）。
_TEMP_DIR: Path | None = None


def _scratch_dir() -> Path:
    """造一个用完即删的临时目录（**不碰用户真实数据目录**）。"""

    global _TEMP_DIR
    if _TEMP_DIR is None:
        _TEMP_DIR = Path(tempfile.mkdtemp(prefix="pmstudio-parity-"))
    return _TEMP_DIR


def backend_paths() -> set[tuple[str, str]]:
    from pmstudio.bootstrap.container import build_container
    from pmstudio.bootstrap.settings import Settings

    # **默认给一个临时库**：`Settings.from_env()` 不给 `PMSTUDIO_DB` 时会退到
    # `~/.pmstudio/pmstudio.db`——那是**用户真实数据目录**，在这里建库等于碰真库。
    # 显式传 `default_db` 才把默认值换成临时的（`PMSTUDIO_DB` 若给了，仍然优先）。
    settings = Settings.from_env(default_db=_scratch_dir() / "parity.db")
    db = str(settings.db_path)
    if Path(db).resolve().is_relative_to((BACKEND / "data").resolve()):
        print(f"拒绝：PMSTUDIO_DB 指向真库目录（{db}），换一个临时库再跑", flush=True)
        return set()
    app = build_container(settings).app
    found: set[tuple[str, str]] = set()
    for path, item in app.openapi()["paths"].items():
        for method in ("get", "post", "put", "patch", "delete"):
            if method in item:
                found.add((method.upper(), path))
    return found


def frontend_calls() -> set[tuple[str, str]]:
    text = FRONTEND_CLIENT.read_text(encoding="utf-8")
    found: set[tuple[str, str]] = set()
    # client.ts 里所有调用都是 `request<T>(\`/…\`)` 或 `request<T>("/…")` 形状；
    # 模板里的 `${…}`（变量或 encodeURIComponent(…)）一律归一成 `{param}`，
    # 只比"段结构"，不比参数名（前端 camelCase，后端 snake_case，本来就对不上）。
    # method 只认**同一次调用括号内**的（`request<…>(` 起，到与之配对的 `)` 止——
    # 用"下一个 request<" 当边界，跨调用抓 method 是误伤，修过一次）。
    chunks = re.split(r"(?=request<[^>]+>\()", text)[1:]
    for chunk in chunks:
        url = re.search(r"\(\s*[`\"']([^`\"']+)[`\"']", chunk)
        if url is None:
            continue
        raw = url.group(1).split("?")[0]  # query 串（?q= / ?to=）不参与路径比对
        if not raw.startswith("/"):
            continue  # request()  helper 自己那行（`fetch(`${BASE}${path}`)`），跳过
        method_match = re.search(r'method:\s*"([A-Z]+)"', chunk)
        method = method_match.group(1) if method_match else "GET"
        path = re.sub(r"\$\{[^}]+\}", "{param}", raw)
        path = re.sub(r"\{[^}]+\}", "{param}", path)
        found.add((method, path))
    # 另外两种发请求的形状（不走 request<T>，正则扫不到就会误报"死契约"——修过一次）：
    # `download("…")` 固定是 GET（导出走它）；FormData 上传的裸 fetch 固定是 POST。
    for match in re.finditer(r"download\(\s*[`\"']([^`\"']+)[`\"']", text):
        raw = match.group(1).split("?")[0]
        found.add(("GET", re.sub(r"\$\{[^}]+\}", "{param}", raw)))
    for match in re.finditer(r"fetch\(`\$\{BASE\}([^`]+)[`\"']", text):
        raw = match.group(1).split("?")[0]
        if not raw.startswith("/"):
            continue  # request()/download() helper 自己那两行（`${BASE}${path}`），跳过
        found.add(("POST", re.sub(r"\$\{[^}]+\}", "{param}", raw)))
    return found


def normalize(paths: set[tuple[str, str]]) -> set[tuple[str, str]]:
    # 两边都去掉 `/api` 前缀（后端 openapi 带、前端 BASE 里带），再归一参数段
    return {
        (method, re.sub(r"\{[^}]+\}", "{param}", path.removeprefix("/api")))
        for method, path in paths
    }


def main() -> int:
    try:
        backend = normalize(backend_paths())
        if not backend:
            return 2
        frontend = normalize(frontend_calls()) | KNOWN_NON_FETCH
        only_backend = sorted(backend - frontend)
        only_frontend = sorted(frontend - backend)
        if not only_backend and not only_frontend:
            print(
                f"OK backend={len(backend)} frontend={len(frontend)} 双向零差",
                flush=True,
            )
            return 0
        for method, path in only_backend:
            print(f"只有后端有：{method} {path}（死契约？界面没调）", flush=True)
        for method, path in only_frontend:
            print(f"只有前端调：{method} {path}（会 404？后端没这个端点）", flush=True)
        return 1
    finally:
        # **用完即删**：临时库用完就收，不在磁盘上留痕。
        if _TEMP_DIR is not None:
            shutil.rmtree(_TEMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
