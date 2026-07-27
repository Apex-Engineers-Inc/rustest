"""The builtin fixtures the v2 worker provides, ported from pytest's own plugins.

Separate from :mod:`rustest.builtin_fixtures` — which is **v1's** set and stays frozen while
v1 ships — because three of the fixtures here had to change shape rather than grow:

* ``capsys`` moved from "swap ``sys.stdout`` for a ``StringIO``" to pytest's
  ``SysCapture``/``MultiCapture``/``CaptureFixture`` stack, so that ``capfd`` could be the
  *same* stack over a different capture class rather than v1's "capfd is an alias for capsys"
  (`builtin_fixtures.py` l. 443-458, whose own docstring says so);
* ``caplog`` stopped forcing the root logger to ``DEBUG``.  pytest changes no level at all
  unless ``log_level`` is configured (`_pytest/logging.py::LoggingPlugin._runtest_for`, which
  passes ``level=self.log_level`` and that is ``None`` by default), so v1's version captured
  ``INFO`` records that pytest drops — a *silently different record list*;
* ``tmpdir`` stopped being its own temporary tree.  pytest's ``tmpdir`` **is** ``tmp_path``
  (`_pytest/legacypath.py`: ``return legacy_path(tmp_path)``), so a test requesting both got
  two directories here and one under pytest.

Provenance is per class and cited there.  Everything in this module is a port of pytest
8.4.2 except :class:`MockerFixture`, which is pytest-mock 3.15.1's.

**Import cost.** ``_v2_worker`` imports this module at *its* module level, so everything at
this one's top level is on every worker's start-up path.  ``tempfile``, ``shutil``, ``py``
and ``unittest.mock`` are therefore imported *inside* the objects that need them:
``unittest.mock`` is ~10 ms and only a suite using ``mocker`` should pay it, ``shutil``
drags ``bz2``/``lzma``/``zlib`` behind it and only a suite using a temporary directory
should.  It is also why this module does not import :mod:`rustest.builtin_fixtures` — the
one class it borrows from there (``MonkeyPatch``) is imported inside the ``monkeypatch``
fixture, so the v2 worker's import graph no longer contains v1's fixture module at all.

``logging`` **is** eager, and that is a measured exception rather than an oversight: 4.9 /
4.5 / 5.6 / 5.0 / 12.3 ms marginal over five runs with ``rustest._v2_worker`` already
imported, against a ~300 ms worker boot that already pays ``unittest`` unconditionally — and
against a real suite, which imports ``logging`` itself.  Deferring it would buy those
milliseconds back only for suites that never log, at the cost of building
``LogCaptureHandler`` inside a function, where its type is unknowable to the type checker
and every use of ``caplog.records`` becomes an ``Unknown``.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import re
import sys
from collections.abc import Generator, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NamedTuple, cast

from .decorators import fixture

if TYPE_CHECKING:
    from .builtin_fixtures import MonkeyPatch

#: ``py.path.local`` at type-check time, ``Any`` at run time — the ``py`` distribution routes
#: its submodules through ``apipkg`` shims, so there is no importable ``py/path.py`` source
#: for a type checker to read (see ``builtin_fixtures.py``'s note on the same warning).
PyPathLocal = Any

__all__ = [
    "Cache",
    "CaptureFixture",
    "CaptureResult",
    "Config",
    "FdCapture",
    "LogCapture",
    "LogCaptureFixture",
    "MockerFixture",
    "MultiCapture",
    "SysCapture",
    "TempPathFactory",
    "TempdirFactory",
    "V2_BUILTIN_FIXTURES",
    "cache",
    "caplog",
    "capfd",
    "capsys",
    "configure",
    "current_config",
    "mocker",
    "monkeypatch",
    "pytestconfig",
    "set_log_capture",
    "tmp_path",
    "tmp_path_factory",
    "tmpdir",
    "tmpdir_factory",
]


# ---------------------------------------------------------------------------
# worker-supplied context
# ---------------------------------------------------------------------------


class _WorkerContext(NamedTuple):
    """What ``init`` told the worker, in the shape the fixtures here need.

    Passed in rather than imported from :mod:`rustest._v2_worker`, so this module has no
    import edge back into the worker and can be unit-tested on its own.
    """

    rootdir: Path
    invocation_dir: Path
    ini: Mapping[str, object]


_context: _WorkerContext | None = None

#: The one active capture fixture, if any — pytest's
#: ``CaptureManager._capture_fixture`` (`_pytest/capture.py` l. 806-816).  It exists to
#: enforce a single rule, and that rule is the reason the slot is global rather than a
#: field: **capsys and capfd cannot both be requested by one test.**
_capture_fixture: CaptureFixture | None = None

#: The per-test logging capture, installed by the worker only when ``caplog`` is in the
#: test's fixture closure.  See :func:`set_log_capture`.
_log_capture: LogCapture | None = None


def configure(rootdir: Path, invocation_dir: Path, ini: Mapping[str, object]) -> None:
    """Record what ``init`` established.  Called once per worker."""
    global _context
    _context = _WorkerContext(rootdir=rootdir, invocation_dir=invocation_dir, ini=dict(ini))


def _worker_context() -> _WorkerContext:
    if _context is None:  # pragma: no cover - the worker always configures before collecting
        raise RuntimeError(
            "rustest builtin fixtures were used before the worker was initialised "
            + "(rustest._v2_builtins.configure has not been called)"
        )
    return _context


def set_log_capture(capture: LogCapture | None) -> None:
    """Install (or clear) the logging capture the ``caplog`` fixture reads.

    The worker calls this around a test whose closure contains ``caplog``, because pytest
    installs its handler in ``pytest_runtest_setup`` — *before* any fixture runs — and a
    fixture that logs during setup is therefore in ``caplog.records`` under pytest.  Doing it
    from the fixture body instead would silently drop those records.
    """
    global _log_capture
    _log_capture = capture


# ---------------------------------------------------------------------------
# capture — port of `_pytest/capture.py`
# ---------------------------------------------------------------------------


class CaptureResult(NamedTuple):
    """`_pytest/capture.py::CaptureResult` — what ``readouterr()`` returns."""

    out: str
    err: str


#: `_pytest/capture.py::patchsysdict`.  ``0`` is deliberately absent: the worker's stdin is
#: the protocol's request channel, and nothing here may touch it.
_PATCH_SYS_NAME: Final[Mapping[int, str]] = {1: "stdout", 2: "stderr"}


class CaptureIO(io.TextIOWrapper):
    """`_pytest/capture.py::CaptureIO` (l. 190-200) — a text stream you can read back."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(io.BytesIO(), encoding="UTF-8", newline="", write_through=True)

    def getvalue(self) -> str:
        assert isinstance(self.buffer, io.BytesIO)
        return self.buffer.getvalue().decode("UTF-8")


