"""Record what a test's outcome can depend on, so a stale cached pass cannot happen silently.

Standalone: stdlib only, nothing from the worker (fingerprints, records and the inverse queries
live in ``_deps_fingerprint``).  The host calls :meth:`Recorder.start` **before it imports any
conftest or test module**, brackets each test (and each wide-scope fixture setup) with
:meth:`Recorder.begin` / :meth:`Recorder.end`, and gets a :class:`Record`: a set of
``(kind, key, fingerprint)`` dependencies, whether the test is ``cacheable`` at all, and why not.
:func:`stale` and :func:`affected_by_paths` answer the inverse questions; the result cache,
``--related`` and watch mode's "affected tests" all sit on those.

The fast-mode contract, in plain words
======================================

**``--fast`` skips a test that passed last time if nothing it was seen to use has changed, and
shows it as ``cached``, never as ``passed``.**  It is never the default.

*Seen* (a change here makes the test run again):

* the Python files of your project that it ran, or that those import, **including what they did
  at import time**: environment variables read, files opened, directories listed, modules tried
  and missing;
* which file each import resolves to (a new file that shadows a module is a change);
* files and directories it opened or listed through Python, and sqlite databases it opened;
* environment variables your code read;
* the Python build and flags, installed package versions, rustest's version and build, the working
  directory, the variables the C runtime reads (``TZ``, ``LANG``, ...) and your config.

*Not seen* (a change here can leave a stale ``cached`` pass; the list at the end names the test
that pins each one):

* the clock and randomness (``os.urandom``, ``uuid4``);
* files opened by C libraries (HDF5, libxml2, ``ssl`` certificate files, ...);
* whether a file exists, its size or its modification time (``os.stat``, ``Path.exists``);
* anything an earlier test left behind: module globals, caches, environment, files in memory;
* edits inside installed packages that keep the same version; ``sitecustomize`` and ``.pth``;
* the machine beyond what is listed under "always runs": the CPU count;
* a new local file named like a standard-library module (``random.py``).

**Always runs** (uncacheable): a test that starts a process, uses the network, locks a file, reads
the terminal, installs a tracer, asks for the machine's identity or the Windows registry, calls
native code (``ctypes``, ``mmap``, an sqlite extension), sleeps, or **writes to, deletes or renames
a file that already existed** (a file the test created itself is its output, not an input).  Every
audit event in CPython 3.14's table is classified, handled or ignored on purpose, by
``test_every_documented_audit_event_is_handled_or_ignored_on_purpose``.

A cached pass expires after 24 hours (the cache owns that).  If any test's result can depend on
something *not seen*, do not use ``--fast`` for it.  Run without ``--fast`` before you merge or
release, and never in CI.

What is seen, and how
=====================

=========  =====================================  =====================================
kind       key                                    fingerprint
=========  =====================================  =====================================
code       a first-party ``.py`` that executed     blake2b of the file, taken when first
                                                  seen executing
file       a file opened through Python           blake2b of the file as it was when
                                                  opened (``absent``)
listing    a directory listed                     blake2b of the sorted ``name:type``
env        a variable read (``*``: the whole)     blake2b of the value (``unset``)
import     a module name and the search path      what the standard path finder makes of
           it was resolved against                the name on that path now
=========  =====================================  =====================================

* ``code``: ``sys.monitoring`` ``PY_START`` / ``PY_RESUME`` / ``PY_THROW`` (a generator or
  coroutine resumed during a test is attributed to it), disabled per code object after its first
  report and re-enabled by ``restart_events()`` at each :meth:`~Recorder.begin`: one callback per
  distinct code object per test.  Plus the **static import closure** of every such file, of the
  test module, and of whatever the host passes in ``static_files``.  Code run in any thread
  during a test is attributed to it.
* ``file`` / ``listing``: audit events ``open``, ``os.listdir`` / ``os.scandir`` (so ``glob``,
  ``iterdir``, ``walk``), ``sqlite3.connect``, and the copy primitives ``shutil.copyfile`` and
  ``_winapi.CopyFile2`` (the source is read).  A file up to 1 MiB is hashed when it is opened, so
  the fingerprint is of what the test could read.
* ``env``: ``sys.monitoring`` on the Python methods of ``os._Environ`` (``__getitem__``,
  ``__iter__``, ``__len__``; ``os.getenv``, ``.get``, ``in`` and ``os.environb`` land there), the key
  read from the callee frame.  Nothing is replaced or wrapped.
* ``import``: for each module the closure imports (statically, including ``try: import x``) and
  each imported during the test, the standard path finder is re-run against the search path the
  import used.  A first-party module a *different* finder resolved (an editable install, a
  ``meta_path`` hook) is fingerprinted by the file it loaded and the finder's own source file;
  with no finder file to hash the test is uncacheable.  Standard-library names are not resolved.

Import time
===========

Modules do real work when they are imported, during collection, before any test runs.  The
recorder runs from :meth:`~Recorder.start`: what happens then is blamed on the **innermost
first-party file on the stack** (the module being imported, a conftest hook) and merged into every
test whose import closure contains that file; an event with only library code on the stack is the
session's and reaches every test; an event raised by rustest's own machinery with no first-party
frame above it is dropped.  The first :meth:`~Recorder.begin` ends collection; a later import phase
must be wrapped in :meth:`Recorder.session_scope`.  Events between tests belong to nobody.

The fingerprint invariant
=========================

**A recorded fingerprint is of content the process could have used, or the record is uncacheable.**

* Code is hashed *when it is first seen executing* (for a module, inside its own import) and
  never again: the code in memory cannot change, so a later edit can only make a *later*
  comparison differ, the safe direction.  The residual window is the microseconds between the
  import system reading the file and that hash; the size and mtime are compared before and after.
  A file first seen executing *not* at import (it was imported before the recorder started, so its
  load time is unknown) must be two seconds older than the recorder, which covers FAT's timestamps.
* A file read is hashed when it is opened (up to 1 MiB); a larger one is hashed at the end and
  compared with its stat when it was opened, and a difference is uncacheable.
* The memo that avoids rehashing a file is trusted only if the file was already two seconds old
  when it was hashed (git's "racily clean" rule), and a memo hit can only return what a rehash
  would.  It is an optimisation; it never widens what is trusted.
* :func:`stale` hashes from scratch: no memo, no stat gate, and the import system's directory
  caches are dropped first.

What the host must do
=====================

1. ``start()`` once per worker, **before** importing any conftest or test module, with
   ``started_ns`` the worker's start.  It raises :class:`Unavailable` if no ``sys.monitoring``
   tool id is free: treat every test as uncacheable.
2. ``begin(key, static_files=...)`` / ``end(key)`` around each test, with the test module, the
   conftest chain, the ``pytest_plugins`` modules, the modules of the fixtures it requests and
   any ``.md`` page whose code blocks it runs (their ``co_filename`` is not a ``.py``).
3. Fixtures with module or session scope set up inside one test do not appear in the others'
   records: bracket their setup with their own ``begin`` / ``end`` and :func:`merge` that record
   into every test that requests them, **transitively** (a fixture that uses another).
4. Pass ``ignore=`` the base temporary directory the run creates (pytest's ``pytest-of-<user>``):
   its cleanup deletes old directories, which is otherwise a pre-existing-file write.
5. Wrap any import phase after the first test in ``session_scope()``.
6. One recorder per process (``start`` raises :class:`Unavailable` for a second).

The holes, each pinned by a test that asserts it is **not** detected
=====================================================================

* existence and metadata checks: ``test_hole_an_existence_check_is_invisible``
* the clock: ``test_hole_the_clock_is_invisible``; randomness: ``test_hole_randomness_is_invisible``
* a C library's own file I/O: ``test_hole_a_file_a_c_library_opens_itself_is_invisible``
* an earlier test's state: ``test_hole_state_left_by_an_earlier_test_is_invisible``
* a cached module read dynamically for data:
  ``test_hole_a_cached_dynamic_import_read_for_data_is_invisible``
* ``importlib.import_module`` of an absent module (no ``import`` event, unlike the statement):
  ``test_hole_importlib_import_module_of_an_absent_module_is_invisible``
* environment read by C: ``test_hole_the_environment_read_by_c_is_invisible``;
  ``repr(os.environ)``: ``test_hole_repr_of_the_environment_is_invisible``; the raw dict:
  ``test_hole_the_raw_environment_dict_is_invisible``
* a new local file named like a standard-library module:
  ``test_hole_a_new_local_file_named_like_a_standard_library_module_is_invisible``
* the CPU count: ``test_hole_the_cpu_count_is_invisible_but_the_node_name_is_caught``
* the machine's identity, by route (``test_machine_identity_routes``): ``socket.gethostname``
  (audit event, caught), ``platform.uname`` / ``platform.node`` / ``platform.system`` and the other
  functions built on it (``sys.monitoring`` on ``platform.uname``, caught on every platform, where
  POSIX raises no audit event), a read of ``/etc/hostname`` or ``/etc/machine-id`` (an ``open``
  event: a file dependency, caught).  **Not caught**: a direct ``os.uname()`` (a C function that
  raises no event): ``test_hole_os_uname_is_invisible_on_posix``.
* Python 3.12: no ``time.sleep`` audit event (raised from 3.13) and no
  ``sys.monitoring.register_callback`` event (3.13), so **on 3.12 a sleep and a second tracer are
  not seen** (a ``--fast`` run on 3.12 may cache a test that sleeps for its result): ``test_hole_time_sleep_on_python_3_12_is_invisible``;
  ``_posixsubprocess.fork_exec`` (documented from 3.14) is covered on every version by
  ``subprocess.Popen``, ``os.fork`` and ``os.posix_spawn``.
* an edit inside an untracked install: ``test_hole_an_edit_inside_an_untracked_install_is_invisible``
* ``sitecustomize`` / ``.pth``: ``test_hole_sitecustomize_and_pth_files_are_in_no_prefix_part``
* worker machinery's own reads at import time:
  ``test_the_workers_own_activity_is_not_blamed_on_the_session``; events between tests:
  ``test_events_between_tests_belong_to_nobody``
* watch-only: ``affected_by_paths`` matches a path as written and by ``realpath``, not
  case-insensitively on APFS (``stale`` is content-based and unaffected); not testable here.
"""

