"""The pytest fallback: a session that needs pytest is handed to pytest, whole.

Three layers: the argv translation and the child process (pure, with ``Popen`` replaced),
the CLI wiring (a stubbed ``rust`` module), and real end-to-end runs. The end-to-end ones
that depend on the engine stopping at the collection barrier (``pytest_fallback`` on
``rust.run``) are marked in their docstrings; the others need no engine change.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rustest import _fallback, cli

from .helpers import clean_env, make_tree, run, stub_rust_module

# -- the argv translation ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--pattern", "a", "--marks=b"], ["-k", "a", "-m", "b"]),
        (["--no-capture", "-x", "--lf"], ["-s", "-x", "--lf"]),
        (["--color", "always"], ["--color=yes"]),
        (["--color=never", "--color=auto"], ["--color=no", "--color=auto"]),
        (["--report-json", "r.json", "--llm", "--llm-full", "--ascii"], []),
        (["--no-pytest-fallback", "--no-codeblocks", "t.py::t[a]"], ["t.py::t[a]"]),
        (["-n", "4", "--workers=2", "-nauto"], ["-n", "4", "-n", "2", "-nauto"]),
        (
            ["--runslow", "-k", "x", "-o", "addopts=", "-p", "plug", "dir"],
            ["--runslow", "-k", "x", "-o", "addopts=", "-p", "plug", "dir"],
        ),
    ],
)
def test_the_argv_translation(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], expected: list[str]
):
    monkeypatch.setattr(_fallback, "_installed", lambda module: True)
    assert _fallback.pytest_argv(argv)[0] == expected


def test_n_is_dropped_without_xdist_and_codeblocks_without_its_plugin(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(_fallback, "_installed", lambda module: False)
    argv, dropped_n = _fallback.pytest_argv(
        ["-n", "4", "--workers", "2", "-n8", "--codeblocks", "x"]
    )
    assert (argv, dropped_n) == (["x"], True)


# -- the child process ---------------------------------------------------------------------


class _Child:
    """A ``Popen`` that records its argv; its first ``wait`` is interrupted, as by Ctrl-C."""

    calls: list[list[str]] = []

    def __init__(self, argv: list[str]) -> None:
        self.calls.append(argv)
        self._waits = 0

    def wait(self) -> int:
        self._waits += 1
        if self._waits == 1:
            raise KeyboardInterrupt
        return 2


def test_pytest_is_started_with_the_users_arguments_and_its_code_is_returned(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(_fallback, "_installed", lambda module: True)
    monkeypatch.setattr(_fallback.subprocess, "Popen", _Child)
    _Child.calls.clear()

    code = _fallback.run(["--pattern", "a", "-n", "3"], ["conftest.py::pytest_configure is a hook"])

    assert code == 2  # pytest's own, after riding out the interrupt
    assert _Child.calls == [
        [sys.executable, "-m", "pytest", "-k", "a", "-n", "3", "-o", "cache_dir=.rustest_cache"]
    ]
    err = capsys.readouterr().err
    assert err.startswith("rustest: running this session under pytest, because ")
    assert "conftest.py::pytest_configure is a hook" in err
    assert "pytest's speed" in err and "--no-pytest-fallback" in err


def test_without_xdist_the_notice_says_n_was_ignored(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(_fallback, "_installed", lambda module: module == "pytest")
    monkeypatch.setattr(_fallback.subprocess, "Popen", _Child)
    _ = _fallback.run(["-n", "3"], ["why"])
    assert "-n was ignored because pytest-xdist is not installed" in capsys.readouterr().err


def test_pytest_missing_is_exit_4_with_instructions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(_fallback, "_installed", lambda module: False)
    monkeypatch.setattr(_fallback.subprocess, "Popen", _Child)
    _Child.calls.clear()

    assert _fallback.run(["-p", "plug"], ["-p plug loads a pytest plugin"]) == 4

    err = capsys.readouterr().err
    assert "pytest is not installed here" in err and "--no-pytest-fallback" in err
    assert _Child.calls == []


@pytest.mark.parametrize(
    "flag", [["--report-json", "r.json"], ["--llm"], ["--collect-only"], ["--co"]]
)
def test_machine_outputs_are_refused_loudly_for_now(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], flag: list[str]
):
    monkeypatch.setattr(_fallback.subprocess, "Popen", _Child)
    _Child.calls.clear()
    assert _fallback.run(["x", *flag], ["why"]) == 4
    assert "not supported under pytest fallback yet" in capsys.readouterr().err
    assert _Child.calls == []


# -- the CLI wiring, with a stubbed engine -------------------------------------------------


def _report(**extra: object) -> str:
    summary = dict.fromkeys(
        ("total", "passed", "failed", "skipped", "xfailed", "xpassed", "error", "deselected"), 0
    )
    return json.dumps(
        {
            "version": 2,
            "rootdir": "/x",
            "exit_code": 0,
            "summary": {**summary, "duration": 0.0},
            "tests": [],
            "collection_errors": [],
            **extra,
        }
    )


def _handoff(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], list[str]]]:
    seen: list[tuple[list[str], list[str]]] = []

    def fake(argv: list[str], reasons: list[str]) -> int:
        seen.append((list(argv), list(reasons)))
        return 17

    monkeypatch.setattr(_fallback, "run", fake)
    return seen


def test_a_report_that_names_hooks_hands_the_original_argv_to_pytest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    seen = _handoff(monkeypatch)
    flags: list[bool] = []

    def fake_run(*args: object, pytest_fallback: bool = False, **kwargs: object) -> str:
        flags.append(pytest_fallback)
        return _report(pytest_fallback=["conftest.py::pytest_collection_modifyitems"])

    monkeypatch.chdir(tmp_path)
    with stub_rust_module(run=fake_run):
        assert cli.main(["-k", "a", "--no-capture"]) == 17

    assert flags == [True]
    assert seen == [
        (
            ["-k", "a", "--no-capture"],
            ["conftest.py::pytest_collection_modifyitems is a pytest hook rustest does not run"],
        )
    ]


def test_no_pytest_fallback_asks_the_engine_not_to_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    seen = _handoff(monkeypatch)
    flags: list[bool] = []

    def fake_run(*args: object, pytest_fallback: bool = False, **kwargs: object) -> str:
        flags.append(pytest_fallback)
        return _report()

    monkeypatch.chdir(tmp_path)
    with stub_rust_module(run=fake_run):
        assert cli.main(["--no-pytest-fallback"]) == 0
    assert flags == [False] and seen == []


def test_collect_only_with_hooks_is_refused_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    def fake_collect(*args: object, pytest_fallback: bool = False, **kwargs: object) -> str:
        return json.dumps({"tests": [], "pytest_fallback": ["conftest.py::pytest_configure"]})

    monkeypatch.chdir(tmp_path)
    with stub_rust_module(collect=fake_collect):
        assert cli.main(["--collect-only"]) == 4
    assert "not supported under pytest fallback yet" in capsys.readouterr().err


def test_an_unknown_flag_without_pytest_keeps_todays_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(_fallback, "_installed", lambda module: False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        _ = cli.main(["--runslow"])
    assert exit_info.value.code == 4
    assert "unrecognized arguments: --runslow" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--pytest-compat", "--v1"])
def test_removed_flags_keep_their_own_message_and_never_reach_pytest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: str
):
    seen = _handoff(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert cli.main([flag, "--runslow"]) == 4
    assert "has been removed" in capsys.readouterr().err and seen == []


# -- real runs -----------------------------------------------------------------------------

_P1 = {
    "conftest.py": (
        "import pytest\n\n\n"
        "def pytest_addoption(parser):\n"
        '    parser.addoption("--runslow", action="store_true", default=False)\n\n\n'
        "def pytest_collection_modifyitems(config, items):\n"
        '    if config.getoption("--runslow"):\n'
        "        return\n"
        '    skip = pytest.mark.skip(reason="need --runslow")\n'
        "    for item in items:\n"
        '        if "slow" in item.keywords:\n'
        "            item.add_marker(skip)\n"
    ),
    "test_p1.py": (
        "import pytest\n\n\n"
        "def test_fast():\n    pass\n\n\n"
        "@pytest.mark.slow\ndef test_slow():\n    pass\n"
    ),
}


def _rustest(tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = clean_env()
    env["COLUMNS"] = "80"
    return run([sys.executable, "-m", "rustest", *args], tree, env)


def test_an_option_only_a_conftest_defines_runs_the_whole_session_under_pytest(tmp_path: Path):
    """``--runslow`` is not a rustest option; pytest knows it from the conftest."""
    tree = make_tree(tmp_path, "p1_flag", _P1)

    result = _rustest(tree, "--runslow")

    assert result.returncode == 0, result.stdout + result.stderr
    # pytest's own banner and summary, on stdout: rustest prints neither, so this is the real one
    assert "test session starts" in result.stdout and "2 passed" in result.stdout
    assert result.stderr.startswith("rustest: running this session under pytest, because ")
    assert "--runslow is not a rustest option" in result.stderr
    assert (tree / ".rustest_cache").exists()  # one cache for both runners


def test_p_plugin_hands_the_session_to_pytest_and_the_plugin_acts(tmp_path: Path):
    files = {
        "myplug.py": "def pytest_collection_modifyitems(items):\n    items[:] = items[:1]\n",
        "test_a.py": "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n",
    }
    tree = make_tree(tmp_path, "p_plugin", files)

    result = _rustest(tree, "-p", "myplug")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout and "2 passed" not in result.stdout
    assert "-p myplug loads a pytest plugin" in result.stderr


def test_a_typo_gets_pytests_own_usage_error(tmp_path: Path):
    tree = make_tree(tmp_path, "typo", _P1)
    result = _rustest(tree, "--runslwo")
    assert result.returncode == 4
    assert "--runslwo is not a rustest option" in result.stderr
    assert "unrecognized arguments: --runslwo" in result.stderr  # pytest's message


def test_a_hook_that_changes_what_runs_hands_the_session_to_pytest(tmp_path: Path):
    """Needs the engine to stop at the collection barrier (``pytest_fallback`` on ``rust.run``)."""
    tree = make_tree(tmp_path, "p1_hook", _P1)

    result = _rustest(tree)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 1 skipped" in result.stdout
    assert "conftest.py::pytest_collection_modifyitems" in result.stderr
    assert result.stderr.count("rustest: running this session under pytest") == 1


def test_no_pytest_fallback_runs_rustest_with_the_note(tmp_path: Path):
    """Needs the engine's hook list on collection (same rebuild)."""
    tree = make_tree(tmp_path, "p1_optout", _P1)

    result = _rustest(tree, "--no-pytest-fallback")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stderr  # rustest's summary line is on stderr
    assert "NOTE" in result.stderr and "running this session under pytest" not in result.stderr
