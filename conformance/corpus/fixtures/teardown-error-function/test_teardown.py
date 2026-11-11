"""A function-scoped fixture whose teardown raises: the test keeps its own outcome and the
teardown is an error of its own (pytest: `2 passed, 1 error`; `1 failed, 1 error` below)."""

import pytest


@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("function teardown")


def test_passes(broken_teardown):
    pass


def test_fails(broken_teardown):
    assert False


def test_clean():
    pass
