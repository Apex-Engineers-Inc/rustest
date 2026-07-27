"""``--cov``: the ``sys.monitoring`` measurement, the wire, the CLI, and the differential.

Three layers, and they answer different questions:

* **unit** -- :class:`rustest._v2_coverage.LineMonitor` in this process, where the questions
  are "which lines" and "what does it cost", both of which a subprocess timing cannot answer
  on a machine whose noise is larger than the effect;
* **CLI** -- the real binary end to end, because every refusal (`--cov-branch`, `--cov` with
  `--v1`, an unknown `--cov-report`) is an *exit code* and a mocked parser proves nothing
  about one;
* **differential** -- the same tree measured by ``rustest --cov`` and by ``coverage run -m
  pytest``, with the executed line sets compared file by file. That is the only test here that
  can catch a measurement that is self-consistently wrong.

The corpus case `conformance/corpus/coverage/line-sets` is the differential's fixture, and it
is a *shared* one on purpose: the three conformance gates already prove that suite collects
and runs identically under both runners, so a divergence found here is about **coverage** and
not about collection.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

# The compiled extension is built and installed by `python/tests/__init__.py`.
from rustest import _v2_coverage
from rustest._v2_coverage import LineMonitor

coverage = pytest.importorskip(
    "coverage",
    reason="--cov is an optional extra (rustest[cov]); these tests measure against coverage.py",
)

CORPUS_CASE = Path(__file__).resolve().parents[2] / "conformance" / "corpus" / "coverage"

#: The executed lines of `conformance/corpus/coverage/line-sets/covered.py` under that
#: directory's suite, measured by **both** rustest and coverage.py.
#:
#: Pinned as a literal as well as compared against coverage.py, and the two assertions catch
#: different things: the comparison catches rustest drifting away from coverage.py, and the
#: literal catches *both* drifting together -- a coverage.py release that changed what it
#: records would otherwise turn this file green while the number a user reads changed.
#:
#: Read it against `covered.py`: 32 is `never_called`'s `def` (executed at import) and its body
#: at 33 is absent, which is the "one function nobody calls" the fixture exists to provide.
COVERED_PY_LINES = [
    1, 18, 20, 22, 25, 26, 27, 28, 29, 32, 36, 37, 38, 39, 40,
    43, 44, 47, 48, 49, 52, 53, 55, 56,
]  # fmt: skip


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for leak in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST", "COVERAGE_FILE"):
        _ = env.pop(leak, None)
    return env


def _run(
    argv: list[str], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, env=env or _clean_env(), check=False
    )


def _rustest(tree: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return _run([sys.executable, "-m", "rustest", *args], tree)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def _lines(data_file: Path) -> dict[str, list[int]]:
    """`{basename: sorted executed lines}` from a coverage.py data file.

    Keyed by **basename** rather than by the full path so the two runs' results are comparable
    without either being canonicalised twice; the trees are flat fixtures, so basenames are
    unique in every case here.
    """
    data = coverage.CoverageData(basename=str(data_file))
    data.read()
    return {
        os.path.basename(name): sorted(data.lines(name) or []) for name in data.measured_files()
    }


# ---------------------------------------------------------------------------
# the monitor itself
# ---------------------------------------------------------------------------


def _sample_tree(root: Path) -> Path:
    """A tiny measured module, returned by path."""
    module = root / "measured" / "sample.py"
    _write(
        module,
        "TOP = 1\n"
        "\n"
        "\n"
        "def taken(flag):\n"
        "    if flag:\n"
        "        return 'yes'\n"
        "    return 'no'\n"
        "\n"
        "\n"
        "def untouched():\n"
        "    return 'never'\n",
    )
    _write(root / "outside" / "other.py", "def helper():\n    return 42\n")
    return module


def test_the_monitor_records_only_lines_inside_the_source_tree(tmp_path: Path) -> None:
    """The `source` scoping rule, asserted on both sides of the boundary at once.

    A monitor that recorded everything would still pass a test that only looked at the
    measured file, so the unmeasured module is imported and called in the same window.
    """
    module = _sample_tree(tmp_path)
    sys.path.insert(0, str(tmp_path / "measured"))
    sys.path.insert(0, str(tmp_path / "outside"))
    monitor = LineMonitor([str(tmp_path / "measured")], str(tmp_path))
    monitor.start()
    try:
        import other  # pyright: ignore[reportMissingImports]
        import sample  # pyright: ignore[reportMissingImports]

        _ = sample.taken(True)
        _ = other.helper()
    finally:
        monitor.stop()
        for name in ("sample", "other"):
            _ = sys.modules.pop(name, None)
        sys.path.remove(str(tmp_path / "measured"))
        sys.path.remove(str(tmp_path / "outside"))

    recorded = {os.path.basename(name): sorted(lines) for name, lines in monitor.lines.items()}
    assert recorded == {"sample.py": [1, 4, 5, 6, 10]}, recorded
    assert str(module)  # the fixture wrote where we think it did


def test_a_line_in_a_loop_is_recorded_once_and_then_disabled(tmp_path: Path) -> None:
    """`DISABLE` from the LINE callback is what makes the steady-state cost zero.

    Asserted through a counter rather than a stopwatch: the callback is wrapped, the loop runs
    a thousand times, and the callback must have fired **once per location**. A timing
    assertion would be flaky on any machine; a call count cannot be.
    """
    module = tmp_path / "src" / "hot.py"
    _write(
        module,
        "def spin(n):\n    total = 0\n    for _ in range(n):\n        total += 1\n    return total\n",
    )
    sys.path.insert(0, str(tmp_path / "src"))
    monitor = LineMonitor([str(tmp_path / "src")], str(tmp_path))
    calls: list[int] = []
    original = monitor._on_line  # pyright: ignore[reportPrivateUsage]

    def counting(code: object, line_number: int) -> object:
        calls.append(line_number)
        return original(code, line_number)  # pyright: ignore[reportArgumentType]

    monitor._on_line = counting  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    monitor.start()
    try:
        import hot  # pyright: ignore[reportMissingImports]

        _ = hot.spin(1000)
        _ = hot.spin(1000)
    finally:
        monitor.stop()
        _ = sys.modules.pop("hot", None)
        sys.path.remove(str(tmp_path / "src"))

    # `DISABLE` retires an *instruction location*, not a line, so a line with two locations
    # fires twice: line 3 is the `for`, whose `GET_ITER` and `FOR_ITER` are separate offsets.
    # Every other line fires once, and `total += 1` — executed 2 000 times across the two
    # calls — is one of them. That is the whole steady-state cost argument, counted.
    assert sorted(calls) == [1, 2, 3, 3, 4, 5], calls
    assert calls.count(4) == 1, calls
    assert sorted(monitor.lines[str(module)]) == [1, 2, 3, 4, 5]


def test_annotation_code_objects_are_not_measured(tmp_path: Path) -> None:
    """PEP 649 `__annotate__` bodies are skipped, as coverage.py's sysmon core skips them.

    Without the skip rustest would match coverage.py's **C tracer** and diverge from its
    `sysmon` core -- measured both ways in the Task 3 report. Since `sysmon` is coverage.py's
    default on 3.14 and the mechanism rustest shares, the skip is what keeps the default
    comparison exact.

    The fixture reads `__annotations__` on purpose: that is the only thing that makes an
    `__annotate__` code object actually run, so without it this test would pass vacuously.
    """
    module = tmp_path / "src" / "ann.py"
    _write(
        module,
        "class Point:\n"
        "    x: int\n"
        "    y: int = 0\n"
        "\n"
        "\n"
        "def named(value: int) -> int:\n"
        "    return value\n"
        "\n"
        "\n"
        "def peek():\n"
        "    return named.__annotations__\n",
    )
    sys.path.insert(0, str(tmp_path / "src"))
    monitor = LineMonitor([str(tmp_path / "src")], str(tmp_path))
    monitor.start()
    try:
        import ann  # pyright: ignore[reportMissingImports]

        assert "value" in ann.peek()
    finally:
        monitor.stop()
        _ = sys.modules.pop("ann", None)
        sys.path.remove(str(tmp_path / "src"))

    recorded = sorted(next(iter(monitor.lines.values())))
    # 2 and 3 are the bare annotations inside the class body. `y: int = 0` (3) *does* execute
    # at class-definition time for the default; `x: int` (2) exists only in `__annotate__`.
    assert 2 not in recorded, recorded
    assert recorded == [1, 3, 6, 10, 11], recorded


def test_write_produces_a_parallel_mode_data_file(tmp_path: Path) -> None:
    """The worker's output is a coverage.py data file, named the way `combine` expects."""
    _ = _sample_tree(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monitor = LineMonitor([str(tmp_path / "measured")], str(data_dir))
    monitor.start()
    monitor.stop()
    written = monitor.write()

    assert written is not None
    files = list(data_dir.iterdir())
    assert len(files) == 1, files
    # `combine` globs `.coverage.*`; a plain `.coverage` would be the *combined* file's name
    # and would be read as the destination rather than as an input.
    assert files[0].name.startswith(".coverage."), files[0].name
    assert coverage.CoverageData(basename=str(files[0])).base_filename()


def test_a_worker_that_measured_nothing_still_writes_a_file(tmp_path: Path) -> None:
    """An empty file and no file are the same to `combine` -- and different to a reader.

    Only the empty file tells "this worker ran and touched no measured code" apart from "this
    worker died before it could write".
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monitor = LineMonitor([str(tmp_path)], str(data_dir))
    monitor.start()
    monitor.stop()
    _ = monitor.write()
    assert len(list(data_dir.iterdir())) == 1


def test_stop_is_idempotent_and_frees_the_tool_id(tmp_path: Path) -> None:
    """A second `stop()` must not raise, because `main`'s finally can reach it twice."""
    monitor = LineMonitor([str(tmp_path)], str(tmp_path))
    monitor.start()
    acquired = monitor.tool_id
    assert acquired is not None
    assert sys.monitoring.get_tool(acquired) == _v2_coverage.TOOL_NAME
    monitor.stop()
    monitor.stop()
    assert sys.monitoring.get_tool(acquired) is None


def test_a_taken_tool_id_is_stepped_over(tmp_path: Path) -> None:
    """`COVERAGE_ID` held by someone else must not fail the run.

    The realistic case is `coverage run -m rustest --cov=...`: coverage.py holds `COVERAGE_ID`
    and the worker has to step to the next one. coverage.py's own tracer does the same search
    (`coverage/sysmon.py` l. 245-253).
    """
    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "squatter")
    monitor = LineMonitor([str(tmp_path)], str(tmp_path))
    try:
        monitor.start()
        assert monitor.tool_id == sys.monitoring.COVERAGE_ID + 1
    finally:
        monitor.stop()
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)


