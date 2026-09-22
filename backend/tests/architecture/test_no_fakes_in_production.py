from tests.architecture.guards import SRC, forbidden_names, iter_sources


def test_guard_flags_fake_names():
    assert forbidden_names("def make_fake_gateway():\n    ...\n") == {"make_fake_gateway"}


def test_production_code_has_no_fake_implementations():
    offenders = {
        path.relative_to(SRC).as_posix(): sorted(names)
        for path, source in iter_sources(SRC)
        if (names := forbidden_names(source))
    }
    assert offenders == {}
