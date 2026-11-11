"""The pytest fallback: a session that needs pytest is handed to pytest, whole.

Three layers: the argv translation and the child process (pure, with ``Popen``/``execv``
replaced), the CLI wiring (a stubbed ``rust`` module), and real end-to-end runs.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rustest import _fallback, cli

from .helpers import clean_env, make_tree, run, stub_rust_module


@pytest.fixture(autouse=True)
def _no_ambient_pytest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both variables are themselves fallback reasons; an ambient one would hand every
    in-process run over."""
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.delenv("PYTEST_PLUGINS", raising=False)


# -- the argv translation ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--pattern", "a", "--marks=b"], ["-k", "a", "-m", "b"]),
        (["--no-capture", "-x", "--lf"], ["-s", "-x", "--lf"]),
        (["--color", "always"], ["--color=yes"]),
        (["--color=never", "--color=auto"], ["--color=no", "--color=auto"]),
        (["--llm-full", "--ascii", "--codeblocks", "--no-codeblocks"], []),
        (["--no-pytest-fallback", "t.py::t[a]"], ["t.py::t[a]"]),
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


def test_n_is_dropped_without_xdist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_fallback, "_installed", lambda module: False)
    argv, dropped_n = _fallback.pytest_argv(["-n", "4", "--workers", "2", "-n8", "x"])
    assert (argv, dropped_n) == (["x"], True)


# -- the child process ---------------------------------------------------------------------


class _Child:
    """A ``Popen`` that records its argv; its first ``wait`` is interrupted, as by Ctrl-C."""

    calls: list[list[str]] = []
    interrupts = 1
    terminated = 0

    def __init__(self, argv: list[str]) -> None:
        self.calls.append(argv)
        self._waits = 0

    def wait(self) -> int:
        self._waits += 1
        if self._waits <= self.interrupts:
            raise KeyboardInterrupt
        return 2

    def terminate(self) -> None:
        type(self).terminated += 1


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> type[_Child]:
    monkeypatch.setattr(_fallback, "_WINDOWS", True)
    monkeypatch.setattr(_fallback, "_installed", lambda module: True)
    monkeypatch.setattr(_fallback.subprocess, "Popen", _Child)
    _Child.calls.clear()
    _Child.interrupts, _Child.terminated = 1, 0
    return _Child


def test_windows_starts_pytest_waits_through_one_ctrl_c_and_returns_its_code(
    windows: type[_Child], capsys: pytest.CaptureFixture[str]
):
    code = _fallback.run(["--pattern", "a", "-n", "3"], ["conftest.py::pytest_configure"])

    assert code == 2
    assert windows.calls == [
        [sys.executable, "-m", "pytest", "-k", "a", "-n", "3", "-o", "cache_dir=.rustest_cache"]
    ]
    assert windows.terminated == 0
    err = capsys.readouterr().err
    assert err.startswith("rustest: handing this session to pytest: conftest.py::pytest_configure.")
    assert "It will run at pytest's speed." in err


def test_windows_terminates_the_child_on_a_second_ctrl_c(windows: type[_Child]):
    windows.interrupts = 2
    assert _fallback.run(["x"], ["why"]) == 2
    assert windows.terminated == 1


def test_posix_replaces_the_process_with_pytest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    class _Exec(Exception):
        pass

    seen: list[tuple[str, list[str]]] = []

    def fake_execv(path: str, argv: list[str]) -> None:
        seen.append((path, argv))
        raise _Exec

    monkeypatch.setattr(_fallback, "_WINDOWS", False)
    monkeypatch.setattr(_fallback, "_installed", lambda module: True)
    monkeypatch.setattr(_fallback.os, "execv", fake_execv)

    with pytest.raises(_Exec):
        _ = _fallback.run(["-k", "a"], ["why"])

    command = [sys.executable, "-m", "pytest", "-k", "a", "-o", "cache_dir=.rustest_cache"]
    assert seen == [(sys.executable, command)]
    assert "handing this session to pytest: why." in capsys.readouterr().err


@pytest.mark.parametrize(("optout", "present"), [("those hooks will not run", True), (None, False)])
def test_the_opt_out_sentence_is_only_said_when_it_is_true(
    windows: type[_Child], capsys: pytest.CaptureFixture[str], optout: str | None, present: bool
):
    _ = _fallback.run(["x"], ["why"], optout)
    err = capsys.readouterr().err
    assert ("Pass --no-pytest-fallback to run it with rustest anyway" in err) is present
    if optout:
        assert "(those hooks will not run)" in err


