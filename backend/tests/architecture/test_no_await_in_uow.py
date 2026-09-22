from tests.architecture.guards import SRC, await_inside_uow, iter_sources

APPLICATION = SRC / "pmstudio" / "application"


def test_guard_flags_await_inside_uow():
    source = "async def run(uow):\n    with uow:\n        await thing()\n"
    assert await_inside_uow(source) == [3]


def test_guard_allows_await_outside_uow():
    source = "async def run(uow):\n    await thing()\n    with uow:\n        save()\n"
    assert await_inside_uow(source) == []


def test_application_never_awaits_inside_a_uow():
    """约定：用例里 UnitOfWork 的变量名必须叫 `uow`——模型调用一律在事务外。"""

    offenders = {
        path.relative_to(SRC).as_posix(): await_inside_uow(source)
        for path, source in iter_sources(APPLICATION)
        if await_inside_uow(source)
    }
    assert offenders == {}
