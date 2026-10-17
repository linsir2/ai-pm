"""磁盘满 → **507**（高并发 P5），真 ENOSPC。

## 为什么要单独挂 tmpfs

判据有两半，**mock 一半都验不到**：

1. **账本会不会裂**：满盘时那笔写有没有回滚、库还读不读得出来；
2. **码位对不对**：映射是否落到 507 而不是 500/503。

第 1 条只有在**内核真的给 `ENOSPC`** 时才发生——mock 掉异常只能验到
异常传播，而"事务会不会回滚"恰恰不在异常传播的路径上。

所以这里挂一个 4M 的 tmpfs、写满、让内核报错，再走**真 HTTP**。

## 挂载为什么要用户命名空间

单跑 `mount -t tmpfs` 需要 root。但用户命名空间够用（本机 uid=1000、
`unshare -Umr` 可用），**不需要任何特权、也不碰宿主的文件系统**：
命名空间一退出，挂载点就消失了。

**环境不支持就跳过，不假装通过**——那台机器上的"绿"是假的。
跳过理由会打印出来。

## 真跑的结论（这就是它存在的理由）

改前满盘回 **500 `internal_error`**。回 500 的代价：磁盘满是**运维事件**
（该叫人腾空间），却和"我们写错了"混在同一堆 500 里；也与 P5b 刚定的
"容量问题不回 500"自相矛盾。而 503 也不行——`Retry-After: 3` 兑现不起。

账本侧改前就**是对的**（`with uow` 的 `BEGIN IMMEDIATE` 会回滚）：
满盘下文档摘要未变、版本仍是 v1、`integrity_check=ok`；腾出空间后同一笔写
回 200、版本推进到 v2。这一组用例把那两条**钉成回归**。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
PROBE = BACKEND / "scripts" / "disk_full_probe.py"

_TIMEOUT = 300


def _probe_report() -> dict:  # noqa: ANN201
    """跑一次注入探针，返回它那一行 JSON。

    **探针自己重进命名空间并挂载**（见 `disk_full_probe.py` 的文档串）——
    测试进程本身没有 `CAP_SYS_ADMIN`，在它里面挂不进来。
    """

    completed = subprocess.run(
        [sys.executable, str(PROBE)],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        cwd=BACKEND,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode not in (0, 1):
        pytest.skip(f"这台机器上跑不了挂载注入：{completed.stderr[-300:]}")
    line = next(
        (text for text in completed.stdout.splitlines() if text.startswith("{")),
        None,
    )
    assert line is not None, f"探针没吐 JSON：{completed.stdout[-300:]} / {completed.stderr[-300:]}"
    return json.loads(line)


def test_挂载注入这台机器上能跑():
    """R：先确认注入本身**真的生效了**。

    I：4M tmpfs 写到 0 字节可用；P：读 `statvfs`；R：可用字节为 0。
    不先钉这一条，后面所有断言都可能在"其实没满盘"的情况下假通过。"""

    report = _probe_report()

    assert report["free_bytes_after_fill"] == 0, "tmpfs 没被填满，这一轮注入是假的"
    assert "full" in report["raw_sqlite_error"].lower(), (
        f"裸 sqlite3 没报满盘：{report['raw_sqlite_error']}"
    )


def test_满盘时写请求回_507_不是_500():
    """**这一组存在的理由**。改前是 500 `internal_error`。

    I：tmpfs 填满；P：走 HTTP 发一次会写库的请求；R：507，body `code`
    是 `storage_full`。"""

    report = _probe_report()

    assert report["full_disk_status"] == 507, (
        f"满盘回的是 {report['full_disk_status']}：{report['full_disk_body']}"
    )
    assert report["full_disk_body"]["code"] == "storage_full"


def test_满盘时不带_RetryAfter():
    """R：**不带**。那个头是"3 秒后再来"的承诺——写锁兑现得起，
    磁盘满兑现不起（得有人在删文件）。给了就是教客户端在一个永远不会好的
    结果上重试。"""

    report = _probe_report()

    assert "Retry-After" not in report["full_disk_headers"], report["full_disk_headers"]


def test_满盘时账本没裂():
    """R：文档正文摘要与版本清单**逐字未变**，且 `integrity_check` 是 ok。
    这一条守的是仓库最硬的不变式（一次改动 = 一版，版本只增不改）：
    满盘不能造出"内容改了但没版本"或"版本加了但内容没改"的半截状态。"""

    report = _probe_report()
    during, before = report["during"], report["before"]

    assert during["readable"] is True, f"满盘后库读不出来了：{during}"
    assert during["integrity"] == "ok"
    assert during["doc_sha"] == before["doc_sha"], "文档被改了但这轮写失败了"
    assert during["versions"] == before["versions"], "多了一个版本"


def test_腾出空间后同一笔写能成():
    """R：满盘**不能把库搞坏**——删掉占位文件后重发同一笔写，回 200 且
    版本正好推进一版。这是"容量问题"与"库烂了"的分界：后者收不回来。"""

    report = _probe_report()

    assert report["retry_status"] == 200, report["retry_body"]
    versions = report["after"]["versions"]
    assert len(versions) == len(report["before"]["versions"]) + 1
    assert report["after"]["integrity"] == "ok"
    assert report["after"]["doc_sha"] != report["before"]["doc_sha"], (
        "回 200 了但文档没变——那说明写没真落库"
    )


@pytest.mark.skipif(
    shutil.which("unshare") is None, reason="这台机器上没有 unshare，挂不了 tmpfs"
)
def test_探针脚本自己还在():
    """R：探针存在且可执行入口没被改坏（上面几条全靠它，探针一坏它们会**集体跳过**
    而不是集体失败——那是最难查的形状）。"""

    assert PROBE.exists(), f"探针没了：{PROBE}"