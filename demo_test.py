"""Simple demo tests to show off the new spinner output."""

import time


def test_quick_one():
    """A quick test."""
    time.sleep(0.1)
    assert 1 + 1 == 2


def test_quick_two():
    """Another quick test."""
    time.sleep(0.1)
    assert 2 + 2 == 4


def test_quick_three():
    """Yet another quick test."""
    time.sleep(0.1)
    assert 3 + 3 == 6


def test_failing():
    """A test that fails."""
    time.sleep(0.1)
    assert 1 == 2


def test_quick_four():
    """One more quick test."""
    time.sleep(0.1)
    assert 4 + 4 == 8
