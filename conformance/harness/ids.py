"""Test-ID normalization so pytest and rustest IDs are comparable."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


def _posix(path_part: str) -> str:
    return str(PurePosixPath(PureWindowsPath(path_part).as_posix()))


def normalize_pytest_nodeid(nodeid: str) -> str:
    """Posixify the path segment and drop intermediate class segments.

    v1 rustest report IDs carry only path::name, so class segments are removed
    from both sides for comparison. v2's report will restore full fidelity.
    """
    parts = nodeid.split("::")
    return f"{_posix(parts[0])}::{parts[-1]}" if len(parts) > 1 else _posix(parts[0])


def normalize_rustest_id(test_id: str, case_dir: Path) -> str:
    """Make a rustest report ID comparable to a normalized pytest nodeid.

    rustest emits ``<path>::<name>`` where ``path`` may be absolute or relative
    to the case directory, and uses the host path separator. The result is always
    posix-form and relative to *case_dir*, with class segments dropped.
    """
    path_part, sep, name = test_id.partition("::")
    candidate = Path(path_part)
    if candidate.is_absolute():
        try:
            path_part = str(candidate.relative_to(case_dir))
        except ValueError:
            pass
    normalized_path = _posix(path_part)
    if not sep:
        return normalized_path
    return f"{normalized_path}::{name.split('::')[-1]}"
