"""Re-derive every ``[pinned]`` corpus answer from the oracle it names.

A pinned case's oracle cannot run in this repository's ``.venv`` (pytest-xdist must not be
installed there), so these tests skip there. To re-derive the pins, run them under any Python
that has pytest and pytest-xdist, from the repository root::

    <python with pytest + pytest-xdist> -m pytest -p no:cacheprovider conformance/tests/test_pinned.py

A failure means the pin no longer says what that pytest says: update ``[pinned]`` (and its
``pytest`` / ``xdist`` versions) from the failure's output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conformance.__main__ import _pinned_run  # pyright: ignore[reportPrivateUsage]
from conformance.harness.grade import load_case_args, load_pinned
from conformance.harness.runners import run_pytest_full

CORPUS = Path(__file__).resolve().parent.parent / "corpus"
PINNED = sorted(path.parent for path in CORPUS.rglob("case.toml") if load_pinned(path.parent))


def test_every_pin_names_the_versions_it_came_from() -> None:
    assert PINNED
    for case in PINNED:
        pinned = load_pinned(case)
        assert pinned is not None
        assert {"pytest", "xdist"} <= pinned.keys(), case


@pytest.mark.parametrize("case", PINNED, ids=[str(c.relative_to(CORPUS)) for c in PINNED])
def test_the_pin_is_what_pytest_xdist_says(case: Path) -> None:
    _ = pytest.importorskip("xdist")
    pinned = load_pinned(case)
    assert pinned is not None

    live = run_pytest_full(case, load_case_args(case))

    assert live == _pinned_run(pinned)