# The module-level audit hook is the recorder's own callback and reaches its private state.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import ast
import contextlib
import importlib.machinery
import os
import sys
import threading
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, NamedTuple

from ._deps_fingerprint import (
    ABSENT,
    CODE,
    ENV,
    FILE,
    IMPORT,
    LISTING,
    Dep,
    Prefix,
    Record,
    _env_all,
    _env_value,
    _norm,
    _untracked_prefixes,
    affected_by_paths,
    digest_file,
    digest_listing,
    STDLIB,
    import_key,
    opaque_fingerprint,
    path_id,
    resolve,
    merge,
    session_prefix,
    stale,
)

__all__ = [
    "ABSENT",
    "Dep",
    "Prefix",
    "Record",
    "Recorder",
    "Unavailable",
    "affected_by_paths",
    "merge",
    "session_prefix",
    "stale",
]

#: A file whose mtime is this close to (or after) the worker's start may be newer than what the
#: process imported: the dependent test is not cacheable.
RACY_NS: Final = 20_000_000
#: A code file first seen executing *other than at import* (it was imported before the recorder
#: started) has an unknown load time: it is trusted only if it was already this old at the start.
_RACY_LAZY_NS: Final = 2_000_000_000
#: Files up to this size are hashed when they are opened, so the fingerprint is of what the test
#: could read; larger ones are hashed at the end and checked for change.
_EVENT_HASH_MAX: Final = 1 << 20
#: A memoised hash is trusted only if the file was older than this when it was hashed (git's
#: "racily clean" rule: an edit in the same timestamp tick with the same size is invisible).
_TRUST_NS: Final = 2_000_000_000

