"""The pytest fallback: a session that needs pytest is handed to pytest, whole.

Three layers: the argv translation and the child process (pure, with ``Popen``/``execv``
replaced), the CLI wiring (a stubbed ``rust`` module), and real end-to-end runs.
"""

from __future__ import annotations

import json
import os
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
    monkeypatch.setenv("PYTHONHASHSEED", "0")  # `run` sets it; this restores it after
    monkeypatch.setattr(_fallback, "_installed", lambda module: True)
    monkeypatch.setattr(subprocess, "Popen", _Child)
    _Child.calls.clear()
    _Child.interrupts, _Child.terminated = 1, 0
    return _Child


def test_a_handed_off_session_keeps_rustests_parallelism(windows: type[_Child]):
    """With xdist installed and no `-n` anywhere, pytest gets rustest's pool size (and the
    project's own `cache_dir`, when its ini names one, is left to pytest)."""
    _ = _fallback.run(["x"], ["why"], workers=4, cache_dir_set=True)
    _ = _fallback.run(["x"], ["why"], workers=1)

    assert windows.calls == [
        [sys.executable, "-m", "pytest", "-n", "4", "x"],
        [sys.executable, "-m", "pytest", "x", "-o", "cache_dir=.rustest_cache"],
    ]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["tests"], True),
        (["-n", "2"], False),
        (["-n2"], False),
        (["--workers=3"], False),
        (["--dist", "loadfile"], False),
        (["--pdb"], False),
        (["-p", "no:xdist"], False),
        (["-p", "no:cacheprovider"], True),
    ],
)
def test_the_default_worker_count_only_fills_a_gap(argv: list[str], expected: bool):
    workers = cli._default_workers(argv, cli._blocked_plugins(argv))  # pyright: ignore[reportPrivateUsage]
    assert (workers is not None) is expected


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

    from rustest.core import _pool_size  # pyright: ignore[reportPrivateUsage]

    common = dict(cache_dir_set=False, workers=_pool_size(None))
    assert outputs == [
        dict(report_json=None, llm=False, llm_full=False, collect_only=True, verbosity=0, **common),
        dict(report_json=None, llm=True, llm_full=True, collect_only=False, verbosity=1, **common),
        dict(
            report_json="r.json",
            llm=False,
            llm_full=False,
            collect_only=False,
            verbosity=-1,
            **common,
        ),
    ]


