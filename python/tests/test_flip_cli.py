"""The flip: ``rustest <paths>`` is the v2 engine, and the CLI surface that came with it.

Phase 1c Task 1 moved the default from v1 to v2 and filled in the option surface a default
engine has to have: ``-x``, ``--lf``/``--ff``, ``-v``, ``-q``, ``-s``, ``--no-codeblocks``.
This module pins the *observable* half of all of it — what the process does, not what the
code looks like — and diffs the semantic claims against **real pytest** in a subprocess, the
same discipline ``test_run_cli.py`` uses.

Everything here drives the real binary end to end. A mocked ``run`` would have proved the
router forwards a flag and nothing about whether the flag works.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

# The compiled extension is built and installed by `python/tests/__init__.py`.
from rustest import cli


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for leak in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST", "RUSTEST_RUNNING"):
        _ = env.pop(leak, None)
    return env


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, env=_clean_env(), check=False
    )


def _rustest(tree: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """The **default** invocation — no mode flag, which is the whole point of this module."""
    return _run([sys.executable, "-m", "rustest", *args], tree)


#: pytest's ``in <n>s`` tail, in both spellings ``format_session_duration`` produces
#: (``in 0.05s`` and, above a minute, ``in 61.00s (0:01:01)``).
_DURATION_TAIL = re.compile(r"\s+in \d[\d.]*s(?: \(\d+:\d\d:\d\d\))?$")


def _summary_line(stderr: str) -> str:
    """The last stderr line with its duration tail removed.

    Task 2 gave the summary pytest's ``in <n>s`` tail. Everything *before* the tail is still
    byte-identical to pytest's own summary line and is what these assertions are about; the
    duration is a wall clock and cannot be compared to a literal. Stripping it here rather
    than asserting a prefix keeps the comparison exact — a trailing bucket that should not be
    there still fails.
    """
    return _DURATION_TAIL.sub("", stderr.strip().splitlines()[-1])


def _pytest(tree: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return _run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider", *args], tree
    )


def _tree(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    """An isolated tree with its own ``pytest.ini``.

    Without one, both runners walk *out* of ``tmp_path`` and land on this repository's
    ``pyproject.toml``, which makes rootdir the repo root and every node id repo-relative.
    """
    tree = tmp_path / name
    _write(tree / "pytest.ini", "[pytest]\n")
    for rel, body in files.items():
        _write(tree / rel, body)
    return tree


#: Four tests, the second and third failing.  The shape every ``-x`` claim below is measured
#: on, and the same one probed against pytest 8.4.2 (``1 failed, 1 passed``, exit 1, no
#: mention of ``test_c`` or ``test_d``).
FOUR_WITH_FAILURES = """\
def test_a():
    assert True


def test_b():
    assert 0


def test_c():
    assert 0


def test_d():
    assert True