class EncodedFile(io.TextIOWrapper):
    """`_pytest/capture.py::EncodedFile` (l. 170-187).

    The two property overrides are not cosmetic: ``TextIOWrapper.name`` delegates to the
    buffer, and a ``TemporaryFile``'s name is an ``int`` on Windows, so a test (or a library)
    doing ``sys.stdout.name`` would get an integer where it expects a string.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        # Ensure that file.name is a string. Workaround for a Python bug.
        return repr(self.buffer)

    @property
    def mode(self) -> str:
        # TextIOWrapper doesn't expose a mode, but at least some of our tests check it.
        mode = cast(str, getattr(self.buffer, "mode", "rb+"))
        return mode.replace("b", "")


class SysCapture:
    """Port of `_pytest/capture.py::SysCaptureBase` + `SysCapture` (l. 362-461).

    Captures what is written to ``sys.stdout``/``sys.stderr`` **by name** — a ``print()`` is
    caught, a write to the file descriptor behind them is not.  That is what ``capsys`` means
    and it is the whole difference from :class:`FdCapture`.

    ``_old`` is captured in ``__init__``, not in ``start()``, which is pytest's own choice and
    is load-bearing here: a ``capsys`` fixture is constructed while the worker's own per-test
    capture is already installed, so ``_old`` is that capture's stream and ``done()`` hands
    the test's remaining output back to it rather than to the terminal.
    """

    EMPTY_BUFFER: Final = ""

    def __init__(self, targetfd: int, tmpfile: io.TextIOBase | None = None) -> None:
        super().__init__()
        self.name: Final = _PATCH_SYS_NAME[targetfd]
        self._old: Any = getattr(sys, self.name)
        self.tmpfile: io.TextIOBase = CaptureIO() if tmpfile is None else tmpfile
        self._state = "initialized"

    def start(self) -> None:
        setattr(sys, self.name, self.tmpfile)
        self._state = "started"

    def snap(self) -> str:
        # Only a `CaptureIO` is ever read back: when this object is the `syscapture` half of
        # an `FdCapture` its tmpfile is that capture's `EncodedFile`, and `FdCapture.snap`
        # reads it directly.  pytest's `SysCapture.snap` has the same latent precondition,
        # spelled as an unchecked `getvalue()`.
        tmpfile = self.tmpfile
        assert isinstance(tmpfile, CaptureIO), "only an owned CaptureIO is snapped"
        res = tmpfile.getvalue()
        _ = tmpfile.seek(0)
        _ = tmpfile.truncate()
        return res

    def done(self) -> None:
        if self._state == "done":
            return
        setattr(sys, self.name, self._old)
        self.tmpfile.close()
        self._state = "done"

    def suspend(self) -> None:
        setattr(sys, self.name, self._old)
        self._state = "suspended"

    def resume(self) -> None:
        setattr(sys, self.name, self.tmpfile)
        self._state = "started"

    def writeorg(self, data: str) -> None:
        _ = self._old.write(data)
        self._old.flush()

    @property
    def broken(self) -> bool:
        """The test closed the stream out from under us — see ``_v2_worker._Capture``."""
        return bool(self.tmpfile.closed)


class FdCapture:
    """Port of `_pytest/capture.py::FDCaptureBase` + `FDCapture` (l. 464-600).

    Redirects an **operating-system file descriptor** into a temporary file, so a write that
    never goes through ``sys.stdout`` — ``os.write(1, ...)``, a C extension, a subprocess that
    inherits the fd — is captured too.  ``sys.stdout`` is redirected as well (pytest's
    ``syscapture``, constructed with the same tmpfile), so a ``print()`` and an
    ``os.write(1, ...)`` interleave in the order they happened: both reach the same open file
    description, which shares one file offset.

    ``targetfd_save`` is a ``dup`` taken **before** the redirect, so ``writeorg`` reaches
    whatever the fd pointed at when this object was constructed.  In the worker that is the
    per-test capture's own temporary file, which is what makes ``capfd``'s unread remainder
    land in the test's reported stdout rather than on the worker's stderr — pytest's
    ``pop_outerr_to_orig`` semantics, for free.
    """

    EMPTY_BUFFER: Final = ""

    def __init__(self, targetfd: int) -> None:
        super().__init__()
        import tempfile

        self.targetfd: Final = targetfd
        try:
            os.fstat(targetfd)
        except OSError:
            # The fd is invalid — a pythonw.exe-style process with no console.  pytest's
            # comment: "Tests themselves shouldn't care if the FD is valid, FD capturing
            # should work regardless of external circumstances."
            self.targetfd_invalid: int | None = os.open(os.devnull, os.O_RDWR)
            _ = os.dup2(self.targetfd_invalid, targetfd)
        else:
            self.targetfd_invalid = None
        self.targetfd_save: Final = os.dup(targetfd)
        self.tmpfile: EncodedFile = EncodedFile(
            tempfile.TemporaryFile(buffering=0),
            encoding="utf-8",
            errors="replace",
            newline="",
            write_through=True,
        )
        self.syscapture: SysCapture | None = (
            SysCapture(targetfd, self.tmpfile) if targetfd in _PATCH_SYS_NAME else None
        )
        self._state = "initialized"

    def start(self) -> None:
        _ = os.dup2(self.tmpfile.fileno(), self.targetfd)
        if self.syscapture is not None:
            self.syscapture.start()
        self._state = "started"

    def snap(self) -> str:
        _ = self.tmpfile.seek(0)
        res = self.tmpfile.read()
        _ = self.tmpfile.seek(0)
        _ = self.tmpfile.truncate()
        return res

    def done(self) -> None:
        if self._state == "done":
            return
        _ = os.dup2(self.targetfd_save, self.targetfd)
        os.close(self.targetfd_save)
        if self.targetfd_invalid is not None:
            if self.targetfd_invalid != self.targetfd:
                os.close(self.targetfd)
            os.close(self.targetfd_invalid)
        if self.syscapture is not None:
            self.syscapture.done()
        self.tmpfile.close()
        self._state = "done"

    def suspend(self) -> None:
        if self._state == "suspended":
            return
        if self.syscapture is not None:
            self.syscapture.suspend()
        _ = os.dup2(self.targetfd_save, self.targetfd)
        self._state = "suspended"

    def resume(self) -> None:
        if self._state == "started":
            return
        if self.syscapture is not None:
            self.syscapture.resume()
        _ = os.dup2(self.tmpfile.fileno(), self.targetfd)
        self._state = "started"

    def writeorg(self, data: str) -> None:
        _ = os.write(self.targetfd_save, data.encode("utf-8", "replace"))

    @property
    def started(self) -> bool:
        """Currently redirecting, as opposed to suspended, done, or not yet started.

        pytest asks the same question through ``MultiCapture.is_started``; it is exposed per
        capture here because the worker suspends and resumes the two halves directly.  It
        exists so a *nested* suspend cannot resume a capture that was already off — which is
        what ``_v2_worker._Capture.disabled`` would otherwise do outside a test.
        """
        return self._state == "started"

    @property
    def broken(self) -> bool:
        """The test closed ``sys.stdout``, which *is* this temporary file.

        The fd itself survives — it is a dup, so ``os.write(1, ...)`` still works and
        ``done()`` can still restore — but every later ``snap()`` would raise
        ``ValueError: I/O operation on closed file``.  The worker turns this into the same
        FAILED/ERROR pair pytest reports; see ``_v2_worker._Capture.broken``.
        """
        return bool(self.tmpfile.closed)


class MultiCapture:
    """Port of `_pytest/capture.py::MultiCapture` (l. 620-745), out and err only.

    ``in_`` is not modelled: capturing stdin would mean touching fd 0, which in a worker is
    the protocol's request channel.
    """

    def __init__(self, out: SysCapture | FdCapture, err: SysCapture | FdCapture) -> None:
        super().__init__()
        self.out: Final = out
        self.err: Final = err
        self._state = "initialized"

    def start_capturing(self) -> None:
        self.out.start()
        self.err.start()
        self._state = "started"

    def pop_outerr_to_orig(self) -> CaptureResult:
        """Snap and immediately write back to the original streams (l. 660-666).

        This is what makes output a fixture captured but never read reappear where it would
        have gone anyway, instead of being silently swallowed at teardown.
        """
        out, err = self.readouterr()
        if out:
            self.out.writeorg(out)
        if err:
            self.err.writeorg(err)
        return CaptureResult(out, err)

    def suspend_capturing(self) -> None:
        self.out.suspend()
        self.err.suspend()
        self._state = "suspended"

    def resume_capturing(self) -> None:
        self.out.resume()
        self.err.resume()
        self._state = "started"

    def stop_capturing(self) -> None:
        if self._state == "stopped":
            return
        self.out.done()
        self.err.done()
        self._state = "stopped"

    def is_started(self) -> bool:
        return self._state == "started"

    def readouterr(self) -> CaptureResult:
        return CaptureResult(self.out.snap(), self.err.snap())

    @property
    def broken(self) -> bool:
        return self.out.broken or self.err.broken


class CaptureFixture:
    """Port of `_pytest/capture.py::CaptureFixture` (l. 916-995).

    One class for ``capsys`` and ``capfd``; the difference is entirely the capture class it
    is handed, which is exactly pytest's structure and is the reason ``capfd`` here is not
    "an alias for capsys" the way v1's was.
    """

    def __init__(self, captureclass: type[SysCapture] | type[FdCapture]) -> None:
        super().__init__()
        self.captureclass: Final = captureclass
        #: The fixture name this object was created for, so the mutual-exclusion refusal can
        #: name the *other* fixture.  pytest reads it off the request (l. 806-812).
        self.request_name: str = ""
        self._capture: MultiCapture | None = None
        self._captured_out: str = ""
        self._captured_err: str = ""

    def _start(self) -> None:
        if self._capture is None:
            self._capture = MultiCapture(out=self.captureclass(1), err=self.captureclass(2))
            self._capture.start_capturing()

    def close(self) -> None:
        if self._capture is not None:
            out, err = self._capture.pop_outerr_to_orig()
            self._captured_out += out
            self._captured_err += err
            self._capture.stop_capturing()
            self._capture = None

    def readouterr(self) -> CaptureResult:
        """Read and reset the captured output so far."""
        captured_out, captured_err = self._captured_out, self._captured_err
        if self._capture is not None:
            out, err = self._capture.readouterr()
            captured_out += out
            captured_err += err
        self._captured_out = ""
        self._captured_err = ""
        return CaptureResult(captured_out, captured_err)

    def _suspend(self) -> None:
        if self._capture is not None:
            self._capture.suspend_capturing()

    def _resume(self) -> None:
        if self._capture is not None:
            self._capture.resume_capturing()

    def _is_started(self) -> bool:
        if self._capture is not None:
            return self._capture.is_started()
        return False

    @contextlib.contextmanager
    def disabled(self) -> Generator[None, None, None]:
        """Temporarily stop capturing, so a ``print`` inside reaches the terminal.

        **Documented divergence.** pytest suspends its *global* capture too
        (`CaptureManager.global_and_fixture_disabled`, l. 838-854), so the output lands on the
        real terminal.  The worker's global capture is suspended as well — but the worker's
        "terminal" is its stderr, which the orchestrator forwards under
        ``RunReport::worker_stderr`` rather than interleaving live.  Same visibility, a
        different place in the output; the same divergence ``-s`` already carries
        (``_v2_worker._capture_window``).
        """
        suspend_global = _global_capture_control()
        do_fixture = self._is_started()
        if do_fixture:
            self._suspend()
        try:
            with suspend_global():
                yield
        finally:
            if do_fixture:
                self._resume()


#: Set by the worker to its own ``_Capture.disabled`` context manager, so
#: :meth:`CaptureFixture.disabled` can reach a global capture this module does not own.
_global_capture_disabled: Any = None


def _global_capture_control() -> Any:
    if _global_capture_disabled is None:
        return contextlib.nullcontext
    return _global_capture_disabled


def set_global_capture_control(factory: Any) -> None:
    """Register the worker's "suspend the per-test capture" context manager factory."""
    global _global_capture_disabled
    _global_capture_disabled = factory


