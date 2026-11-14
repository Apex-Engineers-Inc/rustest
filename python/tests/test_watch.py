"""``rustest._watch``: the watch loop, driven by a fake clock, filesystem and keyboard.

Nothing here needs a terminal.  What cannot be tested without one -- reading real keys through
``termios`` / ``msvcrt``, the screen clearing, how the rich display looks between reruns -- is
listed in the report that accompanies this file.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from rustest import _watch
from rustest._watch import RunSpec, TerminalKeys, watch

from .helpers import clean_env, make_tree, run

ROOT = os.path.abspath("/proj")
CWD = ROOT


def _p(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


class Clock:
    """Time only moves when something sleeps or a fake run says it took a while."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Keyboard:
    """Keys that arrive at given times; ``read`` returns the next one that is due."""

    def __init__(self, clock: Clock, script: list[tuple[float, str]]) -> None:
        self.clock = clock
        self.script = sorted(script)
        self.flushes = 0
        self.prompts: list[str] = []
        self.answers: list[str | None] = []

    @contextlib.contextmanager
    def raw(self) -> Iterator[None]:
        yield

    def read(self) -> str | None:
        if self.script and self.script[0][0] <= self.clock.now:
            return self.script.pop(0)[1]
        return None

    def flush(self) -> None:
        self.flushes += 1

    def prompt(self, text: str) -> str | None:
        self.prompts.append(text)
        return self.answers.pop(0)


class Disk:
    """A filesystem whose snapshot depends on the clock: ``steps`` are ``(time, snapshot)``."""

    def __init__(self, clock: Clock, steps: list[tuple[float, _watch.Snapshot]]) -> None:
        self.clock = clock
        self.steps = steps

    def __call__(self) -> _watch.Snapshot:
        current: _watch.Snapshot = {}
        for at, snapshot in self.steps:
            if at <= self.clock.now:
                current = snapshot
        return current


def _snap(**files: int) -> _watch.Snapshot:
    """``_snap(test_a=1)`` -> one file ``/proj/test_a.py`` with mtime 1; ``conftest``,
    ``src_mod`` and ``pytest_ini`` map to the obvious names."""
    names = {"pytest_ini": "pytest.ini"}
    return {_p(names.get(name, name + ".py")): (version, 10) for name, version in files.items()}


class Session:
    """Everything one ``watch`` call needs, and what it did."""

    def __init__(
        self,
        steps: list[tuple[float, _watch.Snapshot]],
        keys: list[tuple[float, str]],
        results: list[tuple[int, list[str]]] | None = None,
        run_takes: float = 0.0,
        **options: object,
    ) -> None:
        self.clock = Clock()
        self.keyboard = Keyboard(self.clock, keys)
        self.disk = Disk(self.clock, steps)
        self.out = io.StringIO()
        self.specs: list[RunSpec] = []
        self._results = list(results or [])
        self._run_takes = run_takes
        self.options = options

    def run(self, spec: RunSpec) -> tuple[int, list[str]]:
        self.specs.append(spec)
        self.clock.now += self._run_takes
        return self._results.pop(0) if self._results else (0, [])

    def go(self, paths: tuple[str, ...] = ("tests",), keyword: str | None = None) -> int:
        return watch(
            run=self.run,
            scan_files=self.disk,
            is_test_file=_watch.test_file_matcher([]),
            paths=list(paths),
            rootdir=ROOT,
            cwd=CWD,
            keyword=keyword,
            keys=self.keyboard,
            out=self.out,
            clock=self.clock,
            sleep=self.clock.sleep,
            **self.options,  # pyright: ignore[reportArgumentType]
        )


# --------------------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------------------


def test_it_runs_everything_first_then_quits_on_q() -> None:
    session = Session([(0, _snap(test_a=1))], keys=[(1.0, "q")])

    assert session.go() == 0
    assert session.specs == [RunSpec(["tests"], None, "starting")]
    assert "rustest --watch: starting" in session.out.getvalue()
    assert "watching 1 files | press h to show help, q to quit" in session.out.getvalue()