"""


# --------------------------------------------------------------------------------------
# The flip itself
# --------------------------------------------------------------------------------------


def test_a_bare_invocation_runs_the_v2_engine(tmp_path: Path) -> None:
    """No mode flag -> v2.

    Told apart from v1 by something only v2 does: v1's renderer prints a per-file progress
    table and a ``✓ N passed`` line, v2 prints pytest's bare ``N passed`` on **stderr** and
    leaves stdout empty on a green run.  Asserting the empty stdout as well as the summary is
    what makes this a positive identification rather than a substring coincidence.
    """
    tree = _tree(tmp_path, "flip", {"test_ok.py": "def test_one():\n    assert True\n"})

    result = _rustest(tree, [])

    assert result.returncode == 0, result.stderr
    assert result.stdout == "", f"a green v2 run writes nothing to stdout: {result.stdout!r}"
    assert _summary_line(result.stderr) == "1 passed", result.stderr


def test_the_v2_flag_is_gone_and_is_not_a_removed_flag(tmp_path: Path) -> None:
    """``--v2`` was scaffolding, and it left before 1.0.0 rather than being frozen in.

    It was a no-op alias while the engine name still distinguished something. It never
    appeared in a released version, so nothing outside this repository can have run it —
    which is why it is **not** in ``REMOVED_FLAGS`` alongside ``--v1`` and
    ``--pytest-compat``. Those two earn a message naming the change because real CI files
    pass them; this one only ever needed to stop existing. Ordinary argparse rejection is
    the correct outcome, and this pins that it is still a *rejection* — exit 4, not a
    silently swallowed path argument, which is what ``nargs="*"`` would otherwise do.
    """
    tree = _tree(tmp_path, "alias", {"test_ok.py": "def test_one():\n    assert True\n"})

    with_flag = _rustest(tree, ["--v2"])
    without = _rustest(tree, [])

    assert without.returncode == 0, without.stderr
    assert with_flag.returncode == 4, with_flag.stderr
    assert "unrecognized arguments: --v2" in with_flag.stderr, with_flag.stderr


def test_the_v1_flag_is_refused_and_says_what_replaced_it(tmp_path: Path) -> None:
    """``--v1`` selected the legacy engine until Phase 4 Task 2 deleted it.

    It is a **removed flag** now, not an unrecognised one, and the difference is the whole
    point: argparse's "unrecognized arguments: --v1" tells a reader whose CI has passed the
    flag for months nothing about what to do. Exit 4 is pytest's ``USAGE_ERROR``; 2 already
    means "collection error" in this CLI.
    """
    tree = _tree(tmp_path, "legacy", {"test_ok.py": "def test_one():\n    assert True\n"})

    result = _rustest(tree, ["--v1", "--color", "never"])

    assert result.returncode == 4, result.stdout + result.stderr
    assert "--v1 has been removed" in result.stderr, result.stderr
    assert "CHANGELOG" in result.stderr, result.stderr


def test_pytest_compat_is_rejected_with_pytests_usage_exit(tmp_path: Path) -> None:
    """The deleted flag exits **4**, not argparse's 2.

    2 already means "collection error" in this CLI's contract, so reusing it for a usage
    error would make a typo indistinguishable from a broken import.  4 is pytest's
    ``ExitCode.USAGE_ERROR``.
    """
    tree = _tree(tmp_path, "compat", {"test_ok.py": "def test_one():\n    assert True\n"})

    result = _rustest(tree, ["--pytest-compat"])

    assert result.returncode == 4, result.stdout + result.stderr
    assert "--pytest-compat has been removed" in result.stderr, result.stderr
    assert "CHANGELOG" in result.stderr, result.stderr


def test_pytest_compat_is_rejected_before_it_can_be_read_as_a_path(tmp_path: Path) -> None:
    """The check runs on raw argv, ahead of argparse.

    ``paths`` is ``nargs="*"``, so a flag argparse does not know could otherwise be swallowed
    as a path argument in some future refactor and produce ``file or directory not found:
    --pytest-compat`` — technically an error, and a completely useless one.
    """
    tree = _tree(tmp_path, "compat2", {"test_ok.py": "def test_one():\n    assert True\n"})

    result = _rustest(tree, ["--pytest-compat=yes"])

    assert result.returncode == 4, result.stdout + result.stderr
    assert "has been removed" in result.stderr, result.stderr


# --------------------------------------------------------------------------------------
# -x / --exitfirst
# --------------------------------------------------------------------------------------


def test_exitfirst_stops_after_the_first_failure_exactly_as_pytest_does(tmp_path: Path) -> None:
    """Differential: both engines run the same tree with ``-x`` and must agree on all three
    observable claims — how many ran, which ones, and the exit code."""
    tree = _tree(tmp_path, "xfirst", {"test_x.py": FOUR_WITH_FAILURES})
    report = tree / "report.json"

    oracle = _pytest(tree, ["-x"])
    ours = _rustest(tree, ["-x", "-n", "1", "--report-json", str(report)])

    assert oracle.returncode == 1, oracle.stdout
    assert ours.returncode == oracle.returncode, ours.stdout + ours.stderr

    # pytest's summary for this tree is `1 failed, 1 passed`.
    assert "1 failed, 1 passed" in oracle.stdout, oracle.stdout
    assert _summary_line(ours.stderr) == "1 failed, 1 passed", ours.stderr

    payload: dict[str, object] = json.loads(report.read_text(encoding="utf-8"))
    tests = payload["tests"]
    assert isinstance(tests, list)
    assert [entry["id"] for entry in tests] == ["test_x.py::test_a", "test_x.py::test_b"]
    assert payload["stopped_early"] is True
    # ...and the tail is *absent*, not reported as skipped: pytest never mentions it either.
    assert "test_c" not in ours.stdout and "test_c" not in ours.stderr


def test_exitfirst_says_it_stopped(tmp_path: Path) -> None:
    """pytest prints ``!!!! stopping after 1 failures !!!!``; without an equivalent line a
    truncated run is indistinguishable from a suite that simply has fewer tests."""
    tree = _tree(tmp_path, "xnotice", {"test_x.py": FOUR_WITH_FAILURES})

    result = _rustest(tree, ["-x", "-n", "1"])

    assert "stopping after 1 failures" in result.stderr, result.stderr


def test_exitfirst_on_a_green_tree_changes_nothing(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path, "xgreen", {"test_g.py": "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n"}
    )

    result = _rustest(tree, ["-x", "-n", "1"])

    assert result.returncode == 0, result.stderr
    assert _summary_line(result.stderr) == "2 passed"
    assert "stopping after" not in result.stderr


# --------------------------------------------------------------------------------------
# --lf / --ff
# --------------------------------------------------------------------------------------


def test_last_failed_reruns_only_the_failures_and_deselects_the_rest(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "lf", {"test_x.py": FOUR_WITH_FAILURES})

    first = _rustest(tree, [])
    assert first.returncode == 1, first.stderr
    # Hoisted out of the assert so that both the repo's ruff and pre-commit's pinned one
    # format this identically -- they disagree about multi-line assert messages.
    lastfailed = tree / ".rustest_cache" / "v" / "cache" / "lastfailed"
    assert lastfailed.is_file(), "no cache was written"

    second = _rustest(tree, ["--lf"])

    assert _summary_line(second.stderr) == "2 failed, 2 deselected", second.stderr
    assert second.returncode == 1


def test_failed_first_keeps_everything_and_reorders(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "ff", {"test_x.py": FOUR_WITH_FAILURES})
    report = tree / "report.json"

    _ = _rustest(tree, [])
    second = _rustest(tree, ["--ff", "-n", "1", "--report-json", str(report)])

    payload: dict[str, object] = json.loads(report.read_text(encoding="utf-8"))
    tests = payload["tests"]
    assert isinstance(tests, list)
    assert [entry["id"] for entry in tests] == [
        "test_x.py::test_b",
        "test_x.py::test_c",
        "test_x.py::test_a",
        "test_x.py::test_d",
    ]
    assert _summary_line(second.stderr) == "2 failed, 2 passed"


def test_the_v2_cache_is_not_v1s(tmp_path: Path) -> None:
    """Separate files, because the two engines' node ids are different strings.

    v1 writes ``.rustest_cache/lastfailed`` keyed on native-separator display names; v2
    writes ``.rustest_cache/v/cache/lastfailed`` keyed on rootdir-relative posix ids --
    pytest's own ``<cachedir>/v/<key>`` value layout, so the ``cache`` fixture reaches the
    same file through ``cache.get("cache/lastfailed", {})``.  One shared
    file would mean every ``--lf`` after an engine switch matched nothing — silently, and
    worst on Windows.
    """
    tree = _tree(tmp_path, "cachesplit", {"test_x.py": FOUR_WITH_FAILURES})

    _ = _rustest(tree, [])
    v2_cache = tree / ".rustest_cache" / "v" / "cache" / "lastfailed"
    assert v2_cache.is_file()
    assert not (tree / ".rustest_cache" / "lastfailed").exists()

    entries: dict[str, bool] = json.loads(v2_cache.read_text(encoding="utf-8"))
    assert entries == {"test_x.py::test_b": True, "test_x.py::test_c": True}


def test_the_cache_fixture_reads_the_last_failed_set(tmp_path: Path) -> None:
    """``cache.get("cache/lastfailed", {})`` answers with what ``--lf`` just wrote.

    This is the whole reason the last-failed file moved into pytest's ``v/<key>`` value
    layout at Phase 3 Task 2 (``src/engine/cache.rs``): under pytest the last-failed set is not a
    private file, it is an ordinary cache value, and ``config.cache`` is the documented way to
    read it. Asserted **through the fixture**, in a second run, so it exercises the path
    composition on both sides rather than a constant copied into two places — a rename on
    either side breaks this and nothing else would.
    """
    tree = _tree(tmp_path, "cachefixture", {"test_x.py": FOUR_WITH_FAILURES})
    first = _rustest(tree, [])
    assert first.returncode == 1, first.stderr

    reader = tree / "test_reader.py"
    _ = reader.write_text(
        "def test_reads_the_lf_set(cache):\n"
        '    entries = cache.get("cache/lastfailed", None)\n'
        '    assert entries == {"test_x.py::test_b": True, "test_x.py::test_c": True}, entries\n',
        encoding="utf-8",
    )

    second = _rustest(tree, ["-n", "1", str(reader)])
    assert second.returncode == 0, second.stderr + second.stdout


def test_last_failed_with_an_empty_cache_runs_everything(tmp_path: Path) -> None:
    """pytest's own carve-out: ``--lf`` on a first run is not ``--lf`` on nothing."""
    tree = _tree(tmp_path, "lfempty", {"test_x.py": FOUR_WITH_FAILURES})

    result = _rustest(tree, ["--lf"])

    assert _summary_line(result.stderr) == "2 failed, 2 passed", result.stderr