@contextlib.contextmanager
def _capture_fixture_slot(name: str, capture: CaptureFixture) -> Generator[None, None, None]:
    """pytest's ``CaptureManager.set_fixture``/``unset_fixture`` pair (l. 806-816).

    The rule it enforces is the whole reason the slot exists: two capture fixtures in one
    test would each dup the *other's* redirect into place, and whichever ran second would
    silently swallow the first one's output.  pytest refuses instead, with this message —
    probed on pytest 8.4.2: a test requesting both reports ``ERROR`` at setup with
    ``cannot use capfd and capsys at the same time``.
    """
    global _capture_fixture
    if _capture_fixture is not None:
        raise CaptureFixtureConflict(
            f"cannot use {name} and {_capture_fixture.request_name} at the same time"
        )
    capture.request_name = name
    _capture_fixture = capture
    try:
        yield
    finally:
        _capture_fixture = None


class CaptureFixtureConflict(Exception):
    """Both ``capsys`` and ``capfd`` were requested by one test.

    pytest raises this through ``request.raiseerror``, which makes it a fixture lookup error
    and therefore a setup ERROR.  Same outcome here — the exception escapes the fixture body
    during setup — with pytest's message verbatim.
    """


@fixture
def capsys() -> Generator[CaptureFixture, None, None]:
    r"""Enable text capturing of writes to ``sys.stdout`` and ``sys.stderr``.

    Port of `_pytest/capture.py::capsys` (l. 1000-1026).

    Example:
        def test_output(capsys):
            print("hello")
            assert capsys.readouterr().out == "hello\n"
    """
    capture = CaptureFixture(SysCapture)
    with _capture_fixture_slot("capsys", capture):
        capture._start()  # pyright: ignore[reportPrivateUsage]
        try:
            yield capture
        finally:
            capture.close()


@fixture
def capfd() -> Generator[CaptureFixture, None, None]:
    r"""Enable text capturing of writes to file descriptors ``1`` and ``2``.

    Port of `_pytest/capture.py::capfd` (l. 1091-1117).  Unlike ``capsys`` this catches
    output that never passes through ``sys.stdout`` — a subprocess, a C extension, or a bare
    ``os.write(1, ...)``.  (pytest's own docstring example shells out; a direct fd write says
    the same thing without a shell.)

    Example:
        def test_raw_fd_write(capfd):
            os.write(1, b"hello\n")
            assert capfd.readouterr().out == "hello\n"
    """
    capture = CaptureFixture(FdCapture)
    with _capture_fixture_slot("capfd", capture):
        capture._start()  # pyright: ignore[reportPrivateUsage]
        try:
            yield capture
        finally:
            capture.close()


