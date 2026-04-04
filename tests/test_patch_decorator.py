"""Tests for @patch decorator support in pytest-compat mode."""
from unittest.mock import MagicMock, patch

from rustest import fixture


@fixture
def base_value():
    return 42


# Simple @patch on a function
@patch("os.path.exists", return_value=True)
def test_single_patch(mock_exists: MagicMock):
    import os
    assert os.path.exists("/fake/path") is True
    mock_exists.assert_called_once_with("/fake/path")


# Multiple @patch decorators
@patch("os.path.isfile", return_value=False)
@patch("os.path.exists", return_value=True)
def test_multiple_patches(mock_exists: MagicMock, mock_isfile: MagicMock):
    import os
    assert os.path.exists("/fake") is True
    assert os.path.isfile("/fake") is False


# @patch with fixture dependency
@patch("os.path.exists", return_value=True)
def test_patch_with_fixture(mock_exists: MagicMock, base_value):
    assert base_value == 42
    import os
    assert os.path.exists("/fake") is True


# Class with @patch
class TestWithPatch:
    @patch("os.path.exists", return_value=True)
    def test_class_patch(self, mock_exists: MagicMock):
        import os
        assert os.path.exists("/fake") is True
