"""`xfail_strict = true` makes a bare `xfail` strict; the mark's own `strict=` still wins."""

import pytest


@pytest.mark.xfail(reason="strict by the ini")
def test_unexpected_pass_fails():
    pass


@pytest.mark.xfail(strict=False, reason="the mark says not strict")
def test_mark_overrides_the_ini():
    pass


@pytest.mark.xfail(reason="really broken")
def test_expected_failure():
    assert False