# --------------------------------------------------------------------------------------
# -v / -q
# --------------------------------------------------------------------------------------


MIXED = """\
import pytest


def test_pass():
    assert True


def test_fail():
    assert 0


@pytest.mark.skip(reason='nope')
def test_skip():
    pass


@pytest.mark.xfail(reason='known')
def test_xfail():
    assert False
"""


def test_verbose_prints_one_line_per_test_in_pytests_wording(tmp_path: Path) -> None:
    """``PASSED``/``FAILED``/``SKIPPED (reason)``/``XFAIL (reason)`` — probed from
    ``pytest -v`` on this exact file, whose verbose column uses ``XFAIL`` and not the
    summary's ``xfailed``."""
    tree = _tree(tmp_path, "verbose", {"test_m.py": MIXED})

    result = _rustest(tree, ["-v", "-n", "1"])

    # The progress lines are the ones carrying the percent column; the `FAILED` block that
    # follows them also mentions a node id, which is why the filter is on the column.
    progress = [line for line in result.stdout.splitlines() if re.search(r"\[\s*\d+%\]$", line)]
    words = [re.sub(r"\s+\[\s*\d+%\]$", "", line) for line in progress]
    assert words == [
        "test_m.py::test_pass PASSED",
        "test_m.py::test_fail FAILED",
        "test_m.py::test_skip SKIPPED (nope)",
        "test_m.py::test_xfail XFAIL (known)",
    ], result.stdout
    # pytest's right-hand progress column, reproduced — and it really does reach 100%.
    assert progress[-1].endswith("[100%]"), progress


