"""单实例锁：同一个库只能有一个进程打开（库级，靠 flock）。"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pmstudio.common.errors import SingleInstanceViolation
from pmstudio.storage.db import Database


def test_second_open_of_the_same_database_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "pmstudio.sqlite3"
    first = Database.open(path)
    try:
        with pytest.raises(SingleInstanceViolation, match="只能有一个进程"):
            Database.open(path)
    finally:
        first.close()


def test_lock_is_released_on_close(tmp_path: Path) -> None:
    path = tmp_path / "pmstudio.sqlite3"
    Database.open(path).close()
    reopened = Database.open(path)
    reopened.close()


def test_lock_is_released_when_the_holder_dies(tmp_path: Path) -> None:
    """崩溃之后必须还能起来——这是 flock 相比 PID 文件的意义所在。"""
    path = tmp_path / "pmstudio.sqlite3"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import sys, time
                from pmstudio.storage.db import Database

                # 必须留住这个对象：锁跟着它活，丢掉引用就等于放锁
                database = Database.open(sys.argv[1])
                print("locked", flush=True)
                time.sleep(60)
                """
            ),
            str(path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"

        with pytest.raises(SingleInstanceViolation):
            Database.open(path)

        child.kill()
        child.wait(timeout=10)

        reopened = Database.open(path)
        reopened.close()
    finally:
        if child.poll() is None:  # pragma: no cover - 只在断言失败时走到
            child.kill()
            child.wait(timeout=10)
