"""Generate synthetic trivial test suites for benchmarking framework overhead."""

from __future__ import annotations

from pathlib import Path


def generate_suite(root: Path, files: int, tests_per_file: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for file_index in range(files):
        lines: list[str] = []
        for test_index in range(tests_per_file):
            lines.append(f"def test_case_{test_index}():")
            lines.append(f"    assert {test_index} + 1 == {test_index + 1}")
            lines.append("")
        (root / f"test_gen_{file_index:04d}.py").write_text("\n".join(lines), encoding="utf-8")


#: What a real conftest imports at module level: heavy enough that skipping one shows.
_LF_CONFTEST = """import json, decimal, fractions
import pytest


@pytest.fixture
def value():
    return 1
"""


def generate_lf_suite(
    root: Path, layout: str, files: int = 200, tests_per_file: int = 10, packages: int = 20
) -> Path:
    """A suite with exactly one failing test, for timing ``--lf`` against a full run.

    ``layout="flat"`` puts every file beside each other; ``layout="dirs"`` spreads them over
    *packages* directories that each carry a ``conftest.py``, which a ``--lf`` run still has
    to import (pytest imports the conftests of directories it walks).  The failure is
    ``test_case_0`` of the first file, so a remembered failure lives in a module-level test
    and the other files can be skipped.  Returns the path of the failing file.
    """
    if layout == "flat":
        generate_suite(root, files, tests_per_file)
        failing = root / "test_gen_0000.py"
    elif layout == "dirs":
        per_package = files // packages
        for package in range(packages):
            directory = root / f"pkg_{package:02d}"
            generate_suite(directory, per_package, tests_per_file)
            (directory / "conftest.py").write_text(_LF_CONFTEST, encoding="utf-8")
            for index in range(per_package):  # distinct names, as a real tree has
                (directory / f"test_gen_{index:04d}.py").rename(
                    directory / f"test_{package:02d}_m{index:02d}.py"
                )
        failing = root / "pkg_00" / "test_00_m00.py"
    else:
        raise ValueError(f"unknown layout {layout!r}")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    text = failing.read_text(encoding="utf-8")
    failing.write_text(text.replace("assert 0 + 1 == 1", "assert 0 + 1 == 2", 1), encoding="utf-8")
    return failing
