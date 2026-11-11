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
import subprocess
import sys
from collections.abc import Sequence

#: rustest-only flags pytest does not have; the value says whether one is consumed too.
#: ``--codeblocks`` is dropped, not translated: a ``.md`` argument is refused below, and
#: without one the flag has nothing to act on.
_DROPPED = {
    "--llm-full": False,
    "--ascii": False,
    "--no-pytest-fallback": False,
    "--codeblocks": False,
    "--no-codeblocks": False,
}

#: Outputs that are a rustest format and cannot yet be taken from a pytest run.
_MACHINE = ("--report-json", "--llm", "--collect-only", "--co")

_EXIT_USAGE_ERROR = 4
_WINDOWS = sys.platform == "win32"


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


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


def run(argv: Sequence[str], reasons: Sequence[str], optout: str | None = None) -> int:
    """Run ``python -m pytest`` for *argv* (the user's own, before any ``addopts`` splice).

    *optout* says what ``--no-pytest-fallback`` would do about *reasons*, when that is a
    rustest run rather than a usage error; ``None`` leaves the sentence out.
    """
    why = "; ".join(reasons)
    anyway = (
        f" Pass --no-pytest-fallback to run it with rustest anyway ({optout})." if optout else ""
    )
    if any(token == flag or token.startswith(flag + "=") for token in argv for flag in _MACHINE):
        return _refuse(
            f"this session needs pytest: {why}. --report-json, --llm and --collect-only are"
            + f" not supported under pytest fallback yet.{anyway}"
        )
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
    # agree across the two runners.  Argv is a list (no shell); cwd and environment are the
    # caller's.
    command = [sys.executable, "-m", "pytest", *translated, "-o", "cache_dir=.rustest_cache"]
    if not _WINDOWS:
        # pytest *becomes* this process: signals, stdin, process group and exit code are its own.
        sys.stdout.flush()
        os.execv(sys.executable, command)
    child = subprocess.Popen(command)
    # The console sends Ctrl-C to both processes, so the first one is pytest's to answer
    # (it prints its own summary); a second means "stop now".
    interrupted = False
    while True:
        try:
            return child.wait()
        except KeyboardInterrupt:
            if interrupted:
                child.terminate()
            interrupted = True
