"""The flip: ``rustest <paths>`` is the v2 engine, and the CLI surface that came with it.

Phase 1c Task 1 moved the default from v1 to v2 and filled in the option surface a default
engine has to have: ``-x``, ``--lf``/``--ff``, ``-v``, ``-q``, ``-s``, ``--no-codeblocks``.
This module pins the *observable* half of all of it — what the process does, not what the
code looks like — and diffs the semantic claims against **real pytest** in a subprocess, the
same discipline ``test_v2_run_cli.py`` uses.

Everything here drives the real binary end to end. A mocked ``v2_run`` would have proved the
router forwards a flag and nothing about whether the flag works.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

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
    assert result.stderr.strip().splitlines()[-1] == "1 passed", result.stderr


def test_the_v2_flag_is_a_no_op_alias_that_says_so(tmp_path: Path) -> None:
    """``--v2`` still works and warns, so a CI file that predates the flip keeps passing.

    Removing it outright would fail those pipelines with argparse's ``unrecognized
    arguments`` — a worse outcome than a deprecation line, and one that teaches nothing.
    """
    tree = _tree(tmp_path, "alias", {"test_ok.py": "def test_one():\n    assert True\n"})

    with_flag = _rustest(tree, ["--v2"])
    without = _rustest(tree, [])

    assert with_flag.returncode == without.returncode == 0
    assert "--v2 is a no-op" in with_flag.stderr, with_flag.stderr
    assert "--v2 is a no-op" not in without.stderr


def test_the_v1_flag_selects_the_legacy_engine_and_says_so(tmp_path: Path) -> None:
    """``--v1`` reaches v1 — identified by v1's own renderer, not by the banner alone."""
    tree = _tree(tmp_path, "legacy", {"test_ok.py": "def test_one():\n    assert True\n"})

    result = _rustest(tree, ["--v1", "--color", "never"])

    assert result.returncode == 0, result.stderr
    assert "legacy engine" in result.stderr, result.stderr
    assert "removed in a future release" in result.stderr, result.stderr
    assert "1 passed" in result.stdout + result.stderr


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
    assert ours.stderr.strip().splitlines()[-1] == "1 failed, 1 passed", ours.stderr

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
    assert result.stderr.strip().splitlines()[-1] == "2 passed"
    assert "stopping after" not in result.stderr


# --------------------------------------------------------------------------------------
# --lf / --ff
# --------------------------------------------------------------------------------------


def test_last_failed_reruns_only_the_failures_and_deselects_the_rest(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "lf", {"test_x.py": FOUR_WITH_FAILURES})

    first = _rustest(tree, [])
    assert first.returncode == 1, first.stderr
    assert (tree / ".rustest_cache" / "v2" / "lastfailed").is_file(), "no cache was written"

    second = _rustest(tree, ["--lf"])

    assert second.stderr.strip().splitlines()[-1] == "2 failed, 2 deselected", second.stderr
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
    assert second.stderr.strip().splitlines()[-1] == "2 failed, 2 passed"


def test_the_v2_cache_is_not_v1s(tmp_path: Path) -> None:
    """Separate files, because the two engines' node ids are different strings.

    v1 writes ``.rustest_cache/lastfailed`` keyed on native-separator display names; v2
    writes ``.rustest_cache/v2/lastfailed`` keyed on rootdir-relative posix ids.  One shared
    file would mean every ``--lf`` after an engine switch matched nothing — silently, and
    worst on Windows.
    """
    tree = _tree(tmp_path, "cachesplit", {"test_x.py": FOUR_WITH_FAILURES})

    _ = _rustest(tree, [])
    v2_cache = tree / ".rustest_cache" / "v2" / "lastfailed"
    assert v2_cache.is_file()
    assert not (tree / ".rustest_cache" / "lastfailed").exists()

    entries: dict[str, bool] = json.loads(v2_cache.read_text(encoding="utf-8"))
    assert entries == {"test_x.py::test_b": True, "test_x.py::test_c": True}


def test_last_failed_with_an_empty_cache_runs_everything(tmp_path: Path) -> None:
    """pytest's own carve-out: ``--lf`` on a first run is not ``--lf`` on nothing."""
    tree = _tree(tmp_path, "lfempty", {"test_x.py": FOUR_WITH_FAILURES})

    result = _rustest(tree, ["--lf"])

    assert result.stderr.strip().splitlines()[-1] == "2 failed, 2 passed", result.stderr


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


def test_quiet_prints_only_the_summary(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "quiet", {"test_m.py": MIXED})

    quiet = _rustest(tree, ["-q", "-n", "1"])
    default = _rustest(tree, ["-n", "1"])

    assert quiet.stdout == "", f"-q must not write to stdout: {quiet.stdout!r}"
    assert quiet.stderr.strip().splitlines()[-1] == "1 failed, 1 passed, 1 skipped, 1 xfailed"
    # ...and the default rung really does say more, or the assertion above proves nothing.
    assert "FAILED test_m.py::test_fail" in default.stdout, default.stdout


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
    terminal): a v2 worker's *stdout* is the protocol channel, so "not captured" cannot mean
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


def test_markdown_code_blocks_are_collected_by_default(tmp_path: Path) -> None:
    """rustest's own tier, carried over from v1 — the project's README and guide are tested
    this way, and so are its users'.  pytest answers **4** (``found no collectors``) for a
    ``.md`` argument; this is a deliberate superset, switched off by ``--no-codeblocks``."""
    tree = _tree(tmp_path, "md", {"guide.md": MARKDOWN})

    result = _rustest(tree, ["guide.md", "-n", "1"])

    assert result.returncode == 0, result.stdout + result.stderr
    # Two python fences: one runs, one is skipped by the HTML comment.  The ```text``` fence
    # is not collected at all.
    assert result.stderr.strip().splitlines()[-1] == "1 passed, 1 skipped", result.stderr


def test_markdown_files_are_found_by_walking_a_directory(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "mdwalk", {"docs/guide.md": MARKDOWN})

    result = _rustest(tree, ["docs", "-n", "1"])

    assert result.stderr.strip().splitlines()[-1] == "1 passed, 1 skipped", result.stderr


def test_no_codeblocks_restores_pytests_answer(tmp_path: Path) -> None:
    """With the tier off, a ``.md`` argument is pytest's usage error (exit 4) again."""
    tree = _tree(tmp_path, "mdoff", {"guide.md": MARKDOWN})

    result = _rustest(tree, ["--no-codeblocks", "guide.md"])

    assert result.returncode == 4, result.stdout + result.stderr


def test_a_failing_code_block_fails_the_run(tmp_path: Path) -> None:
    """The tier would be worthless if a broken example were silently green."""
    tree = _tree(tmp_path, "mdbad", {"bad.md": "```python\nassert 1 == 2\n```\n"})

    result = _rustest(tree, ["bad.md", "-n", "1"])

    assert result.returncode == 1, result.stdout + result.stderr
    assert "codeblock_0_line_1" in result.stdout, result.stdout


# --------------------------------------------------------------------------------------
# async: the false-green guard
# --------------------------------------------------------------------------------------


def test_a_failing_async_test_fails(tmp_path: Path) -> None:
    """**Regression pin for a silent-green defect found by the flip.**

    Before Phase 1c the v2 worker called an ``async def`` test like a sync one, got a
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
    assert result.stderr.strip().splitlines()[-1] == "1 failed", result.stderr


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
