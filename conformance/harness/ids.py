"""Test-ID normalization so pytest and rustest IDs are comparable."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


def _posix(path_part: str) -> str:
    return str(PurePosixPath(PureWindowsPath(path_part).as_posix()))


def normalize_pytest_nodeid(nodeid: str) -> str:
    """Posixify the path segment of a pytest nodeid, preserving every other segment.

    Class segments are kept: ``tests\\test_a.py::TestX::test_y[1-2]`` normalizes to
    ``tests/test_a.py::TestX::test_y[1-2]``. Dropping them would collapse
    ``f.py::TestA::test_x`` and ``f.py::TestB::test_x`` into one ID and hide a
    runner that missed one of them.
    """
    path_part, sep, rest = nodeid.partition("::")
    normalized_path = _posix(path_part)
    return f"{normalized_path}::{rest}" if sep else normalized_path


def normalize_rustest_id(test_id: str, case_dir: Path) -> str:
    """Make a rustest report ID comparable to a normalized pytest nodeid.

    rustest emits ``<path>::<name>`` where ``path`` uses the host separator and is
    either relative to the process CWD (which the runners always set to *case_dir*)
    or, when the file lies outside that CWD, absolute. The result is always
    posix-form and relative to *case_dir*.

    The name portion is passed through untouched. Verified against real v1 reports:
    class-based tests are emitted as ``sub\\test_nested.py::TestBox::test_in_class``,
    i.e. the same three-segment shape as a pytest nodeid, so preserving the segments
    keeps both sides comparable without losing information.
    """
    path_part, sep, rest = test_id.partition("::")
    candidate = Path(path_part)
    if candidate.is_absolute():
        try:
            path_part = str(candidate.relative_to(case_dir))
        except ValueError:
            pass
    normalized_path = _posix(path_part)
    return f"{normalized_path}::{rest}" if sep else normalized_path
