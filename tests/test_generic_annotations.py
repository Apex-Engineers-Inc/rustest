"""pytest's generic classes evaluate subscripted as runtime annotations.

Deliberately no ``from __future__ import annotations``: the point is that the annotations
below are *evaluated* when ``def`` runs.  Must pass under real pytest and under rustest.
"""

import pytest


def test_capture_fixture_annotation(capsys: pytest.CaptureFixture[str]):
    print("hello")
    assert capsys.readouterr().out == "hello\n"


def test_capfd_annotation(capfd: pytest.CaptureFixture[str]):
    assert capfd.readouterr().out == ""


def test_exception_info_annotation():
    def check(info: pytest.ExceptionInfo[ValueError]) -> str:
        return str(info.value)

    with pytest.raises(ValueError) as info:
        raise ValueError("boom")
    assert check(info) == "boom"


def test_other_generic_pytest_classes_subscript():
    for name in ("CaptureFixture", "ExceptionInfo", "FixtureDef", "CallInfo", "StashKey"):
        getattr(pytest, name)[str]
    pytest.CaptureFixture[bytes]
