"""The pytest fallback: a session that needs pytest is handed to pytest, whole.

Three layers: the argv translation and the child process (pure, with ``Popen``/``execv``
replaced), the CLI wiring (a stubbed ``rust`` module), and real end-to-end runs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from rustest import _fallback, cli

from .helpers import clean_env, make_tree, run, stub_rust_module


_REAL_INSTALLED_PLUGINS = _fallback.installed_plugins


@pytest.fixture(autouse=True)
def _no_ambient_pytest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both variables are themselves fallback reasons; an ambient one would hand every
    in-process run over."""
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.delenv("PYTEST_PLUGINS", raising=False)
    # The dev environment's own plugins are not what these in-process tests are about.
    monkeypatch.setattr(_fallback, "installed_plugins", lambda: [])


# -- the argv translation ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--pattern", "a", "--marks=b"], ["-k", "a", "-m", "b"]),
        (["--no-capture", "-x", "--lf"], ["-s", "-x", "--lf"]),
        (["--color", "always"], ["--color=yes"]),
        (["--color=never", "--color=auto"], ["--color=no", "--color=auto"]),
        (["--llm-full", "--ascii", "--codeblocks", "--no-codeblocks", "--llm"], []),
        (["--report-json", "r.json", "x", "--report-json=y.json"], ["x"]),
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

    def __init__(self, argv: list[str], **kwargs: object) -> None:
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
    monkeypatch.setattr(subprocess, "Popen", _Child)
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


def test_llm_with_collect_only_is_refused(
    windows: type[_Child], capsys: pytest.CaptureFixture[str]
):
    assert _fallback.run(["x"], ["why"], llm=True, collect_only=True) == 4
    assert "different things" in capsys.readouterr().err
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

    def fake(
        argv: list[str], reasons: list[str], optout: str | None = None, **outputs: object
    ) -> int:
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


def test_the_machine_outputs_asked_for_travel_with_the_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    outputs: list[dict[str, object]] = []

    def fake(argv: list[str], reasons: list[str], optout: str | None = None, **kw: object) -> int:
        outputs.append(kw)
        return 0

    monkeypatch.setattr(_fallback, "run", fake)
    monkeypatch.chdir(tmp_path)

    def fake_collect(*args: object, pytest_fallback: bool = False, **kwargs: object) -> str:
        return json.dumps({"tests": [], "pytest_fallback": ["conftest.py::pytest_configure"]})

    with stub_rust_module(
        collect=fake_collect, run=_quiet_engine(pytest_fallback=["conftest.py::pytest_configure"])
    ):
        assert cli.main(["--collect-only"]) == 0
        assert cli.main(["--llm", "--llm-full", "-v"]) == 0
        assert cli.main(["--report-json", "r.json", "-q"]) == 0

    assert outputs == [
        dict(report_json=None, llm=False, llm_full=False, collect_only=True, verbosity=0),
        dict(report_json=None, llm=True, llm_full=True, collect_only=False, verbosity=1),
        dict(report_json="r.json", llm=False, llm_full=False, collect_only=False, verbosity=-1),
    ]


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


def test_pytest_plugins_in_the_environment_is_a_fallback_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    seen = _handoff(monkeypatch)
    monkeypatch.setenv("PYTEST_PLUGINS", "myplug")
    monkeypatch.chdir(tmp_path)
    assert cli.main([]) == 17
    assert seen[0][1] == ["PYTEST_PLUGINS is set, and rustest ignores it"]
    assert seen[0][2] == "they will be ignored"


def test_pytest_addopts_is_spliced_between_addopts_and_the_command_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """pytest's order (`Config._preparse`): ini addopts, PYTEST_ADDOPTS, then argv; the last
    `-k` wins. Understood options hand nothing over, so CI's `--color=yes` costs nothing."""
    seen = _handoff(monkeypatch)
    tree = make_tree(tmp_path, "env_order", {"pytest.ini": "[pytest]\naddopts = -k ini\n"})
    monkeypatch.chdir(tree)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--color=yes -k 'env k'")
    keywords: list[str | None] = []

    def fake_run(*args: object, **kwargs: object) -> str:
        keywords.append(args[4] if len(args) > 4 else None)  # type: ignore[misc]
        return _report()

    with stub_rust_module(run=fake_run):
        assert cli.main([]) == 0
        assert cli.main(["-k", "cli"]) == 0
    assert keywords == ["env k", "cli"] and seen == []


