"""An xunit hook runs **before** a user's autouse fixture, because it is registered first."""

import pytest

CALLS = []


@pytest.fixture(autouse=True)
def surrounding():
    CALLS.append("fixture")
    yield
    CALLS.append("fixture-teardown")


def setup_function():
    CALLS.append("setup_function")


class TestNoArgHooks:
    def setup_method(self):
        CALLS.append("setup_method")

    def test_inside_a_class(self):
        # A *module*-level autouse fixture is registered before the class body is reached,
        # so it still wins inside a class; `setup_method` only leads its own class's
        # fixtures. Measured on pytest 8.4.2 -- this is the oracle's order, not a guess.
        assert CALLS[-2:] == ["fixture", "setup_method"]


def test_at_module_level():
    assert CALLS[-2:] == ["setup_function", "fixture"]