def test_the_screen_is_cleared_before_a_run_unless_told_not_to() -> None:
    cleared = Session([(0, _snap(test_a=1))], keys=[(1.0, "q")])
    _ = cleared.go()
    assert cleared.out.getvalue().startswith("\x1b[2J\x1b[H")

    plain = Session([(0, _snap(test_a=1))], keys=[(1.0, "q")], clear=False, dim=False)
    _ = plain.go()
    assert "\x1b" not in plain.out.getvalue()


def test_a_changed_test_file_reruns_it_with_last_failures_carried_by_node_id() -> None:
    session = Session(
        [(0, _snap(test_a=1, test_b=1)), (2.0, _snap(test_a=2, test_b=1))],
        keys=[(5.0, "q")],
        results=[(1, ["test_b.py::test_two", "test_a.py::test_one"]), (0, [])],
    )

    _ = session.go()

    assert session.specs[1] == RunSpec(
        [_p("test_a.py"), "test_b.py::test_two"], None, "test_a.py changed"
    )


def test_a_changed_source_conftest_or_config_file_runs_everything() -> None:
    for name in ("src_mod", "conftest", "pytest_ini"):
        session = Session(
            [(0, _snap(test_a=1, **{name: 1})), (2.0, _snap(test_a=1, **{name: 2}))],
            keys=[(5.0, "q")],
            results=[(1, ["test_a.py::t"]), (0, [])],
        )

        _ = session.go()

        assert [s.paths for s in session.specs] == [["tests"], ["tests"]], name
        assert "changed, running everything" in session.specs[1].why


def test_a_burst_of_saves_is_one_run() -> None:
    session = Session(
        [
            (0, _snap(test_a=1, test_b=1)),
            (2.0, _snap(test_a=2, test_b=1)),
            (2.05, _snap(test_a=2, test_b=2)),  # inside the 0.1 s debounce window
        ],
        keys=[(6.0, "q")],
    )

    _ = session.go()

    assert len(session.specs) == 2
    assert {os.path.basename(p) for p in session.specs[1].paths} == {"test_a.py", "test_b.py"}


def test_an_edit_made_during_a_run_queues_exactly_one_follow_up() -> None:
    session = Session(
        [(0, _snap(test_a=1)), (1.0, _snap(test_a=2)), (1.5, _snap(test_a=3))],
        keys=[(20.0, "q")],
        run_takes=3.0,  # the first run is still going when both edits land
    )

    _ = session.go()

    assert [s.why for s in session.specs] == ["starting", "test_a.py changed"]


def test_nothing_changing_runs_nothing() -> None:
    session = Session([(0, _snap(test_a=1))], keys=[(30.0, "q")])
    _ = session.go()
    assert len(session.specs) == 1


def test_a_deleted_test_file_forgets_its_failures_and_runs_nothing() -> None:
    session = Session(
        [(0, _snap(test_a=1, test_b=1)), (2.0, _snap(test_b=1))],
        keys=[(5.0, "f"), (6.0, "q")],
        results=[(1, ["test_a.py::t"])],
    )

    _ = session.go()

    assert len(session.specs) == 1
    assert "no failed tests to rerun" in session.out.getvalue()


# -- keys ------------------------------------------------------------------------------


def test_enter_reruns_everything_selected_and_a_clears_the_filters() -> None:
    session = Session(
        [(0, _snap(test_a=1))],
        keys=[(1.0, "t"), (2.0, "\r"), (3.0, "a"), (9.0, "q")],
    )
    session.keyboard.answers = ["slow"]

    _ = session.go()

    assert [(s.paths, s.keyword) for s in session.specs] == [
        (["tests"], None),
        (["tests"], "slow"),  # the name filter's own run
        (["tests"], "slow"),  # Enter keeps it
        (["tests"], None),  # `a` clears it
    ]