# ---------------------------------------------------------------------------
# caplog — port of `_pytest/logging.py`
# ---------------------------------------------------------------------------

#: `_pytest/logging.py` l. 50-51.  The default ``log_format``/``log_date_format`` inis, which
#: is what ``caplog.text`` is formatted with.
DEFAULT_LOG_FORMAT: Final = "%(levelname)-8s %(name)s:%(filename)s:%(lineno)d %(message)s"
DEFAULT_LOG_DATE_FORMAT: Final = "%H:%M:%S"

_ANSI_ESCAPE_SEQ = re.compile(r"\x1b\[[\d;]+m")


def _remove_ansi_escape_sequences(text: str) -> str:
    """`_pytest/logging.py::_remove_ansi_escape_sequences` (l. 57-58)."""
    return _ANSI_ESCAPE_SEQ.sub("", text)


#: `logging.StreamHandler` is generic in typeshed and not subscriptable at run time before
#: 3.11.  pytest carries the same two-faced alias for the same reason
#: (`_pytest/logging.py` l. 40-44, ``logging_StreamHandler``).
if TYPE_CHECKING:
    _StreamHandler = logging.StreamHandler[io.StringIO]
else:
    _StreamHandler = logging.StreamHandler


class LogCaptureHandler(_StreamHandler):
    """Port of `_pytest/logging.py::LogCaptureHandler` (l. 373-400).

    Keeps the records *and* the formatted text: ``caplog.records`` reads the list,
    ``caplog.text`` reads the stream.  They are not redundant — ``text`` is what a
    ``log_format`` change would move and ``records`` is not.
    """

    def __init__(self) -> None:
        super().__init__(io.StringIO())
        self.records: list[logging.LogRecord] = []
        self.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT, DEFAULT_LOG_DATE_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        super().emit(record)

    def reset(self) -> None:
        self.records = []
        self.stream = io.StringIO()

    def clear(self) -> None:
        self.records.clear()
        self.stream = io.StringIO()

    def handleError(self, record: logging.LogRecord) -> None:
        if logging.raiseExceptions:
            # "pytest wants to make such mistakes visible during testing" — a formatting
            # error in a log call fails the test instead of printing to stderr.
            raise  # noqa: PLE0704


