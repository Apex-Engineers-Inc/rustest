"""Test that @patch decorator is auto-skipped with helpful message.

These tests are designed to verify rustest's auto-skip behavior for @patch.
In pytest-compat mode (--pytest-compat flag), tests with @patch decorators
are automatically skipped with a helpful message suggesting monkeypatch instead.
When running with pure pytest, the @patch decorators work normally.
"""

import sys
import pytest
from unittest.mock import patch, MagicMock


def some_function():
    """Function to be patched."""
    return "original"


def test_normal_pass():
    """A normal test that should pass."""
    assert True


@patch(__name__ + ".some_function")
def test_with_patch_decorator(mock_func):
    """Test using @patch decorator - should be auto-skipped."""
    mock_func.return_value = "mocked"
    assert some_function() == "mocked"


@patch(__name__ + ".some_function")
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

    @patch(__name__ + ".some_function")
    def test_with_patch_in_class(self, mock_func):
        """Test with @patch in class - should be auto-skipped."""
        mock_func.return_value = "mocked"
        assert some_function() == "mocked"


def test_another_normal_pass():
    """Another normal test - should pass."""
    assert 1 + 1 == 2