def test_an_option_rustest_does_not_know_in_pytest_addopts_is_handed_over_and_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    seen = _handoff(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--runslow")
    assert cli.main(["-x"]) == 17
    assert seen == [(["-x"], ["--runslow is not a rustest option (from PYTEST_ADDOPTS)"], None)]


def test_pytest_addopts_is_ignored_as_before_under_no_pytest_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    seen = _handoff(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--runslow")
    with stub_rust_module(run=_quiet_engine()):
        assert cli.main(["--no-pytest-fallback"]) == 0
    assert seen == []


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


# -- machine outputs from a pytest run -----------------------------------------------------

_MIXED = {
    "conftest.py": "def pytest_collection_modifyitems(config, items):\n    pass\n",
    "test_m.py": (
        "import pytest\n\n\n"
        "@pytest.fixture\ndef boom():\n    raise RuntimeError('fixture broke')\n\n\n"
        "def test_pass():\n    pass\n\n\n"
        "def test_fail():\n    print('to stdout')\n    assert 1 == 2\n\n\n"
        "@pytest.mark.skip(reason='nope')\ndef test_skip():\n    pass\n\n\n"
        "@pytest.mark.xfail(reason='known')\ndef test_xfail():\n    assert 0\n\n\n"
        "@pytest.mark.xfail(reason='odd')\ndef test_xpass():\n    pass\n\n\n"
        "def test_error(boom):\n    pass\n"
    ),
}


def test_report_json_under_fallback_is_a_schema_2_report_that_names_the_fallback(tmp_path: Path):
    tree = make_tree(tmp_path, "fb_report", _MIXED)

    result = _rustest(tree, "--report-json", "report.json")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "test session starts" in result.stdout  # a human run: pytest's own output
    report = json.loads((tree / "report.json").read_text(encoding="utf-8"))
    assert report["version"] == 2 and report["exit_code"] == 1
    assert report["pytest_fallback"] and "handing this session to pytest" in result.stderr
    assert report["rootdir"] == tree.resolve().as_posix()
    by_id = {t["id"]: t for t in report["tests"]}
    status = {k.split("::")[1]: (v["status"], v.get("message")) for k, v in by_id.items()}
    assert status["test_pass"] == ("passed", None)
    assert status["test_skip"] == ("skipped", "nope")
    assert status["test_xfail"] == ("xfailed", "known")
    assert status["test_xpass"] == ("xpassed", "odd")
    assert status["test_fail"][0] == "failed" and "assert 1 == 2" in status["test_fail"][1]
    assert status["test_error"][0] == "error" and "fixture broke" in status["test_error"][1]
    assert by_id["test_m.py::test_fail"]["stdout"] == "to stdout\n"
    summary = report["summary"]
    counts = ("total", "passed", "failed", "skipped", "xfailed", "xpassed", "error")
    assert [summary[k] for k in counts] == [6, 1, 1, 1, 1, 1, 1]
    assert report["collection_errors"] == []


def test_llm_under_fallback_keeps_stdout_to_the_jsonl_stream(tmp_path: Path):
    tree = make_tree(tmp_path, "fb_llm", _MIXED)

    result = _rustest(tree, "--llm")

    assert result.returncode == 1, result.stdout + result.stderr
    lines = [json.loads(line) for line in result.stdout.splitlines()]  # every line is JSON
    kinds = [line["t"] for line in lines]
    assert kinds[0] == "meta" and kinds[-1] == "summary"
    fails = {line["id"] for line in lines if line["t"] == "fail"}
    assert fails == {"test_m.py::test_fail", "test_m.py::test_error"}
    assert lines[-1]["exit_code"] == 1
    assert "test session starts" in result.stderr  # pytest's own output, moved aside


def test_collect_only_under_fallback_prints_what_pytest_collects_in_rustests_format(tmp_path: Path):
    """The cross-directory case: `a/conftest.py` removes a test of `b/`, and pytest honours it."""
    files = {
        "a/conftest.py": (
            "def pytest_collection_modifyitems(items):\n"
            "    items[:] = [i for i in items if 'test_b' not in i.nodeid]\n"
        ),
        "a/test_a.py": "def test_a():\n    pass\n",
        "b/test_b.py": "def test_b():\n    pass\n",
    }
    tree = make_tree(tmp_path, "fb_collect", files)

    result = _rustest(tree, "--collect-only")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "a/test_a.py::test_a\n"
    assert "1 test collected" in result.stderr and "handing this session to pytest" in result.stderr


# -- the bundled plugin, fed synthetic reports ---------------------------------------------


def _report_module():
    import rustest._pytest_report as plugin

    plugin._tests.clear()
    plugin._errors.clear()
    plugin._collected.clear()
    plugin._deselected = 0
    plugin._module_skips = 0
    return plugin


def _rep(when: str, outcome: str, nodeid: str = "t.py::t", **extra: object):
    from types import SimpleNamespace

    return SimpleNamespace(
        when=when,
        outcome=outcome,
        nodeid=nodeid,
        duration=0.1,
        capstdout="",
        capstderr="",
        longrepr=extra.pop("longrepr", None),
        longreprtext=extra.pop("longreprtext", "boom"),
        passed=outcome == "passed",
        failed=outcome == "failed",
        skipped=outcome == "skipped",
        **extra,
    )


def _status_of(*reports: object) -> dict[str, object]:
    plugin = _report_module()
    for report in reports:
        plugin.pytest_runtest_logreport(report)  # pyright: ignore[reportArgumentType]
    (entry,) = plugin._tests.values()
    return entry


def test_a_rerun_is_not_a_skip():
    """pytest-rerunfailures logs each failed attempt as outcome "rerun"."""
    entry = _status_of(_rep("setup", "passed"), _rep("call", "rerun"), _rep("call", "passed"))
    assert entry["status"] == "passed" and "message" not in entry


def test_phase_mapping_matches_pytest():
    assert _status_of(_rep("setup", "failed"))["status"] == "error"
    assert _status_of(_rep("call", "passed"), _rep("teardown", "failed"))["status"] == "error"
    assert _status_of(_rep("call", "failed"), _rep("teardown", "failed"))["status"] == "error"
    # strict xpass is a plain failure with no `wasxfail`; a lenient one is xpassed
    assert _status_of(_rep("call", "failed"))["status"] == "failed"
    assert _status_of(_rep("call", "passed", wasxfail="odd"))["status"] == "xpassed"
    assert _status_of(_rep("call", "skipped", wasxfail="reason: known")) == {
        "id": "t.py::t",
        "status": "xfailed",
        "duration": 0.1,
        "message": "known",
    }
    # an imperative `pytest.xfail()` in setup
    assert _status_of(_rep("setup", "skipped", wasxfail="why"))["status"] == "xfailed"
    skipped = _status_of(_rep("setup", "skipped", longrepr=("f.py", 3, "Skipped: not here")))
    assert (skipped["status"], skipped["message"]) == ("skipped", "not here")


def test_a_failure_records_pytests_line(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    longrepr = SimpleNamespace(reprcrash=SimpleNamespace(lineno=12))
    entry = _status_of(_rep("call", "failed", longrepr=longrepr))
    assert entry["line"] == 12 and entry["message"] == "boom"


def test_the_session_report_counts_module_skips_and_leaves_the_exit_code_to_the_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from types import SimpleNamespace

    plugin = _report_module()
    plugin.pytest_collectreport(SimpleNamespace(failed=False, skipped=True, nodeid="m.py"))  # pyright: ignore
    plugin.pytest_runtest_logreport(_rep("call", "passed"))  # pyright: ignore
    out = tmp_path / "report.json"
    monkeypatch.setenv("RUSTEST_PYTEST_REPORT", str(out))
    session = SimpleNamespace(
        config=SimpleNamespace(rootpath=tmp_path), shouldfail=False, shouldstop=False
    )

    plugin.pytest_sessionfinish(session, 2)  # pyright: ignore

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["summary"]["skipped"] == 1 and report["summary"]["passed"] == 1
    assert "exit_code" not in report
    assert report["stopped_early"] is True  # exit status 2: Ctrl-C or pytest.exit

    out.unlink()
    session.config.workerinput = {}  # an xdist worker writes nothing
    plugin.pytest_sessionfinish(session, 0)  # pyright: ignore
    assert not out.exists()


# -- process handling ----------------------------------------------------------------------


def test_sigterm_reaches_the_child_and_a_signalled_child_is_128_plus_n(
    monkeypatch: pytest.MonkeyPatch,
):
    import signal

    class Child:
        terminated = False

        def wait(self) -> int:
            signal.raise_signal(signal.SIGTERM)  # aimed at rustest alone
            return -15

        def terminate(self) -> None:
            self.terminated = True

    child = Child()
    before = signal.getsignal(signal.SIGTERM)
    assert _fallback._wait(child) == 143  # pyright: ignore[reportArgumentType]
    assert child.terminated and signal.getsignal(signal.SIGTERM) is before


def test_the_report_file_is_removed_and_a_missing_report_keeps_pytests_code(
    windows: type[_Child], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths: list[str] = []
    real = tempfile.mkstemp

    def spy(*args: object, **kwargs: object):
        handle, path = real(*args, **kwargs)  # pyright: ignore
        paths.append(path)
        return handle, path

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    windows.interrupts = 0

    # the fake child never writes the (empty) file: pytest stopped before it could report
    assert _fallback.run(["--llm"], ["why"], llm=True) == 2

    assert not Path(paths[0]).exists()
    assert "pytest wrote no report (exit code 2)" in capsys.readouterr().err


def test_an_internal_error_under_collect_only_is_shown(
    windows: type[_Child], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    class Dying(_Child):
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            super().__init__(argv)
            kwargs["stdout"].write(b"INTERNALERROR> boom\n")  # pyright: ignore

        def wait(self) -> int:
            return 3

    monkeypatch.setattr(subprocess, "Popen", Dying)
    assert _fallback.run(["--collect-only"], ["why"], collect_only=True) == 3
    assert "INTERNALERROR> boom" in capsys.readouterr().err


# -- `-o` and the opt-out that arrive through addopts --------------------------------------


@pytest.mark.parametrize("source", ["addopts", "PYTEST_ADDOPTS"])
def test_an_ini_override_from_addopts_or_the_environment_is_a_fallback_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
):
    seen = _handoff(monkeypatch)
    ini = "[pytest]\naddopts = -o xfail_strict=true\n" if source == "addopts" else "[pytest]\n"
    tree = make_tree(tmp_path, f"late_o_{source}", {"pytest.ini": ini})
    monkeypatch.chdir(tree)
    if source == "PYTEST_ADDOPTS":
        monkeypatch.setenv("PYTEST_ADDOPTS", "-o xfail_strict=true")

    assert cli.main([]) == 17
    assert seen[0][1] == [f"-o xfail_strict is an ini option rustest cannot apply (from {source})"]


def test_no_pytest_fallback_in_ini_addopts_stops_pytest_addopts_being_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    seen = _handoff(monkeypatch)
    tree = make_tree(
        tmp_path, "optout_env", {"pytest.ini": "[pytest]\naddopts = --no-pytest-fallback\n"}
    )
    monkeypatch.chdir(tree)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--runslow")
    with stub_rust_module(run=_quiet_engine()):
        assert cli.main([]) == 0
    assert seen == []


def test_llm_under_fallback_says_so_in_meta_and_gives_pytests_line_whatever_the_tb(tmp_path: Path):
    tree = make_tree(tmp_path, "fb_llm_meta", _MIXED)
    source = _MIXED["test_m.py"].splitlines()
    assert_line = source.index("    assert 1 == 2") + 1

    result = _rustest(tree, "--llm", "--tb=no")

    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines[0]["pytest_fallback"] and lines[0]["tool"] == "rustest"
    fail = next(line for line in lines if line["t"] == "fail" and line["id"].endswith("test_fail"))
    assert fail["line"] == assert_line
    assert "def test_fail" in fail["msg"]  # a full traceback, not `--tb=no`'s nothing


# -- auto-loaded (`pytest11`) plugins ------------------------------------------------------


def _dist(root: Path, name: str, entry_points: str) -> None:
    info = root / f"{name}.dist-info"
    info.mkdir(parents=True)
    (info / "entry_points.txt").write_text(entry_points, encoding="utf-8")


def test_installed_plugins_reads_the_pytest11_group_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _dist(
        tmp_path,
        "randomthing-1.0",
        "[console_scripts]\ntool = randomthing:main\n\n[pytest11]\nrandomly = pytest_randomly\n"
        "xdist.looponfail = xdist.looponfail\n\n[other]\nx = y\n",
    )
    _dist(tmp_path, "plain-2.0", "[console_scripts]\nplain = plain:main\n")
    (tmp_path / "not_a_dist.dist-info").mkdir()  # no entry_points.txt: skipped, not an error
    monkeypatch.setattr(sys, "path", [str(tmp_path), str(tmp_path / "missing")])

    assert _REAL_INSTALLED_PLUGINS() == [
        ("randomly", "randomthing-1.0"),
        ("xdist.looponfail", "randomthing-1.0"),
    ]


def test_only_the_judged_safe_plugins_are_allowed():
    assert _fallback.SAFE_PLUGINS == {
        "xdist",
        "xdist.looponfail",
        "pytest_cov",
        "asyncio",
        "pytest_mock",
        "icdiff",
        "rich",
        "codeblocks",
    }
    # Each of these was probed or read and found able to change an outcome or an id.
    for unsafe in ("hypothesispytest", "anyio", "timeout", "inline_snapshot", "randomly"):
        assert unsafe not in _fallback.SAFE_PLUGINS


def test_an_unknown_plugin_is_a_reason_and_pytests_own_switches_remove_it(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        _fallback,
        "installed_plugins",
        lambda: [
            ("xdist", "pytest_xdist-3"),
            ("randomly", "pytest_randomly-4"),
            ("pytest_x", "x-1"),
        ],
    )
    monkeypatch.delenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", raising=False)

    reasons = _fallback.autoload_reasons([])
    assert [r.split(" ")[3] for r in reasons] == ["randomly", "pytest_x"]  # xdist is safe
    assert "`-p no:randomly` disables it" in reasons[0]

    assert _fallback.autoload_reasons(["randomly", "x"]) == []  # `no:x` also blocks `pytest_x`
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert _fallback.autoload_reasons([]) == []


def test_an_installed_plugin_hands_the_session_over_unless_it_is_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    seen = _handoff(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", raising=False)
    monkeypatch.setattr(_fallback, "installed_plugins", lambda: [("randomly", "pytest_randomly-4")])

    assert cli.main([]) == 17
    assert "the pytest plugin randomly (pytest_randomly-4)" in seen[0][1][0]
    assert seen[0][2] == "they will be ignored"

    with stub_rust_module(run=_quiet_engine()):
        assert cli.main(["-p", "no:randomly"]) == 0  # pytest would not load it either
        assert cli.main(["--no-pytest-fallback"]) == 0  # the opt-out does not even look
    assert len(seen) == 1


def test_a_plugin_that_only_the_environment_installs_is_handed_over_end_to_end(tmp_path: Path):
    files = {
        "fakeplug-1.0.dist-info/entry_points.txt": "[pytest11]\nfakeplug = fakeplug\n",
        "fakeplug.py": "def pytest_report_header():\n    return 'fakeplug loaded'\n",
        "test_a.py": "def test_a():\n    pass\n",
    }
    tree = make_tree(tmp_path, "ep_plugin", files)

    handed = _rustest(tree)
    assert handed.returncode == 0, handed.stdout + handed.stderr
    assert "the pytest plugin fakeplug (fakeplug-1.0) is installed" in handed.stderr
    assert "fakeplug loaded" in handed.stdout  # pytest really loaded it

    blocked = _rustest(tree, "-p", "no:fakeplug")
    assert blocked.returncode == 0 and "handing this session" not in blocked.stderr
