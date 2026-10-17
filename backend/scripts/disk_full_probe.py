#!/usr/bin/env python3
"""磁盘满注入探针（高并发 P5）——**真 ENOSPC，不是 mock**。

## 为什么必须真挂

判据是"磁盘满时**文档与版本账本不能裂开**"。mock 掉 `sqlite3.OperationalError`
只能验到异常传播——而真正要验的那件事（**事务会不会回滚、库会不会烂**）恰恰
只在真 ENOSPC 下才发生。所以这一份挂一个带上限的 tmpfs，写到 `Errno 28`，
让内核给真错误。

单跑需要 root 挂载，但**用户命名空间够用**（本机 uid=1000，`unshare -Umr` 可用）：

    python3 scripts/disk_full_probe.py            # 自己重进命名空间并挂载
    python3 scripts/disk_full_probe.py <已挂好的目录>  # 调试用

## 退出码

只表达"基础设施跑没跑起来"（挂不上、崩了 → 非 0），**不表达"行为对不对"**——
那是测试的判据。脚本替测试做判定，就等于把"应该断言什么"藏进了一个没人读的
返回值里。

## 输出

一行 JSON：注入是否真生效（`free_bytes_after_fill` / `raw_sqlite_error`）、
满盘下的响应（状态 + body + **头**）、满盘期间与恢复之后的账本对比。
测试只读这一个契约。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SRC = BACKEND / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(BACKEND))

#: tmpfs 上限。给到 4M：够建库、建索引、写 WAL/`-shm`，又不至于让填充动作很慢。
#: 太小（试过 256K）会让 `PRAGMA journal_mode=WAL` 本身就先失败——那时候
#: 测的是"连 WAL 都开不出来"，不是"写库时满盘"。
TMPFS_SIZE = "4M"

_PW = "disk-full-probe-pw"
_USER = "diskfull"


def _fill(mountpoint: Path) -> None:
    """把文件系统写到 **0 字节可用**。

    写到抛 `Errno 28` 为止而不是按 `statvfs` 算着写：算出来的数字与真正写进去的
    会有差（页对齐、稀疏），差一点点 SQLite 仍能写成功，那这次注入就白跑了。
    """

    filler = mountpoint / "filler"
    chunk = b"x" * (1024 * 1024)
    with filler.open("wb") as handle:
        while True:
            try:
                handle.write(chunk)
            except OSError:
                break  # ENOSPC，就是我们要的
            if handle.tell() > 64 * 1024 * 1024:  # 别把 4M 写穿成无限循环
                break
    os.sync()


def _free(mountpoint: Path) -> None:
    (mountpoint / "filler").unlink(missing_ok=True)
    os.sync()


def _ledger(db: Path) -> dict:  # noqa: ANN201
    """账本现状：文档正文摘要 + 版本清单。

    **只读**——满盘时读是能成功的（写才要空间），所以账本能不能读本身就是一条结论：
    读不出来说明库真的烂了，那比版本号多一条严重得多。
    """

    connection = sqlite3.connect(db, timeout=0.2)
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT blocks, title FROM documents LIMIT 1"
        ).fetchone()
        versions = connection.execute(
            "SELECT version_no, trigger, summary FROM document_versions"
            " ORDER BY version_no"
        ).fetchall()
        return {
            "readable": True,
            "doc_sha": hashlib.sha256(
                (row["blocks"] + row["title"]).encode("utf-8")
            ).hexdigest()[:16] if row else None,
            "versions": [dict(v) for v in versions],
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
        }
    except sqlite3.Error as error:
        return {"readable": False, "error": f"{type(error).__name__}: {error}"}
    finally:
        connection.close()


def _raw_sqlite_error(mountpoint: Path) -> str:
    """满盘上让**裸 sqlite3** 写一次，看它到底报什么。

    这一条是给映射逻辑做判据的：`OperationalError` 底下有好几种，
    **报 `database or disk is full`（SQLITE_FULL）还是 `disk I/O error`（SQLITE_IOERR）
    决定了 `api/errors.py` 该不该把它和 "locked" 一起当 503**——两者的处置完全不同。
    """

    path = mountpoint / "raw.db"
    try:
        connection = sqlite3.connect(path, isolation_level=None, timeout=0.2)
    except sqlite3.Error as error:  # pragma: no cover - 连建库都失败的情形
        return f"connect: {type(error).__name__}: {error}"
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE t (x TEXT)")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO t VALUES ('x')")
        return "no-error"  # 没注入成功——调用方要判出来
    except sqlite3.Error as error:
        return f"{type(error).__name__}: {error}"
    finally:
        connection.close()


async def _drive(mountpoint: Path) -> dict:  # noqa: ANN201
    """在给定的挂载点上跑一遍完整注入流程。"""

    # 导入放在函数里：只有在**真的要挂载**的子进程里才需要这些重依赖。
    from tests.e2e.conftest import build_app  # noqa: PLC0415
    from tests.e2e.test_isolation_matrix import _seed_active_user  # noqa: PLC0415
    from tests.support.api_client import async_client  # noqa: PLC0415

    app, _ = build_app(mountpoint, replies={}, autologin=False, first_admin=True)
    db = mountpoint / "pmstudio.db"
    report: dict = {}

    async with async_client(app) as client:
        _seed_active_user(app, username=_USER, password=_PW)
        await client.post("/api/auth/login", json={"username": _USER, "password": _PW})
        created = await client.post("/api/projects", json={"title": "磁盘满探针"})
        project_id = created.json()["project_id"]

        report["before"] = _ledger(db)

        # ── 注入 ──
        _fill(mountpoint)
        report["raw_sqlite_error"] = _raw_sqlite_error(mountpoint)
        report["free_bytes_after_fill"] = os.statvfs(mountpoint).f_bavail * (
            os.statvfs(mountpoint).f_frsize
        )

        edit = {
            "op": "add",
            "target": {"zone": "body", "section_id": "s1"},
            "text": "满盘时写进去的这一段",
        }
        failed = await client.post(f"/api/projects/{project_id}/edits", json=edit)
        report["full_disk_status"] = failed.status_code
        report["full_disk_body"] = _body(failed)
        report["full_disk_headers"] = dict(failed.headers)

        # 满盘时的账本：读得出来就说明没裂，读不出来就是真烂了。
        report["during"] = _ledger(db)

        # ── 收口：腾出空间，同一笔写再走一遍 ──
        _free(mountpoint)
        retried = await client.post(f"/api/projects/{project_id}/edits", json=edit)
        report["retry_status"] = retried.status_code
        report["retry_body"] = _body(retried)
        report["after"] = _ledger(db)

    return report


def _body(response) -> dict:  # noqa: ANN001, ANN201
    try:
        return response.json()
    except ValueError:
        return {"_raw": response.text[:200]}


def _child(mountpoint: Path) -> int:
    """跑完注入，打印一行事实。

    **退出码不表达"对不对"**：那是测试的事（`tests/e2e/test_disk_full.py`）。
    这里非 0 只表示**基础设施层就失败了**（挂不上、探针自己崩了），
    那种情况测试会跳过并把理由打出来——比"用退出码假装通过"诚实。
    """

    report = asyncio.run(_drive(mountpoint))
    print(json.dumps(report, ensure_ascii=False))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2:  # 已经是挂好的目录（调试用）
        return _child(Path(argv[1]))
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="diskfull-") as workspace:
        mountpoint = Path(workspace) / "fs"
        mountpoint.mkdir()
        # 挂载必须与探针在**同一个命名空间**里，所以用 `sh -c` 把两件事串在一起，
        # 再 `exec` 换掉 shell——不留多余进程占着挂载点。
        inner = (
            f"mount -t tmpfs -o size={shlex.quote(TMPFS_SIZE)} tmpfs "
            f"{shlex.quote(str(mountpoint))} && "
            f"exec {shlex.quote(sys.executable)} {shlex.quote(__file__)} "
            f"{shlex.quote(str(mountpoint))}"
        )
        return subprocess.call(
            ["unshare", "-Umr", "--propagation", "private", "--", "sh", "-c", inner]
        )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))