def test_quiet_keeps_the_failure_report_and_matches_the_default_rung(tmp_path: Path) -> None:
    """``-q`` suppresses the progress lines, and rustest's default rung has none to suppress.

    This test used to assert ``quiet.stdout == ""`` under the name
    ``test_quiet_prints_only_the_summary``. That was the behaviour ``e0dc4a8`` deliberately
    removed: ``_print_failure_sections`` was gated on ``verbosity >= 0``, so ``rustest -q``
    on a red run named nothing that failed and carried no traceback. pytest's ``-q`` drops
    the session banner and condenses progress but **keeps the failure report**, so the gate
    went and the report now prints at every rung.

    What is left of ``-q`` is the progress lines — and rustest's *default* rung does not
    print any. It has no session banner, no ``collected N items`` line and no ``.F.s.x``
    column, because there is no ``isatty`` call in either layer and the output is the same
    piped or on a terminal. Only ``-v`` adds the per-test lines. So ``-q`` and the default
    rung agree on stdout, and asserting that is the honest pin: it fails if the report is
    ever re-gated, and it fails if the default rung ever grows a preamble that ``-q``
    should have dropped.

    The cross-runner half — that these section titles match a real ``pytest -q`` — is
    ``test_run_cli.py::test_quiet_still_reports_failures_like_pytest``.
    """
    tree = _tree(tmp_path, "quiet", {"test_m.py": MIXED})

    quiet = _rustest(tree, ["-q", "-n", "1"])
    default = _rustest(tree, ["-n", "1"])

    # The diagnosis survives -q. This is the assertion e0dc4a8's fix exists for.
    assert "FAILED test_m.py::test_fail" in quiet.stdout, quiet.stdout
    assert "AssertionError: assert 0" in quiet.stdout, quiet.stdout
    assert _summary_line(quiet.stderr) == "1 failed, 1 passed, 1 skipped, 1 xfailed"

    # -q and the default rung are indistinguishable on stdout: there is no preamble and no
    # progress column at verbosity 0 for -q to take away.
    assert quiet.stdout == default.stdout, f"-q: {quiet.stdout!r}\ndefault: {default.stdout!r}"

    # ...and the rung that *does* differ is -v, or the equality above proves nothing.
    verbose = _rustest(tree, ["-v", "-n", "1"])
    assert "test_m.py::test_pass PASSED" in verbose.stdout, verbose.stdout
    assert "PASSED" not in quiet.stdout, quiet.stdout


