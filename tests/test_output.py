"""Test file for testing stdout/stderr capture."""

import sys


def test_with_stdout():
    """Test that prints to stdout."""
    print("This is stdout output")
    assert True


def test_with_stderr():
    """Test that prints to stderr."""
    print("This is stderr output", file=sys.stderr)
    assert True


def test_with_both_outputs():
    """Test that prints to both stdout and stderr."""
    print("stdout message")
    print("stderr message", file=sys.stderr)
    assert True


def test_with_multiline_output():
    """Test with multiple lines of output."""
    for i in range(5):
        print(f"Line {i}")
    assert True


def test_failure_with_output():
    """Test that assertion failures with output are properly captured."""
    try:
        import pytest

        raises = pytest.raises
    except ImportError:
        # rustest doesn't have raises, so create a simple context manager
        from contextlib import contextmanager

        @contextmanager
        def raises(exc_type):
            try:
                yield
                raise AssertionError(
                    f"Expected {exc_type.__name__} but no exception was raised"
                )
            except exc_type:
                pass  # Expected exception

    print("Some output before failure")
    with raises(AssertionError):
        assert False, "Expected failure"