class LogCapture:
    """One test's logging capture — the worker's stand-in for ``LoggingPlugin``.

    pytest wraps **each phase** in ``catching_logs(self.caplog_handler, level=self.log_level)``
    (`_pytest/logging.py::LoggingPlugin._runtest_for`, l. 813-835) and resets the handler at
    the start of each, stashing that phase's record list.  Both halves are reproduced:
    :meth:`phase` is the context manager, :attr:`records_by_phase` is the stash.

    Two things pytest does that this deliberately does not:

    * **it installs unconditionally**, for every item, because it also feeds the
      ``add_report_section(when, "log", ...)`` output that the terminal prints under a
      failure.  The v2 wire has no report sections, so the worker installs this only when
      ``caplog`` is in the test's fixture closure and a suite that never asks for it pays
      nothing but the (measured, ~5 ms) module import;
    * **it sets a level** when the ``log_level`` ini is configured.  That ini is not on the
      v2 wire, so ``level`` is always ``None`` here, which is also pytest's default.  It is
      the reason the root logger keeps its ``WARNING`` level and an unqualified
      ``logging.info(...)`` is **not** captured until a test calls
      :meth:`LogCaptureFixture.set_level` — v1's ``caplog`` forced ``DEBUG`` and captured it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.handler: Final = LogCaptureHandler()
        self.records_by_phase: dict[str, list[logging.LogRecord]] = {}

    @contextlib.contextmanager
    def phase(self, when: str) -> Generator[None, None, None]:
        """Capture one phase's records — ``catching_logs`` plus the per-phase reset.

        ``catching_logs.__enter__`` with ``level=None`` is exactly "add the handler to the
        root logger"; ``__exit__`` is "remove it".  The reset in between is
        ``_runtest_for``'s, and the record list is stashed **by identity** — ``reset()``
        rebinds ``records`` to a new list, so each phase's stash keeps its own.
        """
        root = logging.getLogger()
        self.handler.reset()
        self.records_by_phase[when] = self.handler.records
        root.addHandler(self.handler)
        try:
            yield
        finally:
            root.removeHandler(self.handler)


class LogCaptureFixture:
    """Port of `_pytest/logging.py::LogCaptureFixture` (l. 403-592).

    ``filtering()`` and ``get_records()`` are ported; ``handler`` is the live handler, which
    is what makes ``set_level`` observable through it.
    """

    def __init__(self, capture: LogCapture) -> None:
        super().__init__()
        self._capture: Final = capture
        self._initial_handler_level: int | None = None
        self._initial_logger_levels: dict[str | None, int] = {}
        self._initial_disabled_logging_level: int | None = None

    def _finalize(self) -> None:
        """Restore every level :meth:`set_level` moved (l. 415-429)."""
        if self._initial_handler_level is not None:
            self.handler.setLevel(self._initial_handler_level)
        for logger_name, level in self._initial_logger_levels.items():
            logging.getLogger(logger_name).setLevel(level)
        if self._initial_disabled_logging_level is not None:
            logging.disable(self._initial_disabled_logging_level)
            self._initial_disabled_logging_level = None

    @property
    def handler(self) -> LogCaptureHandler:
        return self._capture.handler

    def get_records(self, when: str) -> list[logging.LogRecord]:
        """The records captured during one phase — ``"setup"``, ``"call"``, ``"teardown"``."""
        return self._capture.records_by_phase.get(when, [])

    @property
    def text(self) -> str:
        """The formatted log text (l. 451-454)."""
        return _remove_ansi_escape_sequences(self.handler.stream.getvalue())

    @property
    def records(self) -> list[logging.LogRecord]:
        return self.handler.records

    @property
    def record_tuples(self) -> list[tuple[str, int, str]]:
        return [(r.name, r.levelno, r.getMessage()) for r in self.records]

    @property
    def messages(self) -> list[str]:
        return [r.getMessage() for r in self.records]

    def clear(self) -> None:
        self.handler.clear()

    def _force_enable_logging(self, level: int | str, logger_obj: logging.Logger) -> int:
        """Port of l. 495-527: undo a ``logging.disable()`` that would hide *level*.

        pytest reaches the name -> number mapping through ``logging.getLevelName``, whose
        ``str -> int`` direction is documented as "considered a mistake" and is deprecated.
        ``getLevelNamesMapping()`` is the supported spelling and answers identically for a
        known name; the ``.get(level, level)`` fallback reproduces the *other* half of
        ``getLevelName``'s contract, which is that an unknown name comes back as a string —
        which is exactly what the ``isinstance(level, int)`` check below is testing for.
        """
        original_disable_level: int = logger_obj.manager.disable
        resolved: int | str = level
        if isinstance(level, str):
            resolved = logging.getLevelNamesMapping().get(level, level)
        if not isinstance(resolved, int):
            logging.disable(logging.NOTSET)
        elif not logger_obj.isEnabledFor(resolved):
            logging.disable(max(resolved - 10, logging.NOTSET))
        return original_disable_level

    def set_level(self, level: int | str, logger: str | None = None) -> None:
        """Set a logger's level *and the handler's* for the rest of the test (l. 529-552).

        Both halves matter: the logger level decides whether a record is created at all, the
        handler level decides whether this fixture sees it.  Restored in :meth:`_finalize`.
        """
        logger_obj = logging.getLogger(logger)
        _ = self._initial_logger_levels.setdefault(logger, logger_obj.level)
        logger_obj.setLevel(level)
        if self._initial_handler_level is None:
            self._initial_handler_level = self.handler.level
        self.handler.setLevel(level)
        initial_disabled_logging_level = self._force_enable_logging(level, logger_obj)
        if self._initial_disabled_logging_level is None:
            self._initial_disabled_logging_level = initial_disabled_logging_level

    @contextlib.contextmanager
    def at_level(self, level: int | str, logger: str | None = None) -> Generator[None, None, None]:
        """:meth:`set_level` for the duration of a ``with`` block (l. 554-577)."""
        logger_obj = logging.getLogger(logger)
        orig_level = logger_obj.level
        logger_obj.setLevel(level)
        handler_orig_level = self.handler.level
        self.handler.setLevel(level)
        original_disable_level = self._force_enable_logging(level, logger_obj)
        try:
            yield
        finally:
            logger_obj.setLevel(orig_level)
            self.handler.setLevel(handler_orig_level)
            logging.disable(original_disable_level)

    @contextlib.contextmanager
    def filtering(self, filter_: logging.Filter) -> Generator[None, None, None]:
        """Add a filter to the capture handler for a ``with`` block (l. 579-592)."""
        _ = self.handler.addFilter(filter_)
        try:
            yield
        finally:
            self.handler.removeFilter(filter_)


@fixture
def caplog() -> Generator[LogCaptureFixture, None, None]:
    """Access and control log capturing.

    Port of `_pytest/logging.py::caplog` (l. 595-610).  The handler is on the **root logger**
    for the duration of each phase, exactly as pytest's is, with two consequences worth
    stating because both are shared with pytest rather than divergences from it:

    * a logger with ``propagate = False`` never reaches a root handler, so its records are
      **not** captured — under pytest either.  A suite that needs them adds
      ``caplog.handler`` to that logger itself;
    * the root logger keeps its default ``WARNING`` level, so ``logging.info(...)`` is not
      captured until ``caplog.set_level(logging.INFO)`` is called.
    """
    capture = _log_capture
    if capture is None:  # pragma: no cover - the worker installs one whenever caplog is asked
        raise RuntimeError(
            "the caplog fixture requires the worker's per-test logging capture; "
            + "rustest._v2_builtins.set_log_capture was never called"
        )
    result = LogCaptureFixture(capture)
    try:
        yield result
    finally:
        result._finalize()  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# cache — port of `_pytest/cacheprovider.py`
# ---------------------------------------------------------------------------


class Cache:
    """Port of `_pytest/cacheprovider.py::Cache` (l. 57-215) — ``get``/``set``/``mkdir``.

    **The store is the one ``--lf`` uses.**  pytest keeps its last-failed set under the
    ordinary cache key ``cache/lastfailed``, i.e. at ``.pytest_cache/v/cache/lastfailed``, so
    a plugin can read it through this very API.  ``src/v2/cache.rs`` writes rustest's at
    ``.rustest_cache/v2/v/cache/lastfailed`` for exactly that reason, and in pytest's own
    ``{node_id: true}`` shape — so ``cache.get("cache/lastfailed", {})`` answers here with
    what it answers under pytest.  A second private store would have made this fixture a
    plausible-looking dead end.

    What is **not** ported: ``warn`` (no warnings channel), ``for_config``/``clear_cache``
    (no ``--cache-clear`` on the CLI), and the ``.gitignore``/``CACHEDIR.TAG``/``README.md``
    supporting files — ``.rustest_cache`` is one directory the user already ignores as a
    whole, and writing pytest's files into it would claim it is a pytest cache.
    """

    #: Sub-directory for directories created by ``mkdir()`` (l. 64).
    _CACHE_PREFIX_DIRS: Final = "d"
    #: Sub-directory for values created by ``set()`` (l. 67).
    _CACHE_PREFIX_VALUES: Final = "v"

    def __init__(self, cachedir: Path) -> None:
        super().__init__()
        self._cachedir: Final = cachedir

    def _mkdir(self, path: Path) -> None:
        path.mkdir(exist_ok=True, parents=True)

    def mkdir(self, name: str) -> Path:
        """A directory inside the cache, created if needed (l. 129-149).

        *name* may not contain a path separator, which is pytest's own check and its own
        message: the cache is a flat namespace of plugin-owned directories.
        """
        path = Path(name)
        if len(path.parts) > 1:
            raise ValueError("name is not allowed to contain path separators")
        res = self._cachedir.joinpath(self._CACHE_PREFIX_DIRS, path)
        self._mkdir(res)
        return res

    def _getvaluepath(self, key: str) -> Path:
        return self._cachedir.joinpath(self._CACHE_PREFIX_VALUES, Path(key))

    def get(self, key: str, default: object) -> Any:
        """The cached value for *key*, or *default* (l. 153-170).

        Every failure is a miss — a missing file, unreadable bytes, invalid JSON — because a
        cache that can fail a run is worse than no cache.
        """
        path = self._getvaluepath(key)
        try:
            with path.open("r", encoding="UTF-8") as handle:
                return json.load(handle)
        except (ValueError, OSError):
            return default

    def set(self, key: str, value: object) -> None:
        """Store *value* under *key* (l. 172-201).

        A write failure is swallowed, as pytest's is (it warns; there is no channel here).
        """
        path = self._getvaluepath(key)
        try:
            self._mkdir(path.parent)
        except OSError:
            return
        data = json.dumps(value, ensure_ascii=False, indent=2)
        try:
            with path.open("w", encoding="UTF-8") as handle:
                _ = handle.write(data)
        except OSError:
            return


def _cache_dir(rootdir: Path) -> Path:
    """``<rootdir>/.rustest_cache/v2`` — mirrors ``src/v2/cache.rs``'s two constants."""
    return rootdir / ".rustest_cache" / "v2"


@fixture(scope="session")
def cache() -> Cache:
    """Return a cache object that can persist state between test runs.

    Port of `_pytest/cacheprovider.py::cache` (l. 555-570).  Values live under
    ``.rustest_cache/v2/v/<key>`` and directories under ``.rustest_cache/v2/d/<name>`` —
    pytest's layout, in rustest's cache directory, sharing the store ``--lf`` writes.
    """
    return Cache(_cache_dir(_worker_context().rootdir))


# ---------------------------------------------------------------------------
# tmp_path / tmpdir — ports of `_pytest/tmpdir.py` and `_pytest/legacypath.py`
# ---------------------------------------------------------------------------