def test_verbose_and_quiet_cancel_out_as_they_do_under_pytest(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "vq", {"test_m.py": MIXED})

    result = _rustest(tree, ["-v", "-q", "-n", "1"])

    assert "PASSED" not in result.stdout, result.stdout
    assert "FAILED test_m.py::test_fail" in result.stdout, result.stdout


# --------------------------------------------------------------------------------------
# -s / --no-capture
# --------------------------------------------------------------------------------------


PRINTING = """\
def test_prints():
    print("HELLO-FROM-THE-TEST")
    assert True
"""


def test_no_capture_lets_test_output_through(tmp_path: Path) -> None:
    """With capture on, a passing test's ``print`` is captured and never shown; with ``-s``
    it reaches the user through the worker's stderr.

    The stderr landing is the documented divergence from pytest (which writes to the
    terminal): a worker's *stdout* is the protocol channel, so "not captured" cannot mean
    "this process's stdout".
    """
    tree = _tree(tmp_path, "nocapture", {"test_p.py": PRINTING})

    captured = _rustest(tree, ["-n", "1"])
    uncaptured = _rustest(tree, ["-s", "-n", "1"])

    assert "HELLO-FROM-THE-TEST" not in captured.stdout + captured.stderr
    assert "HELLO-FROM-THE-TEST" in uncaptured.stderr, uncaptured.stderr
    assert uncaptured.returncode == 0


# --------------------------------------------------------------------------------------
# markdown code blocks
# --------------------------------------------------------------------------------------


MARKDOWN = """\
# Guide

```python
assert 1 + 1 == 2
```

<!--rustest.mark.skip-->
```python
this is not python at all
```

```text
not a python fence
```
"""


def test_markdown_code_blocks_are_collected_with_the_flag(tmp_path: Path) -> None:
    """rustest's own tier, carried over from v1 — the project's README and guide are tested
    this way, and so are its users'.  pytest answers **4** (``found no collectors``) for a
    ``.md`` argument; ``--codeblocks`` is the deliberate superset, and with neither flag nor
    ``[tool.rustest] codeblocks`` config the tier stays off (pytest's own answer) -- see
    ``test_no_codeblocks_restores_pytests_answer`` for the un-opted-in case."""
    tree = _tree(tmp_path, "md", {"guide.md": MARKDOWN})

    result = _rustest(tree, ["--codeblocks", "guide.md", "-n", "1"])

    assert result.returncode == 0, result.stdout + result.stderr
    # Two python fences: one runs, one is skipped by the HTML comment.  The ```text``` fence
    # is not collected at all.
    assert _summary_line(result.stderr) == "1 passed, 1 skipped", result.stderr


