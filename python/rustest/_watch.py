"""``rustest --watch``: rerun on save (W1: polling, no dependency tracking yet).

Python-only and stdlib-only.  The loop is a few small pieces, each injectable so it is tested
with a fake clock, a fake filesystem and a fake keyboard:

* **What is watched.** Every ``.py`` file, plus ``pytest.ini`` / ``pyproject.toml`` /
  ``setup.cfg`` / ``tox.ini``, under the rootdir (and under any path argument outside it),
  pruned by name: hidden directories (``.git``, ``.venv``, ``.rustest_cache`` ...), any
  directory holding a ``pyvenv.cfg`` (a virtual environment, whatever it is called),
  ``__pycache__``, ``node_modules``, ``*.egg-info`` and the project's ``norecursedirs``.
  Polling ``os.scandir`` + ``stat`` costs about 50 ms for a thousand files; there is no
  native watcher to depend on (Windows has none in the standard library).  The next scan
  starts no sooner than ``POLL`` after the last one *finished*, and the interval grows to
  three times a slow scan, so an idle watch never pins a core.
* **What a rerun may run.** Only test files inside the selection: the path arguments, else
  ``testpaths``, else the rootdir.  A saved test file outside it is ignored, as a normal run
  would ignore it; the ``p`` filter narrows the selection, it never widens it.
* **When.** A change starts a ``DEBOUNCE`` window; the run starts when a rescan inside it finds
  nothing new.  Edits made *during* a run are seen by the next scan, so they queue exactly one
  follow-up run.
* **What a rerun runs** (until dependency tracking exists): changed **test files** run, plus
  the tests that failed last time, by node id; a changed **non-test** ``.py`` (a source file, a
  ``conftest.py``) or config file runs everything selected, and the status line says so.
* **Keys** (read raw from the terminal, no Enter needed; an arrow or function key is one key and
  is ignored): ``Enter`` rerun everything selected, ``a`` run all tests (clears the filters),
  ``f`` failed tests only, ``p`` filter by file name (a substring or glob of the path relative to
  the rootdir), ``t`` filter by test name (``-k``), ``h`` help, ``q`` or Ctrl-C quit.  Ctrl-D (or
  Ctrl-C) at a prompt cancels it.  Keys typed during a run are discarded.
* **The terminal** is put in cbreak mode only while waiting, and restored on Ctrl-C, on any
  exception, on SIGTERM, and around a Ctrl-Z suspend (SIGTSTP / SIGCONT).
* **Screen.** The terminal is cleared before each run and a one-line header says why it is
  running; a hint line follows (``press h to show help, q to quit``).
* **Exit codes.** ``0`` when the user quits; ``4`` for a refusal (not a terminal, ``--llm``,
  ``--collect-only``, or a session that needs pytest).  A failing run never ends the watch.

**Pre-booted workers are a seam, not a feature.**  ``watch(..., preboot=...)`` calls the
function after every run so the next pool can be started while the user is reading.  Python
cannot do that today: ``rust.run`` spawns its pool itself.  What the engine would have to
expose is ``rust.prestart(invocation_dir, args, python, workers, ...) -> handle`` -- spawn the
workers up to ``ready`` and keep them, importing nothing of the user's -- and a ``pool=handle``
argument on ``rust.run`` that consumes it exactly once (workers are one-shot, so no state leaks
between reruns).  ``cli.py`` passes ``None`` until then.

Imported only for ``--watch``, never on the ``import rustest`` path.
"""

from __future__ import annotations

import contextlib
import fnmatch
import os
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import FrameType, ModuleType
from typing import Any, Final, Protocol, TextIO

#: Seconds between rescans while idle, and the quiet period a change must be followed by.
POLL: Final = 0.3
DEBOUNCE: Final = 0.1
#: How often the keyboard is looked at between rescans.
_TICK: Final = 0.05
#: A scan that took ``t`` seconds is not repeated sooner than ``_BACKOFF * t`` later.
_BACKOFF: Final = 3

_PRUNED: Final = frozenset({"__pycache__", "node_modules"})
_CONFIG_FILES: Final = frozenset({"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"})

Snapshot = dict[str, tuple[int, int]]


# --------------------------------------------------------------------------------------
# what is watched
# --------------------------------------------------------------------------------------


