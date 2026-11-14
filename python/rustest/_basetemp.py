"""pytest's base temporary directory: ``<temproot>/pytest-of-<user>/pytest-<n>``.

Port of `_pytest/tmpdir.py::TempPathFactory.getbasetemp` and the parts of
`_pytest/pathlib.py::make_numbered_dir_with_cleanup` it uses, without ``--basetemp`` (a
hand-off) or the ``tmp_path_retention_*`` ini keys (pytest's defaults: keep three, policy
``all``). Stdlib only: the CLI picks the session's directory before the pool starts.

The number is picked once per session, by the CLI (:func:`session_basetemp`), and reaches
the workers in :data:`ENV`. A worker in a pool of two or more uses ``<it>/popen-gw<k>``, as an
xdist worker's ``--basetemp`` is; a pool of one uses it as is.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

#: The session's ``pytest-<n>``, set by the CLI for its workers.
ENV = "RUSTEST_BASETEMP"

#: pytest's ``tmp_path_retention_count`` default.
KEEP = 3

#: `_pytest/pathlib.py::LOCK_TIMEOUT`: a ``.lock`` older than this belongs to a dead session.
LOCK_TIMEOUT = 60 * 60 * 24 * 3


def _numbers(root: Path, prefix: str) -> list[int]:
    """`_pytest/pathlib.py::find_prefixed`: case-insensitive, as the file system may be."""
    numbers: list[int] = []
    lowered = prefix.lower()
    with contextlib.suppress(OSError):
        for child in root.iterdir():
            tail = child.name[len(prefix) :]
            if child.name.lower().startswith(lowered) and tail.isdigit():
                numbers.append(int(tail))
    return numbers


def make_numbered_dir(root: Path, prefix: str, mode: int = 0o700) -> Path:
    """`_pytest/pathlib.py::make_numbered_dir`: ``<prefix><max + 1>``, created exclusively."""
    for _ in range(10):
        path = root / f"{prefix}{max(_numbers(root, prefix), default=-1) + 1}"
        try:
            path.mkdir(mode=mode)
        except FileExistsError:
            continue  # another session took that number: take the next
        return path
    raise OSError(f"could not create a numbered directory {prefix}* under {root}")


def _locked(path: Path) -> bool:
    """A live session holds it: its ``.lock`` is younger than :data:`LOCK_TIMEOUT`."""
    try:
        return time.time() - (path / ".lock").stat().st_mtime < LOCK_TIMEOUT
    except OSError:
        return False


#: This process's live session directory, until :func:`release`.
_session: Path | None = None


def session_basetemp() -> Path:
    """A new ``pytest-<n>`` for this session, locked until :func:`release` (a previous session
    of this process, a repeated run, is released first)."""
    global _session
    release()
    temproot = Path(os.environ.get("PYTEST_DEBUG_TEMPROOT") or tempfile.gettempdir()).resolve()
    try:
        import getpass

        user = getpass.getuser()
    except (ImportError, OSError, KeyError):
        user = "unknown"
    root = temproot / f"pytest-of-{user}"
    try:
        root.mkdir(mode=0o700, exist_ok=True)
    except OSError:
        root = temproot / "pytest-of-unknown"
        root.mkdir(mode=0o700, exist_ok=True)
    # pytest's own check: on a shared temp directory another user can pre-create this name.
    if hasattr(os, "getuid"):
        st = os.lstat(root)
        if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid():
            raise OSError(f"The temporary directory {root} is not owned by the current user.")
        if st.st_mode & 0o077:
            os.chmod(root, st.st_mode & ~0o077)
    path = make_numbered_dir(root, "pytest-")
    _ = (path / ".lock").write_text(str(os.getpid()), encoding="utf-8")
    _session = path
    return path.resolve()


def release() -> None:
    """End this process's session: drop its ``.lock``. A directory nothing was written to goes
    too, so a run that used no ``tmp_path`` (or handed the session to pytest) costs pytest no
    number; one that was used is kept, and the oldest unlocked sessions beyond the newest
    :data:`KEEP` are removed, as pytest's retention does."""
    global _session
    path, _session = _session, None
    if path is None:
        return
    with contextlib.suppress(OSError):
        (path / ".lock").unlink()
    try:
        path.rmdir()
    except OSError:
        pass  # used: kept
    else:
        return
    newest = int(path.name[len("pytest-") :])
    for number in _numbers(path.parent, "pytest-"):
        old = path.parent / f"pytest-{number}"
        if number <= newest - KEEP and not _locked(old):
            shutil.rmtree(old, ignore_errors=True)


_ = atexit.register(release)


_worker_basetemp: Path | None = None


def worker_basetemp() -> Path:
    """This process's basetemp, made on first use: the session's (:data:`ENV`) as is in a pool
    of one, ``<it>/popen-<worker>`` in a larger one; a session of its own when no CLI picked one
    (the engine driven directly). Nothing is emptied: a nested engine run inside a pool worker
    shares the outer worker's name, and the session directory is new every time anyway."""
    global _worker_basetemp
    if _worker_basetemp is None:
        session = os.environ.get(ENV)
        worker = os.environ.get("PYTEST_XDIST_WORKER")
        if not session:
            base = session_basetemp()
        elif worker:
            base = Path(session) / f"popen-{worker}"
            base.mkdir(mode=0o700, exist_ok=True)
        else:
            base = Path(session)
            base.mkdir(mode=0o700, exist_ok=True)
        _worker_basetemp = base.resolve()
    return _worker_basetemp