def test_markdown_files_are_not_found_by_walking_a_directory(tmp_path: Path) -> None:
    """A directory argument collects **no** markdown — pytest walking it collects none.

    This test asserted the opposite until Phase 4 Task 1's re-sweep measured the cost: any
    repo with python fences in its docs got tests under `rustest tests/` that
    `pytest tests/` never sees (13 of them on the acceptance target, 4 failing, because
    documentation snippets do not import what they reference). Naming the file still works
    and is how this repo tests its own docs — see the test below.
    """
    tree = _tree(tmp_path, "mdwalk", {"docs/guide.md": MARKDOWN})

    result = _rustest(tree, ["docs", "-n", "1"])

    assert result.returncode == 5, result.stdout + result.stderr


def test_a_named_markdown_file_is_still_collected(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "mdnamed", {"docs/guide.md": MARKDOWN})

    result = _rustest(tree, ["--codeblocks", "docs/guide.md", "-n", "1"])

    assert _summary_line(result.stderr) == "1 passed, 1 skipped", result.stderr


def test_no_codeblocks_restores_pytests_answer(tmp_path: Path) -> None:
    """With neither the flag nor config asking for it, a ``.md`` argument is pytest's usage
    error (exit 4) -- the built-in default -- and ``--no-codeblocks`` keeps it that way even
    when it must override a config that turned the tier on."""
    tree = _tree(tmp_path, "mdoff", {"guide.md": MARKDOWN})

    result = _rustest(tree, ["guide.md", "-n", "1"])
    assert result.returncode == 4, result.stdout + result.stderr

    result = _rustest(tree, ["--no-codeblocks", "guide.md"])
    assert result.returncode == 4, result.stdout + result.stderr


def test_a_failing_code_block_fails_the_run(tmp_path: Path) -> None:
    """The tier would be worthless if a broken example were silently green."""
    tree = _tree(tmp_path, "mdbad", {"bad.md": "```python\nassert 1 == 2\n```\n"})

    result = _rustest(tree, ["--codeblocks", "bad.md", "-n", "1"])

    assert result.returncode == 1, result.stdout + result.stderr
    assert "codeblock_0_line_1" in result.stdout, result.stdout


# --------------------------------------------------------------------------------------
# async: the false-green guard
# --------------------------------------------------------------------------------------


def test_a_failing_async_test_fails(tmp_path: Path) -> None:
    """**Regression pin for a silent-green defect found by the flip.**

    Before Phase 1c the worker called an ``async def`` test like a sync one, got a
    coroutine back, raised nothing and reported PASSED — measured: ``async def test():
    assert 1 == 2`` printed ``1 passed`` under v2 while both pytest and rustest v1 printed
    ``1 failed``.  An engine that cannot fail an async test is worse than one that cannot run
    it.
    """
    tree = _tree(
        tmp_path,
        "asyncfail",
        {
            "test_a.py": (
                "import pytest\n\n\n"
                "@pytest.mark.asyncio\n"
                "async def test_async_fails():\n"
                "    assert 1 == 2\n"
            )
        },
    )

    result = _rustest(tree, ["-n", "1"])

    assert result.returncode == 1, result.stdout + result.stderr
    assert _summary_line(result.stderr) == "1 failed", result.stderr


def test_an_async_fixture_reaches_a_test_as_its_value(tmp_path: Path) -> None:
    """...and the same defect on the fixture side: an unawaited async fixture arrives as a
    coroutine object, which is truthy and fails only wherever the test first indexes it."""
    tree = _tree(
        tmp_path,
        "asyncfixture",
        {
            "test_a.py": (
                "import pytest\n\n\n"
                "@pytest.fixture\n"
                "async def value():\n"
                "    return {'v': 7}\n\n\n"
                "def test_sync_sees_the_value(value):\n"
                "    assert value['v'] == 7\n"
            )
        },
    )

    result = _rustest(tree, ["-n", "1"])

    assert result.returncode == 0, result.stdout + result.stderr


