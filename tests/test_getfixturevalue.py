"""Tests for request.getfixturevalue() functionality.

These tests require pytest-compat mode (--pytest-compat flag) or pytest itself.
"""

import sys
import pytest



@pytest.fixture
def simple_fixture():
    """A simple fixture that returns a value."""
    return "hello"


@pytest.fixture
def another_fixture():
    """Another simple fixture."""
    return 42


@pytest.fixture
def fixture_with_dependency(simple_fixture):
    """A fixture that depends on another fixture."""
    return f"{simple_fixture}_world"


def test_getfixturevalue_basic(request):
    """Test basic getfixturevalue functionality."""
    value = request.getfixturevalue("simple_fixture")
    assert value == "hello"


def test_getfixturevalue_multiple(request):
    """Test calling getfixturevalue multiple times."""
    value1 = request.getfixturevalue("simple_fixture")
    value2 = request.getfixturevalue("another_fixture")
    assert value1 == "hello"
    assert value2 == 42


def test_getfixturevalue_cached(request):
    """Test that getfixturevalue caches results."""
    value1 = request.getfixturevalue("simple_fixture")
    value2 = request.getfixturevalue("simple_fixture")
    # Should be the same object (cached)
    assert value1 is value2


def test_getfixturevalue_with_dependency(request):
    """Test getfixturevalue with fixtures that have dependencies."""
    value = request.getfixturevalue("fixture_with_dependency")
    assert value == "hello_world"


def test_getfixturevalue_unknown_fixture(request):
    """Test that requesting an unknown fixture raises an error."""
    # Different error types for pytest vs rustest
    with pytest.raises((ValueError, Exception)):
        request.getfixturevalue("nonexistent")


# Parametrized tests using getfixturevalue
MODEL_FIXTURES = [
    ("simple_fixture", "hello"),
    ("another_fixture", 42),
]


@pytest.mark.parametrize("fixture_name,expected", MODEL_FIXTURES)
def test_parametrized_getfixturevalue(request, fixture_name, expected):
    """Test using getfixturevalue in parametrized tests."""
    value = request.getfixturevalue(fixture_name)
    assert value == expected


# Real-world example: testing multiple model types
@pytest.fixture
def model_a():
    """Fixture for model type A."""
    return {"type": "A", "value": 1}


@pytest.fixture
def model_b():
    """Fixture for model type B."""
    return {"type": "B", "value": 2}


@pytest.fixture
def model_c():
    """Fixture for model type C."""
    return {"type": "C", "value": 3}


MODEL_CONFIGS = [
    ("model_a", "A", 1),
    ("model_b", "B", 2),
    ("model_c", "C", 3),
]


@pytest.mark.parametrize("fixture_name,expected_type,expected_value", MODEL_CONFIGS)
def test_model_types(request, fixture_name, expected_type, expected_value):
    """Test multiple model types using getfixturevalue.

    This simulates the pattern described in the issue where you test
    multiple similar entities with shared test logic.
    """
    model = request.getfixturevalue(fixture_name)
    assert model["type"] == expected_type
    assert model["value"] == expected_value


# Test with generator fixture
@pytest.fixture
def generator_fixture():
    """A fixture using yield for setup/teardown."""
    resource = {"initialized": True}
    yield resource
    # Teardown would happen here
    resource["initialized"] = False


def test_getfixturevalue_generator(request):
    """Test that getfixturevalue works with generator fixtures."""
    value = request.getfixturevalue("generator_fixture")
    assert value["initialized"] is True


# Test with nested dependencies
@pytest.fixture
def level1():
    """Level 1 fixture."""
    return "level1"


@pytest.fixture
def level2(level1):
    """Level 2 fixture depending on level1."""
    return f"{level1}_level2"


@pytest.fixture
def level3(level2):
    """Level 3 fixture depending on level2."""
    return f"{level2}_level3"


def test_getfixturevalue_nested_deps(request):
    """Test getfixturevalue with deeply nested dependencies."""
    value = request.getfixturevalue("level3")
    assert value == "level1_level2_level3"


# Test combining normal fixture injection with getfixturevalue
def test_mixed_fixture_usage(request, simple_fixture):
    """Test using both normal fixture injection and getfixturevalue."""
    # simple_fixture is injected normally
    assert simple_fixture == "hello"

    # another_fixture is loaded dynamically
    another = request.getfixturevalue("another_fixture")
    assert another == 42


# Test that works with class-based tests
class TestGetFixtureValueInClass:
    """Test getfixturevalue in class-based tests."""

    def test_in_class(self, request):
        """Test getfixturevalue works in class methods."""
        value = request.getfixturevalue("simple_fixture")
        assert value == "hello"

    @pytest.mark.parametrize("fixture_name", ["simple_fixture", "another_fixture"])
    def test_parametrized_in_class(self, request, fixture_name):
        """Test parametrized getfixturevalue in class methods."""
        value = request.getfixturevalue(fixture_name)
        assert value is not None
