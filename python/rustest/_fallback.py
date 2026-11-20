"""Hand a whole session to real pytest, for what rustest does not run.

A session is either entirely rustest's or entirely pytest's: a conftest hook such as
``pytest_collection_modifyitems`` sees every directory's items, so a split run would be
wrong, not just awkward.  pytest gets the user's own arguments and streams its output
unmodified; its exit code is the process's exit code.

Imported only when a run is being handed over, never on the ``import rustest`` path.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import threading
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import subprocess

#: rustest-only flags pytest does not have; the value says whether one is consumed too.
#: ``--codeblocks`` is dropped, not translated: a ``.md`` argument is refused below, and
#: without one the flag has nothing to act on.
_DROPPED = {
    "--report-json": True,
    "--llm": False,
    "--llm-full": False,
    "--ascii": False,
    "--no-pytest-fallback": False,
    "--codeblocks": False,
    "--no-codeblocks": False,
    "--asyncio-concurrency": True,
}

_EXIT_USAGE_ERROR = 4
_WINDOWS = sys.platform == "win32"


#: ``pytest11`` entry-point plugins pytest auto-loads and rustest runs without.  Everything
#: else installed is a hand-off reason (a false positive costs speed; a silent difference
#: costs a wrong answer).  Each entry is judged on the plugin's own source, as installed in
#: the ``--real`` target venvs, and the rule is strict: *common* is not *safe*.  An entry must
#: also not reorder or deselect in ``pytest_collection_modifyitems``: B-lite's handed pytest
#: does not count an installed plugin's list changes (``_pytest_report``'s foreign check).
#:
#: | entry point | why running without it cannot change an outcome or an id |
#: |---|---|
#: | ``xdist``, ``xdist.looponfail`` | every behaviour is behind a flag (``-n``, ``--dist``, ``--tx``, ``-f``): rustest owns ``-n``, and any other xdist flag is an unrecognized argument that already hands off. Its ``xdist_group`` mark only steers scheduling, and ``worker_id``/``testrun_uid`` are fixtures a test must request, so a missing one is a loud setup error |
#: | ``pytest_cov`` | flag-gated (``--cov*``: rustest implements ``--cov`` and ``--cov-report``, the rest hand off); its ``no_cover`` mark and fixtures only steer what coverage records, never an outcome |
#: | ``asyncio`` | pytest-asyncio's loop-scope model is what rustest emulates (``@mark.asyncio``, ``asyncio_default_*_loop_scope``); the one difference, ``asyncio_mode`` defaulting to ``auto`` here against ``strict`` there, is documented under "where rustest is more permissive" |
#: | ``pytest_mock`` | the ``mocker`` fixture is a port of pytest-mock 3.15.1 (``_builtins.py``) |
#: | ``icdiff`` | rewrites the text of a failed assertion (``pytest_assertrepr_compare``), never whether it fails; its options are flags (unknown ones hand off) |
#: | ``rich`` (pytest-rich) | terminal reporting, and only with ``--rich`` (an unrecognized flag, so it hands off by itself) |
#: | ``codeblocks`` | collects markdown fences only with ``--codeblocks``, which rustest implements itself |
#: | ``anyio``, ``timeout``, ``syrupy``, ``textual-snapshot``, ``platformdirs``, ``hydra_pytest``, ``faker``, ``typeguard`` | inert until something uses them: a selected test with the plugin's mark or one of its fixtures hands the session off (``_worker._PLUGIN_USES``), as do their flags, and the ini keys and environment that switch them on (:func:`autoload_reasons`) -- anyio's ``anyio_mode = auto``, pytest-timeout's ``timeout``/``session_timeout``/``PYTEST_TIMEOUT``, typeguard's ``typeguard-*`` keys (faker's autouse ``_session_faker`` only builds a ``Faker`` with its own ``random.Random``) |
#: | ``hypothesispytest`` | what it does to outcomes is ported (``_hypothesis``), for the releases checked there and later ones whose plugin is unchanged: another hands off (:func:`autoload_reasons`), as do its ``--hypothesis-*`` flags |
#:
#: Judged and **not** allowed, with the evidence: ``inline_snapshot`` (an autouse fixture
#: that fails a test from recorded snapshot state, and a session report). Unseen plugins (randomly, rerunfailures, env, django, benchmark, ...)
#: are not on the list, so they hand off: ``-p no:NAME`` or ``PYTEST_DISABLE_PLUGIN_AUTOLOAD``
#: opts a project out, exactly as it does for pytest.
SAFE_PLUGINS: Final = frozenset(
    {
        "xdist",
        "xdist.looponfail",
        "pytest_cov",
        "asyncio",
        "pytest_mock",
        "icdiff",
        "rich",
        "codeblocks",
        "hypothesispytest",
        "anyio",
        "timeout",
        "syrupy",
        "textual-snapshot",
        "platformdirs",
        "hydra_pytest",
        "faker",
        "typeguard",
    }
)


def installed_plugins() -> list[tuple[str, str]]:
    """``(entry-point name, distribution)`` for every ``pytest11`` entry point on ``sys.path``.

    Read straight from ``*.dist-info/entry_points.txt`` rather than through
    ``importlib.metadata``, which builds a ``Distribution`` per installed package: this runs on
    every invocation, and a directory listing plus one small file read per package is all it
    needs.  The first entry point of a name wins, as in pluggy, and the order is pytest's
    registration order (``sys.path``, then each directory's listing, as ``importlib.metadata``
    reads it): the order the plugins' ``pytest_configure`` lines land in ``getini("markers")``.
    """
    found: dict[str, str] = {}
    for entry in sys.path:
        try:
            names = os.listdir(entry or ".")
        except OSError:
            continue
        for name in names:
            if not name.endswith((".dist-info", ".egg-info")):
                continue
            try:
                with open(
                    os.path.join(entry, name, "entry_points.txt"), encoding="utf-8"
                ) as handle:
                    lines = handle.read().splitlines()
            except OSError:
                continue
            in_group = False
            for line in lines:
                line = line.strip()
                if line.startswith("["):
                    in_group = line == "[pytest11]"
                elif in_group and "=" in line and not line.startswith("#"):
                    found.setdefault(line.partition("=")[0].strip(), name.rpartition(".")[0])
    return list(found.items())


def autoload_reasons(blocked: Sequence[str], ini: Mapping[str, object] | None = None) -> list[str]:
    """Hand-off reasons for auto-loaded plugins rustest does not run.

    pytest's own switches apply: nothing auto-loads under ``PYTEST_DISABLE_PLUGIN_AUTOLOAD``,
    and ``-p no:NAME`` blocks the entry point called ``NAME`` or ``pytest_NAME``. *ini* is the
    ini file's section: what switches an otherwise inert plugin on.
    """
    if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
        return []
    from ._hypothesis import VERIFIED, supported

    ini = ini or {}
    switched_on = {
        "timeout": [
            *(f"the {key} ini option" for key in ("timeout", "session_timeout") if ini.get(key)),
            *(["PYTEST_TIMEOUT"] * bool(os.environ.get("PYTEST_TIMEOUT"))),
        ],
        "anyio": ["anyio_mode = auto"] * (str(ini.get("anyio_mode", "")).strip() == "auto"),
        "typeguard": [f"the {key} ini option" for key in ini if key.startswith("typeguard-")],
    }
    low, high = (".".join(map(str, bound)) for bound in VERIFIED)
    loaded = [
        (name, dist)
        for name, dist in installed_plugins()
        if name not in blocked and not (name.startswith("pytest_") and name[7:] in blocked)
    ]
    on = [
        f"the pytest plugin {name} ({dist}) acts on {why}, and rustest does not run it"
        for name, dist in loaded
        for why in switched_on.get(name, ())
    ]
    return on + [
        (
            f"hypothesis's pytest plugin ({dist}) is installed, and rustest emulates it only"
            + f" for hypothesis {low} to {high}.x and later releases whose plugin is unchanged"
            if name == "hypothesispytest"
            else f"the pytest plugin {name} ({dist}) is installed and rustest does not run it"
            + f" (`-p no:{name}` disables it)"
        )
        for name, dist in loaded
        if name not in SAFE_PLUGINS or (name == "hypothesispytest" and not supported(dist))
    ]


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def pytest_major() -> int | None:
    """The installed pytest's major version, read from its ``*.dist-info`` directory name on
    ``sys.path`` (not ``importlib.metadata``, ~0.2 s cold: see :func:`installed_plugins`)."""
    for entry in sys.path:
        try:
            names = os.listdir(entry or ".")
        except OSError:
            continue
        if "_pytest" not in names:  # a stale dist-info without the package answers nothing
            continue
        for name in names:
            if name.startswith("pytest-") and name.endswith(".dist-info"):
                head = name[len("pytest-") :].split(".")[0]
                if head.isdigit():  # not `pytest-asyncio-1.3.0.dist-info`
                    return int(head)
        # The package with no dist-info beside it (a source checkout, an editable install):
        # its generated `_version.py` says, as text (`version = '9.0.2'`).
        try:
            with open(os.path.join(entry or ".", "_pytest", "_version.py"), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return None
        match = re.search(r"""\bversion\s*=\s*['"](\d+)\.""", text)
        return int(match.group(1)) if match else None
    return None


def pytest_available() -> bool:
    return _installed("pytest")


def pytest_argv(argv: Sequence[str]) -> tuple[list[str], bool]:
    """The user's own arguments as pytest spells them, and whether ``-n`` had to be dropped.

    Everything not named here goes through verbatim and pytest accepts or rejects it, so a
    typo gets pytest's own usage error (exit 4).
    """
    xdist = _installed("xdist")
    out: list[str] = []
    dropped_n = False
    i = 0
    while i < len(argv):
        token = argv[i]
        i += 1
        name, eq, value = token.partition("=") if token.startswith("--") else (token, "", "")
        if name in _DROPPED:
            i += _DROPPED[name] and not eq  # `--report-json PATH`: the value goes with it
            continue
        if token == "--no-capture":
            out.append("-s")
        elif name in ("--pattern", "--marks", "--color", "--workers") or token == "-n":
            if not eq and i < len(argv):
                value, i = argv[i], i + 1
            if name == "--color":  # pytest's choices are yes/no/auto
                out.append("--color=" + {"always": "yes", "never": "no"}.get(value, value))
            elif name in ("--pattern", "--marks"):
                out += ["-k" if name == "--pattern" else "-m", value]
            elif xdist:
                out += ["-n", value]
            else:
                dropped_n = True
        elif token.startswith("-n") and not token.startswith("--"):  # `-n4`, `-nauto`
            if xdist:
                out.append(token)
            else:
                dropped_n = True
        else:
            out.append(token)
    return out, dropped_n


def _refuse(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return _EXIT_USAGE_ERROR


def _wait(child: subprocess.Popen[bytes]) -> int:
    """The child's exit code.  The console sends Ctrl-C to both processes, so the first one
    is pytest's to answer (it prints its own summary); a second means "stop now"."""
    return _wait_interruptible(child)[0]


def _wait_interruptible(child: subprocess.Popen[bytes]) -> tuple[int, bool]:
    """:func:`_wait`, and whether this process was interrupted (Ctrl-C) while it waited.

    Off the main thread (B-lite's handed run, started while the pool collects) no signal
    reaches Python here: the engine watches for Ctrl-C itself and stops the child
    (:func:`stop_handed`)."""
    import signal
    import threading

    interrupted = False
    main = threading.current_thread() is threading.main_thread()

    # A SIGTERM aimed at this process alone (`timeout`, a CI cancel) must not orphan pytest.
    def forward(signum: int, frame: object) -> None:
        del signum, frame
        child.terminate()

    previous = signal.signal(signal.SIGTERM, forward) if main else None
    try:
        while True:
            try:
                code = child.wait()
                return (128 - code if code < 0 else code), interrupted  # by signal N: 128+N
            except KeyboardInterrupt:
                if interrupted:
                    child.terminate()
                elif not _WINDOWS:
                    # its own process group: the terminal's Ctrl-C did not reach it
                    os.killpg(child.pid, signal.SIGINT)
                interrupted = True
    finally:
        if previous is not None:
            _ = signal.signal(signal.SIGTERM, previous)


#: B-lite's handed pytest while it runs, so :func:`stop_handed` can end it and everything it
#: started: Windows by parent links (``taskkill /T``), elsewhere its own process group.
_running: set[subprocess.Popen[bytes]] = set()
#: What :func:`stop_handed` ended: its `run_files` answers ``None`` without a word.
_stopped: set[subprocess.Popen[bytes]] = set()
#: Set by :func:`stop_handed`: a handed run on another thread that has not started its child
#: yet must not start it (the engine's early run, stopped before it got that far).
_stopping = False
_lock = threading.Lock()


def begin_handed() -> None:
    """A new session's handed runs: forget the last session's stop."""
    global _stopping
    with _lock:
        _stopping = False


def stop_handed() -> None:
    """End the handed pytest and its whole process tree (a ``runpytest_subprocess`` child
    included): the session went to pytest whole while it ran, or the user pressed Ctrl-C."""
    global _stopping
    with _lock:
        _stopping = True
        running = list(_running)
        _stopped.update(running)
    for child in running:
        _kill_tree(child)


def _kill_tree(child: subprocess.Popen[bytes]) -> None:
    import signal
    import subprocess

    if child.poll() is not None:
        return
    if _WINDOWS:
        _ = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(child.pid)], capture_output=True, check=False
        )
    else:
        try:
            os.killpg(child.pid, signal.SIGKILL)  # its own session: the whole group
        except OSError:
            pass
    _ = child.wait()


