"""Test file with various error scenarios.

These tests verify that the test runner correctly handles and reports errors.
"""

try:
    import pytest

    testlib = pytest
    raises = pytest.raises
except ImportError:
    import rustest as testlib
    from rustest import raises


def test_assertion_error():
    """Test that assertion errors are properly caught and reported."""
    with raises(AssertionError):
        assert 1 == 2, "One does not equal two"


def test_runtime_error():
    """Test that runtime errors are properly caught and reported."""
    with raises(RuntimeError):
        raise RuntimeError("Something went wrong")


def test_type_error():
    """Test that type errors are properly caught and reported."""
    with raises(TypeError):
        result = "string" + 5


def test_zero_division():
    """Test that zero division errors are properly caught and reported."""
    with raises(ZeroDivisionError):
        result = 1 / 0


def test_fixture_error_handling():
    """Test that fixture errors can be caught using raises context manager."""
    # Create a fixture-like function that raises an error
    def broken_setup():
        raise ValueError("Broken fixture")

    # Verify the error is properly caught
    with raises(ValueError):
        broken_setup()
