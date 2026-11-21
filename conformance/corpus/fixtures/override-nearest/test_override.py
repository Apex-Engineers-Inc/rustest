import pytest


@pytest.fixture
def value():
    return "module"  # module fixture shadows conftest fixture


def test_nearest_wins(value):
    assert value == "module"