class TempPathFactory:
    """Port of `_pytest/tmpdir.py::TempPathFactory` (l. 47-190), numbering included.

    The base directory is one ``mkdtemp`` per worker rather than pytest's
    ``<temproot>/pytest-of-<user>/pytest-<n>`` with its retention policy: keeping the last
    three runs' trees is a *debugging* affordance tied to pytest's ``--basetemp`` and
    ``tmp_path_retention_policy`` options, neither of which is on the v2 wire, and a worker
    pool would race on the ``pytest-<n>`` counter.  Recorded rather than silently different:
    ``getbasetemp()`` answers with a real directory and everything below it behaves.
    """

    def __init__(self, prefix: str = "rustest") -> None:
        super().__init__()
        import itertools
        import tempfile

        self._base: Final[Path] = Path(tempfile.mkdtemp(prefix=f"{prefix}-"))
        self._counter: Final = itertools.count()
        self._created: Final[list[Path]] = []

    def mktemp(self, basename: str, numbered: bool = True) -> Path:
        """A new directory under the base (l. 105-135).

        ``numbered=False`` is pytest's "use this exact name"; it raises if the directory is
        already there, which is what makes it usable as a uniqueness assertion.
        """
        if not basename:
            raise ValueError("basename must be a non-empty string")
        name = f"{basename}{next(self._counter)}" if numbered else basename
        path = self._base / name
        path.mkdir(parents=True, exist_ok=False)
        self._created.append(path)
        return path

    def getbasetemp(self) -> Path:
        return self._base

    def cleanup(self) -> None:
        import shutil

        for path in reversed(self._created):
            shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(self._base, ignore_errors=True)
        self._created.clear()


class TempdirFactory:
    """Port of `_pytest/legacypath.py::TempdirFactory` — ``py.path`` over the same tree."""

    def __init__(self, path_factory: TempPathFactory) -> None:
        super().__init__()
        self._factory: Final = path_factory

    def mktemp(self, basename: str, numbered: bool = True) -> PyPathLocal:
        return _legacy_path(self._factory.mktemp(basename, numbered).resolve())

    def getbasetemp(self) -> PyPathLocal:
        return _legacy_path(self._factory.getbasetemp().resolve())


def _legacy_path(path: Path) -> PyPathLocal:
    """``py.path.local(path)`` — `_pytest/legacypath.py::legacy_path`.

    ``py`` is imported here rather than at module level: it is a rustest dependency, so the
    import cannot fail in a normal install, but it is also dead weight on the start-up path
    of every worker running a suite that never touches ``tmpdir``.
    """
    try:
        import py
    except ImportError as exc:  # pragma: no cover - exercised only without the dependency
        raise RuntimeError("the 'py' library is required for the tmpdir fixtures") from exc
    return py.path.local(path)


@fixture(scope="session")
def tmp_path_factory() -> Iterator[TempPathFactory]:
    """Session-scoped factory for temporary directories (`_pytest/tmpdir.py` l. 240-245)."""
    factory = TempPathFactory()
    try:
        yield factory
    finally:
        factory.cleanup()


def _mk_tmp(request: Any, factory: TempPathFactory) -> Path:
    """Port of `_pytest/tmpdir.py::_mk_tmp` (l. 248-253).

    The directory is named after the **test**, sanitised and truncated to 30 characters, so
    a failing run leaves a tree an operator can read.  v1 named every directory ``tmp_pathN``.
    """
    name = request.node.name
    name = re.sub(r"[\W]", "_", name)
    return factory.mktemp(name[:30], numbered=True)


@fixture
def tmp_path(request: Any, tmp_path_factory: TempPathFactory) -> Path:
    """A unique temporary directory for this test (`_pytest/tmpdir.py` l. 256-289).

    pytest additionally *removes* it at teardown under its retention policy; this factory
    cleans the whole tree up at session end instead — see :class:`TempPathFactory`.
    """
    return _mk_tmp(request, tmp_path_factory)


@fixture(scope="session")
def tmpdir_factory(tmp_path_factory: TempPathFactory) -> TempdirFactory:
    """The ``py.path`` flavour of :func:`tmp_path_factory` (`_pytest/legacypath.py`)."""
    return TempdirFactory(tmp_path_factory)


@fixture
def monkeypatch() -> Generator[MonkeyPatch, None, None]:
    """Patch objects, dict items, environment variables, ``sys.path`` and the cwd, undone
    at the end of the test.

    The class is v1's :class:`rustest.builtin_fixtures.MonkeyPatch`, borrowed rather than
    reimplemented: it has no capture, logging or path semantics for the two engines to
    diverge on, and one implementation is one thing to keep faithful to
    `_pytest/monkeypatch.py`.  Imported inside the body so v1's fixture module — which pulls
    ``shutil`` and ``py`` — stays off every worker's start-up path.
    """
    from .builtin_fixtures import MonkeyPatch

    patch = MonkeyPatch()
    try:
        yield patch
    finally:
        patch.undo()


@fixture
def tmpdir(tmp_path: Path) -> PyPathLocal:
    """``py.path.local`` over **the same directory** ``tmp_path`` returns.

    Port of `_pytest/legacypath.py::tmpdir` — one line, ``return legacy_path(tmp_path)``, and
    the identity is the point: a test requesting both fixtures gets one directory under
    pytest, and got two under v1's independent ``TmpDirFactory``.
    """
    return _legacy_path(tmp_path)


# ---------------------------------------------------------------------------
# mocker — port of pytest-mock 3.15.1
# ---------------------------------------------------------------------------


def _accepts_anything(*args: Any, **kwargs: Any) -> None:
    """The ``spec`` a stub is built against — pytest-mock spells it ``lambda *args,
    **kwargs: None`` inline (l. 228-230).

    ``spec`` is what makes a stub *callable-shaped*: ``callable(stub)`` is true and an
    attribute access raises instead of conjuring another mock.  A named function rather than
    a fresh lambda per call because ``spec`` is only ever introspected, never called, so one
    object is as good as N — and this one has annotated parameters.
    """


class _MockCacheItem(NamedTuple):
    """`pytest_mock/plugin.py::MockCacheItem` — a mock and the patch that installed it."""

    mock: Any
    patch: Any | None


