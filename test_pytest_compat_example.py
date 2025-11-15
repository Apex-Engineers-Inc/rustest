"""
Example pytest test file to demonstrate pytest compatibility mode.

This file uses pytest imports and should work with rustest --pytest-compat
"""
import pytest


@pytest.fixture
def sample_data():
    """A simple fixture returning sample data."""
    return {"name": "Alice", "age": 30}


@pytest.fixture(scope="module")
def database():
    """A module-scoped fixture simulating database setup."""
    print("Setting up database...")
    db = {"connection": "active", "data": []}
    yield db
    print("Tearing down database...")


def test_simple():
    """A simple test with no fixtures."""
    assert 1 + 1 == 2


def test_with_fixture(sample_data):
    """Test using a fixture."""
    assert sample_data["name"] == "Alice"
    assert sample_data["age"] == 30


def test_with_module_fixture(database):
    """Test using a module-scoped fixture."""
    assert database["connection"] == "active"
    database["data"].append("test_entry")


@pytest.mark.parametrize("value,expected", [
    (1, 2),
    (2, 4),
    (3, 6),
])
def test_parametrized(value, expected):
    """Parametrized test."""
    assert value * 2 == expected


@pytest.mark.parametrize("x,y,result", [
    (1, 2, 3),
    (5, 7, 12),
    (10, 20, 30),
], ids=["small", "medium", "large"])
def test_addition_with_ids(x, y, result):
    """Parametrized test with IDs."""
    assert x + y == result


def test_with_approx():
    """Test using pytest.approx for floating point comparisons."""
    assert 0.1 + 0.2 == pytest.approx(0.3)
    assert 1.0001 == pytest.approx(1.0, abs=0.001)


def test_with_raises():
    """Test exception handling with pytest.raises."""
    with pytest.raises(ValueError):
        int("not a number")

    with pytest.raises(ValueError, match="invalid literal"):
        int("not a number")


@pytest.mark.skip(reason="Demonstrating skip")
def test_skipped():
    """This test should be skipped."""
    assert False


@pytest.mark.slow
def test_marked_slow():
    """Test marked as slow."""
    result = sum(range(1000))
    assert result > 0


@pytest.mark.slow
@pytest.mark.integration
def test_multiple_marks():
    """Test with multiple marks."""
    assert True


class TestClass:
    """Test class demonstrating class-based tests."""

    @pytest.fixture
    def class_fixture(self):
        """Fixture specific to this class."""
        return "class_data"

    def test_in_class(self, class_fixture):
        """Test method in a class."""
        assert class_fixture == "class_data"

    def test_another_in_class(self, sample_data):
        """Another test method using module fixture."""
        assert "name" in sample_data


# Async test example
@pytest.mark.asyncio
async def test_async_operation():
    """Async test using pytest.mark.asyncio."""
    import asyncio
    await asyncio.sleep(0.001)
    result = 42
    assert result == 42
