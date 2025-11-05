"""Test file with various error scenarios."""

from rustest import fixture


def test_assertion_error():
    """Test that fails with an assertion error."""
    assert 1 == 2, "One does not equal two"


def test_runtime_error():
    """Test that raises a runtime error."""
    raise RuntimeError("Something went wrong")


def test_type_error():
    """Test that raises a type error."""
    result = "string" + 5


def test_zero_division():
    """Test that raises a zero division error."""
    result = 1 / 0


@fixture
def broken_fixture():
    """Fixture that raises an error."""
    raise ValueError("Broken fixture")


def test_with_broken_fixture(broken_fixture):
    """Test that uses a fixture that raises an error."""
    assert True
