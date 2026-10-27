"""eval 脚本自己的守卫：一条用例崩了别的照跑 + 临时库从 URL 派生。"""

from types import SimpleNamespace

import pytest
from scripts.eval import load_cases, run_one
from scripts.pg_scratch import _base_urls

from pmstudio.bootstrap.settings import ConfigError
from pmstudio.harness.errors import ApplicationError


def test_load_cases_reads_every_case_and_filters_by_name():
    every = load_cases(None)
    assert every
    chosen = load_cases(every[0]["name"])
    assert [case["name"] for case in chosen] == [every[0]["name"]]
    assert load_cases("no-such-case") == []


async def _boom(*args, **kwargs):
    raise ApplicationError("炸了")


async def _make_project(*, title):
    return SimpleNamespace(project_id="p-eval")


async def _no_rounds(*args, **kwargs):
    return []


async def test_a_crashing_case_is_reported_instead_of_killing_the_run():
    services = SimpleNamespace(
        create_project=_make_project,
        list_rounds=_no_rounds,
        runner=SimpleNamespace(run=_boom),
    )

    ok, why = await run_one(
        services, {"name": "x", "note": "随便一条", "input": "随便说点啥"}
    )

    assert ok is False
    assert "ApplicationError" in why
    assert "炸了" in why


def test_scratch_database_requires_both_urls() -> None:
    with pytest.raises(ConfigError, match="PMSTUDIO_DATABASE_URL"):
        _base_urls(
            {
                "PMSTUDIO_DIRECT_DATABASE_URL": "postgresql://owner@db/pmstudio",
            }
        )
    with pytest.raises(ConfigError, match="PMSTUDIO_DIRECT_DATABASE_URL"):
        _base_urls(
            {
                "PMSTUDIO_DATABASE_URL": "postgresql://app@pgbouncer/pmstudio",
            }
        )
