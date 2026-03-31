"""Test file with skip decorators."""

import pytest

from rustest import skip_decorator as skip, parametrize


@skip()
def test_skipped_without_reason():
    """This test should be skipped with default reason."""
    assert False, "This should not run"


@skip("Not implemented yet")
def test_skipped_with_reason():
    """This test should be skipped with a custom reason."""
    assert False, "This should not run"


@skip("Feature not ready")
@parametrize("value", [1, 2, 3])
def test_skipped_parametrized(value):
    """Parametrized test that is skipped."""
    assert False, "This should not run"


def test_not_skipped():
    """This test should run normally."""
    assert True


# --- Dynamic skip tests (from test_skip_simple.py) ---


def test_pass():
    """A passing test."""
    assert True


def test_explicit_skip():
    """Test that calls pytest.skip()."""
    pytest.skip("This test is skipped")
    assert False  # Should not reach here


def test_another_pass():
    """Another passing test."""
    assert True


# --- Skip counting tests (from test_skip_counting.py) ---


def test_normal_pass():
    """A normal passing test."""
    assert True


def test_dynamic_skip(request):
    """Test that dynamically skips itself."""
    pytest.skip("Dynamically skipped")


@pytest.mark.skip(reason="Statically skipped")
def test_static_skip():
    """Test with skip decorator."""
    assert False  # Should not execute


def test_skipif_true():
    """Test that is skipped due to condition."""
    import sys
    if sys.platform.startswith("linux") or sys.platform.startswith("darwin") or sys.platform.startswith("win"):
        pytest.skip("Skipped on all platforms for testing")


class TestSkippingInClass:
    """Test class with skip tests."""

    def test_pass_in_class(self):
        """Passing test in class."""
        assert True

    def test_skip_in_class(self):
        """Skipped test in class."""
        pytest.skip("Skipped in class")


def test_conditional_skip():
    """Test that conditionally skips."""
    condition = True
    if condition:
        pytest.skip("Condition met, skipping")
    assert False  # Should not reach here
