"""Colour precedence of ``rustest._progress``; the bytes are diffed against pytest in
``test_progress_oracle.py``.
"""

from __future__ import annotations

import io

import pytest

from rustest import _progress
from rustest._progress import use_color

# -- colour precedence ---------------------------------------------------------------------


class _Stream(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.mark.parametrize(
    ("mode", "env", "tty", "expected"),
    [
        # --color beats everything
        ("yes", {"NO_COLOR": "1", "PY_COLORS": "0"}, False, True),
        ("always", {}, False, True),
        ("no", {"FORCE_COLOR": "1", "PY_COLORS": "1"}, True, False),
        ("never", {}, True, False),
        # then PY_COLORS, then NO_COLOR, then FORCE_COLOR
        ("auto", {"PY_COLORS": "1", "NO_COLOR": "1"}, False, True),
        ("auto", {"PY_COLORS": "0", "FORCE_COLOR": "1"}, True, False),
        ("auto", {"NO_COLOR": "1", "FORCE_COLOR": "1"}, True, False),
        ("auto", {"FORCE_COLOR": "1"}, False, True),
        # an empty variable is unset
        ("auto", {"NO_COLOR": "", "FORCE_COLOR": ""}, False, False),
        # finally a terminal that is not "dumb"
        ("auto", {}, True, True),
        ("auto", {"TERM": "dumb"}, True, False),
        ("auto", {}, False, False),
    ],
)
def test_colour_precedence(
    monkeypatch: pytest.MonkeyPatch, mode: str, env: dict[str, str], tty: bool, expected: bool
) -> None:
    for name in ("PY_COLORS", "NO_COLOR", "FORCE_COLOR", "TERM"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(_progress, "_enable_vt", lambda stream: True)
    assert use_color(_Stream(tty), mode) is expected


def test_a_windows_console_that_refuses_ansi_gets_no_colour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forced colour on a console whose VT mode will not switch on is off; a redirected
    stream needs no VT and keeps it."""
    for name in ("PY_COLORS", "NO_COLOR", "TERM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr(_progress.sys, "platform", "win32")
    monkeypatch.setattr(_progress, "_enable_vt", lambda stream: False)
    assert use_color(_Stream(True), "auto") is False
    assert use_color(_Stream(False), "auto") is True


def test_main_colour_follows_pytest() -> None:
    main = _progress.main_color
    assert main({"passed", "failed"}, last=False) == "red"
    assert main({"error"}, last=True) == "red"
    assert main({"passed", "xpassed"}, last=True) == "yellow"
    assert main({"passed", "skipped"}, last=False) == "green"
    assert main(set(), last=False) == "green"
    assert main({"skipped"}, last=True) == "yellow"