def test_f_reruns_only_the_failures_as_arguments() -> None:
    session = Session(
        [(0, _snap(test_a=1))],
        keys=[(1.0, "f"), (9.0, "q")],
        results=[
            (1, ["sub/test_x.py::TestC::test_y[1-2]"]),
            (1, ["sub/test_x.py::TestC::test_y[1-2]"]),
        ],
    )

    _ = session.go()

    expected = os.path.relpath(_p("sub", "test_x.py"), CWD) + "::TestC::test_y[1-2]"
    assert session.specs[1] == RunSpec([expected], None, "rerunning failed tests")


def test_p_filters_by_file_name_and_empty_clears_it() -> None:
    session = Session(
        [(0, _snap(test_alpha=1, test_beta=1, src_mod=1))],
        keys=[(1.0, "p"), (2.0, "p"), (9.0, "q")],
    )
    session.keyboard.answers = ["alpha", ""]

    _ = session.go()

    assert session.specs[1].paths == [_p("test_alpha.py")]
    assert session.specs[2].paths == ["tests"]
    assert "[file: alpha]" in session.out.getvalue()


def test_a_file_filter_that_matches_nothing_runs_nothing_and_says_so() -> None:
    session = Session([(0, _snap(test_a=1))], keys=[(1.0, "p"), (9.0, "q")])
    session.keyboard.answers = ["zzz"]

    _ = session.go()

    assert len(session.specs) == 1
    assert "no test file matches 'zzz'" in session.out.getvalue()


def test_the_file_filter_also_limits_what_a_change_reruns() -> None:
    session = Session(
        [(0, _snap(test_alpha=1, test_beta=1)), (3.0, _snap(test_alpha=2, test_beta=2))],
        keys=[(1.0, "p"), (9.0, "q")],
    )
    session.keyboard.answers = ["alpha"]

    _ = session.go()

    assert session.specs[-1].paths == [_p("test_alpha.py")]


def test_unknown_keys_do_nothing_and_ctrl_c_as_a_key_quits() -> None:
    session = Session([(0, _snap(test_a=1))], keys=[(1.0, "x"), (2.0, "\x1b"), (3.0, "\x03")])
    assert session.go() == 0
    assert len(session.specs) == 1


def test_keys_typed_during_a_run_are_discarded_and_preboot_runs_after_each() -> None:
    calls: list[int] = []
    session = Session(
        [(0, _snap(test_a=1)), (2.0, _snap(test_a=2))],
        keys=[(9.0, "q")],
        preboot=lambda: calls.append(1),
    )

    _ = session.go()

    assert session.keyboard.flushes == 2 and calls == [1, 1]


def test_ctrl_c_while_idle_quits_and_ctrl_c_during_a_run_does_not() -> None:
    interrupted = Session([(0, _snap(test_a=1)), (2.0, _snap(test_a=2))], keys=[(9.0, "q")])
    attempts = iter([KeyboardInterrupt, None])
    original = interrupted.run

    def run(spec: RunSpec) -> tuple[int, list[str]]:
        if next(attempts) is KeyboardInterrupt:
            raise KeyboardInterrupt
        return original(spec)

    interrupted.run = run  # pyright: ignore[reportAttributeAccessIssue]
    assert interrupted.go() == 0
    assert "interrupted" in interrupted.out.getvalue()
    assert len(interrupted.specs) == 1  # the first run died, the second ran

    idle = Session([(0, _snap(test_a=1))], keys=[])
    idle.keyboard.read = lambda: (_ for _ in ()).throw(KeyboardInterrupt())  # pyright: ignore
    assert idle.go() == 0


def test_only_test_files_inside_the_selection_rerun() -> None:
    chosen = _watch.selection(ROOT, ["tests/unit"], [], CWD)
    session = Session(
        [
            (
                0,
                {
                    _p("tests", "unit", "test_a.py"): (1, 1),
                    _p("tests", "integration", "test_b.py"): (1, 1),
                },
            ),
            (
                2.0,
                {
                    _p("tests", "unit", "test_a.py"): (1, 1),
                    _p("tests", "integration", "test_b.py"): (2, 1),
                },
            ),
            (
                4.0,
                {
                    _p("tests", "unit", "test_a.py"): (2, 1),
                    _p("tests", "integration", "test_b.py"): (2, 1),
                },
            ),
        ],
        keys=[(9.0, "q")],
        selected=chosen,
    )

    _ = session.go(paths=("tests/unit",))

    # the integration file's save (t=2) ran nothing; the unit file's (t=4) did
    assert [s.why for s in session.specs] == ["starting", "test_a.py changed"]


