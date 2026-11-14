"""What the CLI hands the native hook runtime (``hook_context``), and how its reasons read."""

from __future__ import annotations

import argparse
from pathlib import Path

from rustest import _hookwire
from rustest.cli import _hook_context, _raw_ini, build_parser  # pyright: ignore[reportPrivateUsage]
from rustest.core import NeedsPytest


def _args(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(list(argv))


def test_the_context_carries_argv_and_the_core_options_by_flag_and_by_dest() -> None:
    args = _args("-vv", "-k", "slow", "--maxfail", "3", "--lf", "tests")

    argv = ["-vv", "-k", "slow", "--maxfail", "3", "--lf", "tests"]
    context = _hook_context(args, argv, {}, argv, ["asyncio"])

    options = context["options"]
    assert isinstance(options, dict)
    assert context["args"] == ["-vv", "-k", "slow", "--maxfail", "3", "--lf", "tests"]
    assert options["verbose"] == options["-v"] == options["--verbose"] == 2
    assert options["keyword"] == options["-k"] == "slow"
    assert options["markexpr"] == options["-m"] == ""
    assert options["maxfail"] == options["--maxfail"] == 3
    assert options["lf"] is True and options["--last-failed"] is True
    assert options["failedfirst"] is False and options["collectonly"] is False
    assert context["blocked_plugins"] == ["asyncio"]


def test_verbose_counts_what_was_parsed_not_only_what_was_typed() -> None:
    """`addopts = -v` is a verbose run (pytest reads `config.option.verbose` after the splice)."""
    options = _hook_context(_args("-v", "tests"), ["tests"], {}, ["-v", "tests"], [])["options"]
    assert isinstance(options, dict) and options["verbose"] == 1


def test_exitfirst_is_maxfail_one() -> None:
    options = _hook_context(_args("-x"), ["-x"], {}, ["-x"], [])["options"]
    assert isinstance(options, dict) and options["maxfail"] == 1


def test_the_raw_ini_reaches_getini_as_pytest_converts_it(tmp_path: Path) -> None:
    """Each config file kind, through the runtime's ``getini``: a TOML list stays a list (an
    ``args`` value is not re-split), a TOML scalar is ``str`` (``make_scalar``)."""
    from rustest._hookshim import HookRunner

    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    slow: s\n    fast\nmy_flag = yes\n", encoding="utf-8"
    )
    (tmp_path / "setup.cfg").write_text("[tool:pytest]\nMixedCase = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nmarkers = ["slow: s", "fast"]\nmyargs = ["a b", "c"]\n'
        + "strict = true\n",
        encoding="utf-8",
    )

    def runner(name: str) -> HookRunner:
        return HookRunner(rootpath=tmp_path, ini=_raw_ini(tmp_path / name))

    assert runner("pytest.ini").markers == ["slow: s", "fast"]
    assert runner("pyproject.toml").markers == ["slow: s", "fast"]
    assert _raw_ini(tmp_path / "setup.cfg") == {"MixedCase": "1"}
    raw = _raw_ini(tmp_path / "pyproject.toml")
    assert raw["myargs"] == ["a b", "c"] and raw["strict"] == "True"
    assert _raw_ini(None) == {} and _raw_ini(tmp_path / "missing.ini") == {}


def test_a_hook_runtime_reason_is_a_whole_reason_not_a_hook_label() -> None:
    reason = f"{_hookwire.REASON_PREFIX}conftest.py::pytest_configure uses config.rootdir"
    needs = NeedsPytest([reason, "conftest.py::pytest_generate_tests"])
    assert needs.reasons == [
        reason,
        "conftest.py::pytest_generate_tests is a pytest hook rustest does not run",
    ]


def test_the_prefix_core_recognises_is_the_one_the_worker_writes() -> None:
    from rustest import core

    assert _hookwire.REASON_PREFIX in core._WHOLE_REASONS  # pyright: ignore[reportPrivateUsage]
