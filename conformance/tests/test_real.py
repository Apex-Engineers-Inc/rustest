"""Unit tests for the ``--real`` gate's config parsing, grading and status reduction.

Nothing here clones, provisions or runs a real suite -- those are the gate itself. What is
tested is the machinery that decides whether a real run *passed*, because that machinery is
the only thing standing between "6132 tests agree" and "6132 tests were never compared".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conformance.harness import real as real_mod
from conformance.harness.real import (
    ConfigError,
    RealTarget,
    RunOutcome,
    grade_real,
    load_target,
)

_MINIMAL = """
[repo]
kind = "oss"
url = "https://example.invalid/x.git"
tag = "v1"
rev = "deadbeef"
test_paths = ["tests"]

[env]
setup = []
pythonpath = []

[addopts]
configured = []
replay = []
dropped = []

[run]
timeout_s = 60

[divergence.ids]

[divergence.global]
"""


def _write_target(tmp_path: Path, name: str, text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / f"{name}.toml").write_text(text, encoding="utf-8")
    monkeypatch.setattr(real_mod, "REAL_DIR", tmp_path)
    monkeypatch.setattr(real_mod, "WORK_DIR", tmp_path / "_work")


def _target(**overrides: object) -> RealTarget:
    base: dict[str, object] = {
        "name": "t",
        "kind": "oss",
        "url": "",
        "tag": "",
        "rev": "",
        "repo": Path("."),
        "test_paths": [],
        "venv": Path("."),
        "setup": [],
        "pythonpath": [],
        "addopts_configured": [],
        "addopts_replay": [],
        "addopts_dropped": [],
        "pytest_args": [],
        "rustest_args": [],
        "timeout_s": 60,
    }
    base.update(overrides)
    return RealTarget(**base)  # pyright: ignore[reportArgumentType]


def _run(runner: str, statuses: dict[str, str], exit_code: int = 0) -> RunOutcome:
    return RunOutcome(
        runner=runner,
        exit_code=exit_code,
        seconds=1.0,
        statuses=statuses,
        deselected=0,
        collection_errors=[],
    )


def test_load_target_rejects_unknown_global_ledger_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo'd global key must fail loudly, not sit in the file grading nothing forever."""
    _write_target(tmp_path, "x", _MINIMAL + '\nexit_kode = "typo"\n', monkeypatch)

    with pytest.raises(ConfigError) as excinfo:
        load_target("x")

    assert "exit_kode" in str(excinfo.value)