def test_the_file_filter_narrows_the_selection_and_never_widens_it() -> None:
    chosen = _watch.selection(ROOT, ["tests/unit"], [], CWD)
    files = {
        _p("tests", "unit", "test_a.py"): (1, 1),
        _p("tests", "integration", "test_a.py"): (1, 1),
    }
    session = Session([(0, files)], keys=[(1.0, "p"), (9.0, "q")], selected=chosen)
    session.keyboard.answers = ["test_a"]

    _ = session.go(paths=("tests/unit",))

    assert session.specs[1].paths == [_p("tests", "unit", "test_a.py")]


def test_selection_follows_arguments_then_testpaths_then_the_rootdir() -> None:
    by_args = _watch.selection(ROOT, ["tests/unit", "x/test_f.py::t"], ["ignored"], CWD)
    assert by_args(_p("tests", "unit", "test_a.py")) and by_args(_p("x", "test_f.py"))
    assert not by_args(_p("tests", "unit_other", "test_a.py")) and not by_args(_p("other.py"))
    by_testpaths = _watch.selection(ROOT, [], ["tests"], CWD)
    assert by_testpaths(_p("tests", "a", "test_a.py")) and not by_testpaths(
        _p("scripts", "test_r.py")
    )
    by_root = _watch.selection(ROOT, [], [], CWD)
    assert by_root(_p("anything", "test_a.py"))


@pytest.mark.parametrize(
    ("relative", "pattern", "expected"),
    [
        ("tests/test_a.py", "", True),
        ("tests/test_a.py", "test_a", True),
        ("tests/test_a.py", "tests/", True),
        ("src/test_a.py", "tests", False),
        ("tests/test_a.py", "*_a.py", True),
        ("tests/test_a.py", "tests/*.py", True),
        ("tests/test_a.py", "t[e]sts", True),
        ("tests/test_a.py", "*_b.py", False),
    ],
)
def test_the_p_filter_matches_substrings_and_globs_of_the_relative_path(
    relative: str, pattern: str, expected: bool
) -> None:
    assert _watch.matches_filter(relative, pattern) is expected


def test_h_prints_the_keys_and_runs_nothing_and_every_run_ends_with_the_hint() -> None:
    session = Session([(0, _snap(test_a=1))], keys=[(1.0, "h"), (9.0, "q")])

    _ = session.go()

    text = session.out.getvalue()
    assert len(session.specs) == 1
    assert "Enter  rerun the selected tests" in text and "p      filter by file name" in text
    assert text.count("press h to show help, q to quit") == 2  # after the run, after `h`


def test_ctrl_d_at_a_prompt_cancels_it_and_keeps_the_filter() -> None:
    session = Session(
        [(0, _snap(test_alpha=1, test_beta=1))],
        keys=[(1.0, "p"), (2.0, "p"), (3.0, "t"), (9.0, "q")],
    )
    session.keyboard.answers = ["alpha", None, None]  # a filter, then two cancelled prompts

    _ = session.go()

    assert [s.paths for s in session.specs] == [
        ["tests"],
        [_p("test_alpha.py")],
    ]  # the cancelled prompts ran nothing
    assert "[file: alpha]" in session.out.getvalue()


def test_a_source_change_says_why_everything_reran() -> None:
    session = Session(
        [(0, _snap(test_a=1, src_mod=1)), (2.0, _snap(test_a=1, src_mod=2))], keys=[(9.0, "q")]
    )

    _ = session.go()

    assert "dependency tracking is not available yet" in session.specs[1].note
    assert "everything selected reran" in session.out.getvalue()