# ---------------------------------------------------------------------------
# argument handling
# ---------------------------------------------------------------------------


def test_report_specs_are_parsed_and_the_rest_are_named() -> None:
    assert _v2_coverage.parse_report_spec("term") == ("term", None)
    assert _v2_coverage.parse_report_spec("xml") == ("xml", None)
    assert _v2_coverage.parse_report_spec("xml:build/cov.xml") == ("xml", "build/cov.xml")

    # The refusal must *name* the type: every one of these is a real pytest-cov value that a
    # user has in a Makefile, and "invalid --cov-report" is not actionable.
    for spec in ("html", "term-missing", "lcov", "json", "annotate"):
        with pytest.raises(ValueError, match="term"):
            _ = _v2_coverage.parse_report_spec(spec)


def test_sources_resolve_to_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    rootdir = str(tmp_path)

    assert _v2_coverage.resolve_sources([""], rootdir) == [rootdir]
    assert _v2_coverage.resolve_sources([str(tmp_path / "src")], rootdir) == [str(tmp_path / "src")]
    # Repeats collapse: `--cov=src --cov=src` is one tree, and a duplicate in `source` would
    # make `_touch_unexecuted` walk it twice.
    assert len(_v2_coverage.resolve_sources([str(tmp_path / "src")] * 2, rootdir)) == 1

    with pytest.raises(ValueError, match="not a directory"):
        _ = _v2_coverage.resolve_sources(["nope"], rootdir)


