#!/usr/bin/env python3
"""并发基线压测（零依赖，标准库 urllib + ThreadPoolExecutor）。

为什么自己写而不是装 k6/vegeta：这两个都要额外二进制与安装步骤，而我们要量的是
**同一台机器上**的行为。它跑的是真实 HTTP：

1. 登录（拿会话 cookie）；
2. 建一个项目；
3. `--http-requests` 个 `/api/health`（HTTP 面）；以及
4. `--rounds` 个并发 `POST /api/projects/{id}/rounds`（轮内写，**不含真模型**）。

⚠ 轮内写会调用分析者模型。压在**脚本化模型测试服务**上（`tests/support/uvicorn_app.py`）
才是"不含模型"的负载口径；对真服务跑会真的调模型。

用法（起服务后）：

    python3 scripts/load_probe.py --base http://127.0.0.1:8100 \\
        --username pm --password … --requests 100 --rounds 20

判据：0 连接错误/超时、0 SQLSTATE 55P03/57014、HTTP p95 ≤ 2s（可用 `--p95-limit` 调）。
退出码非 0 = 有失败。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar


class Probe:
    def __init__(self, base: str, *, timeout: float) -> None:
        self._base = base.rstrip("/")
        self._timeout = timeout
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, dict, float, str]:
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            self._base + path, data=body, method=method
        )
        if body is not None:
            request.add_header("Content-Type", "application/json")
        started = time.perf_counter()
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            raw = error.read()
            status = error.code
        except Exception as error:  # noqa: BLE001 —— 压测里什么都可能发生
            return 0, {}, time.perf_counter() - started, type(error).__name__
        elapsed = time.perf_counter() - started
        try:
            parsed = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            parsed = {}
        return status, parsed if isinstance(parsed, dict) else {}, elapsed, ""


def _pct(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index] * 1000


def _bad(status: int, body: dict, why: str) -> bool:
    if status == 0 or status >= 500:
        return True
    # 服务端已经明确说"过一会儿再来"也计入失败：负载门要的是 0 个。
    return body.get("code") in {
        "database_busy",
        "database_unavailable",
        "storage_full",
    } or why in {"timeout", "URLError", "ConnectionResetError"}


def main() -> int:
    parser = argparse.ArgumentParser(description="并发基线压测（HTTP + 轮内写）")
    parser.add_argument("--base", default="http://127.0.0.1:8100")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--requests", type=int, default=100, help="HTTP 请求数")
    parser.add_argument("--path", default="/api/health", help="HTTP 负载路径")
    parser.add_argument("--concurrency", type=int, default=50, help="HTTP 并发")
    parser.add_argument("--rounds", type=int, default=20, help="轮内写次数")
    parser.add_argument("--round-workers", type=int, default=20, help="轮内写并发")
    parser.add_argument("--p95-limit", type=float, default=2.0, help="HTTP p95 上限（秒）")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    probe = Probe(args.base, timeout=args.timeout)
    if args.username:
        status, body, _, why = probe.request(
            "POST",
            "/api/auth/login",
            payload={"username": args.username, "password": args.password},
        )
        if status != 200:
            print(f"登录失败 {status}: {body or why}", file=sys.stderr)
            return 1
    status, body, _, why = probe.request("POST", "/api/projects", payload={"title": "负载探针"})
    if status not in (200, 201):
        print(f"建项目失败 {status}: {body or why}", file=sys.stderr)
        return 1
    project = str(body["project_id"])

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        http_results = list(
            pool.map(lambda _: probe.request("GET", args.path), range(args.requests))
        )
    http_latencies = [item[2] for item in http_results]
    http_failures = [
        (status, body, why)
        for status, body, _, why in http_results
        if _bad(status, body, why)
    ]

    def _round(index: int) -> tuple[int, dict, float, str]:
        return probe.request(
            "POST",
            f"/api/projects/{project}/rounds",
            payload={"input": f"想做个订单导出的功能（负载探针第 {index} 轮）"},
        )

    with ThreadPoolExecutor(max_workers=max(1, args.round_workers)) as pool:
        round_results = list(pool.map(_round, range(args.rounds)))
    round_failures = [
        (status, body, why)
        for status, body, _, why in round_results
        if _bad(status, body, why)
    ]

    http_p95 = _pct(http_latencies, 0.95)
    round_p95 = _pct([item[2] for item in round_results], 0.95)
    print(
        f"HTTP {args.path}  并发 {args.concurrency}  请求 {args.requests}"
        f"  成功 {len(http_results) - len(http_failures)}  失败 {len(http_failures)}"
        f"  p50 {_pct(http_latencies, 0.50):.0f}ms  p95 {http_p95:.0f}ms"
    )
    print(
        f"轮内写 并发 {args.round_workers}  次数 {args.rounds}"
        f"  成功 {len(round_results) - len(round_failures)}  失败 {len(round_failures)}"
        f"  p50 {_pct([item[2] for item in round_results], 0.50):.0f}ms"
        f"  p95 {round_p95:.0f}ms"
    )
    if http_failures:
        kinds: dict[str, int] = {}
        for status, body, why in http_failures:
            kinds[str(body.get("code") or status or why)] = (
                kinds.get(str(body.get("code") or status or why), 0) + 1
            )
        print("HTTP 失败分布:", kinds)
    if round_failures:
        kinds = {}
        for status, body, why in round_failures:
            kinds[str(body.get("code") or status or why)] = (
                kinds.get(str(body.get("code") or status or why), 0) + 1
            )
        print("轮内写失败分布:", kinds)

    print(
        json.dumps(
            {
                "http": {
                    "count": len(http_results),
                    "failures": len(http_failures),
                    "p95_ms": round(http_p95, 1),
                },
                "rounds": {"count": len(round_results), "failures": len(round_failures)},
            },
            ensure_ascii=False,
        )
    )
    failed = bool(http_failures or round_failures) or http_p95 > args.p95_limit * 1000
    if http_p95 > args.p95_limit * 1000:
        print(
            f"HTTP p95 {http_p95:.0f}ms 超过上限 {args.p95_limit * 1000:.0f}ms",
            file=sys.stderr,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
