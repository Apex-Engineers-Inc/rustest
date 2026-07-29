"""Unit tests for the ``--real`` gate's config parsing, grading and status reduction.

Nothing here clones, provisions or runs a real suite -- those are the gate itself. What is
tested is the machinery that decides whether a real run *passed*, because that machinery is
the only thing standing between "6132 tests agree" and "6132 tests were never compared".
"""

from __future__ import annotations

import os
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


# --------------------------------------------------------------------------------------
# The staleness gate (Phase 4 convergence wave; Phase 4c Concern 3)
# --------------------------------------------------------------------------------------


def _staleness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    wheel_age: float,
    install_age: float | None,
) -> RealTarget:
    """A target whose named wheel and installed extension have chosen ages.

    Ages are seconds *relative to the newest build input*, so a negative number is "older
    than the source" -- the shape the gate exists to refuse. The real build inputs are
    replaced by one file in ``tmp_path``, because a test that stats this repository would
    pass or fail according to when someone last touched it.
    """
    source = tmp_path / "src" / "v2" / "engine.rs"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("// build input\n", encoding="utf-8")
    monkeypatch.setattr(real_mod, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(real_mod, "_BUILD_INPUT_GLOBS", ("src/**/*.rs",))
    monkeypatch.setattr(real_mod, "WORK_DIR", tmp_path / "_work")

    base = source.stat().st_mtime
    wheels = tmp_path / "_work" / "_wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    wheel = wheels / "rustest-0.0.0-cp314-cp314-win_amd64.whl"
    wheel.write_bytes(b"PK\x03\x04")
    os.utime(wheel, (base + wheel_age, base + wheel_age))

    if install_age is None:
        monkeypatch.setattr(real_mod, "_installed_rustest_path", lambda _target: None)
    else:
        installed = tmp_path / "_venv" / "rust.pyd"
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_bytes(b"MZ")
        os.utime(installed, (base + install_age, base + install_age))
        monkeypatch.setattr(real_mod, "_installed_rustest_path", lambda _target: installed)

    return _target(setup=[["uv", "pip", "install", "{wheels}/" + wheel.name]])


def test_a_wheel_older_than_the_source_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 4c Concern 3, and the reason this gate exists.

    ``ensure_env`` returns the moment the venv's interpreter exists, so a `--real` run used
    to measure whatever rustest was installed last. That failure has **no symptom**: the
    suite runs, the ids match, the wall-clock is a real measurement -- of the wrong build.
    One member-designer run was started that way and caught only because a human thought to
    check by hand.

    The message must name the wheel *and* the file that outran it: a fresh `git checkout`
    rewrites every mtime, and the reader has to be able to tell that apart from real drift.
    """
    target = _staleness_fixture(tmp_path, monkeypatch, wheel_age=-60.0, install_age=None)

    with pytest.raises(RuntimeError) as excinfo:
        real_mod.assert_build_is_current(target)

    message = str(excinfo.value)
    assert "STALE WHEEL" in message
    assert "rustest-0.0.0-cp314-cp314-win_amd64.whl" in message
    assert "src/v2/engine.rs" in message


def test_a_wheel_rebuilt_but_never_reinstalled_is_refused_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second hop, and it fails independently of the first.

    Rebuilding the wheel makes the wheel-vs-source check pass while the venv still holds the
    previous build -- the exact state a `maturin build` with no reinstall leaves behind, and
    the one `--real-rebuild-env` is for.
    """
    target = _staleness_fixture(tmp_path, monkeypatch, wheel_age=60.0, install_age=-60.0)

    with pytest.raises(RuntimeError) as excinfo:
        real_mod.assert_build_is_current(target)

    assert "STALE INSTALL" in str(excinfo.value)


def test_a_current_wheel_and_install_pass_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the gate must be quiet on a freshly built, freshly installed target.

    Without this the two refusals above are also satisfied by a function that raises always,
    and a check that always fires is a check that gets deleted.
    """
    target = _staleness_fixture(tmp_path, monkeypatch, wheel_age=60.0, install_age=120.0)

    real_mod.assert_build_is_current(target)


def test_a_missing_wheel_is_refused_before_anything_is_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config naming a wheel that is not there must say so, not fall through to the run."""
    target = _staleness_fixture(tmp_path, monkeypatch, wheel_age=60.0, install_age=120.0)
    next(iter((tmp_path / "_work" / "_wheels").glob("*.whl"))).unlink()

    with pytest.raises(RuntimeError) as excinfo:
        real_mod.assert_build_is_current(target)

    assert "does not exist" in str(excinfo.value)


def test_a_target_that_names_no_wheel_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An editable-install target has nothing to compare, and must not be refused for it.

    `verify_env`'s import probe is still the backstop there -- this gate only ever speaks
    about wheels a config actually names.
    """
    _ = _staleness_fixture(tmp_path, monkeypatch, wheel_age=-600.0, install_age=None)
    real_mod.assert_build_is_current(_target(setup=[["uv", "pip", "install", "-e", "{repo}"]]))
