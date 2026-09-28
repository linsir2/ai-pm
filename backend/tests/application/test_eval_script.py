"""eval 脚本自己的两条守卫：

1. "别碰工作库"（真踩过：`.env` 的 PMSTUDIO_DB 赢了 default_db，一次 eval 把项目建进了
   作者的工作库）；
2. "一条用例崩了别的照跑"（真踩过：case-a 第一次真模型运行时复述对不上原文 →
   `PacketParseError` 冒到 `main`，8 条用例在第 1 条就整体退出，等于没有 eval 报告）。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.eval import DEFAULT_EVAL_DB, resolve_eval_db, run_one


def test_default_eval_db_is_a_temp_path():
    assert (
        resolve_eval_db(None, working_db=Path("data/pmstudio.db")) == DEFAULT_EVAL_DB
    )


def test_eval_refuses_the_working_db(tmp_path):
    working = tmp_path / "pmstudio.db"

    with pytest.raises(SystemExit) as error:
        resolve_eval_db(str(working), working_db=working)

    assert "工作库" in str(error.value)


def test_eval_accepts_another_path(tmp_path):
    chosen = tmp_path / "eval.db"

    assert resolve_eval_db(str(chosen), working_db=tmp_path / "pmstudio.db") == chosen


async def _boom(*args, **kwargs):
    raise RuntimeError("炸了")


async def _make_project(*, title):
    return SimpleNamespace(project_id="p-eval")


async def test_a_crashing_case_is_reported_instead_of_killing_the_run():
    services = SimpleNamespace(
        create_project=_make_project,
        runner=SimpleNamespace(run=_boom),
    )

    ok, why = await run_one(
        services, {"name": "x", "note": "随便一条", "input": "随便说点啥"}
    )

    assert ok is False
    assert "RuntimeError" in why
    assert "炸了" in why