# --------------------------------------------------------------------------------------
# parser-level surface
# --------------------------------------------------------------------------------------


def test_the_parser_carries_the_whole_default_surface() -> None:
    """One assertion per option the flip promises, so removing one is a test failure rather
    than a quiet loss of a documented flag."""
    parser = cli.build_parser()
    args = parser.parse_args(
        ["-x", "--lf", "-v", "-s", "-n", "3", "-k", "kw", "-m", "slow", "--no-codeblocks", "tests"]
    )

    assert args.fail_fast is True
    assert args.last_failed is True
    assert args.verbose is True
    assert args.capture_output is False
    assert args.workers == 3
    assert args.pattern == "kw"
    assert args.mark_expr == "slow"
    assert args.enable_codeblocks is False
    assert args.paths == ["tests"]


def test_last_failed_wins_over_failed_first() -> None:
    """pytest checks ``lf`` first and only then falls through to ``ff``."""
    parser = cli.build_parser()
    assert cli._last_failed_mode(parser.parse_args(["--lf", "--ff"])) == "only"  # pyright: ignore[reportPrivateUsage]
    assert cli._last_failed_mode(parser.parse_args(["--ff"])) == "first"  # pyright: ignore[reportPrivateUsage]
    assert cli._last_failed_mode(parser.parse_args([])) == "none"  # pyright: ignore[reportPrivateUsage]


# --------------------------------------------------------------------------------------
# pytest.exit()
# --------------------------------------------------------------------------------------


BAILING = """\
import pytest


def test_first():
    assert True


def test_bails():
    pytest.exit("stopping here")


def test_never():
    raise AssertionError("must not run: pytest.exit() stopped the session above")
"""


def test_pytest_exit_stops_the_session_exactly_as_pytest_does(tmp_path: Path) -> None:
    """Differential. Before this, ``pytest.exit()`` was a *silent no-op*: the compat shim's
    catch-all ``__getattr__`` manufactured a do-nothing stub, the call returned, the test
    passed and the session carried on — so the run's verdict was whatever the tests that
    should never have run happened to do.
    """
    tree = _tree(tmp_path, "bail", {"test_bail.py": BAILING})

    oracle = _pytest(tree, [])
    ours = _rustest(tree, ["-n", "1"])

    assert oracle.returncode == 2, oracle.stdout
    assert ours.returncode == 2, ours.stdout + ours.stderr

    # The tests before the call keep their reports; the exiting test gets none, and the
    # tests after it never run — all three claims are pytest's, and all three are checked.
    assert "1 passed" in oracle.stdout, oracle.stdout
    assert ours.stderr.strip().splitlines()[-1].startswith("1 passed"), ours.stderr
    assert "test_never" not in ours.stdout + ours.stderr
    assert "test_bails" not in ours.stdout


def test_pytest_exit_shows_the_reason(tmp_path: Path) -> None:
    """pytest writes ``Exit: <reason>``; a bail-out with no visible reason is a run that
    stopped for no stated cause."""
    tree = _tree(tmp_path, "bailmsg", {"test_bail.py": BAILING})

    result = _rustest(tree, ["-n", "1"])

    assert "Exit: stopping here" in result.stderr, result.stderr


def test_pytest_exit_outranks_an_earlier_failure(tmp_path: Path) -> None:
    """Exit code **2**, not 1, even with a failure already reported.

    ``wrap_session`` catches ``Exit`` in the same arm as ``KeyboardInterrupt`` and sets
    ``INTERRUPTED`` regardless of ``session.testsfailed`` — the ``except Failed`` arm never
    runs, because the exception that escaped is the ``Exit``. Probed on pytest 8.4.2:
    ``1 failed`` and exit 2.
    """
    tree = _tree(
        tmp_path,
        "bailfail",
        {
            "test_bail.py": (
                "import pytest\n\n\n"
                "def test_fails():\n    assert 0\n\n\n"
                "def test_bails():\n    pytest.exit('stopping here')\n"
            )
        },
    )

    oracle = _pytest(tree, [])
    ours = _rustest(tree, ["-n", "1"])

    assert oracle.returncode == 2 and "1 failed" in oracle.stdout, oracle.stdout
    assert ours.returncode == 2, ours.stdout + ours.stderr
    assert ours.stderr.strip().splitlines()[-1].startswith("1 failed"), ours.stderr


