"""Integration tests for @mark.xfail support."""

from rustest import mark


@mark.xfail(reason="Known failure")
def test_expected_failure() -> None:
    assert False, "This should fail"


@mark.xfail(reason="Should pass unexpectedly")
def test_unexpected_pass() -> None:
    assert True


@mark.xfail(False, reason="Condition not met")
def test_condition_false() -> None:
    assert True  # Should pass normally since condition is False


def test_normal() -> None:
    assert True