class MockerFixture:
    """Port of `pytest_mock/plugin.py::MockerFixture` (pytest-mock 3.15.1).

    pytest-mock is **not** installed in this repository's environment, so the port is against
    the released source rather than against a live oracle, and the differential in
    ``python/tests/test_v2_builtins_mocker.py`` is written against ``unittest.mock``'s own
    semantics — which is what pytest-mock itself delegates every patch to.

    The one thing this fixture adds over calling ``unittest.mock`` directly is **undo
    ordering**, and it is the reason the cache is a list rather than a set: patches are
    stopped in reverse registration order (``MockCache.clear``, "for mock_item in
    reversed(self.cache)").  Two patches of the same attribute nest, and stopping them in
    registration order would restore the *intermediate* value permanently.

    Not ported, each for a stated reason:

    * ``mocker.patch.context_manager`` — its only difference from ``patch.object`` is
      suppressing the ``PytestMockWarning`` that ``_start_patch`` attaches to a mock used as
      a context manager, and there is no warnings channel to suppress on;
    * the ``mock_use_standalone_module`` ini and ``get_mock_module`` — the standalone ``mock``
      distribution is not a rustest dependency, so the module is always ``unittest.mock``;
    * ``class_mocker``/``module_mocker``/``package_mocker``/``session_mocker`` — the same
      fixture at wider scopes.  Cheap to add when something asks; a wider-scoped mocker in a
      worker pool has the per-worker caveat every wide fixture here has, and shipping it
      without that written down would be the more expensive mistake;
    * ``assert_wrapper`` and friends — pytest-mock monkey-patches ``unittest.mock``'s
      ``assert_called_with`` family process-wide to add argument introspection to the failure
      message.  That is a change to a stdlib module's behaviour for the whole process, opt-out
      via an ini this worker does not carry.
    """

    def __init__(self) -> None:
        super().__init__()
        from unittest import mock as mock_module

        self._mock_cache: list[_MockCacheItem] = []
        self.mock_module: Final = mock_module
        self.patch: Final = self._Patcher(self._mock_cache, mock_module)
        # Aliases for convenience — pytest-mock l. 88-104.
        self.Mock: Final = mock_module.Mock
        self.MagicMock: Final = mock_module.MagicMock
        self.NonCallableMock: Final = mock_module.NonCallableMock
        self.NonCallableMagicMock: Final = mock_module.NonCallableMagicMock
        self.PropertyMock: Final = mock_module.PropertyMock
        self.AsyncMock: Final = mock_module.AsyncMock
        self.call: Final = mock_module.call
        self.ANY: Final = mock_module.ANY
        self.DEFAULT: Final = mock_module.DEFAULT
        self.sentinel: Final = mock_module.sentinel
        self.mock_open: Final = mock_module.mock_open
        self.seal: Final = mock_module.seal

    def create_autospec(
        self, spec: Any, spec_set: bool = False, instance: bool = False, **kwargs: Any
    ) -> Any:
        """`MockerFixture.create_autospec` (l. 107-113) — tracked for :meth:`resetall`."""
        created = self.mock_module.create_autospec(spec, spec_set, instance, **kwargs)
        self._mock_cache.append(_MockCacheItem(mock=created, patch=None))
        return created

    def resetall(self, *, return_value: bool = False, side_effect: bool = False) -> None:
        """``reset_mock()`` every mock this fixture created (l. 115-144).

        ``return_value``/``side_effect`` are forwarded only to mocks that accept them —
        ``patch.dict`` hands back a plain ``dict``, and pytest-mock's own issue #237 is
        exactly the ``AttributeError`` that caused.
        """
        supports_reset_with_args = (self.Mock, self.AsyncMock)
        for item in self._mock_cache:
            mock_obj = item.mock
            if not hasattr(mock_obj, "reset_mock"):
                continue
            if hasattr(mock_obj, "spy_return_list"):
                mock_obj.spy_return_list = []
            if isinstance(mock_obj, supports_reset_with_args):
                mock_obj.reset_mock(return_value=return_value, side_effect=side_effect)
            else:
                mock_obj.reset_mock()

    def stopall(self) -> None:
        """Stop every patch, newest first (l. 146-151 -> ``MockCache.clear``).

        Safe to call twice: the cache is cleared, so the second call has nothing to stop.
        """
        for item in reversed(self._mock_cache):
            if item.patch is not None:
                item.patch.stop()
        self._mock_cache.clear()

    def stop(self, mock: Any) -> None:
        """Stop one patch by the mock it returned (l. 153-158 -> ``MockCache.remove``).

        ``ValueError`` for a mock this fixture never handed out, which is pytest-mock's own
        message and is the difference between "already stopped" and "wrong object".
        """
        for index, item in enumerate(self._mock_cache):
            if item.mock is mock:
                if item.patch is not None:
                    item.patch.stop()
                del self._mock_cache[index]
                return
        raise ValueError("This mock object is not registered")

    def spy(self, obj: object, name: str) -> Any:
        """Replace ``obj.name`` with a mock that **calls through** (l. 160-218).

        The wrapper records the outcome on the mock before re-raising or returning, so
        ``spy.spy_return``, ``spy.spy_return_list`` and ``spy.spy_exception`` describe the
        real call.  ``functools.update_wrapper`` is what preserves the signature, and it is
        what makes ``autospec=True`` legal on the patch below — which in turn is what makes
        ``spy.assert_called_once_with(2, 3)`` report the *declared* argument names.

        ``duplicate_iterators`` (and its ``spy_return_iter``) is not ported: it exists to let
        a test consume a generator the spied function returned without stealing it from the
        caller, needs ``itertools.tee`` bookkeeping on every call, and no target suite uses it.
        """
        import functools
        import inspect

        method = getattr(obj, name)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            spy_obj.spy_return = None
            spy_obj.spy_exception = None
            try:
                result = method(*args, **kwargs)
            except BaseException as exc:
                spy_obj.spy_exception = exc
                raise
            spy_obj.spy_return = result
            spy_obj.spy_return_list.append(result)
            return result

        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            spy_obj.spy_return = None
            spy_obj.spy_exception = None
            try:
                result = await method(*args, **kwargs)
            except BaseException as exc:
                spy_obj.spy_exception = exc
                raise
            spy_obj.spy_return = result
            spy_obj.spy_return_list.append(result)
            return result

        # pytest-mock spells this `asyncio.iscoroutinefunction`, which is deprecated as of
        # 3.14 and slated for removal in 3.16 *because* `inspect`'s version now subsumes it
        # (CPython gh-122875).  Same answer, no `import asyncio` — which matters here, since
        # this module is on the worker's start-up path and asyncio is ~240 ms of it.
        if inspect.iscoroutinefunction(method):
            wrapped = functools.update_wrapper(async_wrapper, method)
        else:
            wrapped = functools.update_wrapper(wrapper, method)

        autospec = inspect.ismethod(method) or inspect.isfunction(method)
        spy_obj = self.patch.object(obj, name, side_effect=wrapped, autospec=autospec)
        spy_obj.spy_return = None
        spy_obj.spy_return_list = []
        spy_obj.spy_exception = None
        return spy_obj

    def stub(self, name: str | None = None) -> Any:
        """A callable that accepts anything and records the call (l. 220-231).

        ``spec=lambda *args, **kwargs: None`` is not decoration: it is what makes the stub
        *callable-shaped*, so ``callable(stub)`` is true and an attribute access on it raises
        rather than conjuring another mock.
        """
        return self.mock_module.MagicMock(spec=_accepts_anything, name=name)

    def async_stub(self, name: str | None = None) -> Any:
        """:meth:`stub` for a coroutine function (l. 233-244)."""
        return self.mock_module.AsyncMock(spec=_accepts_anything, name=name)

    class _Patcher:
        """`MockerFixture._Patcher` (l. 246-460) — ``mock.patch``'s surface, undone at teardown.

        The indirection exists so ``mocker.patch`` is callable *and* carries ``.object``,
        ``.multiple`` and ``.dict``, which is ``mock.patch``'s own shape.
        """

        DEFAULT: Final = object()

        def __init__(self, mock_cache: list[_MockCacheItem], mock_module: Any) -> None:
            super().__init__()
            self._mock_cache: Final = mock_cache
            self.mock_module: Final = mock_module

        def _start_patch(self, mock_func: Any, *args: Any, **kwargs: Any) -> Any:
            """Start a patch and register it for teardown (l. 258-280)."""
            patcher = mock_func(*args, **kwargs)
            mocked = patcher.start()
            self._mock_cache.append(_MockCacheItem(mock=mocked, patch=patcher))
            return mocked

        def __call__(
            self,
            target: str,
            new: object = DEFAULT,
            spec: object | None = None,
            create: bool = False,
            spec_set: object | None = None,
            autospec: object | None = None,
            new_callable: object | None = None,
            **kwargs: Any,
        ) -> Any:
            """``mock.patch`` (l. 434-460)."""
            if new is self.DEFAULT:
                new = self.mock_module.DEFAULT
            return self._start_patch(
                self.mock_module.patch,
                target,
                new=new,
                spec=spec,
                create=create,
                spec_set=spec_set,
                autospec=autospec,
                new_callable=new_callable,
                **kwargs,
            )

        def object(
            self,
            target: object,
            attribute: str,
            new: object = DEFAULT,
            spec: object | None = None,
            create: bool = False,
            spec_set: object | None = None,
            autospec: object | None = None,
            new_callable: object | None = None,
            **kwargs: Any,
        ) -> Any:
            """``mock.patch.object`` (l. 282-309)."""
            if new is self.DEFAULT:
                new = self.mock_module.DEFAULT
            return self._start_patch(
                self.mock_module.patch.object,
                target,
                attribute,
                new=new,
                spec=spec,
                create=create,
                spec_set=spec_set,
                autospec=autospec,
                new_callable=new_callable,
                **kwargs,
            )

        def multiple(
            self,
            target: object,
            spec: object | None = None,
            create: bool = False,
            spec_set: object | None = None,
            autospec: object | None = None,
            new_callable: object | None = None,
            **kwargs: Any,
        ) -> dict[str, Any]:
            """``mock.patch.multiple`` (l. 341-362)."""
            return self._start_patch(
                self.mock_module.patch.multiple,
                target,
                spec=spec,
                create=create,
                spec_set=spec_set,
                autospec=autospec,
                new_callable=new_callable,
                **kwargs,
            )

        def dict(
            self,
            in_dict: Mapping[Any, Any] | str,
            values: Mapping[Any, Any] | Iterable[tuple[Any, Any]] = (),
            clear: bool = False,
            **kwargs: Any,
        ) -> Any:
            """``mock.patch.dict`` (l. 364-380)."""
            return self._start_patch(
                self.mock_module.patch.dict, in_dict, values=values, clear=clear, **kwargs
            )