_UNCACHEABLE: Final = {
    # a process
    "subprocess.Popen": "subprocess",
    "_posixsubprocess.fork_exec": "subprocess",
    "os.system": "subprocess",
    "os.exec": "subprocess",
    "os.spawn": "subprocess",
    "os.posix_spawn": "subprocess",
    "os.fork": "subprocess",
    "os.forkpty": "subprocess",
    "_winapi.CreateProcess": "subprocess",
    "pty.spawn": "subprocess",
    "os.startfile": "subprocess",
    "os.startfile/2": "subprocess",
    "webbrowser.open": "subprocess",
    # the network
    "socket.connect": "network",
    "socket.bind": "network",
    "socket.sendto": "network",
    "socket.sendmsg": "network",
    "socket.getaddrinfo": "network",
    "socket.gethostbyname": "network",
    "socket.gethostbyaddr": "network",
    "socket.getnameinfo": "network",
    "socket.getservbyname": "network",
    "socket.getservbyport": "network",
    "urllib.Request": "network",
    "http.client.connect": "network",
    "http.client.send": "network",
    "smtplib.connect": "network",
    "smtplib.send": "network",
    "ftplib.connect": "network",
    "ftplib.sendcmd": "network",
    "poplib.connect": "network",
    "poplib.putline": "network",
    "imaplib.open": "network",
    "imaplib.send": "network",
    # the machine's identity
    "socket.gethostname": "the machine's identity",
    "socket.sethostname": "the machine's identity",
    "os.listdrives": "the machine's identity",
    "os.listmounts": "the machine's identity",
    "os.listvolumes": "the machine's identity",
    # native code the recorder cannot see into
    "mmap.__new__": "native code (mmap)",
    "ctypes.dlopen": "native code (ctypes)",
    "ctypes.dlsym": "native code (ctypes)",
    "ctypes.dlsym/handle": "native code (ctypes)",
    "sqlite3.load_extension": "native code (sqlite3 extension)",
    "sqlite3.enable_load_extension": "native code (sqlite3 extension)",
    "winreg.ConnectRegistry": "the Windows registry",
    "winreg.CreateKey": "the Windows registry",
    "winreg.DeleteKey": "the Windows registry",
    "winreg.DeleteValue": "the Windows registry",
    "winreg.DisableReflectionKey": "the Windows registry",
    "winreg.EnableReflectionKey": "the Windows registry",
    "winreg.EnumKey": "the Windows registry",
    "winreg.EnumValue": "the Windows registry",
    "winreg.ExpandEnvironmentStrings": "the Windows registry",
    "winreg.LoadKey": "the Windows registry",
    "winreg.OpenKey": "the Windows registry",
    "winreg.OpenKey/result": "the Windows registry",
    "winreg.PyHKEY.Detach": "the Windows registry",
    "winreg.QueryInfoKey": "the Windows registry",
    "winreg.QueryReflectionKey": "the Windows registry",
    "winreg.QueryValue": "the Windows registry",
    "winreg.SaveKey": "the Windows registry",
    "winreg.SetValue": "the Windows registry",
    "sys.remote_exec": "another process",
    "_winapi.OpenProcess": "another process",
    "_winapi.TerminateProcess": "another process",
    "pdb.Pdb": "the terminal (debugger)",
    "builtins.breakpoint": "the terminal (debugger)",
    "ensurepip.bootstrap": "installing packages",
    # other processes, the terminal, tracing
    "fcntl.flock": "file locking",
    "fcntl.lockf": "file locking",
    "os.lockf": "file locking",
    "msvcrt.locking": "file locking",
    "os.kill": "signals",
    "os.killpg": "signals",
    "signal.pthread_kill": "signals",
    "builtins.input": "the terminal (input)",
    "builtins.input/result": "the terminal (input)",
    "sys.settrace": "a tracer",
    "sys.setprofile": "a tracer",
    "sys.monitoring.register_callback": "a tracer",
    "os.fwalk": "a directory walk by file descriptor",
}

#: Audit events that create, write, delete or re-stamp a path, and which arguments are paths.
#: A path that did not exist is the test's own output; one that did is an input the test is
#: changing, which makes the test uncacheable.
_WRITES: Final = {
    "os.mkdir": (0,),
    "os.rename": (0, 1),
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.symlink": (1,),
    "os.link": (1,),
    "os.utime": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.chflags": (0,),
    "os.setxattr": (0,),
    "os.removexattr": (0,),
    "shutil.copyfile": (1,),
    "shutil.copymode": (1,),
    "shutil.copystat": (1,),
    "shutil.copytree": (1,),
    "shutil.move": (0, 1),
    "shutil.rmtree": (0,),
    "shutil.chown": (0,),
    "shutil.unpack_archive": (1,),
    "_winapi.CopyFile2": (1,),
    "_winapi.CreateJunction": (1,),
    "tempfile.mkdtemp": (0,),
    "tempfile.mkstemp": (0,),
}
#: Events whose argument is a path the operation *reads*.
_READS: Final = {"shutil.copyfile": 0, "_winapi.CopyFile2": 0, "os.getxattr": 0, "os.listxattr": 0}
#: Writes whose target may be a directory (everything under it is then the test's own).
_DIRECTORY_WRITES: Final = frozenset(
    {"os.mkdir", "os.rename", "shutil.move", "shutil.copytree", "shutil.unpack_archive"}
    | {"tempfile.mkdtemp", "os.symlink", "_winapi.CreateJunction"}
)
#: ``tempfile`` raises its event *after* creating the (unique, new) path.
_ALWAYS_NEW: Final = frozenset({"tempfile.mkdtemp", "tempfile.mkstemp"})
_WRITE_FLAGS: Final = (
    os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND | getattr(os, "O_EXCL", 0)
)


