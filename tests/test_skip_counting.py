"""Test that skipped tests are counted correctly."""

import pytest


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


def test_another_pass():
    """Another normal passing test."""
    assert 1 + 1 == 2


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