@fixture
def mocker() -> Generator[MockerFixture, None, None]:
    """`pytest_mock/plugin.py::_mocker` (l. 462-470) — patches undone after every test."""
    result = MockerFixture()
    try:
        yield result
    finally:
        result.stopall()


# ---------------------------------------------------------------------------
# config — the `request.config` / `pytestconfig` subset
# ---------------------------------------------------------------------------


class _Notset:
    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return "<NOTSET>"


_NOTSET: Final = _Notset()

#: Ini names real pytest knows and this worker does **not** carry.  They get their own
#: message: answering ``unknown configuration value`` for ``markers`` would send an operator
#: hunting for a typo in a name pytest documents, and answering with a plausible default
#: would be worse — a suite branching on ``getini("markers")`` would take the wrong branch in
#: silence.
_UNCARRIED_INI: Final = frozenset(
    {
        "addopts",
        "cache_dir",
        "console_output_style",
        "doctest_optionflags",
        "empty_parameter_set_mark",
        "faulthandler_timeout",
        "filterwarnings",
        "junit_family",
        "log_cli",
        "log_cli_level",
        "log_date_format",
        "log_file",
        "log_format",
        "log_level",
        "markers",
        "minversion",
        "norecursedirs",
        "required_plugins",
        "testpaths",
        "tmp_path_retention_count",
        "tmp_path_retention_policy",
        "usefixtures",
        "xfail_strict",
    }
)


class Config:
    """The subset of `_pytest/config/__init__.py::Config` that ``request.config`` answers.

    Deliberately small, and **loud** past its edge.  A config object that answered plausibly
    for everything would be the worst of the three options: a suite reading an option this
    worker does not have would branch on a fabricated value and report a green run about
    something it never checked.
    """

    def __init__(self, rootdir: Path, invocation_dir: Path, ini: Mapping[str, object]) -> None:
        super().__init__()
        self.rootpath: Final = rootdir
        self.invocation_params: Final = _InvocationParams(dir=invocation_dir)
        #: pytest's ``Config.inipath`` is the config file it found.  The v2 wire does not
        #: carry it, and ``None`` is a legal pytest answer (a run with no ini at all), so a
        #: reader that handles pytest's ``None`` handles this.
        self.inipath: Final[Path | None] = None
        self._ini: Final = ini

    @property
    def rootdir(self) -> PyPathLocal:
        """pytest's deprecated ``py.path`` spelling of :attr:`rootpath`."""
        return _legacy_path(self.rootpath)

    @property
    def cache(self) -> Cache:
        """`Config.cache` — the same object the :func:`cache` fixture yields."""
        return Cache(_cache_dir(self.rootpath))

    def getini(self, name: str) -> object:
        """An ini value (`Config.getini`), for the names ``init`` carries.

        Six of them: the three ``python_*`` naming patterns and the three ``asyncio_*``
        options.  Anything else raises — see :data:`_UNCARRIED_INI` for why the two refusals
        are worded differently.
        """
        if name in self._ini:
            return self._ini[name]
        if name in _UNCARRIED_INI:
            carried = ", ".join(sorted(self._ini))
            raise ValueError(
                f"the ini value {name!r} is not available to a rustest v2 worker "
                + f"(it carries: {carried})"
            )
        raise ValueError(f"unknown configuration value: {name!r}")

    def getoption(self, name: str, default: object = _NOTSET, skip: bool = False) -> object:
        """A command-line option (`Config.getoption` l. 1747-1774).

        **No option is carried on the v2 wire**, so this always takes the failure path: with
        a *default* it returns the default, which is what the overwhelming majority of real
        call sites pass, and without one it raises pytest's own ``no option named`` error.
        Reporting the flags this run was actually invoked with needs them on the wire; a
        fabricated ``verbose=0`` would let a suite report on a mode it never ran in.
        """
        if default is not _NOTSET:
            return default
        if skip:
            from rustest.compat.pytest import skip as _skip

            _skip(f"no {name!r} option found")
        raise ValueError(f"no option named {name!r}")


class _InvocationParams(NamedTuple):
    """`Config.InvocationParams`, the one field the worker knows."""

    dir: Path


def current_config() -> Config:
    """The worker's :class:`Config`, built from what ``init`` established."""
    context = _worker_context()
    return Config(context.rootdir, context.invocation_dir, context.ini)


@fixture(scope="session")
def pytestconfig() -> Config:
    """The session's :class:`Config` — pytest's ``pytestconfig`` fixture.

    Also reachable as ``request.config``; the two are the same subset, and neither carries
    command-line options (see :meth:`Config.getoption`).
    """
    return current_config()


#: The fixtures this module contributes, in the order
#: ``_v2_worker._register_builtin_fixtures`` registers them.  Dependencies first, so the
#: registration order reads like the dependency order even though the registry does not care.
V2_BUILTIN_FIXTURES: Final[Sequence[str]] = (
    "tmp_path_factory",
    "tmp_path",
    "tmpdir_factory",
    "tmpdir",
    "monkeypatch",
    "capsys",
    "capfd",
    "caplog",
    "cache",
    "mocker",
    "pytestconfig",
)