def _deliver(
    path: str,
    code: int,
    reasons: Sequence[str],
    *,
    report_json: str | None,
    llm: bool,
    llm_full: bool,
    collect_only: bool,
    verbosity: int,
) -> int:
    """Turn what ``rustest._pytest_report`` wrote into the output rustest was asked for."""
    import json

    try:
        with open(path, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError):  # pytest stopped before it could report (a usage error)
        print(f"rustest: pytest wrote no report (exit code {code}).", file=sys.stderr)
        return code
    report["exit_code"] = code
    report["pytest_fallback"] = list(reasons)
    collected = report.pop("collected")
    if report_json is not None:
        with open(report_json, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(report, indent=2))
    if llm:
        from ._llm import render

        render(report, verbosity=verbosity, full=llm_full)
        sys.stdout.flush()
    if collect_only:
        from .core import report_collection

        _ = report_collection(
            collected, report["collection_errors"], report["summary"]["deselected"]
        )
    return code


def run_files(
    argv: Sequence[str],
    files: Sequence[str],
    cache_dir: str,
    *,
    rootdir: str,
    inifile: str | None,
    worker: tuple[str, int, str, bool] | None = None,
) -> str | None:
    """B-lite: run *files* -- test modules that use pytest's ``pytester`` -- under one pytest
    subprocess, before the native run, and answer with ``rustest._pytest_report``'s report plus
    ``exit_code`` (``None``: pytest wrote none, and the whole session goes to pytest).

    pytest gets the user's own arguments with their targets already removed (*argv*), without
    ``-n`` (one process), the files instead, and the session's rootdir, ini file and cache
    directory, so node ids, configuration and ``--lf`` agree with the native run's. Its terminal
    output is kept back: the run renders these results with its own. pytest's ``nfplugin`` is
    off: its ``cache/nodeids`` would list the handed files only, which rustest never writes.

    With *worker* -- ``(worker id, worker count, testrun uid, records)``, the session under the
    xdist model -- pytest runs as one more xdist worker, in one process (``-n0``, as an xdist
    worker is: a project's ``addopts = -n auto`` would make it a controller): the identity in
    its environment, ``config.workerinput`` set by the report plugin before any conftest loads,
    and, with *records* (the session has a controller), its phase reports for that controller.
    """
    import contextlib
    import json
    import subprocess
    import tempfile

    if not pytest_available():
        return None
    translated, _ = pytest_argv(argv)
    serial: list[str] = []
    skip = False
    for token in translated:
        if skip:
            skip = False
        elif token == "-n":
            skip = True
        elif not (token.startswith("-n") and not token.startswith("--")):
            serial.append(token)
    print(
        f"rustest: running {len(files)} file(s) under pytest first (they use pytester): "
        + ", ".join(os.path.relpath(f, rootdir) for f in files),
        file=sys.stderr,
        flush=True,
    )
    with contextlib.ExitStack() as stack:
        scratch = stack.enter_context(tempfile.TemporaryDirectory(prefix="rustest-handed-"))
        report = os.path.join(scratch, "report.json")
        ini = inifile
        confcutdir: list[str] = []
        if ini is None:  # no ini: an empty one keeps pytest from finding one nearer the files,
            ini = os.path.join(scratch, "pytest.ini")  # and conftests above the rootdir stay out
            with open(ini, "w", encoding="utf-8") as handle:
                _ = handle.write("[pytest]\n")
            confcutdir = [f"--confcutdir={rootdir}"]
        output = stack.enter_context(tempfile.TemporaryFile())
        command = [
            sys.executable,
            "-m",
            "pytest",
            *confcutdir,  # before the user's own, so theirs wins
            *serial,
            f"--rootdir={rootdir}",
            "-c",
            ini,
            "-o",
            f"cache_dir={cache_dir}",
            "-p",
            "rustest._pytest_report",
            "-p",
            "no:nfplugin",
            # ponytail: xdist importable but not loaded (`-p no:xdist` in addopts,
            # PYTEST_DISABLE_PLUGIN_AUTOLOAD) makes `-n0` a usage error and the session goes
            # to pytest whole; setting `config.option.dist` from the report plugin would not.
            # One process in every mode: without a worker identity too (a pool of one under the
            # xdist model, `addopts = -n auto`), or it would become an xdist controller.
            *(["-n0"] if _installed("xdist") else []),
            *files,
        ]
        early = threading.current_thread() is not threading.main_thread()
        if early and _stopping:
            return None
        child = subprocess.Popen(
            command,
            env={
                **os.environ,
                "RUSTEST_PYTEST_REPORT": report,
                "RUSTEST_HANDED_WATCH": "1",
                **_worker_env(worker),
            },
            # A pipe nobody writes: its end of file means rustest is gone (killed, crashed),
            # and the report plugin then ends this pytest's whole tree.
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.STDOUT,
            # Its own process group, so `stop_handed` can end the whole tree. Ctrl-Z no longer
            # stops it with rustest and it has no `/dev/tty`; pytester's `spawn` makes its own.
            start_new_session=not _WINDOWS,
        )
        with _lock:
            _running.add(child)
            stop_now = early and _stopping
        try:
            if stop_now:  # stopped while it was being started
                _stopped.add(child)
                _kill_tree(child)
            code, interrupted = _wait_interruptible(child)
        finally:
            with _lock:
                _running.discard(child)
                stopped = child in _stopped
                _stopped.discard(child)
            if child.stdin is not None:
                child.stdin.close()
        if stopped:
            return None  # ended on purpose: its partial output is nobody's business
        if interrupted:
            # The user pressed Ctrl-C: the session ends here, exit 2, and nothing is handed on
            # to a whole pytest run (`py.rs` re-raises a callback's exception).
            raise KeyboardInterrupt
        try:
            with open(report, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            _ = output.seek(0)
            _ = sys.stderr.write(output.read().decode(errors="replace"))
            return None
    data["exit_code"] = code
    return json.dumps(data)


def _worker_env(worker: tuple[str, int, str, bool] | None) -> dict[str, str]:
    """xdist's worker environment (`remote.py`), and the flags the report plugin keys on."""
    if worker is None:
        return {}
    workerid, count, uid, records = worker
    return {
        "PYTEST_XDIST_WORKER": workerid,
        "PYTEST_XDIST_WORKER_COUNT": str(count),
        "PYTEST_XDIST_TESTRUNUID": uid,
        "RUSTEST_HANDED_WORKER": "1",
        **({"RUSTEST_HANDED_RECORDS": "1"} if records else {}),
    }


def run(
    argv: Sequence[str],
    reasons: Sequence[str],
    optout: str | None = None,
    *,
    report_json: str | None = None,
    llm: bool = False,
    llm_full: bool = False,
    collect_only: bool = False,
    verbosity: int = 0,
    cache_dir_set: bool = False,
    workers: int | None = None,
) -> int:
    """Run ``python -m pytest`` for *argv* (the user's own, before any ``addopts`` splice).

    *optout* says what ``--no-pytest-fallback`` would do about *reasons*, when that is a
    rustest run rather than a usage error; ``None`` leaves the sentence out.

    ``--report-json``, ``--llm`` and ``--collect-only`` (the keywords) are taken from a
    bundled pytest plugin's report instead of pytest's own output.  Under ``--llm`` pytest's
    stdout goes to stderr so that stdout is the JSONL stream and nothing else; under
    ``--collect-only`` it is dropped, and the ids are printed in rustest's format.
    """
    import contextlib
    import subprocess
    import tempfile

    why = "; ".join(reasons)
    anyway = (
        f" Pass --no-pytest-fallback to run it with rustest anyway ({optout})." if optout else ""
    )
    if llm and collect_only:
        return _refuse("--llm and --collect-only ask for different things; drop one.")
    # A fence only rustest's collector runs: pytest-codeblocks would skip nothing the
    # `<!--rustest.mark.skip-->` marker marks and number the nodes differently.
    if any(not t.startswith("-") and t.partition("::")[0].lower().endswith(".md") for t in argv):
        return _refuse(
            f"this session needs pytest: {why}. pytest cannot run rustest's documentation code"
            + f" blocks (a .md argument).{anyway}"
        )
    if not pytest_available():
        return _refuse(
            f"this session needs pytest: {why}. pytest is not installed here. Install it,"
            + f" or pass --no-pytest-fallback to run without that ({optout or 'results may differ'})."
        )
    translated, dropped_n = pytest_argv(argv)
    # The parallelism rustest would have used, so a handed-off suite is not slower than the
    # run it replaces: pytest-xdist's `-n`, when it is installed and nothing chose otherwise.
    if workers and workers > 1 and _installed("xdist"):
        translated = ["-n", str(workers), *translated]
    serial = (
        " -n was ignored because pytest-xdist is not installed, so it runs serially."
        if dropped_n
        else ""
    )
    print(
        f"rustest: handing this session to pytest: {why}. It will run at pytest's speed."
        + f"{serial}{anyway}",
        file=sys.stderr,
        flush=True,
    )
    # `-o cache_dir=` makes pytest's cache the one rustest already writes, so `--lf`/`--ff`
    # agree across the two runners; a project whose ini names one (*cache_dir_set*) has both
    # runners there already.  Argv is a list (no shell); cwd and environment are the caller's.
    command = [
        sys.executable,
        "-m",
        "pytest",
        *translated,
        *([] if cache_dir_set else ["-o", "cache_dir=.rustest_cache"]),
    ]
    if not (report_json or llm or collect_only):
        if not _WINDOWS:
            # pytest *becomes* this process: signals, stdin, process group and exit code are its own.
            sys.stdout.flush()
            os.execv(sys.executable, command)
        return _wait(subprocess.Popen(command))
    handle, path = tempfile.mkstemp(prefix="rustest-report-", suffix=".json")
    os.close(handle)
    # `--llm` reads each failure's message, so it needs a traceback style it can count on,
    # whatever `--tb` the user's own arguments say (pytest's stdout is on stderr under `--llm`).
    plugin = [*command, "-p", "rustest._pytest_report", *(["--tb=long"] if llm else [])]
    # `--collect-only`: pytest's tree is noise (the ids come from the plugin), but an
    # INTERNALERROR is written there too, so it is kept and shown if pytest dies (exit 3).
    try:
        with tempfile.TemporaryFile() as tree:
            sys.stdout.flush()
            child = subprocess.Popen(
                plugin,
                env={**os.environ, "RUSTEST_PYTEST_REPORT": path},
                stdout=sys.stderr if llm else tree if collect_only else None,
            )
            code = _wait(child)
            if collect_only and code == 3:
                _ = tree.seek(0)
                _ = sys.stderr.write(tree.read().decode(errors="replace"))
        return _deliver(
            path,
            code,
            reasons,
            report_json=report_json,
            llm=llm,
            llm_full=llm_full,
            collect_only=collect_only,
            verbosity=verbosity,
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)
