"""
Basic tests to verify rustest can collect and run simple pytest-style tests.

This file contains minimal tests that should work with rustest's current
feature set, adapted from pytest's testing patterns.
"""


def test_simple_passing():
    """A simple passing test."""
    assert True


def test_simple_assertion():
    """Test basic assertion."""
    assert 1 + 1 == 2


def test_string_assertion():
    """Test string comparison."""
    assert "hello" == "hello"


def test_list_assertion():
    """Test list comparison."""
    assert [1, 2, 3] == [1, 2, 3]


class TestClassBased:
    """Test that class-based tests work."""

    def test_in_class(self):
        """Test method in a class."""
        assert 2 + 2 == 4

    def test_another_in_class(self):
        """Another test method."""
        assert "foo" != "bar"
