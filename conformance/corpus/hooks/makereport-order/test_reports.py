import pytest

from hook_log import LOG


def test_passes():
    pass


@pytest.mark.xfail(reason="known")
def test_xfails():
    assert False


@pytest.mark.xfail(reason="lucky")
def test_xpasses():
    pass


@pytest.mark.skip(reason="not today")
def test_skipped():
    pass


def test_imperative_xfail():
    pytest.xfail("said so")


def test_zz_the_log_is_pytests():
    assert LOG == EXPECTED


EXPECTED: list[object] = [
    ("trylast", "test_passes", "passed", False),
    ("plain", "test_passes", "passed", False),
    ("tryfirst", "test_passes", "passed"),
    ("log", "test_passes", "passed", [("seen", "test_passes")], None),
    ("trylast", "test_xfails", "failed", False),
    ("plain", "test_xfails", "skipped", True),
    ("tryfirst", "test_xfails", "skipped"),
    ("log", "test_xfails", "skipped", [("seen", "test_xfails")], 1),
    ("trylast", "test_xpasses", "passed", False),
    ("plain", "test_xpasses", "passed", True),
    ("tryfirst", "test_xpasses", "passed"),
    ("log", "test_xpasses", "passed", [("seen", "test_xpasses")], 1),
    ("log-skip", "test_skipped", "Skipped: not today"),
    ("trylast", "test_imperative_xfail", "failed", False),
    ("plain", "test_imperative_xfail", "skipped", True),
    ("tryfirst", "test_imperative_xfail", "skipped"),
    ("log", "test_imperative_xfail", "skipped", [("seen", "test_imperative_xfail")], None),
]
