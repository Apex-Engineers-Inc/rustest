"""Test file with skip decorators."""

from rustest import skip, parametrize


@skip()
def test_skipped_without_reason():
    """This test should be skipped with default reason."""
    assert False, "This should not run"


@skip("Not implemented yet")
def test_skipped_with_reason():
    """This test should be skipped with a custom reason."""
    assert False, "This should not run"


@skip("Feature not ready")
@parametrize("value", [1, 2, 3])
def test_skipped_parametrized(value):
    """Parametrized test that is skipped."""
    assert False, "This should not run"


def test_not_skipped():
    """This test should run normally."""
    assert True
