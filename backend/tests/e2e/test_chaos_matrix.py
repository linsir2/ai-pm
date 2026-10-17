"""混沌矩阵的 pytest 包装（高并发 P5）。

## 为什么包一层

矩阵本身是 `scripts/chaos_matrix.py`——它要起桩 server、掐 TCP、挂 tmpfs，
这些在 pytest 进程里直接做会把 fixtures 搞得很脏（端口、命名空间、子进程）。
惯例照抄 `test_disk_full.py`：脚本跑事实、测试读最后一行 JSON 做判定。

## 它断言什么

五行的 `status` 全是 `PASS`。注意它**不重复** D/E 行的细节断言——
那些在各自的文件里；这里只钉"矩阵跑下来全绿"。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
MATRIX = BACKEND / "scripts" / "chaos_matrix.py"

_TIMEOUT = 900


def _run_matrix() -> dict:  # noqa: ANN201
    if not os.environ.get("PMSTUDIO_TEST_REDIS_URL"):
        pytest.skip(
            "没有 PMSTUDIO_TEST_REDIS_URL（C 行要真 Redis）——跳过，不假装通过"
        )
    completed = subprocess.run(
        [sys.executable, str(MATRIX)],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        cwd=BACKEND,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode == 2:
        pytest.skip(f"这台机器上跑不了混沌矩阵：{completed.stdout[-300:]}")
    line = next(
        (
            text
            for text in completed.stdout.splitlines()
            if '"matrix": "chaos"' in text
        ),
        None,
    )
    assert line is not None, (
        f"矩阵没吐结果行：rc={completed.returncode}\n"
        f"{completed.stdout[-500:]}\n{completed.stderr[-500:]}"
    )
    return json.loads(line)


def test_混沌矩阵全绿():
    """R：六种故障各自按设计降级，一条命令全跑完。

    I：真 5xx / 真慢响应 / 真断 TCP / 真锁库 / 真 ENOSPC；
    P：跑 `scripts/chaos_matrix.py`；R：五行全 PASS。
    哪一行红，`why` 里写着原因——去修产品，不要改矩阵。"""

    result = _run_matrix()
    assert "crashed" not in result, f"矩阵自己崩了：{result['crashed']}"

    rows = {row["row"]: row for row in result["rows"]}
    assert len(rows) == 5, f"矩阵只跑了 {len(rows)} 行：{sorted(rows)}"

    bad = {name: row for name, row in rows.items() if row["status"] != "PASS"}
    assert not bad, (
        "混沌矩阵有红行——去修产品，不要改矩阵：\n"
        + "\n".join(f"  {name}: {row.get('why', row)}" for name, row in bad.items())
    )


def test_矩阵脚本自己还在():
    """R：上面那条全靠脚本，脚本一坏它会**集体跳过**而不是失败——
    那是最难查的形状，所以存在性单独钉一条。"""

    assert MATRIX.exists(), f"矩阵脚本没了：{MATRIX}"
