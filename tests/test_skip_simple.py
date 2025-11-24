"""Simple test to verify skip detection."""

import pytest


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