def test_the_next_scan_waits_after_the_last_one_ended_and_backs_off_when_slow() -> None:
    class SlowDisk(Disk):
        def __init__(self, clock: Clock, takes: float) -> None:
            super().__init__(clock, [(0, _snap(test_a=1))])
            self.takes = takes
            self.starts: list[float] = []

        def __call__(self) -> _watch.Snapshot:
            self.starts.append(self.clock.now)
            self.clock.now += self.takes
            return super().__call__()

    for takes, floor in ((0.0, _watch.POLL), (1.0, 3.0)):
        session = Session([(0, _snap(test_a=1))], keys=[(40.0, "q")])
        session.disk = SlowDisk(session.clock, takes)

        _ = session.go()

        starts = session.disk.starts[1:]  # the first is the start-up snapshot
        gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
        assert len(gaps) > 3 and min(gaps) >= floor - 1e-9, (takes, min(gaps))


def test_an_idle_watch_scans_a_bounded_number_of_times() -> None:
    session = Session([(0, _snap(test_a=1))], keys=[(30.0, "q")])
    counter = [0]
    original = session.disk

    def counting() -> _watch.Snapshot:
        counter[0] += 1
        return original()

    session.disk = counting  # pyright: ignore[reportAttributeAccessIssue]
    _ = session.go()
    assert counter[0] <= 30.0 / _watch.POLL + 3


def test_cross_drive_paths_fall_back_to_absolute_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.path.relpath

    def relpath(path: str, start: str = os.curdir) -> str:
        if "failing" in path:
            raise ValueError("path is on mount 'D:', start on mount 'C:'")
        return real(path, start)

    monkeypatch.setattr(os.path, "relpath", relpath)
    session = Session(
        [(0, _snap(test_a=1))],
        keys=[(1.0, "f"), (9.0, "q")],
        results=[(1, ["failing/test_x.py::t"]), (1, [])],
    )

    _ = session.go()

    assert session.specs[1].paths == [os.path.join(ROOT, "failing/test_x.py") + "::t"]


# --------------------------------------------------------------------------------------
# what is watched
# --------------------------------------------------------------------------------------


def test_scan_watches_python_and_config_files_and_prunes_the_rest(tmp_path: Path) -> None:
    for rel in (
        "pkg/mod.py",
        "tests/test_a.py",
        "tests/conftest.py",
        "pytest.ini",
        "pyproject.toml",
        "notes.txt",
        "data.json",
        ".git/hooks.py",
        ".venv/lib/x.py",
        ".rustest_cache/v/c.py",
        "pkg/__pycache__/m.py",
        "node_modules/p/i.py",
        "thing.egg-info/top.py",
        "build_out/gen.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    found = {
        os.path.relpath(p, tmp_path).replace(os.sep, "/")
        for p in _watch.scan([str(tmp_path)], ["build_*"])
    }

    assert found == {
        "pkg/mod.py",
        "tests/test_a.py",
        "tests/conftest.py",
        "pytest.ini",
        "pyproject.toml",
    }


