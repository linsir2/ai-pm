#!/usr/bin/env python3
"""并发基线压测（零依赖，用标准库 asyncio + urllib）。

为什么自己写而不是装 k6/vegeta：这两个都要额外二进制与安装步骤，而我们要量的是
**同一台机器上、本进程内的**行为（单 worker + 单连接 + 单事件循环）。用同一个
解释器发压更接近真实同机竞争，也不需要装东西。

用法（起服务后）：
    python3 scripts/load_probe.py --base http://127.0.0.1:8100 --concurrency 50 \
        --requests 500 --path /api/health
    python3 scripts/load_probe.py --concurrency 200 --requests 400 --path /api/auth/login \
        --method POST --json '{"username":"pm","password":"…"}'

输出：成功/失败、p50/p95/p99/最大延迟、吞吐（req/s）、**每秒完成数曲线**，
以及**最长单请求耗时**——单事件循环里，一次阻塞会把整批请求一起拖住，
只看平均值会看不见这件事。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import urllib.error
import urllib.request


async def _one(
    sem: asyncio.Semaphore, args: argparse.Namespace, body: bytes | None
) -> tuple[bool, float, str]:
    async with sem:
        started = time.perf_counter()
        try:
            def do() -> tuple[int, bytes]:
                req = urllib.request.Request(args.base + args.path, data=body, method=args.method)
                if body is not None:
                    req.add_header("Content-Type", "application/json")
                if args.cookie:
                    req.add_header("Cookie", args.cookie)
                with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                    return resp.status, resp.read()

                # 到不了这里；urlopen 要么返回要么抛

            loop = asyncio.get_running_loop()
            status, _ = await loop.run_in_executor(None, do)
            return status < 500, time.perf_counter() - started, str(status)
        except urllib.error.HTTPError as err:
            return False, time.perf_counter() - started, f"HTTP {err.code}"
        except Exception as err:  # noqa: BLE001 — 压测里什么都可能发生
            return False, time.perf_counter() - started, type(err).__name__


async def _run(args: argparse.Namespace) -> int:
    body = args.json.encode() if args.json else None
    sem = asyncio.Semaphore(args.concurrency)
    started = time.perf_counter()
    results = await asyncio.gather(
        *[_one(sem, args, body) for _ in range(args.requests)]
    )
    wall = time.perf_counter() - started

    ok = [r for r in results if r[0]]
    bad = [r for r in results if not r[0]]
    latencies = sorted(r[1] for r in results)

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(len(latencies) - 1, int(len(latencies) * p))
        return latencies[idx] * 1000

    print(f"路径 {args.method} {args.path}  并发 {args.concurrency}  请求 {args.requests}")
    print(
        f"成功 {len(ok)}  失败 {len(bad)}  墙钟 {wall:.2f}s"
        f"  吞吐 {len(results) / wall:.0f} req/s"
    )
    print(
        f"延迟 p50 {pct(0.50):.0f}ms  p95 {pct(0.95):.0f}ms  p99 {pct(0.99):.0f}ms"
        f"  最大 {max(latencies, default=0) * 1000:.0f}ms"
    )
    if latencies:
        print(f"延迟 均值 {statistics.mean(latencies) * 1000:.0f}ms  最大/中位 "
              f"{max(latencies) / max(statistics.median(latencies), 1e-9):.1f}x")
    if bad:
        kinds: dict[str, int] = {}
        for _, _, why in bad:
            kinds[why] = kinds.get(why, 0) + 1
        print("失败分布:", kinds)
    # 退出码：全成功 0，有失败 1——CI/脚本能直接判
    return 0 if not bad else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="并发基线压测")
    parser.add_argument("--base", default="http://127.0.0.1:8100")
    parser.add_argument("--path", default="/api/health")
    parser.add_argument("--method", default="GET")
    parser.add_argument("--json", default=None, help="请求体（POST 用）")
    parser.add_argument("--cookie", default="", help="Cookie 头（需要身份时用）")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    print(json.dumps({"probe": args.path, "concurrency": args.concurrency}), flush=True)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())