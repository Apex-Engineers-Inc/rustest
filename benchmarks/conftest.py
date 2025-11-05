"""Shared fixtures for benchmark tests."""

from rustest import fixture


@fixture
def simple_number():
    """A simple number fixture."""
    return 42


@fixture
def simple_list():
    """A simple list fixture."""
    return [1, 2, 3, 4, 5]


@fixture
def simple_dict():
    """A simple dictionary fixture."""
    return {"name": "test", "value": 100, "active": True}


@fixture
def computed_value():
    """A fixture that does some computation."""
    return sum(range(100))


@fixture
def large_list():
    """A fixture that creates a larger data structure."""
    return list(range(1000))


@fixture
def nested_data():
    """A fixture with nested data structures."""
    return {
        "users": [
            {"id": 1, "name": "Alice", "scores": [85, 90, 88]},
            {"id": 2, "name": "Bob", "scores": [78, 82, 80]},
            {"id": 3, "name": "Charlie", "scores": [92, 95, 93]},
        ],
        "metadata": {"total": 3, "active": True},
    }


@fixture
def base_value():
    """Base fixture for nested fixture tests."""
    return 10


@fixture
def doubled(base_value):
    """Fixture that depends on base_value."""
    return base_value * 2


@fixture
def tripled(base_value):
    """Fixture that depends on base_value."""
    return base_value * 3


@fixture
def combined(doubled, tripled):
    """Fixture that depends on multiple other fixtures."""
    return doubled + tripled