def is_pruned(name: str, norecursedirs: Sequence[str] = ()) -> bool:
    """Is the directory called *name* left out of the watch?"""
    return (
        name.startswith(".")
        or name in _PRUNED
        or name.endswith(".egg-info")
        or any(fnmatch.fnmatch(name, pattern) for pattern in norecursedirs)
    )


def choose_roots(rootdir: str, paths: Sequence[str], cwd: str) -> list[str]:
    """The directories to scan: the rootdir, plus where a path argument lives outside it."""
    root = os.path.abspath(rootdir)
    roots = [root]
    for arg in paths:
        path = os.path.abspath(os.path.join(cwd, arg.partition("::")[0]))
        base = path if os.path.isdir(path) else os.path.dirname(path)
        if os.path.commonpath([root, base]) != root and base not in roots:
            roots.append(base)
    return roots


def selection(
    rootdir: str, paths: Sequence[str], testpaths: Sequence[str], cwd: str
) -> Callable[[str], bool]:
    """Is this (absolute) test file inside what a normal run would collect?  The path arguments
    (``::`` parts ignored), else ``testpaths`` (relative to the rootdir), else the rootdir."""
    if paths:
        bases = [os.path.abspath(os.path.join(cwd, p.partition("::")[0])) for p in paths]
    elif testpaths:
        bases = [os.path.abspath(os.path.join(rootdir, p)) for p in testpaths]
    else:
        bases = [os.path.abspath(rootdir)]
    bases = [os.path.normcase(b) for b in bases]

    def chosen(path: str) -> bool:
        full = os.path.normcase(os.path.abspath(path))
        return any(full == b or full.startswith(b.rstrip(os.sep) + os.sep) for b in bases)

    return chosen


def matches_filter(relative: str, pattern: str) -> bool:
    """The ``p`` filter: a substring of the path relative to the rootdir, or a glob (``*``,
    ``?``, ``[...]``) of that path or of the file name."""
    if not pattern:
        return True
    if any(c in pattern for c in "*?["):
        return (
            fnmatch.fnmatch(relative, pattern)
            or fnmatch.fnmatch(os.path.basename(relative), pattern)
            or fnmatch.fnmatch(relative, f"*{pattern}*")
        )
    return pattern in relative


def scan(roots: Sequence[str], norecursedirs: Sequence[str] = ()) -> Snapshot:
    """``{path: (mtime_ns, size)}`` for every watched file under *roots*."""
    found: Snapshot = {}
    pending = list(roots)
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not is_pruned(entry.name, norecursedirs) and not os.path.isfile(
                        os.path.join(entry.path, "pyvenv.cfg")
                    ):
                        pending.append(entry.path)
                elif entry.name.endswith(".py") or entry.name in _CONFIG_FILES:
                    info = entry.stat()
                    found[entry.path] = (info.st_mtime_ns, info.st_size)
            except OSError:  # vanished between the listing and the stat
                continue
    return found


def changes(before: Snapshot, after: Snapshot) -> set[str]:
    """Paths added, removed or modified between two snapshots."""
    return {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}


def test_file_matcher(patterns: Sequence[str]) -> Callable[[str], bool]:
    """``python_files``-style matching on a file's basename (``test_*.py``, ``*_test.py``)."""
    globs = list(patterns) or ["test_*.py", "*_test.py"]

    def matches(path: str) -> bool:
        name = os.path.basename(path)
        return any(fnmatch.fnmatch(name, glob) for glob in globs)

    return matches


# --------------------------------------------------------------------------------------
# the keyboard
# --------------------------------------------------------------------------------------


class Keys(Protocol):
    """What the loop needs from a keyboard."""

    def raw(self) -> contextlib.AbstractContextManager[None]: ...

    def read(self) -> str | None:
        """One pending key, or ``None``; never blocks."""
        ...

    def flush(self) -> None:
        """Discard whatever was typed while a run was in progress."""
        ...

    def prompt(self, text: str) -> str | None:
        """One line of input with the terminal in its normal mode; ``None`` if cancelled."""
        ...


def parse_key(data: bytes) -> str | None:
    """The one key in what a raw ``read`` returned.  An escape sequence (an arrow key is
    ``ESC [ A``) is a single key and not one the watch uses: ``None``.  Anything else is its first
    character; the rest of a burst (a paste, a key held down) is dropped, not queued."""
    if not data:
        return None
    if data[:1] == b"\x1b":
        return "\x1b" if len(data) == 1 else None
    text = data.decode("utf-8", "ignore")
    return text[:1] or None