class Unavailable(RuntimeError):
    """The recorder cannot run here (no free ``sys.monitoring`` tool id)."""


# --------------------------------------------------------------------------------------
# the recorder
# --------------------------------------------------------------------------------------


class _Stat(NamedTuple):
    size: int
    mtime_ns: int


def _stat(path: str) -> _Stat | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return _Stat(st.st_size, st.st_mtime_ns)


@dataclass
class _Facts:
    """What was seen: by one test, by one module's import-time code, or by the session at large."""

    files: dict[str, tuple[_Stat | None, str | None]] = field(
        default_factory=dict[str, "tuple[_Stat | None, str | None]"]
    )
    listings: dict[str, str] = field(default_factory=dict[str, str])
    envs: dict[str, str] = field(default_factory=dict[str, str])
    imports: dict[str, tuple[str, ...] | None] = field(
        default_factory=dict[str, "tuple[str, ...] | None"]
    )
    own_files: set[str] = field(default_factory=set[str])
    own_dirs: set[str] = field(default_factory=set[str])
    own_env: set[str] = field(default_factory=set[str])
    reasons: set[str] = field(default_factory=set[str])

    def own(self, path: str) -> bool:
        return (
            path in self.own_files
            or path in self.own_dirs
            or any(path.startswith(d + os.sep) for d in self.own_dirs)
        )


@dataclass
class _Collector(_Facts):
    key: str = ""
    static: tuple[str, ...] = ()
    code: set[str] = field(default_factory=set[str])


#: How a frame's file is classified when blaming import-time events on a module.
_SKIP, _OWN, _USER, _OTHER = range(4)
_SELF_FILE: Final = sys._getframe(0).f_code.co_filename
_PKG_DIR: Final = _norm(Path(__file__).resolve().parent) + os.sep

_TOOL_IDS: Final = (5, 4, 3)
_ACTIVE: list[Recorder | None] = [None]
_HOOKED = [False]
#: Re-entrancy guard, per thread: while one thread is inside the recorder, its *own* events are not
#: recorded (the recorder's reads and hashes are not the test's), and no other thread's are lost.
_tls = threading.local()


def _is_busy() -> bool:
    return getattr(_tls, "busy", False)


#: Every audit event the recorder looks at; the hook returns at once for the (many) others.
_EVENTS: Final = (
    frozenset(_UNCACHEABLE)
    | frozenset(_WRITES)
    | {
        "open",
        "os.listdir",
        "os.scandir",
        "import",
        "sqlite3.connect",
        "os.putenv",
        "os.unsetenv",
        "time.sleep",
        "_winapi.CreateFile",
    }
    | frozenset(_READS)
)


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event not in _EVENTS:
        return
    recorder = _ACTIVE[0]
    if recorder is None or _is_busy() or not (recorder._stack or recorder._in_session):
        return
    _tls.busy = True
    try:
        _tls.targets = recorder._targets(sys._getframe(0))
        recorder._on_audit(event, args)
    except Exception as exc:  # noqa: BLE001 - never raise into the operation being audited
        recorder._fail(f"the recorder failed on {event}: {type(exc).__name__}")
    finally:
        _tls.targets = None
        _tls.busy = False