def test_a_virtual_environment_is_pruned_whatever_it_is_called(tmp_path: Path) -> None:
    (tmp_path / "myenv").mkdir()
    (tmp_path / "myenv" / "pyvenv.cfg").write_text("home = x", encoding="utf-8")
    (tmp_path / "myenv" / "lib.py").write_text("x", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x", encoding="utf-8")
    found = {os.path.basename(p) for p in _watch.scan([str(tmp_path)])}
    assert found == {"mod.py"}


def test_changes_are_additions_removals_and_modifications(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("1", encoding="utf-8")
    before = _watch.scan([str(tmp_path)])
    assert _watch.changes(before, _watch.scan([str(tmp_path)])) == set()

    target.write_text("22", encoding="utf-8")
    (tmp_path / "b.py").write_text("x", encoding="utf-8")
    after = _watch.scan([str(tmp_path)])
    assert _watch.changes(before, after) == {str(target), str(tmp_path / "b.py")}

    target.unlink()
    assert str(target) in _watch.changes(after, _watch.scan([str(tmp_path)]))


def test_roots_are_the_rootdir_plus_arguments_outside_it(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    outside = tmp_path / "elsewhere"
    root.mkdir()
    outside.mkdir()
    (outside / "test_o.py").write_text("", encoding="utf-8")
    roots = _watch.choose_roots(str(root), ["tests", str(outside / "test_o.py::t"), "."], str(root))
    assert roots == [str(root), str(outside)]


def test_test_files_are_recognised_by_python_files_patterns() -> None:
    default = _watch.test_file_matcher([])
    assert default("/x/test_a.py") and default("/x/a_test.py") and not default("/x/conftest.py")
    custom = _watch.test_file_matcher(["check_*.py"])
    assert custom("/x/check_a.py") and not custom("/x/test_a.py")


def test_failed_ids_come_from_a_report() -> None:
    report = {
        "tests": [
            {"id": "a::1", "status": "passed"},
            {"id": "a::2", "status": "failed"},
            {"id": "a::3", "status": "error"},
            {"id": "a::4", "status": "skipped"},
        ]
    }
    assert _watch.failed_ids(report) == ["a::2", "a::3"]
    assert _watch.failed_ids({}) == []


# --------------------------------------------------------------------------------------
# refusals, and the command line
# --------------------------------------------------------------------------------------


def test_refusals() -> None:
    assert _watch.refusal(llm=False, collect_only=False, interactive=True) is None
    assert "needs a terminal" in str(
        _watch.refusal(llm=False, collect_only=False, interactive=False)
    )
    assert "--llm" in str(_watch.refusal(llm=True, collect_only=False, interactive=True))
    assert "--collect-only" in str(_watch.refusal(llm=False, collect_only=True, interactive=True))
    assert "--no-pytest-fallback" in _watch.needs_pytest(["a hook"])


def _rustest(tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, "-m", "rustest", *args], tree, clean_env())


def test_watch_without_a_terminal_is_refused_with_exit_4(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "watch_pipe", {"test_a.py": "def test_a(): pass\n"})
    result = _rustest(tree, "--watch")  # stdin and stdout are pipes here
    assert result.returncode == 4
    assert "--watch needs a terminal" in result.stderr


def test_watch_with_a_session_that_needs_pytest_is_refused_not_looped(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "watch_fallback", {"test_a.py": "def test_a(): pass\n"})
    result = _rustest(tree, "--watch", "--runslow")  # an option only pytest could know
    assert result.returncode == 4
    assert "--watch does not run under pytest fallback yet" in result.stderr


# --------------------------------------------------------------------------------------
# the keyboard on Windows, with a fake console
# --------------------------------------------------------------------------------------


def test_windows_keys_read_one_character_and_swallow_arrow_prefixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    typed = list("a\x00Hq")

    class FakeMsvcrt:
        @staticmethod
        def kbhit() -> bool:
            return bool(typed)

        @staticmethod
        def getwch() -> str:
            return typed.pop(0)

    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt)
    monkeypatch.setattr(sys, "platform", "win32")
    keys = TerminalKeys()

    assert keys.read() == "a"
    assert keys.read() is None  # an arrow key: `\x00` then its code, both consumed
    assert keys.read() == "q"
    assert keys.read() is None
    typed.extend("xyz")
    keys.flush()
    assert typed == []


# --------------------------------------------------------------------------------------
# the keyboard on POSIX, with a fake fd, select and signals
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "key"),
    [
        (b"", None),
        (b"f", "f"),
        (b"ff", "f"),  # a burst is one key; the rest is dropped, not queued
        (b"\x1b[A", None),  # an arrow key is one key, and not one the watch uses
        (b"\x1bOP", None),
        (b"\x1b", "\x1b"),  # a lone Escape
        ("\u00e9".encode(), "\u00e9"),
        (b"\xff", None),
    ],
)
def test_one_read_is_one_key(data: bytes, key: str | None) -> None:
    assert _watch.parse_key(data) == key