class _Cbreak:
    """cbreak mode on a terminal fd, with the signals that would otherwise leave it set.

    SIGTERM raises ``SystemExit`` (so a ``finally`` runs); Ctrl-Z restores the mode, stops the
    process, and sets cbreak again when it is continued.  Everything it touches is injected, so
    the handlers are tested without a terminal.
    """

    def __init__(
        self,
        fd: int,
        termios: Any,
        tty: Any,
        signals: ModuleType,
        kill: Callable[[int, int], None],
    ) -> None:
        super().__init__()
        self._fd = fd
        self._termios = termios
        self._tty = tty
        self._signal = signals
        self._kill = kill
        self._saved: Any = None
        self._previous: dict[int, Any] = {}

    def enter(self) -> None:
        self._saved = self._termios.tcgetattr(self._fd)
        self._tty.setcbreak(self._fd)  # keys arrive unbuffered; Ctrl-C still interrupts
        for name, handler in (
            ("SIGTERM", self._on_term),
            ("SIGTSTP", self._on_tstp),
            ("SIGCONT", self._on_cont),
        ):
            number = getattr(self._signal, name, None)
            if number is not None:
                self._previous[number] = self._signal.signal(number, handler)

    def leave(self) -> None:
        for number, previous in self._previous.items():
            _ = self._signal.signal(number, previous)
        self._previous.clear()
        self._restore()

    def _restore(self) -> None:
        self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._saved)

    def _on_term(self, number: int, frame: FrameType | None) -> None:
        raise SystemExit(128 + number)

    def _on_tstp(self, number: int, frame: FrameType | None) -> None:
        self._restore()
        _ = self._signal.signal(number, self._signal.SIG_DFL)
        self._kill(os.getpid(), number)  # stops here until SIGCONT

    def _on_cont(self, number: int, frame: FrameType | None) -> None:
        self._tty.setcbreak(self._fd)
        tstp = getattr(self._signal, "SIGTSTP", None)
        if tstp is not None:
            _ = self._signal.signal(tstp, self._on_tstp)


class TerminalKeys:
    """Keys from a real console: ``msvcrt`` on Windows, the raw fd elsewhere.

    On POSIX the fd is read with ``os.read``, never through ``sys.stdin``: a buffered text
    stream swallows everything waiting on the fd into a userspace buffer that ``select`` and
    ``tcflush`` cannot see, so keys would lag and stale ones would fire later.
    """

    def __init__(self) -> None:
        super().__init__()
        self._select: Callable[..., Any] | None = None
        self._read: Callable[[int, int], bytes] = os.read

    @contextmanager
    def raw(self) -> Iterator[None]:
        if sys.platform == "win32":
            yield  # `msvcrt` reads keys without any mode switch
            return
        import signal
        import termios
        import tty

        mode = _Cbreak(sys.stdin.fileno(), termios, tty, signal, os.kill)
        mode.enter()
        try:
            yield
        finally:
            mode.leave()

    def read(self) -> str | None:
        if sys.platform == "win32":
            import msvcrt

            if not msvcrt.kbhit():
                return None
            key = msvcrt.getwch()
            if key in ("\x00", "\xe0"):  # an arrow or function key: its code follows
                _ = msvcrt.getwch()
                return None
            return key
        if self._select is None:
            import select

            self._select = select.select
        fd = sys.stdin.fileno()
        ready, _, _ = self._select([fd], [], [], 0)
        return parse_key(self._read(fd, 64)) if ready else None

    def flush(self) -> None:
        if sys.platform == "win32":
            import msvcrt

            while msvcrt.kbhit():
                _ = msvcrt.getwch()
            return
        import termios

        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)

    def prompt(self, text: str) -> str | None:
        try:
            return input(text)
        except (EOFError, KeyboardInterrupt):  # Ctrl-D, or Ctrl-C: cancel the prompt
            _ = sys.stdout.write("\n")
            return None


# --------------------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSpec:
    """One run: what to pass as paths and ``-k``, and why, for the header.  ``note`` is an
    extra status line for a run that is bigger than the user may expect."""

    paths: list[str]
    keyword: str | None
    why: str
    note: str = ""


