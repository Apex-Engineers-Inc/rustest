import sys

import pytest

_needs_pytest_compat = "pytest" not in sys.argv[0] and "--pytest-compat" not in sys.argv


def _skip_if_native_mode(func):
    if _needs_pytest_compat:
        return pytest.mark.skip(reason="Requires pytest or rustest --pytest-compat mode")(func)
    return func


_usefixtures_log: list[str] = []


@pytest.fixture
def touch_state():
    _usefixtures_log.append("ran")


@_skip_if_native_mode
@pytest.mark.usefixtures("touch_state")
def test_usefixtures_runs_fixture():
    assert _usefixtures_log == ["ran"]
    _usefixtures_log.clear()