def test_the_branch_refusal_names_what_it_would_take() -> None:
    message = _v2_coverage.branch_refusal()
    assert "--cov-branch" in message
    # The refusal must say *why* it is not a silent downgrade to line coverage, because that
    # is the failure mode being avoided.
    assert "overstate" in message
    # ...and it must name whichever door the request came through, because the remedy differs:
    # a flag is dropped from a command line, a config setting is removed from a file.
    config = _v2_coverage.branch_refusal("branch = True in the coverage configuration")
    assert "branch = True in the coverage configuration" in config
    assert "--cov-branch" not in config
    assert "Remove it" in config


def test_the_branch_config_probe_reads_the_project_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`config_requests_branch` must read the *file*, which means no `branch=` argument.

    A constructor argument overrides the configuration file, so the obvious-looking
    `Coverage(branch=False).config.branch` answers `False` for every project on earth and
    detects nothing. This is the test that fails if that argument is ever added to the probe.
    """
    monkeypatch.chdir(tmp_path)
    assert _v2_coverage.config_requests_branch() is False

    _write(tmp_path / ".coveragerc", "[run]\nbranch = True\n")
    assert _v2_coverage.config_requests_branch() is True

    (tmp_path / ".coveragerc").unlink()
    _write(tmp_path / "pyproject.toml", "[tool.coverage.run]\nbranch = true\n")
    assert _v2_coverage.config_requests_branch() is True


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------


def _tiny_tree(root: Path) -> None:
    _write(root / "pytest.ini", "[pytest]\n")
    _write(
        root / "src" / "lib.py",
        "def add(a, b):\n    return a + b\n\n\ndef unused():\n    return 0\n",
    )
    _write(
        root / "tests" / "test_lib.py",
        "import os\nimport sys\n\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))\n"
        "import lib\n\n\ndef test_add():\n    assert lib.add(1, 2) == 3\n",
    )


def test_a_run_without_cov_registers_no_monitoring_tool(tmp_path: Path) -> None:
    """The overhead claim, made structurally rather than statistically.

    "Near-zero when `--cov` is absent" is a timing claim on a machine whose noise swamps it.
    The *real* statement is stronger and exact: no tool id is acquired, so CPython's monitoring
    machinery is never involved in the interpreter loop at all. A test in the worker asserts
    that from inside the worker, which is the only place it can be observed.
    """
    _tiny_tree(tmp_path)
    _write(
        tmp_path / "tests" / "test_probe.py",
        "import sys\n"
        "\n"
        "\n"
        "def test_no_monitoring_tool_is_registered():\n"
        "    assert [sys.monitoring.get_tool(i) for i in range(6)] == [None] * 6\n",
    )
    result = _rustest(tmp_path, ["tests/", "-v"])

    assert result.returncode == 0, result.stderr
    # `-v` and the node id, because exit 0 alone is not evidence: a probe that stopped being
    # collected -- a renamed file, a `python_files` change, a collection error swallowed
    # somewhere -- would leave this run green while asserting nothing at all.
    expected = "tests/test_probe.py::test_no_monitoring_tool_is_registered PASSED"
    assert expected in result.stdout, result.stdout


def test_a_cov_run_registers_exactly_one_tool_named_rustest(tmp_path: Path) -> None:
    """...and its mirror image, so the probe above cannot pass by never running."""
    _tiny_tree(tmp_path)
    _write(
        tmp_path / "tests" / "test_probe.py",
        "import sys\n"
        "\n"
        "\n"
        "def test_one_monitoring_tool_is_registered():\n"
        "    tools = [sys.monitoring.get_tool(i) for i in range(6)]\n"
        "    assert [t for t in tools if t is not None] == ['rustest --cov'], tools\n",
    )
    result = _rustest(tmp_path, ["tests/", "-v", "--cov=src"])

    assert result.returncode == 0, result.stdout + result.stderr
    expected = "tests/test_probe.py::test_one_monitoring_tool_is_registered PASSED"
    assert expected in result.stdout, result.stdout


def test_code_compiled_with_an_empty_filename_is_not_measured(tmp_path: Path) -> None:
    """The empty `co_filename` guard, as the failure it prevents rather than as a branch.

    `exec(compile(src, "", "exec"))` -- `pytest.importorskip`'s shape (`_pytest/outcomes.py`
    l. 256), and rustest's own compat shim's (`rustest/compat/pytest.py` l. 979) -- makes code
    objects whose `co_filename` is `""`. `canonical_filename("")` is `abspath("")`, i.e. the
    **current directory**, so under a bare `--cov` the directory matched the source tree and was
    recorded as a measured file. Measured before the fix: the terminal report died with

        ERROR: could not produce the coverage report: No source for code: '<dir>':
        [Errno 13] Permission denied

    and -- worse, because it outlives the run -- the written `.coverage` stayed poisoned, so a
    later `coverage report` or `coverage html` failed the same way. Both halves are asserted.
    """
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    _write(
        tmp_path / "gen.py",
        "def build(name):\n"
        '    src = f"def {name}():\\n    return {name!r}\\n"\n'
        "    namespace = {}\n"
        '    exec(compile(src, "", "exec"), namespace)\n'
        "    return namespace[name]\n",
    )
    _write(
        tmp_path / "test_gen.py",
        "import gen\n\n\ndef test_generated():\n    assert gen.build('greet')() == 'greet'\n",
    )

    result = _rustest(tmp_path, [".", "-q", "--cov"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "No source for code" not in result.stdout + result.stderr, result.stdout + result.stderr
    assert "TOTAL" in result.stdout, result.stdout

    # The directory itself must not be in the data at all -- the report merely happening to
    # succeed would not rule out a stray entry that a different reporter chokes on.
    assert os.path.basename(tmp_path) not in _lines(tmp_path / ".coverage")
    assert sorted(_lines(tmp_path / ".coverage")) == ["gen.py", "test_gen.py"]

    # ...and the file rustest wrote is still usable by coverage.py afterwards, which is the
    # half that survives the run.
    follow_up = _run([sys.executable, "-m", "coverage", "report"], tmp_path)
    assert follow_up.returncode == 0, follow_up.stdout + follow_up.stderr


def test_a_branch_configuration_is_refused_before_the_run(tmp_path: Path) -> None:
    """`branch = True` in the config is the same request as `--cov-branch`, arriving quietly.

    Without the refusal the run completes and prints a **line** percentage under a
    configuration that asked for branches, with no Branch/BrPart columns to signal it: measured
    on this fixture at 81 % against the 75 % a real branch run reports. That is the
    overstatement `branch_refusal` exists to prevent, and a config file is the one door where
    nothing on the command line hints at it.
    """
    _tiny_tree(tmp_path)
    _write(tmp_path / ".coveragerc", "[run]\nbranch = True\n")

    result = _rustest(tmp_path, ["tests/", "-q", "--cov=src"])

    assert result.returncode == 4, (result.returncode, result.stdout, result.stderr)
    assert "branch = True in the coverage configuration" in result.stderr, result.stderr
    # Refused *before* anything ran: no data file, and no test output.
    assert not (tmp_path / ".coverage").exists()
    assert "passed" not in result.stderr, result.stderr


def test_the_reporter_is_built_with_branches_off(tmp_path: Path) -> None:
    """The structural half of the same guarantee: no path reaches the reporter with branches on.

    `prepare` refuses a configured branch request, but `combine_and_report` must not depend on
    that having happened -- an embedded caller, or a future config source, could reach it
    directly. Asserted by handing it a tree whose `.coveragerc` asks for branches and checking
    the `Coverage` it builds.
    """
    import coverage

    _write(tmp_path / ".coveragerc", "[run]\nbranch = True\n")
    _write(tmp_path / "lib.py", "def f(n):\n    return n\n")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    built: list[object] = []
    original = coverage.Coverage

    class Recording(original):  # pyright: ignore[reportUntypedBaseClass]
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # pyright: ignore[reportArgumentType]
            built.append(self)

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        coverage.Coverage = Recording  # pyright: ignore[reportAttributeAccessIssue]
        _ = _v2_coverage.combine_and_report(
            data_dir=str(data_dir),
            sources=[str(tmp_path)],
            data_file=str(tmp_path / ".coverage"),
            reports=[("term", None)],
            stream=io.StringIO(),
        )
    finally:
        coverage.Coverage = original  # pyright: ignore[reportAttributeAccessIssue]
        os.chdir(cwd)

    assert built, "combine_and_report must build a Coverage object"
    assert all(not getattr(c, "config").branch for c in built), [
        getattr(c, "config").branch for c in built
    ]


def test_cov_writes_a_combined_coverage_file_and_a_term_report(tmp_path: Path) -> None:
    """The default surface end to end: `.coverage` in coverage.py's own place and format."""
    _tiny_tree(tmp_path)
    result = _rustest(tmp_path, ["tests/", "-q", "--cov=src"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "TOTAL" in result.stdout, result.stdout
    combined = tmp_path / ".coverage"
    assert combined.exists(), sorted(p.name for p in tmp_path.iterdir())

    # coverage.py's own CLI reads it with no arguments -- the whole ecosystem-compat claim.
    follow_up = _run([sys.executable, "-m", "coverage", "report"], tmp_path)
    assert follow_up.returncode == 0, follow_up.stdout + follow_up.stderr
    assert "lib.py" in follow_up.stdout


def test_an_unimported_source_file_reports_zero_rather_than_vanishing(tmp_path: Path) -> None:
    """`Coverage._post_save_work`'s job, reproduced -- see `_v2_coverage._touch_unexecuted`.

    The file the suite never imports is the one a coverage number exists to find, and a plain
    `combine` + `report` omits it entirely.
    """
    _tiny_tree(tmp_path)
    _write(tmp_path / "src" / "forgotten.py", "def nobody_calls_me():\n    return 1\n")
    result = _rustest(tmp_path, ["tests/", "-q", "--cov=src"])

    assert result.returncode == 0, result.stdout + result.stderr
    row = [line for line in result.stdout.splitlines() if "forgotten.py" in line]
    assert row and row[0].rstrip().endswith("0%"), result.stdout


def test_the_per_worker_data_directory_is_removed(tmp_path: Path) -> None:
    """The scratch directory is intermediate state, and a leftover would seed the next run.

    Asserted on **the directory this run created**, obtained from the `_CoverageRun` itself.
    Globbing the machine's `%TEMP%` for `rustest-cov-*` before and after -- the first version of
    this test -- is a race against every other rustest process on the box, including the rest of
    this suite running in parallel: it can fail because someone else's run is mid-flight, and it
    can pass while leaking, if another run happens to clean up in the same window.
    """
    from rustest.core import _CoverageRun  # pyright: ignore[reportPrivateUsage]

    _write(tmp_path / "pytest.ini", "[pytest]\n")
    (tmp_path / "src").mkdir()

    prepared = _CoverageRun.prepare([str(tmp_path / "src")], None, [str(tmp_path)])
    data_dir = Path(json.loads(prepared.wire or "{}")["data_dir"])
    assert data_dir.is_dir(), data_dir

    with prepared:
        pass
    assert not data_dir.exists(), data_dir

    # ...and `cleanup` is idempotent, because `__exit__` and an explicit call can both reach it.
    prepared.cleanup()

    # The end-to-end half: a real run leaves nothing behind under the directory it made. The
    # run's own `.coverage` is the only artefact, and it is not in the scratch tree.
    _tiny_tree(tmp_path)
    result = _rustest(tmp_path, ["tests/", "-q", "--cov=src"])
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".coverage").exists()


def test_xml_report_is_written_where_asked(tmp_path: Path) -> None:
    _tiny_tree(tmp_path)
    result = _rustest(tmp_path, ["tests/", "-q", "--cov=src", "--cov-report=xml:out/cov.xml"])

    assert result.returncode == 0, result.stdout + result.stderr
    written = tmp_path / "out" / "cov.xml"
    assert written.exists()
    assert "<coverage" in written.read_text(encoding="utf-8")
    # `xml` alone means no terminal table, exactly as pytest-cov's `--cov-report=xml` does.
    assert "TOTAL" not in result.stdout, result.stdout


def test_cov_branch_is_refused_loudly(tmp_path: Path) -> None:
    """Deferred, not approximated. Silently measuring lines under a branch threshold would
    report a number higher than the truth, which is the one failure a coverage tool must not
    have -- so this is exit 4, pytest's usage error, and it fires even without `--cov`."""
    _tiny_tree(tmp_path)
    result = _rustest(tmp_path, ["tests/", "-q", "--cov=src", "--cov-branch"])
    assert result.returncode == 4
    assert "--cov-branch asks for branch coverage" in result.stderr

    alone = _rustest(tmp_path, ["tests/", "-q", "--cov-branch"])
    assert alone.returncode == 4, alone.stderr


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--v1", "--cov=src"], "cannot be combined with --v1"),
        (["--v2-collect-only", "--cov=src"], "collect-only"),
        (["--cov-report=term"], "without --cov"),
        (["--cov=src", "--cov-report=html"], "term"),
        (["--cov=nope"], "not a directory"),
    ],
)
def test_usage_errors_exit_four(tmp_path: Path, args: list[str], expected: str) -> None:
    """Every one of these is a request rustest cannot honour, and the alternative to refusing
    is running the suite and reporting nothing -- the silent-no-op shape the CLI refuses
    everywhere else."""
    _tiny_tree(tmp_path)
    result = _rustest(tmp_path, ["tests/", "-q", *args])
    assert result.returncode == 4, (result.returncode, result.stdout, result.stderr)
    assert expected in result.stderr, result.stderr