def test_load_target_rejects_empty_mechanism(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole discipline is 'mechanism or it doesn't pass' -- an empty reason is neither."""
    text = _MINIMAL.replace("[divergence.ids]\n", '[divergence.ids]\n"a::b" = "   "\n')
    _write_target(tmp_path, "x", text, monkeypatch)

    with pytest.raises(ConfigError):
        load_target("x")


def test_identical_runs_match() -> None:
    verdict = grade_real(
        _target(),
        _run("pytest", {"t.py::a": "passed", "t.py::b": "skipped"}),
        _run("rustest", {"t.py::a": "passed", "t.py::b": "skipped"}),
    )

    assert verdict.status == "MATCH"
    assert verdict.unexplained == []


def test_status_swap_with_equal_counts_is_caught() -> None:
    """The reason this gate diffs ids and not only counts.

    ``passed`` and ``skipped`` traded between two tests leaves every count identical and
    the exit code identical. A counts-only gate calls that a perfect match.
    """
    verdict = grade_real(
        _target(),
        _run("pytest", {"t.py::a": "passed", "t.py::b": "skipped"}),
        _run("rustest", {"t.py::a": "skipped", "t.py::b": "passed"}),
    )

    assert verdict.status == "DIVERGE"
    assert len(verdict.unexplained) == 2


def test_ledger_glob_explains_a_family_of_divergences() -> None:
    """A glob covers a family of ids -- and the tally it implies still needs its own entry.

    The `tally` entry is not boilerplate: two tests moving from passed to failed changes
    the counts, and the counts are graded separately precisely so that a ledger cannot
    explain away an id and leave the summary unaccounted for.
    """
    verdict = grade_real(
        _target(
            ids_ledger={"t.py::*": "mechanism: the whole file needs a plugin"},
            global_ledger={"tally": "the same two tests, counted"},
        ),
        _run("pytest", {"t.py::a": "passed", "t.py::b": "passed"}),
        _run("rustest", {"t.py::a": "failed", "t.py::b": "failed"}),
    )

    assert verdict.status == "EXPLAINED"
    assert verdict.unexplained == []
    assert len(verdict.explained) == 3


def test_a_glob_over_ids_does_not_cover_the_tally() -> None:
    """The other half of the rule above, asserted directly."""
    verdict = grade_real(
        _target(ids_ledger={"t.py::*": "mechanism"}),
        _run("pytest", {"t.py::a": "passed"}),
        _run("rustest", {"t.py::a": "failed"}),
    )

    assert verdict.status == "DIVERGE"
    assert [p for p in verdict.unexplained if p.startswith("tally ")] == verdict.unexplained


def test_ledger_entry_that_matches_nothing_is_stale() -> None:
    """Same rule as the corpus ledgers: an inert entry must not survive a fix unnoticed."""
    verdict = grade_real(
        _target(ids_ledger={"t.py::gone": "fixed long ago"}),
        _run("pytest", {"t.py::a": "passed"}),
        _run("rustest", {"t.py::a": "passed"}),
    )

    assert verdict.status == "STALE-LEDGER"
    assert verdict.stale == ["t.py::gone"]


def test_a_run_where_neither_side_executed_anything_is_a_harness_error() -> None:
    """Two runners that both collapsed agree about nothing, and must not print green.

    Measured, not imagined: jinja2's async tests import `trio`, `trio` imports `ssl`, and
    on a uv CPython 3.14.2 install missing `libcrypto-3-x64.dll` both runners exited 2 with
    two collection errors each -- and the gate reported `1 match`.
    """
    verdict = grade_real(
        _target(),
        RunOutcome("pytest", 2, 1.0, {}, 0, ["tests/test_async.py"]),
        RunOutcome("rustest", 2, 1.0, {}, 0, ["tests/test_async.py"]),
    )

    assert verdict.status == "HARNESS-ERROR"
    assert "vacuous" in verdict.detail


def test_strip_params_grades_functions_and_raises_the_id_divergence_itself() -> None:
    """The relaxation must cost exactly one thing: *which* parameter case moved.

    Outcomes are still compared (as a per-function multiset), and the raw id difference
    becomes its own graded `ids` problem -- so it cannot be absorbed silently, and it goes
    stale the moment rustest's id generation matches.
    """
    target = _target(id_policy="strip_params")
    pytest_run = _run("pytest", {"t.py::f[x0]": "passed", "t.py::f[x1]": "passed"})
    rustest_run = _run("rustest", {"t.py::f[dict(1)]": "passed", "t.py::f[empty_dict]": "passed"})

    verdict = grade_real(target, pytest_run, rustest_run)

    assert verdict.status == "DIVERGE"
    assert verdict.unexplained == [
        v for v in verdict.unexplained if v.startswith("raw node ids differ")
    ]
    assert len(verdict.unexplained) == 1

    ledgered = grade_real(
        _target(id_policy="strip_params", global_ledger={"ids": "IdMaker divergence"}),
        pytest_run,
        rustest_run,
    )
    assert ledgered.status == "EXPLAINED"


def test_strip_params_still_catches_an_outcome_change() -> None:
    verdict = grade_real(
        _target(id_policy="strip_params", global_ledger={"ids": "IdMaker divergence"}),
        _run("pytest", {"t.py::f[x0]": "passed", "t.py::f[x1]": "passed"}),
        _run("rustest", {"t.py::f[dict(1)]": "passed", "t.py::f[empty_dict]": "failed"}),
    )

    assert verdict.status == "DIVERGE"
    assert any("t.py::f" in line and "multiset" in line for line in verdict.unexplained)


def test_exit_code_divergence_is_global_and_ledgerable() -> None:
    unledgered = grade_real(
        _target(),
        _run("pytest", {"t.py::a": "passed"}, exit_code=0),
        _run("rustest", {"t.py::a": "passed"}, exit_code=1),
    )
    assert unledgered.status == "DIVERGE"

    ledgered = grade_real(
        _target(global_ledger={"exit_code": "mechanism: see report"}),
        _run("pytest", {"t.py::a": "passed"}, exit_code=0),
        _run("rustest", {"t.py::a": "passed"}, exit_code=1),
    )
    assert ledgered.status == "EXPLAINED"


def test_tally_counts_reduced_statuses_not_reports() -> None:
    outcome = _run(
        "pytest",
        {"a": "passed", "b": "passed", "c": "xfailed", "d": "error"},
    )

    tally = outcome.tally()

    assert tally["passed"] == 2
    assert tally["xfailed"] == 1
    assert tally["error"] == 1
    assert tally["failed"] == 0


@pytest.mark.parametrize(
    ("phases", "expected"),
    [
        ([("setup", "passed"), ("call", "passed"), ("teardown", "passed")], "passed"),
        ([("setup", "passed"), ("call", "failed"), ("teardown", "passed")], "failed"),
        ([("setup", "failed")], "error"),
        # pytest's summary line tallies this as `1 passed, 1 error`; one *test* is one
        # entry here, and the stronger status wins, which is what rustest's report carries.
        ([("setup", "passed"), ("call", "passed"), ("teardown", "failed")], "error"),
    ],
)
def test_plugin_reduction_precedence(phases: list[tuple[str, str]], expected: str) -> None:
    from conformance.harness import _real_report_plugin as plugin

    class _Report:
        def __init__(self, when: str, outcome: str) -> None:
            self.when = when
            self.nodeid = "t.py::x"
            self.passed = outcome == "passed"
            self.failed = outcome == "failed"
            self.skipped = outcome == "skipped"

    current: str | None = None
    for when, outcome in phases:
        # A *passing* setup or teardown contributes nothing -- pytest emits a report for it,
        # but the test's status is decided by its call phase. `_reduce` returns None there
        # and the accumulator must skip it, exactly as `pytest_runtest_logreport` does.
        status = plugin._reduce(_Report(when, outcome))  # pyright: ignore[reportPrivateUsage, reportArgumentType]
        if status is None:
            assert when in ("setup", "teardown") and outcome == "passed"
            continue
        current = plugin._stronger(current, status)  # pyright: ignore[reportPrivateUsage]

    assert current == expected