@dataclass
class _State:
    failed: list[str] = field(default_factory=list[str])
    file_filter: str = ""
    keyword: str | None = None


_HINT: Final = "press h to show help, q to quit"
_KEYS: Final = (
    "Enter  rerun the selected tests",
    "a      run all tests (clears the filters)",
    "f      rerun only the failed tests",
    "p      filter by file name (substring or glob of the path; empty clears)",
    "t      filter by test name (-k expression; empty clears)",
    "h      this help",
    "q      quit (Ctrl-C too)",
)
_SOURCE_NOTE: Final = (
    "a source or config file changed: everything selected reran"
    + " (per-test dependency tracking is not available yet)"
)


def _short(paths: Sequence[str]) -> str:
    names = sorted({os.path.basename(p) for p in paths})
    return ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")


def watch(
    *,
    run: Callable[[RunSpec], tuple[int, list[str]]],
    scan_files: Callable[[], Snapshot],
    is_test_file: Callable[[str], bool],
    paths: Sequence[str],
    rootdir: str,
    cwd: str,
    keyword: str | None,
    keys: Keys,
    out: TextIO,
    selected: Callable[[str], bool] = lambda path: True,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    preboot: Callable[[], None] | None = None,
    clear: bool = True,
    dim: bool = True,
) -> int:
    """Run, then wait for a change or a key, forever; ``0`` when the user quits.

    ``run`` executes one :class:`RunSpec` and returns ``(exit code, failed node ids)``.
    ``scan_files`` returns the current snapshot; failed node ids are relative to *rootdir* and
    become arguments relative to *cwd*.  ``selected`` says whether a test file is inside what a
    normal run would collect.  ``clear`` / ``dim`` are whether the terminal takes escape codes
    and colour.  ``preboot`` is called after every run (see the module docstring: nothing
    implements it yet).
    """
    state = _State(keyword=keyword)
    snapshot = scan_files()

    def say(text: str, *, faint: bool = False) -> None:
        _ = out.write((f"\x1b[2m{text}\x1b[0m" if faint and dim else text) + "\n")
        out.flush()

    def hint() -> None:
        notes = [f"file: {state.file_filter}"] if state.file_filter else []
        notes += [f"name: {state.keyword}"] if state.keyword else []
        say(
            f"watching {len(snapshot)} files"
            + (f" [{', '.join(notes)}]" if notes else "")
            + f" | {_HINT}",
            faint=True,
        )

    def execute(spec: RunSpec) -> None:
        nonlocal snapshot
        _ = out.write("\x1b[2J\x1b[H" if clear else "\n")
        say(f"rustest --watch: {spec.why}", faint=True)
        try:
            _, state.failed = run(spec)
        except KeyboardInterrupt:  # the run was interrupted; the watch is not
            say("interrupted", faint=True)
        if spec.note:
            say(spec.note, faint=True)
        hint()
        if preboot is not None:
            preboot()
        keys.flush()

    def rel(path: str) -> str:
        try:
            return os.path.relpath(path, rootdir).replace(os.sep, "/")
        except ValueError:  # another drive
            return path.replace(os.sep, "/")

    def candidates() -> list[str]:
        return sorted(
            p
            for p in snapshot
            if is_test_file(p) and selected(p) and matches_filter(rel(p), state.file_filter)
        )

    def everything(why: str, note: str = "") -> RunSpec | None:
        if not state.file_filter:
            return RunSpec(list(paths), state.keyword, why, note)
        files = candidates()
        if not files:
            say(f"no test file matches {state.file_filter!r}", faint=True)
            return None
        return RunSpec(files, state.keyword, why, note)

    def argument(node_id: str) -> str:
        """A rootdir-relative node id as a command-line argument (relative to the cwd)."""
        file, sep, rest = node_id.partition("::")
        full = os.path.join(rootdir, file)
        try:
            return os.path.relpath(full, cwd) + sep + rest
        except ValueError:  # another drive: the absolute path is the argument
            return full + sep + rest

    def on_change(changed: set[str]) -> RunSpec | None:
        tests = sorted(
            p
            for p in changed
            if p in snapshot
            and is_test_file(p)
            and selected(p)
            and matches_filter(rel(p), state.file_filter)
        )
        others = sorted(p for p in changed if not is_test_file(p))
        if others:  # a source file, a conftest or config: no dependency records yet
            return everything(f"{_short(others)} changed, running everything", _SOURCE_NOTE)
        gone = {rel(p) for p in changed if p not in snapshot}
        state.failed = [f for f in state.failed if f.partition("::")[0] not in gone]
        if not tests:
            return None
        rerun = {rel(p) for p in tests}
        carried = [argument(f) for f in state.failed if f.partition("::")[0] not in rerun]
        return RunSpec([*tests, *carried], state.keyword, f"{_short(tests)} changed")

    def on_key(key: str) -> RunSpec | None | bool:
        if key in ("q", "\x03"):
            return False
        if key in ("\r", "\n"):
            return everything("rerun")
        if key == "a":
            state.file_filter, state.keyword = "", None
            return everything("running all tests (filters cleared)")
        if key == "f":
            if not state.failed:
                say("no failed tests to rerun", faint=True)
                return None
            return RunSpec(
                [argument(f) for f in state.failed], state.keyword, "rerunning failed tests"
            )
        if key == "h":
            say("")
            for line in _KEYS:
                say("  " + line)
            hint()
            return None
        if key == "p":
            answer = keys.prompt("filter by file name (empty clears, Ctrl-D cancels): ")
            if answer is None:
                return None
            state.file_filter = answer.strip()
            return everything("file filter changed")
        if key == "t":
            answer = keys.prompt("filter by test name (-k; empty clears, Ctrl-D cancels): ")
            if answer is None:
                return None
            state.keyword = answer.strip() or None
            return everything("name filter changed")
        return None

    def wait() -> tuple[str, object]:
        """Block until there is something to do: ``("key", ch)`` or ``("change", paths)``."""
        nonlocal snapshot
        next_scan = 0.0
        with keys.raw():
            while True:
                key = keys.read()
                if key is not None:
                    return "key", key
                now = clock()
                if now >= next_scan:
                    current = scan_files()
                    took = clock() - now
                    # never back to back: wait POLL after this scan *ended*, longer if it was slow
                    next_scan = clock() + max(POLL, _BACKOFF * took)
                    found = changes(snapshot, current)
                    if found:
                        snapshot = current
                        while True:  # debounce: wait until a rescan finds nothing new
                            sleep(max(DEBOUNCE, took))
                            started = clock()
                            current = scan_files()
                            took = clock() - started
                            more = changes(snapshot, current)
                            if not more:
                                return "change", found
                            found |= more
                            snapshot = current
                sleep(_TICK)

    spec = everything("starting")
    if spec is not None:
        execute(spec)
    try:
        while True:
            kind, payload = wait()
            if kind == "key":
                result = on_key(str(payload))
                if result is False:
                    return 0
                spec = result if isinstance(result, RunSpec) else None
            else:
                spec = on_change(payload if isinstance(payload, set) else set())  # pyright: ignore
            if spec is not None:
                execute(spec)
    except KeyboardInterrupt:
        say("")
        return 0