def _forwarded(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """What the CLI hands the workers to parse against the conftests' declarations."""
    seen: list[object] = []

    def fake_run(*args: object, hook_context: str | None = None, **kwargs: object) -> str:
        seen.append(json.loads(hook_context or "{}").get("unknown_args"))
        return _report()

    monkeypatch.setattr("rustest.core.rust", type("R", (), {"run": staticmethod(fake_run)}))
    return seen


def test_an_unknown_flag_goes_to_the_workers_and_an_abbreviation_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """``--report=html`` could be a conftest option; rustest must not read it as
    ``--report-json`` (argparse abbreviation), exactly as pytest's own parser does not. Both
    go to the workers, which parse them against the conftests' declarations."""
    seen = _handoff(monkeypatch)
    forwarded = _forwarded(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--report=html", "--runslow"]) == 0
    assert seen == [] and forwarded == [
        [{"tokens": ["--report=html"], "values": []}, {"tokens": ["--runslow"], "values": []}]
    ]


def test_a_pytest_option_rustest_does_not_have_is_handed_over_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """No conftest can declare ``--doctest-modules`` (pytest owns it), so nothing is forwarded."""
    seen = _handoff(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--doctest-modules"]) == 17
    assert seen == [(["--doctest-modules"], ["--doctest-modules is not a rustest option"], None)]


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
    assert seen[0][2] == "without what these reasons ask for"


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


def test_a_conftest_option_in_pytest_addopts_goes_to_the_workers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    seen = _handoff(monkeypatch)
    forwarded = _forwarded(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--runslow")
    assert cli.main(["-x"]) == 0
    assert seen == [] and forwarded == [[{"tokens": ["--runslow"], "values": []}]]


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


#: The same recipe through a hook rustest does not run: `pytest_itemcollected` marks a skip.
_P1_SETUP = {
    "conftest.py": (
        "import pytest\n\n\n"
        "def pytest_itemcollected(item):\n"
        '    if "slow" in item.keywords:\n'
        '        item.add_marker(pytest.mark.skip(reason="need --runslow"))\n'
    ),
    "test_p1.py": _P1["test_p1.py"],
}


def _rustest(tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = clean_env()
    env["COLUMNS"] = "80"
    return run([sys.executable, "-m", "rustest", *args], tree, env)


def test_an_option_only_a_conftest_defines_runs_natively(tmp_path: Path):
    """``--runslow`` is not a rustest option; the conftest declares it, so the workers parse
    it as pytest's second argparse pass would (see test_conftest_args.py)."""
    tree = make_tree(tmp_path, "p1_flag", _P1)

    result = _rustest(tree, "--runslow")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stderr  # rustest's summary line
    assert "handing this session to pytest" not in result.stderr


_SIDE_EFFECT = (
    "from hypothesis import strategies as st\n\nst.deferred(st.integers).wrapped_strategy\n"
)


@pytest.mark.parametrize(
    ("files", "warning"),
    [
        # no hook: the conftest still imports inside the plugin's initialization window
        ({"conftest.py": _SIDE_EFFECT}, "HypothesisSideeffectWarning"),
        # a hook: the same window, through the hook runtime
        (
            {"conftest.py": _SIDE_EFFECT + "\n\ndef pytest_configure(config):\n    pass\n"},
            "HypothesisSideeffectWarning",
        ),
        # an unpruned `.hypothesis` directory: the plugin warns of it
        (
            {"pytest.ini": "[pytest]\nnorecursedirs = build\n", ".hypothesis/examples/x": ""},
            "Skipping collection of '.hypothesis' directory",
        ),
    ],
    ids=["side-effect", "side-effect-with-hook", "hypothesis-dir"],
)
def test_a_warning_hypothesiss_plugin_gives_hands_the_session_to_pytest(
    tmp_path: Path, files: dict[str, str], warning: str
):
    """pytest counts these warnings in its summary (and a `filterwarnings = error` makes them
    errors), which rustest does not reproduce: review M7, M8."""
    tree = make_tree(tmp_path, "hyp_warn", {**files, "test_a.py": "def test_a():\n    pass\n"})

    result = _rustest(tree)

    assert "handing this session to pytest: rustest's hook runtime: hypothesis" in result.stderr
    # The warning this is about, not the count: under load Windows can deny pytest's own cache
    # write for a moment (a `PytestCacheWarning`, "Access is denied"), a second warning that is
    # the machine's, not hypothesis's.
    assert "1 passed" in result.stdout, result.stdout + result.stderr
    assert warning in result.stdout, result.stdout + result.stderr


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
    assert "(without what these reasons ask for)" in result.stderr


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
    assert "--runslwo is not an option rustest or a conftest declares" in result.stderr
    # Only pytest's usage line has `[file_or_dir]`; rustest's own error would say `rustest: error`.
    assert "[options] [file_or_dir]" in result.stderr and "rustest: error" not in result.stderr


def test_the_failures_of_a_fallen_back_run_feed_lf_in_a_rustest_run(tmp_path: Path):
    """One cache: pytest runs under `-o cache_dir=.rustest_cache`, which rustest also reads."""
    files = {
        "conftest.py": "def pytest_itemcollected(item):\n    pass\n",
        "test_a.py": "def test_ok():\n    pass\n\n\ndef test_bad():\n    assert 0\n",
    }
    tree = make_tree(tmp_path, "lf_across", files)

    first = _rustest(tree)
    assert first.returncode == 1, first.stdout + first.stderr
    assert "1 failed, 1 passed" in first.stdout  # pytest's summary

    second = _rustest(tree, "--no-pytest-fallback", "--lf")
    assert "1 failed" in second.stderr and "passed" not in second.stderr


def test_a_hook_that_changes_what_runs_hands_the_session_to_pytest(tmp_path: Path):
    tree = make_tree(tmp_path, "p1_hook", _P1_SETUP)

    result = _rustest(tree)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 1 skipped" in result.stdout
    assert "conftest.py::pytest_itemcollected" in result.stderr
    assert result.stderr.count("handing this session to pytest") == 1
    assert "(those hooks will not run)" in result.stderr


def test_verbose_from_addopts_reaches_a_hook_as_it_does_under_pytest(tmp_path: Path):
    """`addopts = -v` makes `config.getoption("verbose")` 1 (review M6): both runners skip."""
    files = {
        "pytest.ini": "[pytest]\naddopts = -v\n",
        "conftest.py": (
            "import pytest\n\n\n"
            "def pytest_collection_modifyitems(config, items):\n"
            '    if config.getoption("verbose") > 0:\n'
            "        for item in items:\n"
            '            item.add_marker(pytest.mark.skip(reason="verbose run"))\n'
        ),
        "test_a.py": "def test_a():\n    pass\n",
    }
    tree = make_tree(tmp_path, "verbose_addopts", files)

    oracle = run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider"], tree, clean_env())
    ours = _rustest(tree)

    assert "1 skipped" in oracle.stdout
    assert "1 skipped" in ours.stderr and "handing" not in ours.stderr, ours.stderr


def test_configure_runs_once_per_worker_and_a_later_worker_failure_is_exit_3(tmp_path: Path):
    """Review M8: worker 0 runs `pytest_configure` first. A hook that fails only the second time
    (it makes a directory) fails the session as pytest reports a configure error, naming the
    per-worker execution, rather than handing a second runner the state it left."""
    files = {
        "conftest.py": "import os\n\n\ndef pytest_configure(config):\n    os.mkdir('made_once')\n",
        "test_a.py": "def test_a():\n    pass\n",
        "test_b.py": "def test_b():\n    pass\n",
    }
    tree = make_tree(tmp_path, "configure_twice", files)

    result = _rustest(tree, "-n", "2")

    assert result.returncode == 3, result.stdout + result.stderr
    assert "once per worker process, as pytest-xdist does" in result.stderr
    assert "handing this session to pytest" not in result.stderr


def test_a_reason_in_worker_0_hands_off_before_any_other_worker_runs_configure(tmp_path: Path):
    """Review M8: with a reason found at `init`, the other workers never start their hooks, so
    `pytest_configure` runs once in rustest and once in pytest -- not once per worker first."""
    files = {
        "conftest.py": (
            "def pytest_configure(config):\n"
            "    with open('configured.log', 'a') as handle:\n"
            "        handle.write('x')\n"
            "    config.pluginmanager.getplugin('probe')\n"
        ),
        "test_a.py": "def test_a():\n    pass\n",
        "test_b.py": "def test_b():\n    pass\n",
        "test_c.py": "def test_c():\n    pass\n",
    }
    tree = make_tree(tmp_path, "configure_reason", files)

    result = _rustest(tree, "-n", "3")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "handing this session to pytest" in result.stderr
    assert (tree / "configured.log").read_text(encoding="utf-8") == "xx"


def test_the_runslow_recipe_without_the_flag_runs_natively(tmp_path: Path):
    """``pytest_addoption`` + ``pytest_collection_modifyitems`` run in the hook runtime."""
    tree = make_tree(tmp_path, "p1_native", _P1)

    result = _rustest(tree)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 1 skipped" in result.stderr  # rustest's summary line is on stderr
    assert "handing this session to pytest" not in result.stderr
    assert "NOTE" not in result.stderr


def test_no_pytest_fallback_runs_rustest_with_the_note(tmp_path: Path):
    tree = make_tree(tmp_path, "p1_optout", _P1_SETUP)

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
    "conftest.py": "def pytest_itemcollected(item):\n    pass\n",
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
    plugin._open.clear()
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
    (entry,) = plugin._tests
    return entry


def test_a_rerun_is_not_a_skip():
    """pytest-rerunfailures logs each failed attempt as outcome "rerun"."""
    entry = _status_of(_rep("setup", "passed"), _rep("call", "rerun"), _rep("call", "passed"))
    assert entry["status"] == "passed" and "message" not in entry


def test_phase_mapping_matches_pytest():
    assert _status_of(_rep("setup", "failed"))["status"] == "error"
    # a failed teardown is an error of its own beside the status: pytest's `1 passed, 1 error`
    for call, status in (("passed", "passed"), ("failed", "failed")):
        torn = _status_of(_rep("call", call), _rep("teardown", "failed"))
        assert (torn["status"], torn.get("teardown_error")) == (status, "boom")
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
        "hypothesispytest",  # its outcome-changing parts are ported (`_hypothesis`)
        # inert until a selected test uses their mark or fixture (`_worker._PLUGIN_USES`)
        "anyio",
        "timeout",
        "syrupy",
        "textual-snapshot",
        "platformdirs",
        "hydra_pytest",
        "faker",
        "typeguard",
        "rerunfailures",
        "benchmark",
        "repeat",
        "pytest_order",
        "socket",
        # terminal reporting only
        "sugar",
        "instafail",
        # ported (`_fallback.apply_pytest_env`)
        "env",
    }
    # Each of these was probed or read and found able to change an outcome or an id.
    for unsafe in ("inline_snapshot", "randomly"):
        assert unsafe not in _fallback.SAFE_PLUGINS


def test_the_installed_hypothesis_is_one_rustest_emulates() -> None:
    """The dev dependency has no ceiling: a release whose plugin changed fails here, before a
    gate grades the port against a plugin it does not port. Add its fingerprint once the
    port matches it (``_hypothesis.FINGERPRINTS``)."""
    from importlib.metadata import version

    from rustest import _hypothesis

    installed = version("hypothesis")
    assert _hypothesis.supported(f"hypothesis-{installed}"), installed


def test_past_the_verified_releases_only_an_unchanged_plugin_is_emulated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rustest import _hypothesis

    assert _hypothesis.supported("hypothesis-6.140.0")
    assert not _hypothesis.supported("hypothesis-6.139.9")
    assert _hypothesis.supported("hypothesis-6.999.0")  # the installed plugin is a verified one
    monkeypatch.setattr(_hypothesis, "FINGERPRINTS", frozenset())
    assert not _hypothesis.supported("hypothesis-6.999.0")
    monkeypatch.setattr(
        _fallback, "installed_plugins", lambda: [("hypothesispytest", "hypothesis-6.999.0")]
    )
    assert _fallback.autoload_reasons([]) == [
        "hypothesis's pytest plugin (hypothesis-6.999.0) is installed, and rustest emulates it "
        + "only for hypothesis 6.140 to 6.170.x and later releases whose plugin is unchanged"
    ]


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
    assert seen[0][2] == "without what these reasons ask for"

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


def _site_plugin(name: str, module: str, source: str) -> dict[str, str]:
    """A plugin installed the way pytest finds one: a ``pytest11`` entry point named *name*."""
    dist = f"site/fake_{module}-1.0.dist-info"
    return {
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: fake-{module}\nVersion: 1.0\n",
        f"{dist}/entry_points.txt": f"[pytest11]\n{name} = {module}\n",
        f"site/{module}.py": source,
    }


#: A stand-in for anyio's pytest plugin with the part that changes ids: a test marked `anyio`
#: gets `usefixtures("anyio_backend")` at collection, and `anyio_backend` is parametrized, so
#: pytest selects on `test_p[asyncio]` / `test_p[trio]` (anyio 4.x `pytest_pycollect_makeitem`).
_FAKE_ANYIO = _site_plugin(
    "anyio",
    "fake_anyio_plugin",
    "import pytest\n\n\n"
    "def pytest_configure(config):\n"
    "    config.addinivalue_line('markers', 'anyio: run with anyio')\n\n\n"
    "@pytest.hookimpl(tryfirst=True)\n"
    "def pytest_pycollect_makeitem(collector, name, obj):\n"
    "    if collector.istestfunction(obj, name) and (\n"
    "        any(m.name == 'anyio' for m in getattr(obj, 'pytestmark', ()))\n"
    "        or collector.get_closest_marker('anyio')\n"
    "    ):\n"
    "        pytest.mark.usefixtures('anyio_backend')(obj)\n\n\n"
    "@pytest.fixture(scope='module', params=['asyncio', 'trio'])\n"
    "def anyio_backend(request):\n    return request.param\n",
)

#: A stand-in for Faker's plugin: one fixture, `faker`.
_FAKE_FAKER = _site_plugin(
    "faker",
    "fake_faker_plugin",
    "import pytest\n\n\n@pytest.fixture\ndef faker():\n    return 'fake'\n",
)

_MARKED = (
    "import pytest\n\npytestmark = pytest.mark.anyio\n\n\n"
    "def test_p():\n    pass\n\n\ndef test_q():\n    pass\n"
)


#: A stand-in for pytest-timeout: installed, registering its mark and nothing else.
_FAKE_TIMEOUT = _site_plugin(
    "timeout",
    "fake_timeout_plugin",
    "def pytest_configure(config):\n"
    "    config.addinivalue_line('markers', 'timeout: bound a test')\n",
)


def test_a_timeout_mark_runs_natively_and_ends_a_test_that_overruns(tmp_path: Path):
    """pytest-timeout's mark: a test inside its bound is untouched; one that overruns is
    failed where ``SIGALRM`` exists, and elsewhere takes its worker down, as the plugin's
    thread method takes pytest down."""
    env = clean_env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    fine = make_tree(
        tmp_path,
        "fine",
        {
            **_FAKE_TIMEOUT,
            "test_t.py": "import pytest\n\n\n@pytest.mark.timeout(60)\ndef test_ok():\n    pass\n",
        },
    )
    env["PYTHONPATH"] = str(fine / "site")
    result = run([sys.executable, "-m", "rustest", "-p", "no:cacheprovider"], fine, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "handing" not in result.stderr
    assert "1 passed" in result.stdout + result.stderr

    slow = make_tree(
        tmp_path,
        "slow",
        {
            **_FAKE_TIMEOUT,
            "test_t.py": (
                "import time\n\nimport pytest\n\n\n"
                "@pytest.mark.timeout(0.3)\ndef test_slow():\n    time.sleep(60)\n"
            ),
        },
    )
    env["PYTHONPATH"] = str(slow / "site")
    result = run([sys.executable, "-m", "rustest", "-p", "no:cacheprovider"], slow, env)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "1 passed" not in result.stdout + result.stderr


def test_the_timeout_ini_and_environment_bound_every_test(tmp_path: Path):
    """pytest-timeout's ``timeout`` ini key and ``PYTEST_TIMEOUT`` run natively: a suite inside
    the bound passes untouched, one over it does not pass; ``session_timeout`` is pytest's."""
    env = clean_env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    def rustest(name: str, body: str, ini: str, **extra: str) -> subprocess.CompletedProcess[str]:
        tree = make_tree(
            tmp_path,
            name,
            {**_FAKE_TIMEOUT, "test_t.py": body, "pytest.ini": f"[pytest]\n{ini}\n"},
        )
        child = {**env, **extra, "PYTHONPATH": str(tree / "site")}
        return run([sys.executable, "-m", "rustest", "-p", "no:cacheprovider"], tree, child)

    quick = "def test_ok():\n    pass\n"
    slow = "import time\n\n\ndef test_slow():\n    time.sleep(60)\n"

    result = rustest("ini_ok", quick, "timeout = 60")
    assert result.returncode == 0 and "handing" not in result.stderr, result.stderr
    result = rustest("ini_slow", slow, "timeout = 0.3")
    assert result.returncode != 0 and "handing" not in result.stderr, result.stderr
    result = rustest("env_slow", slow, "", PYTEST_TIMEOUT="0.3")
    assert result.returncode != 0 and "handing" not in result.stderr, result.stderr
    result = rustest("session", quick, "session_timeout = 60")
    assert "handing this session to pytest" in result.stderr


def test_pytest_env_sets_the_environment_as_the_plugin_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """pytest-env 1.2.0: ``env`` ini lines with the ``D:`` and ``R:`` flags, and a
    ``[tool.pytest_env]`` table that wins over them; nothing without the plugin."""
    for name in ("RT_A", "RT_B", "RT_RAW", "RT_KEPT", "RT_T"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", raising=False)
    ini = {"env": "RT_A=one\nD:RT_B={RT_A}/two\nR:RT_RAW={RT_A}\nD:RT_KEPT=new\n"}

    monkeypatch.setattr(_fallback, "installed_plugins", lambda: [])
    _fallback.apply_pytest_env(str(tmp_path), ini, [])
    assert "RT_A" not in os.environ

    monkeypatch.setattr(_fallback, "installed_plugins", lambda: [("env", "pytest_env-1.2.0")])
    _fallback.apply_pytest_env(str(tmp_path), ini, ["env"])  # `-p no:env`
    assert "RT_A" not in os.environ

    monkeypatch.setenv("RT_KEPT", "old")
    _fallback.apply_pytest_env(str(tmp_path), ini, [])
    assert [os.environ[k] for k in ("RT_A", "RT_B", "RT_RAW", "RT_KEPT")] == [
        "one",
        "one/two",
        "{RT_A}",
        "old",
    ]

    _ = (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest_env]\nRT_T = {value = "{RT_A}!", transform = true}\nRT_A = "table"\n',
        encoding="utf-8",
    )
    _fallback.apply_pytest_env(str(tmp_path), ini, [])
    assert os.environ["RT_T"] == "one!" and os.environ["RT_A"] == "table"


def test_an_installed_rerunfailures_is_inert_until_something_asks_for_a_rerun(tmp_path: Path):
    """Installed and unused it changes nothing, so the session stays; its ``flaky`` mark or its
    ``reruns`` ini key is a request only the plugin can honour."""
    plugin = _site_plugin(
        "rerunfailures",
        "fake_rerun_plugin",
        "def pytest_addoption(parser):\n"
        "    parser.addini('reruns', 'x')\n\n\n"
        "def pytest_configure(config):\n"
        "    config.addinivalue_line('markers', 'flaky: rerun')\n",
    )
    env = clean_env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    def rustest(name: str, body: str, ini: str = "") -> subprocess.CompletedProcess[str]:
        tree = make_tree(
            tmp_path, name, {**plugin, "test_t.py": body, "pytest.ini": f"[pytest]\n{ini}\n"}
        )
        child = {**env, "PYTHONPATH": str(tree / "site")}
        return run([sys.executable, "-m", "rustest", "-p", "no:cacheprovider"], tree, child)

    plain = "def test_ok():\n    pass\n"
    marked = "import pytest\n\n\n@pytest.mark.flaky(reruns=2)\ndef test_ok():\n    pass\n"
    assert "handing" not in rustest("plain", plain).stderr
    assert "mark 'flaky'" in rustest("marked", marked).stderr
    assert "the reruns ini option" in rustest("ini", plain, "reruns = 2").stderr


#: A stand-in for the anyio distribution with the runner API ``rustest._anyio`` drives: one
#: backend, whose test runner records what it ran in ``anyio.LOG``.
_FAKE_ANYIO_PACKAGE = {
    "site/anyio-4.99.dist-info/METADATA": "Metadata-Version: 2.1\nName: anyio\nVersion: 4.99\n",
    "site/anyio-4.99.dist-info/entry_points.txt": "[pytest11]\nanyio = anyio.pytest_plugin\n",
    "site/anyio/__init__.py": (
        "LOG = []\n\n\ndef get_available_backends():\n    return ('asyncio',)\n"
    ),
    "site/anyio/pytest_plugin.py": "raise ImportError('rustest must not import this')\n",
    "site/anyio/_core/__init__.py": "",
    "site/anyio/_core/_eventloop.py": (
        "import asyncio\nimport contextlib\n\nimport anyio\n\n\n"
        "class _Runner:\n"
        "    def __init__(self):\n        self.loop = asyncio.new_event_loop()\n\n"
        "    def is_running(self):\n        return self.loop.is_running()\n\n"
        "    def run_test(self, func, kwargs):\n"
        "        anyio.LOG.append('test')\n"
        "        self.loop.run_until_complete(func(**kwargs))\n\n"
        "    def run_fixture(self, func, kwargs):\n"
        "        anyio.LOG.append('fixture')\n"
        "        return self.loop.run_until_complete(func(**kwargs))\n\n"
        "    def run_asyncgen_fixture(self, func, kwargs):\n"
        "        anyio.LOG.append('asyncgen')\n"
        "        agen = func(**kwargs)\n"
        "        yield self.loop.run_until_complete(agen.__anext__())\n"
        "        with contextlib.suppress(StopAsyncIteration):\n"
        "            self.loop.run_until_complete(agen.__anext__())\n\n\n"
        "class _Backend:\n"
        "    @contextlib.contextmanager\n"
        "    def create_test_runner(self, options):\n"
        "        runner = _Runner()\n"
        "        try:\n            yield runner\n"
        "        finally:\n            runner.loop.close()\n\n\n"
        "def get_async_backend(name):\n    return _Backend()\n"
    ),
}


def test_an_anyio_test_runs_on_anyios_runner_with_the_backend_in_its_id(tmp_path: Path):
    """anyio's plugin, natively: an ``async def`` test marked ``anyio`` gets ``anyio_backend``
    (so its id carries the backend) and it and its async fixtures run on anyio's test runner;
    an unmarked test is untouched, and the session stays with rustest."""
    tree = make_tree(
        tmp_path,
        "anyio_native",
        {
            **_FAKE_ANYIO_PACKAGE,
            "test_a.py": (
                "import anyio\nimport pytest\n\nDONE = []\n\n\n"
                "@pytest.fixture\nasync def value():\n    return 2\n\n\n"
                "@pytest.fixture\nasync def resource():\n"
                "    yield 'open'\n    DONE.append('closed')\n\n\n"
                "@pytest.mark.anyio\n"
                "async def test_marked(value, resource, anyio_backend_name):\n"
                "    assert (value, resource, anyio_backend_name) == (2, 'open', 'asyncio')\n"
                "    assert anyio.LOG == ['fixture', 'asyncgen', 'test']\n\n\n"
                "def test_plain():\n    assert DONE == ['closed']\n"
            ),
        },
    )
    env = clean_env()
    env["PYTHONPATH"] = str(tree / "site")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = run(
        [sys.executable, "-m", "rustest", "-p", "no:cacheprovider", "-v", "--color=no"], tree, env
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "handing" not in result.stderr
    assert "test_marked[asyncio]" in output and "2 passed" in output


def test_ignore_and_ignore_glob_run_natively(tmp_path: Path):
    """`--ignore PATH` and `--ignore-glob PATTERN` drop files and directories from the walk."""
    tree = make_tree(
        tmp_path,
        "ignore",
        {
            "test_one.py": "def test_a():\n    pass\n",
            "sub/test_two.py": "def test_b():\n    pass\n",
            "gen/test_three.py": "def test_c():\n    raise SystemExit(3)\n",
            "gen/conftest.py": "raise SystemExit(3)\n",
        },
    )
    result = _rustest(tree, "--ignore", "gen", "--ignore-glob", "*/test_tw?.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout + result.stderr
    assert "handing" not in result.stderr


def _both_with_site(
    tree: Path, args: list[str]
) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    env = clean_env()
    env["PYTHONPATH"] = str(tree / "site")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    oracle = run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args], tree, env)
    ours = run([sys.executable, "-m", "rustest", "-p", "no:cacheprovider", *args], tree, env)
    return oracle, ours


def _summary(text: str) -> str:
    """pytest's last summary line, without its timing."""
    import re

    return re.findall(r"^=* ?(\d.*?) in [\d.]+s", text, re.M)[-1]


@pytest.mark.parametrize(
    ("files", "args", "native"),
    [
        ({"test_a.py": "def test_a():\n    pass\n"}, [], True),
        ({"test_a.py": "def test_a(anyio_backend):\n    pass\n"}, [], False),
        ({"test_a.py": _MARKED}, [], False),
        # anyio decides ids at collection: `-k asyncio` selects `test_p[asyncio]` under pytest,
        # so the decision is the whole collection's (review #4)
        ({"test_a.py": _MARKED}, ["-k", "asyncio"], False),
        ({"test_a.py": _MARKED}, ["test_a.py::test_p[asyncio]"], False),
        (
            {"test_a.py": _MARKED + "\n\ndef test_plain():\n    pass\n"},
            ["-m", "not anyio", "--strict-markers"],
            False,
        ),
        # a conftest's own `anyio_backend` is still anyio's: it acts on the name (review #1)
        (
            {
                "conftest.py": "import pytest\n\n\n@pytest.fixture\ndef anyio_backend():\n"
                "    return 'trio'\n",
                "test_a.py": "def test_a(anyio_backend):\n    assert anyio_backend == 'trio'\n",
            },
            [],
            False,
        ),
        # `--collect-only` through Tier S must see the fixture too (review #3)
        ({"test_a.py": "def test_f(anyio_backend):\n    pass\n"}, ["--collect-only", "-q"], False),
        # a mark added by a modifyitems hook is pytest's to expand (review #5)
        (
            {
                "conftest.py": "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
                "    for item in items:\n        item.add_marker(pytest.mark.anyio)\n",
                "test_a.py": "def test_m():\n    pass\n",
            },
            [],
            False,
        ),
        # a fixture asked for by name at run time (review #8)
        (
            {"test_a.py": "def test_d(request):\n    request.getfixturevalue('anyio_backend')\n"},
            [],
            False,
        ),
    ],
    ids=[
        "unused",
        "fixture",
        "mark",
        "k-on-backend-id",
        "node-id-with-backend",
        "mark-deselected",
        "user-anyio-backend",
        "collect-only-static",
        "modifyitems-adds-mark",
        "getfixturevalue-literal",
    ],
)
def test_anyio_hands_off_wherever_it_would_act(
    tmp_path: Path, files: dict[str, str], args: list[str], native: bool
):
    tree = make_tree(tmp_path, "inert", {**_FAKE_ANYIO, **files})

    oracle, ours = _both_with_site(tree, args)

    assert ours.returncode == oracle.returncode, ours.stdout + ours.stderr
    assert ("handing this session to pytest" not in ours.stderr) is native, ours.stderr
    if "--collect-only" in args:
        assert (
            "test_a.py::test_f[asyncio]" in ours.stdout and "test_a.py::test_f[trio]" in ours.stdout
        )
    else:
        # the same tests, the same deselected count: pytest's own answer either way
        assert _summary(ours.stdout + "\n" + ours.stderr) == _summary(oracle.stdout), (
            ours.stdout + ours.stderr
        )


@pytest.mark.parametrize(
    ("conftest", "test", "native"),
    [
        # shadowing without asking for the plugin's: the user's fixture alone, native
        (
            "@pytest.fixture\ndef faker():\n    return 'mine'\n",
            "def test_a(faker):\n    assert faker == 'mine'\n",
            True,
        ),
        # overriding and requesting it: the plugin's fixture is used (review #2)
        (
            "@pytest.fixture\ndef faker(faker):\n    return faker + '!'\n",
            "def test_a(faker):\n    assert faker == 'fake!'\n",
            False,
        ),
    ],
    ids=["shadowed", "override-requests-it"],
)
def test_a_plugin_fixture_counts_as_used_when_an_override_requests_it(
    tmp_path: Path, conftest: str, test: str, native: bool
):
    tree = make_tree(
        tmp_path,
        "faker",
        {**_FAKE_FAKER, "conftest.py": "import pytest\n\n\n" + conftest, "test_a.py": test},
    )

    oracle, ours = _both_with_site(tree, [])

    assert ours.returncode == oracle.returncode == 0, ours.stdout + ours.stderr
    assert ("handing this session to pytest" not in ours.stderr) is native, ours.stderr


def test_a_computed_plugin_fixture_name_errors_naming_the_plugin(tmp_path: Path):
    """No literal to find: the lookup fails at run time, and says whose fixture it is."""
    tree = make_tree(
        tmp_path,
        "computed",
        {
            **_FAKE_FAKER,
            "test_a.py": "def test_a(request):\n    request.getfixturevalue('fa' + 'ker')\n",
        },
    )

    _, ours = _both_with_site(tree, [])

    assert "fixture 'faker' is the pytest plugin faker's, which rustest does not run" in (
        ours.stdout + ours.stderr
    )


def test_an_inert_plugin_leaves_strict_markers_on(tmp_path: Path):
    """A loaded inert plugin keeps the marker registry known (review #6)."""
    tree = make_tree(
        tmp_path,
        "strict",
        {
            **_FAKE_FAKER,
            "test_a.py": "import pytest\n\n\n@pytest.mark.foo\ndef test_a():\n    pass\n",
        },
    )

    oracle, ours = _both_with_site(tree, ["--strict-markers"])

    assert oracle.returncode == ours.returncode == 2, ours.stdout + ours.stderr
    assert "'foo' not found in `markers`" in ours.stdout + ours.stderr


def test_config_cache_is_gone_without_the_cache_provider(tmp_path: Path):
    """``config.cache`` is the cache provider's attribute (review #7)."""
    tree = make_tree(
        tmp_path,
        "cfgcache",
        {"test_a.py": "def test_b(pytestconfig):\n    assert pytestconfig.cache is not None\n"},
    )

    oracle, ours = _both_with_site(tree, [])

    assert oracle.returncode == ours.returncode == 1
    assert "has no attribute 'cache'" in ours.stdout + ours.stderr


def test_the_cache_dir_ini_is_where_rustest_keeps_its_cache(tmp_path: Path):
    """pytest's ``cache_dir`` ini: last-failed (and the ``cache`` fixture) go there, and no
    ``.rustest_cache`` appears."""
    tree = make_tree(
        tmp_path,
        "cachedir",
        {
            "pytest.ini": "[pytest]\ncache_dir = mycache\n",
            "test_a.py": "def test_a(cache):\n    cache.set('k/v', 1)\n    assert 0\n",
        },
    )
    env = clean_env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = run([sys.executable, "-m", "rustest"], tree, env)

    assert result.returncode == 1, result.stdout + result.stderr
    assert (tree / "mycache" / "v" / "cache" / "lastfailed").is_file()
    assert (tree / "mycache" / "v" / "k" / "v").is_file()
    assert not (tree / ".rustest_cache").exists()


def test_no_cacheprovider_and_no_bytecode_leave_the_tree_untouched(tmp_path: Path):
    """`-p no:cacheprovider` keeps no cache (no `cache/lastfailed`, no `cache` fixture) and
    `PYTHONDONTWRITEBYTECODE` no compiled assertion rewrites: nothing lands under the rootdir,
    as with pytest."""
    tree = make_tree(
        tmp_path,
        "nocache",
        {
            "test_a.py": "def test_a():\n    assert 1 == 2\n\n\ndef test_b(cache):\n    pass\n",
        },
    )
    env = clean_env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    before = sorted(p.name for p in tree.rglob("*"))
    oracle = run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider"], tree, env)
    ours = run([sys.executable, "-m", "rustest", "-p", "no:cacheprovider"], tree, env)
    assert sorted(p.name for p in tree.rglob("*")) == before
    assert ours.returncode == oracle.returncode == 1
    assert "1 failed, 1 error" in oracle.stdout and "fixture 'cache' not found" in oracle.stdout
    assert "1 failed, 1 error" in ours.stderr + ours.stdout, ours.stderr


def test_a_module_naming_pytester_runs_under_pytest_first(tmp_path: Path):
    """``pytest_plugins = ["pytester"]`` is pytest's own in-process runner: only pytest can run
    a test that uses it, so that file runs under pytest first (B-lite, ``test_blite.py``) rather
    than ending in a collection error, and the rest natively."""
    tree = make_tree(
        tmp_path,
        "pytester",
        {
            "test_p.py": 'pytest_plugins = ["pytester"]\n\n\n'
            "def test_inner(pytester):\n"
            '    pytester.makepyfile("def test_x():\\n    pass\\n")\n'
            "    pytester.runpytest().assert_outcomes(passed=1)\n",
            "test_q.py": "def test_q():\n    pass\n",
        },
    )

    result = _rustest(tree)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "running 1 file(s) under pytest first" in result.stderr
    assert "2 passed" in result.stderr


#: Enough of pytest-xdist for its entry point to load: `worker_id` and `testrun_uid` are
#: then rustest's, as xdist's would be.
_FAKE_XDIST = _site_plugin("xdist", "fake_xdist_plugin", "")

_IDENTITY = (
    "import json, os, pathlib\n\n\n"
    "def _record(name, worker_id, testrun_uid, pytestconfig):\n"
    "    workerinput = getattr(pytestconfig, 'workerinput', None)\n"
    "    env = {k: v for k, v in os.environ.items() if k.startswith('PYTEST_XDIST_')}\n"
    "    out = pathlib.Path(__file__).with_name(name + '.json')\n"
    "    out.write_text(json.dumps([worker_id, testrun_uid, workerinput, env]))\n\n\n"
    "def test_one(worker_id, testrun_uid, pytestconfig):\n"
    "    _record('one', worker_id, testrun_uid, pytestconfig)\n"
)


@pytest.mark.parametrize("workers", [1, 2])
def test_a_pool_of_two_or_more_has_xdist_worker_identity(tmp_path: Path, workers: int):
    """xdist's model (`xdist/remote.py`, `plugin.py::worker_id`): `gwN`, the count and one
    session uid in each worker's environment and `config.workerinput`; one worker is serial
    pytest, `"master"` and none of it."""
    tree = make_tree(
        tmp_path,
        "ident",
        {
            **_FAKE_XDIST,
            "test_a.py": _IDENTITY,
            "test_b.py": _IDENTITY.replace("'one'", "'two'").replace("test_one", "test_two"),
        },
    )
    env = clean_env("PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT", "PYTEST_XDIST_TESTRUNUID")
    env["PYTHONPATH"] = str(tree / "site")

    result = run([sys.executable, "-m", "rustest", "-n", str(workers)], tree, env)

    assert result.returncode == 0, result.stdout + result.stderr
    one, two = (json.loads((tree / f"{n}.json").read_text()) for n in ("one", "two"))
    if workers == 1:
        assert one[0] == two[0] == "master"
        assert one[2] is None and one[3] == {}
        return
    assert {one[0], two[0]} == {"gw0", "gw1"}
    assert one[1] == two[1] and len(one[1]) == 32  # one session id, uuid4().hex-shaped
    assert one[2] == {"workerid": one[0], "workercount": 2, "testrunuid": one[1]}
    assert one[3] == {
        "PYTEST_XDIST_WORKER": one[0],
        "PYTEST_XDIST_WORKER_COUNT": "2",
        "PYTEST_XDIST_TESTRUNUID": one[1],
    }


def test_o_cache_dir_moves_every_cache_out_of_the_tree(tmp_path: Path):
    """``-o cache_dir=PATH``, as pytest takes it: last-failed, the duration record and the
    compiled rewrites all go there, and the tree gets no ``.rustest_cache``."""
    tree = make_tree(tmp_path, "ocache", {"test_a.py": "def test_a():\n    assert 0\n"})
    elsewhere = tmp_path / "elsewhere"

    result = run(
        [sys.executable, "-m", "rustest", "-o", f"cache_dir={elsewhere}"], tree, clean_env()
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert (elsewhere / "v" / "cache" / "lastfailed").is_file()
    assert (elsewhere / "v" / "rustest" / "durations").is_file()
    assert not (tree / ".rustest_cache").exists()


#: A test only pytest can run: it asks the plugin manager for a plugin.
_NEEDS_PYTEST = (
    "def test_pm(pytestconfig):\n"
    "    assert pytestconfig.pluginmanager.get_plugin('logging-plugin')\n"
)


def test_unregistering_the_logging_plugin_stays_with_rustest(tmp_path: Path):
    """flask's suite takes pytest's logging plugin out for each test and puts it back, so no
    handler of pytest's sits on the root logger: rustest's capture steps aside the same way."""
    tree = make_tree(
        tmp_path,
        "logging_plugin",
        {
            "test_log.py": (
                "import logging\n\nimport pytest\n\n\n"
                "@pytest.fixture(autouse=True)\n"
                "def reset_logging(pytestconfig):\n"
                "    plugin = pytestconfig.pluginmanager.unregister(name='logging-plugin')\n"
                "    yield\n"
                "    if plugin:\n"
                "        pytestconfig.pluginmanager.register(plugin, 'logging-plugin')\n\n\n"
                "def test_no_handler(caplog):\n"
                "    assert caplog.handler not in logging.root.handlers\n\n\n"
                "def test_again():\n"
                "    pass\n"
            )
        },
    )
    result = _rustest(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "handing" not in result.stderr
    assert "2 passed" in result.stdout + result.stderr


def test_reading_the_plugin_manager_hands_the_session_to_pytest(tmp_path: Path):
    """``pytestconfig.pluginmanager`` is pytest's plugin system (flask's suite unregisters the
    logging plugin through it): found while the test runs, and the session goes to pytest."""
    tree = make_tree(
        tmp_path,
        "pluginmanager",
        {
            "test_pm.py": (
                "def test_first():\n    pass\n\n"
                "def test_pm(pytestconfig):\n"
                "    assert pytestconfig.pluginmanager.get_plugin('logging-plugin')\n"
            )
        },
    )

    handed = _rustest(tree)
    assert handed.returncode == 0, handed.stdout + handed.stderr
    assert "config.pluginmanager" in handed.stderr
    assert "2 passed" in handed.stdout  # pytest's summary line

    kept = _rustest(tree, "--no-pytest-fallback")
    assert kept.returncode == 1, kept.stdout + kept.stderr


def test_rustests_own_xdist_pins_the_hash_seed(
    windows: type[_Child], monkeypatch: pytest.MonkeyPatch
):
    """xdist workers must collect identical ids, and a suite that parametrizes from a set does
    so only under one hash seed (pydantic). The user's own seed is left alone."""
    monkeypatch.delenv("PYTHONHASHSEED", raising=False)
    _ = _fallback.run(["x"], ["why"], workers=1)
    assert "PYTHONHASHSEED" not in os.environ
    _ = _fallback.run(["x"], ["why"], workers=4)
    assert os.environ["PYTHONHASHSEED"] == "0"
    monkeypatch.setenv("PYTHONHASHSEED", "7")
    _ = _fallback.run(["x"], ["why"], workers=4)
    assert os.environ["PYTHONHASHSEED"] == "7"


def test_a_hand_off_without_the_cache_plugin_passes_no_cache_dir(tmp_path: Path):
    """`-p no:cacheprovider` removes pytest's `cache_dir` option, and pytest 9 refuses an unknown
    one (pydantic's addopts). The user's own `-o cache_dir=` still reaches pytest."""
    tree = make_tree(
        tmp_path,
        "nocache",
        {"test_pm.py": _NEEDS_PYTEST},
    )
    handed = _rustest(tree, "-p", "no:cacheprovider")
    assert handed.returncode == 0, handed.stdout + handed.stderr
    assert "1 passed" in handed.stdout
    assert "cache_dir" not in handed.stdout + handed.stderr

    own = _rustest(tree, "-o", "cache_dir=mine")
    assert own.returncode == 0, own.stdout + own.stderr
    assert (tree / "mine").is_dir() and not (tree / ".rustest_cache").exists()


def test_one_worker_is_handed_over_as_plain_pytest():
    """`pytest -n 1` starts xdist for a single worker, which is slower than no xdist at all."""
    assert _fallback.pytest_argv(["-n", "1", "--workers=1", "-n1", "x"]) == (["x"], False)


def test_a_session_that_needed_pytest_goes_straight_there_next_time(tmp_path: Path):
    """The first run finds out under rustest and remembers; the second starts in pytest; an
    edited file makes rustest look again."""
    tree = make_tree(
        tmp_path,
        "memo",
        {"test_pm.py": _NEEDS_PYTEST},
    )
    memo = tree / ".rustest_cache" / "handoff.json"

    first = _rustest(tree)
    assert first.returncode == 0, first.stdout + first.stderr
    assert memo.is_file()
    root, argv = str(tree), list[str]()
    assert _fallback.recall(str(memo), root, argv, [])

    second = _rustest(tree)
    assert second.returncode == 0 and "1 passed" in second.stdout
    assert "handing this session to pytest" in second.stderr
    assert "discarding" not in second.stderr

    _ = (tree / "test_pm.py").write_text("def test_pm():\n    pass\n", encoding="utf-8")
    assert _fallback.recall(str(memo), root, argv, []) == []
    third = _rustest(tree)
    assert third.returncode == 0 and "handing" not in third.stderr


def test_deselect_runs_natively_and_counts_as_pytest_counts(tmp_path: Path):
    """`--deselect PREFIX` drops every test whose node id starts with it: a test, a class, a
    file or a directory."""
    tree = make_tree(
        tmp_path,
        "deselect",
        {
            "test_one.py": "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n",
            "sub/test_two.py": "def test_z():\n    pass\n",
        },
    )
    result = _rustest(tree, "--deselect", "test_one.py::test_a", "--deselect", "sub")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 2 deselected" in result.stdout + result.stderr
    assert "handing" not in result.stderr