class Recorder:
    """Records per-test dependencies.  One per process (``start`` raises if another is running)."""

    def __init__(
        self,
        *,
        started_ns: int | None = None,
        root: str | os.PathLike[str] | None = None,
        ignore: Sequence[str] = (),
    ) -> None:
        super().__init__()
        self._started_ns = time.time_ns() if started_ns is None else started_ns
        self._root = _norm(root) if root is not None else None
        self._ignore = tuple(_norm(p) + os.sep for p in ignore)
        self._untracked = _untracked_prefixes() + self._ignore
        self._stack: list[_Collector] = []
        self._tool: int | None = None
        self._tracked_cache: dict[str, bool] = {}
        self._code_paths: dict[str, str | None] = {}
        self._hashes: dict[str, tuple[_Stat, int, str]] = {}
        self._facts: dict[str, tuple[str, str | None]] = {}
        self._imports: dict[str, tuple[str, tuple[str, ...]]] = {}
        self._closures: dict[
            str, tuple[tuple[int, int], frozenset[Dep], frozenset[str], frozenset[str]]
        ] = {}
        self._epoch = 0  # bumped by every `import` event: a module was loaded
        self._resolutions: dict[tuple[str, str], tuple[str, str | None]] = {}
        self._modnames: tuple[int, dict[str, str]] = (-1, {})
        self._env_codes: dict[Any, str] = {}
        self._identity_codes: frozenset[Any] = frozenset()
        self._in_session = False
        self._session_global = _Facts()
        self._session_files: dict[str, _Facts] = {}
        self._kinds: dict[str, tuple[int, str | None]] = {}

    # -- lifecycle ----------------------------------------------------------------------

    def start(self) -> None:
        if _ACTIVE[0] is not None:
            raise Unavailable("another deps recorder is running in this process")
        monitoring = sys.monitoring
        for candidate in _TOOL_IDS:
            try:
                monitoring.use_tool_id(candidate, "rustest deps")
            except ValueError:
                continue
            self._tool = candidate
            monitoring.restart_events()  # locations a previous tool disabled report again
            break
        else:
            raise Unavailable(f"no sys.monitoring tool id is free (tried {_TOOL_IDS})")
        environ = type(os.environ)
        if hasattr(environ, "__getitem__") and hasattr(environ, "__iter__"):
            self._env_codes = {
                environ.__getitem__.__code__: "item",
                environ.__iter__.__code__: "all",
                environ.__len__.__code__: "all",
            }
        import platform

        # `platform.uname()` is the route to the node name on POSIX (`os.uname()` raises no audit
        # event) and on Windows; it is a Python function, so its start is seen even when cached.
        self._identity_codes = frozenset({platform.uname.__code__})
        events = monitoring.events
        wanted = events.PY_START | events.PY_RESUME | events.PY_THROW
        for event in (events.PY_START, events.PY_RESUME):
            monitoring.register_callback(self._tool, event, self._on_start)
        monitoring.register_callback(self._tool, events.PY_THROW, self._on_throw)
        monitoring.set_events(self._tool, wanted)
        _ACTIVE[0] = self
        self._in_session = True  # collection: events are blamed on the module that caused them
        if not _HOOKED[0]:
            sys.addaudithook(_audit)
            _HOOKED[0] = True

    def stop(self) -> None:
        if self._tool is not None:
            monitoring = sys.monitoring
            monitoring.set_events(self._tool, 0)
            for event in (
                monitoring.events.PY_START,
                monitoring.events.PY_RESUME,
                monitoring.events.PY_THROW,
            ):
                monitoring.register_callback(self._tool, event, None)
            monitoring.free_tool_id(self._tool)
            self._tool = None
        if _ACTIVE[0] is self:
            _ACTIVE[0] = None
        self._stack.clear()

    def begin(self, key: str, static_files: Iterable[str | os.PathLike[str]] = ()) -> None:
        """A test (or a fixture setup) is about to run.  ``static_files`` are paths it depends
        on without necessarily executing them: its module, the conftest chain, fixture modules."""
        _tls.busy = True
        self._in_session = False  # the first test ends collection
        try:
            self._stack.append(_Collector(key=key, static=tuple(_norm(p) for p in static_files)))
            sys.monitoring.restart_events()  # every code object reports once more
        finally:
            _tls.busy = False

    @contextlib.contextmanager
    def session_scope(self) -> Iterator[None]:
        """Wrap any import phase that happens *after* tests have begun (collecting a later file,
        a plugin loaded late): what it does is import-time input, not a test's."""
        before = self._in_session
        self._in_session = True
        try:
            yield
        finally:
            self._in_session = before

    def end(self, key: str) -> Record:
        """The test has finished: its :class:`Record`."""
        _tls.busy = True
        try:
            collector = self._stack.pop()
            if collector.key != key:
                raise RuntimeError(f"end({key!r}) but {collector.key!r} is the innermost begin")
            return self._finish(collector)
        finally:
            _tls.busy = False

    # -- events -------------------------------------------------------------------------

    def _cur(self) -> list[_Facts]:
        targets = getattr(_tls, "targets", None)
        return targets if targets is not None else self._targets()

    def _fail(self, reason: str) -> None:
        for facts in self._cur():
            facts.reasons.add(reason)

    def _tracked(self, path: str) -> bool:
        known = self._tracked_cache.get(path)
        if known is None:
            known = self._tracked_cache[path] = not (
                path.startswith(self._untracked) or "__pycache__" in path or path.endswith(".pyc")
            )
        return known

    def _classify(self, filename: str) -> tuple[int, str | None]:
        if filename == _SELF_FILE:
            return _SKIP, None
        if filename.endswith(".py") and filename[:1] != "<":
            path = _norm(filename)
            if path.startswith(_PKG_DIR):
                return _OWN, None
            if self._tracked(path):
                return _USER, path
        return _OTHER, None

    def _session_target(self, frame: Any) -> _Facts | None:
        """Who to blame an event raised during collection on: the innermost first-party file on the
        stack (a module being imported, a conftest hook).  Worker machinery with no first-party
        frame above it is not blamed on anything; library code with none is the session's."""
        while frame is not None:
            name = frame.f_code.co_filename
            kind = self._kinds.get(name)
            if kind is None:
                kind = self._kinds[name] = self._classify(name)
            if kind[0] == _USER and kind[1] is not None:
                return self._session_files.setdefault(kind[1], _Facts())
            if kind[0] == _OWN:
                return None
            frame = frame.f_back
        return self._session_global

    def _targets(self, frame: Any = None) -> list[_Facts]:
        """Every place an event raised now is recorded: each open test or fixture, and (while the
        session is collecting) the module or the session that caused it."""
        found: list[_Facts] = list(self._stack)
        if self._in_session:
            blamed = self._session_target(frame if frame is not None else sys._getframe(1))
            if blamed is not None:
                found.append(blamed)
        return found

    def _on_throw(self, code: Any, offset: int, exception: BaseException) -> object:
        return self._on_start(code, offset)

    def _on_start(self, code: Any, offset: int) -> object:
        try:
            return self._on_start_unsafe(code)
        except Exception as exc:  # noqa: BLE001 - never raise into the code being monitored
            self._fail(f"the recorder failed on a code object: {type(exc).__name__}")
            return None

    def _on_start_unsafe(self, code: Any) -> object:
        if _is_busy():
            return None  # this thread's own work, or its event was dropped: never DISABLE it
        kind = self._env_codes.get(code)
        if kind is not None:
            if self._stack or self._in_session:
                try:
                    self._on_env(kind)
                except Exception as exc:  # noqa: BLE001
                    self._fail(f"the recorder failed on os.environ: {type(exc).__name__}")
            return None  # keep reporting: every read matters
        if code in self._identity_codes:
            if self._stack or self._in_session:
                self._fail("uses the machine's identity (platform.uname)")
            return None  # keep reporting: every call matters
        name = code.co_filename
        try:
            path = self._code_paths[name]
        except KeyError:
            path = _norm(name) if name.endswith(".py") and name[:1] != "<" else None
            if path is not None and not self._tracked(path):
                path = None
            self._code_paths[name] = path
        if path is not None:
            if path not in self._facts:  # first sight: fingerprint now, inside the import
                _tls.busy = True
                try:
                    _ = self._code_fact(path, at_import=code.co_name == "<module>")
                finally:
                    _tls.busy = False
            for collector in self._stack:
                collector.code.add(path)
        return sys.monitoring.DISABLE

    def _on_env(self, kind: str) -> None:
        frame: Any = sys._getframe(1)
        while frame is not None and frame.f_code not in self._env_codes:
            frame = frame.f_back  # up to the `os._Environ` method that just started
        if frame is None:
            return
        caller = frame.f_back
        if kind == "all":
            for facts in self._targets(caller):
                facts.envs.setdefault("*", _env_all())
            return
        key = frame.f_locals.get("key")
        if isinstance(key, bytes):  # os.environb
            key = os.fsdecode(key)
        if not isinstance(key, str):
            return
        name = key.upper() if os.name == "nt" else key
        _tls.busy = True
        try:
            value = _env_value(key)
        finally:
            _tls.busy = False
        for facts in self._targets(caller):
            if name not in facts.own_env:
                facts.envs.setdefault(name, value)

    def _on_audit(self, event: str, args: tuple[Any, ...]) -> None:
        reason = _UNCACHEABLE.get(event)
        if reason is not None:
            if event in ("socket.bind", "socket.connect") and _in_socketpair():
                return
            self._fail(f"uses {reason} ({event})")
        elif event == "open":
            self._on_open(args)
        elif event in ("os.listdir", "os.scandir"):
            self._on_listing(args[0])
        elif event == "import":
            self._epoch += 1
            name = str(args[0])
            snapshot = tuple(map(str, args[2])) if "." not in name else None
            for facts in self._cur():
                facts.imports.setdefault(name, snapshot)
        elif event == "sqlite3.connect":
            self._on_sqlite(args[0])
        elif event in ("os.putenv", "os.unsetenv"):
            name = os.fsdecode(args[0])
            for collector in self._cur():
                collector.own_env.add(name.upper() if os.name == "nt" else name)
        elif event == "time.sleep":
            if isinstance(args[0], (int, float)) and args[0] > 0:
                self._fail("uses the clock (time.sleep)")
        elif event == "_winapi.CreateFile":
            self._on_createfile(args)
        else:
            if event in _READS and (path := self._path(args[_READS[event]])) is not None:
                self._read_file(path)
            for index in _WRITES.get(event, ()):
                self._on_write(event, args[index])

    def _path(self, value: object) -> str | None:
        if value is None:
            return _norm(os.getcwd())
        if isinstance(value, (str, bytes, os.PathLike)):
            return _norm(value)  # pyright: ignore[reportUnknownArgumentType]
        return None  # a file descriptor

    def _on_open(self, args: tuple[Any, ...]) -> None:
        path = self._path(args[0])
        if path is None:
            return
        mode, flags = args[1], args[2]
        writing = (
            any(c in mode for c in "wax+")
            if isinstance(mode, str)
            else bool(isinstance(flags, int) and flags & _WRITE_FLAGS)
        )
        if writing:
            self._on_write("open", path, normalised=True)
        else:
            self._read_file(path)

    def _read_file(self, path: str) -> None:
        if not self._tracked(path):
            return
        pending = [c for c in self._cur() if not c.own(path) and path not in c.files]
        if not pending:
            return
        now = _stat(path)
        digest = None
        if now is None:
            digest = ABSENT
        elif now.size <= _EVENT_HASH_MAX:
            digest = self._digest(path, now)  # what the test is about to read
        for collector in pending:
            collector.files[path] = (now, digest)

    def _on_write(self, event: str, value: object, *, normalised: bool = False) -> None:
        """A path is created, written, deleted or re-stamped.  One that did not exist is the test's
        own output.  One that did is an input the test changes (the next run would see a different
        world), so the test is uncacheable -- whether or not it read the file first."""
        path = value if normalised else self._path(value)
        if not isinstance(path, str):
            return
        existed = event not in _ALWAYS_NEW and os.path.lexists(path)
        for collector in self._cur():
            if collector.own(path):
                continue
            if not existed:
                if path in collector.files:
                    collector.reasons.add(f"creates a file it found missing ({self._show(path)})")
                collector.own_files.add(path)
                if event in _DIRECTORY_WRITES or event == "tempfile.mkdtemp":
                    collector.own_dirs.add(path)
            elif self._tracked(path):
                collector.reasons.add(f"modifies a pre-existing file ({self._show(path)})")

    def _on_createfile(self, args: tuple[Any, ...]) -> None:
        name, access = args[0], args[1]
        if not isinstance(name, str) or name.startswith("\\\\"):  # a named pipe, not a file
            return
        path = self._path(name)
        if path is None:
            return
        if isinstance(access, int) and access & 0x40000006:  # GENERIC_WRITE, FILE_WRITE_*
            self._on_write("open", path, normalised=True)
        else:
            self._read_file(path)

    def _on_listing(self, value: object) -> None:
        path = self._path(value)
        if path is None or not self._tracked(path + os.sep):
            return
        fingerprint = digest_listing(path)
        for collector in self._cur():
            if not collector.own(path):
                collector.listings.setdefault(path, fingerprint)

    def _on_sqlite(self, value: object) -> None:
        if isinstance(value, (str, bytes)) and os.fsdecode(value).startswith("file:"):
            self._fail("uses a sqlite3 URI database")
            return
        path = self._path(value)
        if path is None or os.fsdecode(value) in ("", ":memory:"):  # pyright: ignore[reportArgumentType]
            return
        if _stat(path) is None:  # sqlite creates it: the test's own output
            self._on_write("open", path, normalised=True)
        else:
            self._read_file(path)

    # -- finishing ----------------------------------------------------------------------

    def _show(self, path: str) -> str:
        if self._root and path.startswith(self._root + os.sep):
            return path[len(self._root) + 1 :]
        return path

    def _digest(self, path: str, now: _Stat | None) -> str:
        """Hash with a stat gate that trusts a memo only for files old enough when hashed."""
        if now is None:
            return ABSENT
        memo = self._hashes.get(path)
        if memo is not None and memo[0] == now:
            return memo[2]
        hashed_at = time.time_ns()
        digest = digest_file(path)
        if now.mtime_ns + _TRUST_NS <= hashed_at:
            self._hashes[path] = (now, hashed_at, digest)
        return digest

    def _file_dep(
        self,
        kind: str,
        key: str,
        path: str,
        before: _Stat | None | str,
        deps: set[Dep],
        reasons: set[str],
    ) -> None:
        """One file dependency, with the checks that make a fingerprint trustworthy: it did not
        change (or appear) while the test ran, and is not newer than the worker."""
        now = _stat(path)
        if before != "unseen" and now != before:
            what = "appeared" if before is None else "changed"
            reasons.add(f"{self._show(path)} {what} while the test ran")
        if now is not None and now.mtime_ns >= self._started_ns - RACY_NS:
            reasons.add(f"{self._show(path)} was modified after the worker started")
        deps.add((kind, key, self._digest(path, now)))

    def _finish(self, c: _Collector) -> Record:
        deps: set[Dep] = set()
        reasons = set(c.reasons)
        covered: set[str] = set()
        static_names: set[str] = set()
        for root in sorted({*c.static, *c.code}):
            if root not in covered:  # a root inside an earlier closure adds nothing new
                closure_deps, closure_reasons, closure_names = self._closure_deps(root)
                deps |= closure_deps
                reasons |= closure_reasons
                static_names |= closure_names
                covered.update(path for _, path, _ in closure_deps)
        parts: list[_Facts] = [c, self._session_global]
        parts += [
            self._session_files[f] for f in sorted(covered | c.code) if f in self._session_files
        ]
        files: dict[str, tuple[_Stat | None, str | None]] = {}
        listings: dict[str, str] = {}
        envs: dict[str, str] = {}
        imports: dict[str, tuple[str, ...] | None] = dict.fromkeys(static_names)
        for part in parts:
            reasons |= part.reasons
            for name, snapshot in part.imports.items():
                if imports.get(name) is None:
                    imports[name] = snapshot
            for path, entry in part.files.items():
                files.setdefault(path, entry)
            for table, merged, label in (
                (part.listings, listings, "listing"),
                (part.envs, envs, "value"),
            ):
                for key, fingerprint in table.items():
                    if merged.setdefault(key, fingerprint) != fingerprint:
                        reasons.add(f"the {label} of {key} differed between two reads")
        for path, (token, digest) in files.items():
            if path in covered:  # a source file the import system read is a code dependency
                continue
            if digest is None:  # too big to hash on open: hashed now, so it must not have moved
                self._file_dep(FILE, path, path, token, deps, reasons)
                continue
            now = _stat(path)
            if now != token:
                what = "appeared" if token is None else "changed"
                reasons.add(f"{self._show(path)} {what} while the test ran")
            deps.add((FILE, path, digest))
        for path, fingerprint in listings.items():
            deps.add((LISTING, path, fingerprint))
        for name, fingerprint in envs.items():
            deps.add((ENV, name, fingerprint))
        paths: dict[str, tuple[str, ...]] = {}
        self._import_deps(imports, deps, reasons, paths)
        return Record(
            frozenset(deps), not reasons, tuple(sorted(reasons)), tuple(sorted(paths.items()))
        )

    def _import_deps(
        self,
        wanted: Mapping[str, tuple[str, ...] | None],
        deps: set[Dep],
        reasons: set[str],
        paths: dict[str, tuple[str, ...]],
    ) -> None:
        """One ``import`` dependency per module the tests' code imports or tried to: the
        *resolution* against the search path it was imported with (``sys.path`` as it was, or the
        parent package's ``__path__``)."""
        for name in sorted(wanted):
            if name.partition(".")[0] in STDLIB:
                continue
            search = wanted[name]
            if search is None:
                parent_name = name.rpartition(".")[0]
                if parent_name:
                    found = getattr(sys.modules.get(parent_name), "__path__", None)
                    if found is None:
                        continue  # not a submodule of a package: an attribute
                    search = tuple(map(str, found))
                else:
                    search = tuple(map(str, sys.path))
            pid = path_id(search)
            paths[pid] = search
            resolved = self._resolutions.get((name, pid))
            if resolved is None:
                resolved = self._resolutions[(name, pid)] = resolve(name, search)
            fingerprint, origin = resolved
            spec = getattr(sys.modules.get(name), "__spec__", None)
            loaded = getattr(spec, "origin", None)
            if (
                isinstance(loaded, str)
                and loaded not in ("built-in", "frozen")
                and self._tracked(_norm(loaded))
                and (origin is None or _norm(origin) != _norm(loaded))
            ):
                self._opaque_import(name, search, pid, _norm(loaded), deps, reasons)
                continue
            deps.add((IMPORT, import_key(name, pid), fingerprint))

    def _opaque_import(
        self,
        name: str,
        search: tuple[str, ...],
        pid: str,
        origin: str,
        deps: set[Dep],
        reasons: set[str],
    ) -> None:
        """A first-party module the standard path finder does not resolve to the file that was
        loaded: an editable-install finder, a ``meta_path`` hook.  It is fingerprinted by the
        loaded file and the finder's own source file, or the test is uncacheable."""
        standard: tuple[object, ...] = (
            importlib.machinery.BuiltinImporter,
            importlib.machinery.FrozenImporter,
            importlib.machinery.PathFinder,
        )
        parent = search if "." in name else None
        finders: list[Any] = list(sys.meta_path)
        for finder in finders:
            if finder in standard:
                continue
            try:
                spec = finder.find_spec(name, list(parent) if parent else None)
            except Exception:  # noqa: BLE001 - a hook that cannot answer is not a fingerprint
                continue
            if spec is None or not spec.origin or _norm(spec.origin) != origin:
                continue
            module = getattr(finder, "__module__", None) or type(finder).__module__
            qualname = getattr(finder, "__qualname__", None) or type(finder).__qualname__
            finder_file = getattr(sys.modules.get(module), "__file__", None)
            if not isinstance(finder_file, str):
                reasons.add(f"{name} was resolved by {qualname}, whose source file is unknown")
                return
            key = import_key(name, pid, (qualname, _norm(finder_file), origin))
            deps.add((IMPORT, key, opaque_fingerprint(origin, _norm(finder_file))))
            return
        reasons.add(f"{name} was resolved by an import finder rustest cannot identify")

    # -- code: facts about loaded files, and the static import closure --------------------

    def _code_fact(self, path: str, at_import: bool = False) -> tuple[str, str | None]:
        """``(fingerprint, reason)`` for a file whose code this process has loaded.

        Taken the first time the file is seen executing -- for a module, inside its own import,
        before any test runs -- and memoised for the process: the code in memory cannot change, so
        a later edit can only make a *later* comparison differ, which is the safe direction.  A file
        seen at import is flagged if its mtime is within ``RACY_NS`` of the worker's start; one
        first seen otherwise (its load time unknown) needs ``_RACY_LAZY_NS``, a margin that covers
        coarse timestamps.  A missing source file (a sourceless module) is uncacheable.
        """
        fact = self._facts.get(path)
        if fact is not None:
            return fact
        before = _stat(path)
        digest = digest_file(path) if before is not None else ABSENT
        after = _stat(path)
        if after != before:
            return digest, f"{self._show(path)} changed while it was being fingerprinted"
        if after is None:
            return digest, f"{self._show(path)} has no source file (a sourceless module?)"
        margin = RACY_NS if at_import else _RACY_LAZY_NS
        reason = None
        if after.mtime_ns >= self._started_ns - margin:
            reason = f"{self._show(path)} was modified after the worker started"
        self._facts[path] = (digest, reason)
        return digest, reason

    def _closure_deps(self, root: str) -> tuple[frozenset[Dep], frozenset[str], frozenset[str]]:
        """The ``code`` dependencies of ``root`` and everything it statically imports, the reasons
        they are not cacheable, and every module name those files import (memoised until a new
        module is loaded)."""
        stamp = (len(sys.modules), self._epoch)
        memo = self._closures.get(root)
        if memo is not None and memo[0] == stamp:
            return memo[1], memo[2], memo[3]
        deps: set[Dep] = set()
        reasons: set[str] = set()
        names: set[str] = set()
        for path in self._closure([root]):
            digest, reason = self._code_fact(path)
            deps.add((CODE, path, digest))
            if reason:
                reasons.add(reason)
            if path in self._imports:
                names.update(self._imports[path][1])
        result = (stamp, frozenset(deps), frozenset(reasons), frozenset(names))
        self._closures[root] = result
        return result[1], result[2], result[3]

    def _module_names(self) -> dict[str, str]:
        size, names = self._modnames
        if size != len(sys.modules):
            names = dict[str, str]()
            for name, module in list(sys.modules.items()):
                file = getattr(module, "__file__", None)
                if isinstance(file, str):
                    names[_norm(file)] = names.get(_norm(file), name)
            self._modnames = (len(sys.modules), names)
        return names

    def _closure(self, roots: Iterable[str]) -> set[str]:
        seen: set[str] = set()
        todo = [p for p in roots if self._tracked(p)]
        while todo:
            path = todo.pop()
            if path in seen:
                continue
            seen.add(path)
            if path.endswith(".py"):
                todo.extend(self._imported_files(path))
        return seen

    def _imported_files(self, path: str) -> list[str]:
        digest = self._code_fact(path)[0]
        memo = self._imports.get(path)
        if memo is None or memo[0] != digest:
            memo = (digest, tuple(self._import_names(path)))
            self._imports[path] = memo
        files: list[str] = []
        for name in memo[1]:
            module = sys.modules.get(name)
            file = getattr(module, "__file__", None)
            if isinstance(file, str) and self._tracked(_norm(file)):
                files.append(_norm(file))
        return files

    def _import_names(self, path: str) -> set[str]:
        try:
            tree = ast.parse(Path(path).read_bytes())
        except (OSError, SyntaxError, ValueError):
            return set()
        module = self._module_names().get(path, "")
        package = module if os.path.basename(path) == "__init__.py" else module.rpartition(".")[0]
        names: set[str] = set()

        def add(dotted: str) -> None:
            parts = dotted.split(".")
            names.update(".".join(parts[: i + 1]) for i in range(len(parts)))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = package.rsplit(".", node.level - 1)[0] if package else ""
                    base = f"{parent}.{base}".strip(".") if base else parent
                if base:
                    add(base)
                    for alias in node.names:
                        add(f"{base}.{alias.name}")
        return names


def _in_socketpair() -> bool:
    """True inside ``socket.socketpair`` (Windows builds it from a loopback bind and connect)."""
    frame: Any = sys._getframe(0)
    for _ in range(8):
        if frame is None:
            return False
        if frame.f_code.co_name in (
            "socketpair",
            "_fallback_socketpair",
        ) and frame.f_code.co_filename.endswith("socket.py"):
            return True
        frame = frame.f_back
    return False
