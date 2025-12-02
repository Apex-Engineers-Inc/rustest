"""Tests for request.getfixturevalue() functionality.

These tests require pytest-compat mode (--pytest-compat flag) or pytest itself.
"""

import sys
import pytest

# Create a conditional skip decorator for tests that require pytest or pytest-compat mode
_needs_pytest_compat = "pytest" not in sys.argv[0] and "--pytest-compat" not in sys.argv

_session_instance_ids: list[int] = []
_generator_teardowns: list[str] = []

def _skip_if_native_mode(func):
    """Decorator to skip tests when running rustest in native mode."""
    if _needs_pytest_compat:
        return pytest.mark.skip(reason="Requires pytest or rustest --pytest-compat mode")(func)
    return func



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


@_skip_if_native_mode
def test_getfixturevalue_basic(request):
    """Test basic getfixturevalue functionality."""
    value = request.getfixturevalue("simple_fixture")
    assert value == "hello"


@_skip_if_native_mode
def test_getfixturevalue_multiple(request):
    """Test calling getfixturevalue multiple times."""
    value1 = request.getfixturevalue("simple_fixture")
    value2 = request.getfixturevalue("another_fixture")
    assert value1 == "hello"
    assert value2 == 42


@_skip_if_native_mode
def test_getfixturevalue_cached(request):
    """Test that getfixturevalue caches results."""
    value1 = request.getfixturevalue("simple_fixture")
    value2 = request.getfixturevalue("simple_fixture")
    # Should be the same object (cached)
    assert value1 is value2


@_skip_if_native_mode
def test_getfixturevalue_with_dependency(request):
    """Test getfixturevalue with fixtures that have dependencies."""
    value = request.getfixturevalue("fixture_with_dependency")
    assert value == "hello_world"


@_skip_if_native_mode
def test_getfixturevalue_unknown_fixture(request):
    """Test that requesting an unknown fixture raises an error."""
    # Different error types for pytest vs rustest
    with pytest.raises((ValueError, Exception)):
        request.getfixturevalue("nonexistent")


@pytest.fixture
async def async_generator_fixture():
    """Async generator fixture for testing error handling."""
    yield "async_value"


@_skip_if_native_mode
def test_getfixturevalue_async_generator(request):
    """Test that async generator fixtures raise NotImplementedError with helpful message.

    This test verifies that getfixturevalue() raises a clear, helpful error when trying
    to use async fixtures, explaining why it doesn't work and how to fix it.

    Note: Async fixtures work perfectly when injected as test parameters - the error
    only occurs when trying to use getfixturevalue() with them.
    """
    import os
    # Skip if running with pure pytest (detected via PYTEST_CURRENT_TEST env var)
    if "PYTEST_CURRENT_TEST" in os.environ:
        pytest.skip(
            "Skipping: This test verifies rustest's getfixturevalue() error handling "
            "for async fixtures. Pure pytest handles async fixtures natively, so this "
            "behavior only applies when running with rustest --pytest-compat mode."
        )

    # Attempt to use getfixturevalue() with an async fixture
    # This should raise NotImplementedError with a helpful, detailed message
    with pytest.raises(NotImplementedError) as exc_info:
        request.getfixturevalue("async_generator_fixture")

    # Verify the error message is comprehensive and helpful
    error_msg = str(exc_info.value)
    assert "async_generator_fixture" in error_msg
    assert "async" in error_msg.lower()
    assert "getfixturevalue()" in error_msg
    # Check it explains WHY it fails
    assert "synchronous function" in error_msg.lower() or "sync context" in error_msg.lower()
    # Check it shows HOW to fix
    assert "def test_" in error_msg or "normal injection" in error_msg.lower()
    assert "✅" in error_msg or "instead use" in error_msg.lower()


# Parametrized tests using getfixturevalue
MODEL_FIXTURES = [
    ("simple_fixture", "hello"),
    ("another_fixture", 42),
]


@pytest.mark.parametrize("fixture_name,expected", MODEL_FIXTURES)
@_skip_if_native_mode
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
@_skip_if_native_mode
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


@_skip_if_native_mode
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


@pytest.fixture(scope="session")
def tracked_session_fixture():
    """Session fixture that tracks instantiations."""
    instance = object()
    _session_instance_ids.append(id(instance))
    return instance


@pytest.fixture
def request_aware_fixture(request):
    """Fixture that asserts request object is available."""
    return {"node": request.node.name, "scope": request.scope}


@pytest.fixture
def generator_teardown_fixture():
    """Generator fixture used to ensure teardown runs."""
    data = {"alive": True}
    try:
        yield data
    finally:
        _generator_teardowns.append("done")


@_skip_if_native_mode
def test_getfixturevalue_nested_deps(request):
    """Test getfixturevalue with deeply nested dependencies."""
    value = request.getfixturevalue("level3")
    assert value == "level1_level2_level3"


# Test combining normal fixture injection with getfixturevalue
@_skip_if_native_mode
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

    @_skip_if_native_mode
    def test_in_class(self, request):
        """Test getfixturevalue works in class methods."""
        value = request.getfixturevalue("simple_fixture")
        assert value == "hello"

    @pytest.mark.parametrize("fixture_name", ["simple_fixture", "another_fixture"])
    @_skip_if_native_mode
    def test_parametrized_in_class(self, request, fixture_name):
        """Test parametrized getfixturevalue in class methods."""
        value = request.getfixturevalue(fixture_name)
        assert value is not None


@_skip_if_native_mode
def test_getfixturevalue_passes_request_object(request):
    """request.getfixturevalue should provide the same request object to fixtures."""
    value = request.getfixturevalue("request_aware_fixture")
    assert value["scope"] == "function"
    assert value["node"].startswith("test_getfixturevalue_passes_request_object")


@_skip_if_native_mode
def test_getfixturevalue_session_scope_single_instance(request):
    """Session-scoped fixtures fetched dynamically should not be recreated."""
    first = request.getfixturevalue("tracked_session_fixture")
    second = request.getfixturevalue("tracked_session_fixture")
    assert first is second


@_skip_if_native_mode
def test_getfixturevalue_session_scope_shared_between_tests(request):
    """Subsequent tests should reuse the same session fixture instance."""
    current = request.getfixturevalue("tracked_session_fixture")
    assert len(_session_instance_ids) == 1
    assert id(current) == _session_instance_ids[0]


@_skip_if_native_mode
def test_getfixturevalue_generator_fixture_teardown(request):
    """Generator fixtures fetched dynamically should still execute teardown."""
    value = request.getfixturevalue("generator_teardown_fixture")
    assert value["alive"] is True
    assert _generator_teardowns == []


def teardown_module(_module):
    """Verify teardown side effects that occur after the module finishes."""
    if _needs_pytest_compat:
        return
    assert len(_session_instance_ids) == 1
    assert _generator_teardowns == ["done"]
