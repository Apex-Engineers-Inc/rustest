"""pytest 9's strictness inis: ``strict_xfail`` (alias ``xfail_strict``, the canonical name
winning), ``strict_markers``, and the ``strict`` ini both fall back to.

Expected outcomes probed with pytest 9.0.2, each tree run with ``-q -p no:cacheprovider``.
This repository's ``.venv`` has pytest 8, where these keys do not exist: a ``pytest-9.0.0``
``dist-info`` directory first on ``PYTHONPATH`` is what rustest reads as pytest 9 installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from .helpers import clean_env, make_tree, run

_XPASS = "import pytest\n@pytest.mark.xfail\ndef test_x():\n    pass\n"
_MARK = "import pytest\n@pytest.mark.nosuchmark\ndef test_m():\n    pass\n"


@pytest.mark.parametrize(
    ("ini", "body", "rc", "summary"),
    [
        ("strict_xfail = true\n", _XPASS, 1, "1 failed"),
        ("xfail_strict = true\n", _XPASS, 1, "1 failed"),
        ("strict_xfail = false\nxfail_strict = true\n", _XPASS, 0, "1 xpassed"),
        ("strict = true\n", _XPASS, 1, "1 failed"),
        ("strict = true\nstrict_xfail = false\n", _XPASS, 0, "1 xpassed"),
        ("strict = true\n", _MARK, 2, "1 error"),
        ("strict_markers = true\n", _MARK, 2, "1 error"),
        ("strict_markers = true\n", _XPASS, 0, "1 xpassed"),
        ("strict_xfail = true\n", _MARK, 0, "1 passed, 1 warning"),
    ],
    ids=[
        "strict_xfail",
        "xfail_strict-alias",
        "canonical-wins",
        "strict-xfail",
        "strict-but-xfail-off",
        "strict-markers-via-strict",
        "strict_markers",
        "strict_markers-leaves-xfail",
        "strict_xfail-leaves-markers",
    ],
)
def test_pytest_9_strictness_inis(tmp_path: Path, ini: str, body: str, rc: int, summary: str):
    tree = make_tree(tmp_path, "strict", {"test_a.py": body})
    _ = (tree / "pytest.ini").write_text("[pytest]\n" + ini, encoding="utf-8")

    result = run([sys.executable, "-m", "rustest"], tree, _pytest9(tmp_path))

    assert result.returncode == rc, result.stdout + result.stderr
    assert summary in result.stdout + result.stderr


def _pytest9(tmp_path: Path) -> dict[str, str]:
    site = tmp_path / "site9"
    (site / "pytest-9.0.0.dist-info").mkdir(parents=True)
    (site / "_pytest").mkdir()  # beside its package; an empty directory imports nothing
    return {**clean_env(), "PYTHONPATH": str(site)}


@pytest.mark.parametrize("ini", ["strict_xfail = true\n", "strict = true\n"])
def test_under_pytest_8_the_pytest_9_keys_do_nothing(tmp_path: Path, ini: str):
    """pytest 8 does not know them (a config warning): an xpass stays an xpass."""
    tree = make_tree(tmp_path, "strict8", {"test_a.py": _XPASS})
    _ = (tree / "pytest.ini").write_text("[pytest]\n" + ini, encoding="utf-8")

    result = run([sys.executable, "-m", "rustest"], tree)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 xpassed" in result.stdout + result.stderr