def test_the_wire_object_is_omitted_without_cov_and_shaped_like_the_contract(
    tmp_path: Path,
) -> None:
    """`_CoverageRun.wire` is the *whole* protocol footprint, so its two shapes are pinned.

    The keys and their spelling are `src/v2/protocol.rs::CoverageWire`'s golden line; the
    `None` is what makes a plain run's `init` byte-identical to the pre-v5 form.
    """
    from rustest.core import _CoverageRun  # pyright: ignore[reportPrivateUsage]

    assert _CoverageRun.disabled().wire is None

    (tmp_path / "src").mkdir()
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    prepared = _CoverageRun.prepare([str(tmp_path / "src")], None, [str(tmp_path)])
    try:
        wire = prepared.wire
        assert wire is not None
        decoded = json.loads(wire)
        assert sorted(decoded) == ["data_dir", "sources"]
        assert decoded["sources"] == [str(tmp_path / "src").replace("\\", "/")]
        assert "\\" not in decoded["data_dir"]
    finally:
        prepared.cleanup()


# ---------------------------------------------------------------------------
# the differential
# ---------------------------------------------------------------------------


def _measure_both(tree: Path, source: str) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Measure *tree* with `rustest --cov` and with `coverage run -m pytest`."""
    rustest = _rustest(tree, [".", "-q", f"--cov={source}"])
    assert rustest.returncode == 0, rustest.stdout + rustest.stderr

    env = _clean_env()
    env["COVERAGE_FILE"] = str(tree / ".coverage.oracle")
    oracle = _run(
        [sys.executable, "-m", "coverage", "run", f"--source={source}", "-m", "pytest", ".", "-q"],
        tree,
        env,
    )
    assert oracle.returncode == 0, oracle.stdout + oracle.stderr

    return _lines(tree / ".coverage"), _lines(tree / ".coverage.oracle")


def test_the_corpus_smoke_case_line_set_matches_coverage_py(tmp_path: Path) -> None:
    """The plan's smoke case: one known file, one asserted line set, two measurements.

    Tolerance: **none**. The sets are compared for equality, not for a subset or a percentage,
    because the accepted differences documented in the Task 3 report are all *outside* what
    this fixture contains -- it has no PEP 649 annotation bodies (the one construct where
    coverage.py's own two cores disagree) and no third-party code inside the source tree.
    A tolerance here would only ever hide a regression.

    The case is copied to `tmp_path` rather than measured in place: both runners write a
    `.coverage` into the tree, and the conformance corpus is not a scratch directory.
    """
    tree = tmp_path / "line-sets"
    shutil.copytree(
        CORPUS_CASE / "line-sets",
        tree,
        ignore=shutil.ignore_patterns("__pycache__", ".rustest_cache"),
    )
    _write(tree / "pytest.ini", "[pytest]\n")

    ours, theirs = _measure_both(tree, ".")

    assert ours == theirs, {
        name: (ours.get(name), theirs.get(name))
        for name in set(ours) | set(theirs)
        if ours.get(name) != theirs.get(name)
    }
    assert ours["covered.py"] == COVERED_PY_LINES, ours["covered.py"]


def test_line_sets_match_across_a_multi_worker_pool(tmp_path: Path) -> None:
    """The merge is the part pytest has no analogue for: N processes, one data file.

    A per-worker bug -- a lost file, a worker that never wrote, a suffix collision -- shows up
    here as a *missing* line rather than as a wrong one, which is exactly the failure a
    combined report cannot make visible on its own.
    """
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    _write(
        tmp_path / "conftest.py",
        "import os\nimport sys\n\nsys.path.insert(0, os.path.dirname(__file__))\n",
    )
    _write(
        tmp_path / "lib.py",
        "".join(f"def f{i}(x):\n    return x + {i}\n\n\n" for i in range(6)),
    )
    for i in range(6):
        _write(
            tmp_path / f"test_{i}.py",
            f"import lib\n\n\ndef test_{i}():\n    assert lib.f{i}(1) == {1 + i}\n",
        )

    rustest = _rustest(tmp_path, [".", "-q", "-n", "4", "--cov=."])
    assert rustest.returncode == 0, rustest.stdout + rustest.stderr
    env = _clean_env()
    env["COVERAGE_FILE"] = str(tmp_path / ".coverage.oracle")
    oracle = _run(
        [sys.executable, "-m", "coverage", "run", "--source=.", "-m", "pytest", ".", "-q"],
        tmp_path,
        env,
    )
    assert oracle.returncode == 0, oracle.stdout + oracle.stderr

    ours, theirs = _lines(tmp_path / ".coverage"), _lines(tmp_path / ".coverage.oracle")
    assert ours == theirs, (ours, theirs)
    # ...and the merge really did carry every worker's share: all six functions ran, each in
    # whichever process the stem hash sent its file to.
    assert len(ours["lib.py"]) == 12, ours["lib.py"]