# --------------------------------------------------------------------------------------
# removed flags
# --------------------------------------------------------------------------------------


def test_no_removed_flag_is_advertised_in_help(tmp_path: Path) -> None:
    """``--help`` must not list a flag that exits 4.

    The pairing is what makes ``REMOVED_FLAGS`` honest: the flag is *recognised* well enough
    to produce a real message, and *absent* everywhere a user could be told to use it. A
    removed flag still in the help text is an instruction to type something that fails.
    """
    tree = _tree(tmp_path, "help", {"test_ok.py": "def test_one():\n    assert True\n"})

    result = _rustest(tree, ["--help"])

    assert result.returncode == 0, result.stderr
    for flag in cli.REMOVED_FLAGS:
        assert flag not in result.stdout, f"{flag} is refused but still advertised in --help"


@pytest.mark.parametrize("flag", sorted(cli.REMOVED_FLAGS))
def test_every_removed_flag_exits_four_and_names_itself(flag: str, tmp_path: Path) -> None:
    """Parametrized over the table, so a flag added to it cannot skip the contract."""
    tree = _tree(tmp_path, "removed", {"test_ok.py": "def test_one():\n    assert True\n"})

    result = _rustest(tree, [flag])

    assert result.returncode == 4, result.stdout + result.stderr
    assert f"{flag} has been removed" in result.stderr, result.stderr


# --------------------------------------------------------------------------------------
# -v percent column
# --------------------------------------------------------------------------------------


def test_the_verbose_percent_denominator_is_selected_not_run(tmp_path: Path) -> None:
    """pytest's percent column is over ``session.testscollected`` — what the run *selected* —
    so a truncated run never reaches 100%.

    Probed on pytest 8.4.2: ``pytest -x -v`` over three tests, stopping at the second, prints
    ``[ 33%]`` then ``[ 66%]``. Using "how many ran" as the denominator would print
    ``[ 50%]``, ``[100%]`` and claim the run finished.
    """
    tree = _tree(
        tmp_path,
        "denominator",
        {
            "test_d.py": (
                "def test_a():\n    assert True\n\n\n"
                "def test_b():\n    assert 0\n\n\n"
                "def test_c():\n    assert True\n"
            )
        },
    )

    # Not `_pytest`, which passes `-q`: pytest's verbosity is an int and `-q` cancels the
    # `-v` this test is entirely about.
    oracle = _run(
        [sys.executable, "-m", "pytest", "-v", "-x", "--no-header", "-p", "no:cacheprovider"],
        tree,
    )
    ours = _rustest(tree, ["-v", "-x", "-n", "1"])

    percents = re.findall(r"\[\s*(\d+)%\]", ours.stdout)
    assert percents == ["33", "66"], ours.stdout
    assert re.findall(r"\[\s*(\d+)%\]", oracle.stdout) == percents, oracle.stdout


def test_the_verbose_percent_denominator_follows_deselection(tmp_path: Path) -> None:
    """...and it is *post*-deselection, so ``-k`` selecting 2 of 4 reaches 100% at the
    second test rather than at the fourth."""
    tree = _tree(
        tmp_path,
        "denominator_k",
        {
            "test_d.py": (
                "def test_a():\n    assert True\n\n\n"
                "def test_b():\n    assert True\n\n\n"
                "def test_c():\n    assert True\n"
            )
        },
    )

    ours = _rustest(tree, ["-v", "-k", "test_a or test_b", "-n", "1"])

    assert re.findall(r"\[\s*(\d+)%\]", ours.stdout) == ["50", "100"], ours.stdout
