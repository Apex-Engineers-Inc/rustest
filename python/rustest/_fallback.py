"""Hand a whole session to real pytest, for what rustest does not run.

A session is either entirely rustest's or entirely pytest's: a conftest hook such as
``pytest_collection_modifyitems`` sees every directory's items, so a split run would be
wrong, not just awkward.  pytest is started as a child with the user's own arguments and
streams its output unmodified; its exit code is returned verbatim.

Imported only when a run is being handed over, never on the ``import rustest`` path.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Sequence

#: rustest-only flags pytest does not have: value-taking ones consume a following token.
_DROPPED = {
    "--report-json": True,
    "--llm": False,
    "--llm-full": False,
    "--ascii": False,
    "--no-pytest-fallback": False,
    "--no-codeblocks": False,
}

#: Outputs that are a rustest format and cannot yet be taken from a pytest run.
_MACHINE = ("--report-json", "--llm", "--collect-only", "--co")

_EXIT_USAGE_ERROR = 4


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
            if _DROPPED[name] and not eq:
                i += 1
        elif name == "--codeblocks":
            if _installed("pytest_codeblocks"):
                out.append(token)
        elif token == "--no-capture":
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


def run(argv: Sequence[str], reasons: Sequence[str]) -> int:
    """Run ``python -m pytest`` for *argv* (the user's own, before any ``addopts`` splice)."""
    why = "; ".join(reasons)
    if any(token == flag or token.startswith(flag + "=") for token in argv for flag in _MACHINE):
        print(
            f"ERROR: {why}, so this session needs pytest, and --report-json, --llm and"
            + " --collect-only are not supported under pytest fallback yet."
            + " Pass --no-pytest-fallback to run it with rustest instead, without them"
            + " (results may differ from pytest).",
            file=sys.stderr,
        )
        return _EXIT_USAGE_ERROR
    if not pytest_available():
        print(
            f"ERROR: {why}. rustest cannot run these and pytest is not installed here."
            + " Install pytest, or pass --no-pytest-fallback to run without them"
            + " (results may differ from pytest).",
            file=sys.stderr,
        )
        return _EXIT_USAGE_ERROR
    translated, dropped_n = pytest_argv(argv)
    serial = (
        " -n was ignored because pytest-xdist is not installed, so it runs serially."
        if dropped_n
        else ""
    )
    print(
        f"rustest: running this session under pytest, because {why}. It runs at pytest's"
        + f" speed.{serial} --no-pytest-fallback runs it with rustest instead, without them.",
        file=sys.stderr,
        flush=True,
    )
    # `-o cache_dir=` makes pytest's cache the one rustest already writes, so `--lf`/`--ff`
    # agree across the two runners.  Argv is a list (no shell), the cwd and environment are
    # the caller's, and stdin is inherited.
    child = subprocess.Popen(
        [sys.executable, "-m", "pytest", *translated, "-o", "cache_dir=.rustest_cache"]
    )
    # The terminal sends Ctrl-C to the whole foreground group, so pytest already has it, and
    # a second one from us would mean "abort now" to it.  Keep waiting for its own summary.
    while True:
        try:
            code = child.wait()
            break
        except KeyboardInterrupt:
            continue
    return 128 - code if code < 0 else code