def test_posix_keys_are_read_from_the_fd_not_from_buffered_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = [b"\x1b[A", b"f", b"q"]
    calls: list[str] = []

    class FakeStdin:
        @staticmethod
        def fileno() -> int:
            return 7

        @staticmethod
        def read(n: int) -> str:  # must never be used
            calls.append("sys.stdin.read")
            return ""

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "stdin", FakeStdin)
    keys = TerminalKeys()
    keys._select = lambda r, w, x, t: (r if chunks else [], [], [])  # pyright: ignore
    keys._read = lambda fd, n: (calls.append(f"os.read({fd})"), chunks.pop(0))[1]  # pyright: ignore

    assert [keys.read(), keys.read(), keys.read(), keys.read()] == [None, "f", "q", None]
    assert calls == ["os.read(7)"] * 3  # nothing went through `sys.stdin.read`


class _FakeSignals:
    SIGTERM, SIGTSTP, SIGCONT, SIG_DFL = 15, 20, 18, 0

    def __init__(self) -> None:
        self.handlers: dict[int, object] = {15: "old-term", 20: "old-tstp", 18: "old-cont"}

    def signal(self, number: int, handler: object) -> object:
        previous = self.handlers[number]
        self.handlers[number] = handler
        return previous


class _FakeTermios:
    TCSADRAIN = 1

    def __init__(self) -> None:
        self.mode = "cooked"
        self.log: list[str] = []

    def tcgetattr(self, fd: int) -> str:
        return self.mode

    def tcsetattr(self, fd: int, when: int, attrs: str) -> None:
        self.mode = attrs
        self.log.append(f"restore:{attrs}")


class _FakeTty:
    def __init__(self, termios: _FakeTermios) -> None:
        self.termios = termios

    def setcbreak(self, fd: int) -> None:
        self.termios.mode = "cbreak"
        self.termios.log.append("cbreak")


def _cbreak() -> tuple[_watch._Cbreak, _FakeTermios, _FakeSignals, list[tuple[int, int]]]:  # pyright: ignore
    termios, signals, kills = _FakeTermios(), _FakeSignals(), []
    mode = _watch._Cbreak(  # pyright: ignore[reportPrivateUsage]
        3,
        termios,
        _FakeTty(termios),
        signals,
        lambda pid, number: kills.append((pid, number)),  # pyright: ignore
    )
    return mode, termios, signals, kills


def test_cbreak_restores_the_terminal_and_the_handlers_on_leaving() -> None:
    mode, termios, signals, _ = _cbreak()
    mode.enter()
    assert termios.mode == "cbreak" and signals.handlers[15] == mode._on_term  # pyright: ignore
    mode.leave()
    assert termios.mode == "cooked"
    assert signals.handlers == {15: "old-term", 20: "old-tstp", 18: "old-cont"}


def test_sigterm_raises_systemexit_so_the_finally_that_restores_the_mode_runs() -> None:
    mode, termios, _, _ = _cbreak()
    mode.enter()
    with pytest.raises(SystemExit) as raised:
        try:
            mode._on_term(15, None)  # pyright: ignore[reportPrivateUsage]
        finally:
            mode.leave()
    assert raised.value.code == 143 and termios.mode == "cooked"


def test_ctrl_z_restores_the_mode_stops_the_process_and_resumes_in_cbreak() -> None:
    mode, termios, signals, kills = _cbreak()
    mode.enter()

    mode._on_tstp(20, None)  # pyright: ignore[reportPrivateUsage]
    assert termios.mode == "cooked"  # the shell gets a normal terminal while stopped
    assert signals.handlers[20] == signals.SIG_DFL and kills == [(os.getpid(), 20)]

    mode._on_cont(18, None)  # pyright: ignore[reportPrivateUsage]
    assert termios.mode == "cbreak" and signals.handlers[20] == mode._on_tstp  # pyright: ignore


def test_a_cancelled_prompt_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert TerminalKeys().prompt("x: ") is None
    monkeypatch.setattr("builtins.input", lambda prompt: "typed")
    assert TerminalKeys().prompt("x: ") == "typed"
