"""Test that @patch decorator is auto-skipped with helpful message.

These tests are designed to verify rustest's auto-skip behavior for @patch.
When running with pytest, these tests would fail, so we skip them.
"""

import sys
import pytest
from unittest.mock import patch, MagicMock

# Skip all tests in this file when running with pytest
# These tests are meant to verify rustest's @patch auto-skip behavior
pytestmark = pytest.mark.skipif(
    "_pytest" in sys.modules,
    reason="These tests verify rustest's @patch auto-skip behavior, not meant for pytest"
)


def some_function():
    """Function to be patched."""
    return "original"


def test_normal_pass():
    """A normal test that should pass."""
    assert True


@patch("tests.test_patch_decorator.some_function")
def test_with_patch_decorator(mock_func):
    """Test using @patch decorator - should be auto-skipped."""
    mock_func.return_value = "mocked"
    assert some_function() == "mocked"


@patch("tests.test_patch_decorator.some_function")
@patch("builtins.open")
def test_with_multiple_patches(mock_open, mock_func):
    """Test using multiple @patch decorators - should be auto-skipped."""
    mock_func.return_value = "mocked"
    assert some_function() == "mocked"


class TestPatchInClass:
    """Test class with @patch decorators."""

    def test_normal_in_class(self):
        """Normal test in class - should pass."""
        assert True

    @patch("tests.test_patch_decorator.some_function")
    def test_with_patch_in_class(self, mock_func):
        """Test with @patch in class - should be auto-skipped."""
        mock_func.return_value = "mocked"
        assert some_function() == "mocked"


def test_another_normal_pass():
    """Another normal test - should pass."""
    assert 1 + 1 == 2
