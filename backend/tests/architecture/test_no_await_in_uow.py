from tests.architecture.guards import (
    await_inside_uow,
    await_inside_uow_offenders,
)


def test_guard_flags_await_inside_uow():
    source = "async def run(uow):\n    with uow:\n        await thing()\n"
    assert await_inside_uow(source) == [3]


def test_guard_flags_await_inside_self_dot_uow():
    source = "async def run(self):\n    with self._uow:\n        await thing()\n"
    assert await_inside_uow(source) == [3]


def test_guard_allows_await_outside_uow():
    source = "async def run(uow):\n    await thing()\n    with uow:\n        save()\n"
    assert await_inside_uow(source) == []


def test_guard_scans_every_layer_not_just_application(tmp_path):
    """约定：用例里 UnitOfWork 的变量名必须叫 `uow`——模型调用一律在事务外。

    扫描范围是**整个 src**，不是只有 `application/`：这条规矩对每一层都成立
    （2026-10-04 之前只扫 application，新加的层会静默逃逸）。
    """

    api = tmp_path / "pmstudio" / "api"
    api.mkdir(parents=True)
    (api / "x.py").write_text(
        "async def run(uow):\n    with uow:\n        await thing()\n", encoding="utf-8"
    )
    assert await_inside_uow_offenders(tmp_path) == {"pmstudio/api/x.py": [3]}


def test_src_never_awaits_inside_a_uow():
    assert await_inside_uow_offenders() == {}