def test_without_xdist_the_notice_says_n_was_ignored(
    windows: type[_Child], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(_fallback, "_installed", lambda module: module == "pytest")
    _ = _fallback.run(["-n", "3"], ["why"])
    assert "-n was ignored because pytest-xdist is not installed" in capsys.readouterr().err


def test_pytest_missing_is_exit_4_with_instructions(
    windows: type[_Child], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(_fallback, "_installed", lambda module: False)
    assert _fallback.run(["-p", "plug"], ["-p plug loads a pytest plugin"]) == 4
    err = capsys.readouterr().err
    assert "pytest is not installed here" in err and "--no-pytest-fallback" in err
    assert windows.calls == []


@pytest.mark.parametrize(
    "flag", [["--report-json", "r.json"], ["--llm"], ["--collect-only"], ["--co"]]
)
def test_machine_outputs_are_refused_loudly_for_now(
    windows: type[_Child], capsys: pytest.CaptureFixture[str], flag: list[str]
):
    assert _fallback.run(["x", *flag], ["why"]) == 4
    assert "not supported under pytest fallback yet" in capsys.readouterr().err
    assert windows.calls == []


@pytest.mark.parametrize("arg", ["README.md", "docs/Guide.MD", "README.md::block"])
def test_a_markdown_argument_is_refused_because_pytest_cannot_run_rustests_blocks(
    windows: type[_Child], capsys: pytest.CaptureFixture[str], arg: str
):
    assert _fallback.run([arg, "--codeblocks"], ["why"], "they will be ignored") == 4
    err = capsys.readouterr().err
    assert "cannot run rustest's documentation code blocks" in err
    assert "--no-pytest-fallback" in err and windows.calls == []


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


Handoff = tuple[list[str], list[str], "str | None"]


def _handoff(monkeypatch: pytest.MonkeyPatch) -> list[Handoff]:
    seen: list[Handoff] = []

    def fake(argv: list[str], reasons: list[str], optout: str | None = None) -> int:
        seen.append((list(argv), list(reasons), optout))
        return 17

    monkeypatch.setattr(_fallback, "run", fake)
    return seen


def _quiet_engine(**extra: object):
    def fake_run(*args: object, pytest_fallback: bool = False, **kwargs: object) -> str:
        return _report(**extra)

    return fake_run


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
            "those hooks will not run",
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


def test_no_pytest_fallback_in_addopts_counts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    seen = _handoff(monkeypatch)
    tree = make_tree(
        tmp_path, "optout_addopts", {"pytest.ini": "[pytest]\naddopts = --no-pytest-fallback\n"}
    )
    monkeypatch.chdir(tree)
    with stub_rust_module(run=_quiet_engine()):
        assert cli.main([]) == 0
    assert seen == []


def test_collect_only_with_hooks_is_refused_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    def fake_collect(*args: object, pytest_fallback: bool = False, **kwargs: object) -> str:
        return json.dumps({"tests": [], "pytest_fallback": ["conftest.py::pytest_configure"]})

    monkeypatch.chdir(tmp_path)
    with stub_rust_module(collect=fake_collect):
        assert cli.main(["--collect-only"]) == 4
    assert "not supported under pytest fallback yet" in capsys.readouterr().err


def test_an_unknown_flag_is_handed_over_and_an_abbreviation_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """``--report=html`` could be a conftest option; rustest must not read it as
    ``--report-json`` (argparse abbreviation), exactly as pytest's own parser does not."""
    seen = _handoff(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--report=html", "--runslow"]) == 17
    assert seen == [
        (
            ["--report=html", "--runslow"],
            ["--report=html is not a rustest option", "--runslow is not a rustest option"],
            None,
        )
    ]


def test_a_reason_that_came_from_addopts_names_the_ini(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    seen = _handoff(monkeypatch)
    tree = make_tree(
        tmp_path, "addopts_src", {"pytest.ini": "[pytest]\naddopts = --runslow -p myplug\n"}
    )
    monkeypatch.chdir(tree)
    assert cli.main(["-x"]) == 17
    reasons = seen[0][1]
    assert any("-p myplug loads a pytest plugin (from addopts in " in r for r in reasons)
    assert seen[0][0] == ["-x"]  # pytest reads its own addopts


def test_a_rustest_flag_with_a_bad_value_is_rustests_own_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    seen = _handoff(monkeypatch)
    monkeypatch.chdir(tmp_path)
    for argv in (["-n", "abc"], ["-n"], ["--color=blue"]):
        with pytest.raises(SystemExit) as exit_info:
            _ = cli.main(argv)
        assert exit_info.value.code == 4
    assert "rustest: error:" in capsys.readouterr().err and seen == []


def test_help_is_rustests_even_when_addopts_names_a_plugin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    seen = _handoff(monkeypatch)
    tree = make_tree(tmp_path, "help", {"pytest.ini": "[pytest]\naddopts = -p myplug\n"})
    monkeypatch.chdir(tree)
    with pytest.raises(SystemExit) as exit_info:
        _ = cli.main(["--help"])
    assert exit_info.value.code == 0 and seen == []
    assert "usage: rustest" in capsys.readouterr().out


def test_an_unsupported_ini_override_is_a_fallback_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    seen = _handoff(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["-o", "xfail_strict=true"]) == 17
    assert seen[0][1] == ["-o xfail_strict is an ini option rustest cannot override"]


@pytest.mark.parametrize("var", ["PYTEST_ADDOPTS", "PYTEST_PLUGINS"])
def test_the_pytest_environment_variables_rustest_ignores_are_a_fallback_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, var: str
):
    seen = _handoff(monkeypatch)
    monkeypatch.setenv(var, "-q" if var == "PYTEST_ADDOPTS" else "myplug")
    monkeypatch.chdir(tmp_path)
    assert cli.main([]) == 17
    assert seen[0][1] == [f"{var} is set, and rustest ignores it"]
    assert seen[0][2] == "they will be ignored"


def test_ignored_flag_notes_wait_until_the_session_stays_with_rustest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    _ = _handoff(monkeypatch)
    monkeypatch.chdir(tmp_path)

    with stub_rust_module(run=_quiet_engine()):
        assert cli.main(["-ra"]) == 0
    assert "NOTE: -ra is a pytest reporting option" in capsys.readouterr().err

    with stub_rust_module(run=_quiet_engine(pytest_fallback=["conftest.py::pytest_configure"])):
        assert cli.main(["-ra"]) == 17
    assert "NOTE" not in capsys.readouterr().err


def test_an_unknown_flag_without_pytest_keeps_todays_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(_fallback, "_installed", lambda module: False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        _ = cli.main(["--runslow"])
    assert exit_info.value.code == 4
    assert "rustest: error: unrecognized arguments: --runslow" in capsys.readouterr().err


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
    assert result.stderr.startswith("rustest: handing this session to pytest: ")
    assert "--runslow is not a rustest option" in result.stderr
    assert "--no-pytest-fallback" not in result.stderr  # with it, --runslow is a usage error


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
    assert "(they will be ignored)" in result.stderr


def test_a_plugin_named_in_addopts_is_handed_over_and_says_where_it_came_from(tmp_path: Path):
    files = {
        "pytest.ini": "[pytest]\naddopts = -p myplug\n",
        "myplug.py": "def pytest_collection_modifyitems(items):\n    items[:] = items[:1]\n",
        "test_a.py": "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n",
    }
    tree = make_tree(tmp_path, "p_addopts", files)

    result = _rustest(tree)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "(from addopts in " in result.stderr


def test_a_typo_gets_pytests_own_usage_error(tmp_path: Path):
    tree = make_tree(tmp_path, "typo", _P1)

    result = _rustest(tree, "--runslwo")

    assert result.returncode == 4
    assert "--runslwo is not a rustest option" in result.stderr
    # Only pytest's usage line has `[file_or_dir]`; rustest's own error would say `rustest: error`.
    assert "[options] [file_or_dir]" in result.stderr and "rustest: error" not in result.stderr


def test_the_failures_of_a_fallen_back_run_feed_lf_in_a_rustest_run(tmp_path: Path):
    """One cache: pytest runs under `-o cache_dir=.rustest_cache`, which rustest also reads."""
    files = {
        "conftest.py": "def pytest_collection_modifyitems(config, items):\n    pass\n",
        "test_a.py": "def test_ok():\n    pass\n\n\ndef test_bad():\n    assert 0\n",
    }
    tree = make_tree(tmp_path, "lf_across", files)

    first = _rustest(tree)
    assert first.returncode == 1, first.stdout + first.stderr
    assert "1 failed, 1 passed" in first.stdout  # pytest's summary

    second = _rustest(tree, "--no-pytest-fallback", "--lf")
    assert "1 failed" in second.stderr and "passed" not in second.stderr


def test_a_hook_that_changes_what_runs_hands_the_session_to_pytest(tmp_path: Path):
    tree = make_tree(tmp_path, "p1_hook", _P1)

    result = _rustest(tree)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 1 skipped" in result.stdout
    assert "conftest.py::pytest_collection_modifyitems" in result.stderr
    assert result.stderr.count("handing this session to pytest") == 1
    assert "(those hooks will not run)" in result.stderr


def test_no_pytest_fallback_runs_rustest_with_the_note(tmp_path: Path):
    tree = make_tree(tmp_path, "p1_optout", _P1)

    result = _rustest(tree, "--no-pytest-fallback")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stderr  # rustest's summary line is on stderr
    assert "NOTE" in result.stderr and "handing this session to pytest" not in result.stderr


def test_an_import_failure_label_is_a_whole_reason_and_has_no_opt_out_sentence():
    from rustest.core import NeedsPytest

    needs = NeedsPytest(
        ["rustest could not import conftest.py: ModuleNotFoundError: No module named 'x'"]
    )
    assert needs.reasons == [
        "rustest could not import conftest.py: ModuleNotFoundError: No module named 'x'"
    ]
    assert needs.optout is None
    assert NeedsPytest(["conftest.py::pytest_configure"]).optout == "those hooks will not run"
