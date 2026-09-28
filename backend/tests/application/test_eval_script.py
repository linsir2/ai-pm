"""eval 脚本的"别碰工作库"这条守卫（真踩过：`.env` 的 PMSTUDIO_DB 赢了 default_db，
一次 eval 把项目建进了作者的工作库）。"""

from pathlib import Path

import pytest
from scripts.eval import DEFAULT_EVAL_DB, resolve_eval_db


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