# --------------------------------------------------------------------------------------
# the command-line entry
# --------------------------------------------------------------------------------------


def refusal(*, llm: bool, collect_only: bool, interactive: bool) -> str | None:
    """Why ``--watch`` cannot start with these arguments, or ``None``."""
    if not interactive:
        return (
            "--watch needs a terminal: it redraws the screen and reads single keys, and"
            + " stdin and stdout must both be terminals."
        )
    if llm:
        return (
            "--watch and --llm ask for different things: --llm writes one JSONL document per run."
        )
    if collect_only:
        return "--watch and --collect-only ask for different things: collect-only runs no tests."
    return None


def needs_pytest(reasons: Sequence[str]) -> str:
    """The message for a session that is handed to pytest, which ``--watch`` does not loop."""
    return (
        "ERROR: --watch does not run under pytest fallback yet: this session needs pytest: "
        + "; ".join(reasons)
        + ". Pass --no-pytest-fallback to watch it with rustest anyway."
    )


def failed_ids(report: Mapping[str, object]) -> list[str]:
    """The node ids of a ``--report-json`` document whose test failed or errored."""
    tests = report.get("tests", [])
    if not isinstance(tests, list):
        return []
    return [
        str(test["id"])  # pyright: ignore[reportUnknownArgumentType]
        for test in tests  # pyright: ignore[reportUnknownVariableType]
        if isinstance(test, dict) and test.get("status") in ("failed", "error")  # pyright: ignore
    ]
