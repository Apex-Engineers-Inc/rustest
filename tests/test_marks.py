"""Test file demonstrating pytest mark support."""

from rustest import mark, parametrize


@mark.slow
def test_marked_slow():
    """Test marked as slow."""
    assert True


@mark.integration
def test_marked_integration():
    """Test marked as integration."""
    assert True


@mark.unit
@mark.fast
def test_multiple_marks():
    """Test with multiple marks."""
    assert True


@mark.timeout(seconds=30)
def test_mark_with_args():
    """Test with mark that has arguments."""
    assert True


@mark.custom(key="value", priority=1)
def test_mark_with_kwargs():
    """Test with mark that has keyword arguments."""
    assert True


@mark.regression
@parametrize("value", [1, 2, 3])
def test_marked_parametrized(value):
    """Parametrized test with marks."""
    assert value > 0


@mark.smoke
@mark.critical
@parametrize("x,y", [(1, 2), (3, 4)])
def test_multiple_marks_parametrized(x, y):
    """Parametrized test with multiple marks."""
    assert x < y


def test_unmarked():
    """Test without any marks."""
    assert True
