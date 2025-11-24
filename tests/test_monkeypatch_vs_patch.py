"""Test monkeypatch fixture as an alternative to unittest.mock.patch."""

import pytest


def get_config_value():
    """Function that we want to mock."""
    return "production"


def test_monkeypatch_setattr(monkeypatch):
    """Test using monkeypatch.setattr to replace a function."""
    # Mock the function
    monkeypatch.setattr(__name__ + ".get_config_value", lambda: "test")

    # Call the mocked function
    result = get_config_value()
    assert result == "test"


def test_monkeypatch_dict(monkeypatch):
    """Test using monkeypatch to patch a dictionary."""
    import os

    # Set environment variable
    monkeypatch.setenv("TEST_VAR", "test_value")
    assert os.environ["TEST_VAR"] == "test_value"


class MyClass:
    """Test class for patching."""

    def get_value(self):
        """Method to be patched."""
        return "original"


def test_monkeypatch_class_method(monkeypatch):
    """Test patching a class method."""
    obj = MyClass()

    # Patch the method
    monkeypatch.setattr(obj, "get_value", lambda: "patched")

    assert obj.get_value() == "patched"


def test_monkeypatch_module_attribute(monkeypatch):
    """Test patching a module attribute."""
    import sys

    # Save original value
    original_platform = sys.platform

    # Patch it
    monkeypatch.setattr(sys, "platform", "test_platform")
    assert sys.platform == "test_platform"

    # After test, it should be restored automatically


# This is what the user would need to convert from @patch to monkeypatch:
#
# OLD (unittest.mock):
# from unittest.mock import patch, MagicMock
#
# @patch("module.function")
# def test_with_patch(mock_function):
#     mock_function.return_value = 42
#     # test code
#
# NEW (monkeypatch):
# def test_with_monkeypatch(monkeypatch):
#     monkeypatch.setattr("module.function", lambda: 42)
#     # test code
