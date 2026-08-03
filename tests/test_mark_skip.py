"""Test that @mark.skip properly skips tests.

Regression test: @rustest.mark.skip was not being detected by the Rust
discovery layer, causing skipped tests to run and report as passed.
"""

from rustest import mark


@mark.skip(reason="not implemented yet")
def test_mark_skip_with_reason():
    """This test should be skipped, not run."""
    assert False, "This should never execute"


@mark.skip
def test_mark_skip_bare():
    """Bare @mark.skip (no args) should also skip."""
    assert False, "This should never execute"


def test_not_skipped():
    """This test should run and pass normally."""
    assert True
