"""Differential tests: the v2 config subsystem vs. REAL pytest.

Every assertion here is a diff against pytest itself, not against remembered behaviour.
Four ``tmp_path`` layouts are built, real pytest is run in each via ``subprocess``, its
``rootdir:``/``configfile:`` header lines are parsed, and the same layout is handed to
``rust.v2_resolve_config`` (the Rust port of
``_pytest/config/findpaths.py::determine_setup``). Any disagreement is a bug in the port.

Header-parsing note (measured, not assumed): the task brief suggested
``pytest --collect-only -q``, but ``-q`` drives verbosity to ``-1`` and
``_pytest/terminal.py::TerminalReporter.showheader`` is ``self.verbosity >= 0``, so the
header — and with it the ``rootdir:`` line — is suppressed entirely. These tests therefore
run ``--collect-only`` at default verbosity. Observed pytest 8.4.2 output puts
``configfile:`` on its own line right after ``rootdir:``; older pytest appended
``, inifile: ...`` to the ``rootdir:`` line itself, so both shapes are handled.

Windows note: the JSON carries posix separators and pytest's header prints native ones, so
every path comparison goes through ``Path`` + ``os.path.normcase``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from rustest import rust

# --------------------------------------------------------------------------------------
# ini defaults extracted from the installed pytest source in Task 3 (pytest 8.4.2).
#
#   python_files     _pytest/python.py::pytest_addoption
#                    addini("python_files", type="args", default=["test_*.py", "*_test.py"])
#   python_classes   _pytest/python.py::pytest_addoption
#                    addini("python_classes", type="args", default=["Test"])
#   python_functions _pytest/python.py::pytest_addoption
#                    addini("python_functions", type="args", default=["test"])
#                    -- the bare prefix "test", NOT "test_*", which is why `testfoo` is
#                    collected (corpus case collection/naming-testfoo).
#   norecursedirs    _pytest/main.py::pytest_addoption
#                    addini("norecursedirs", type="args", default=[...])
# --------------------------------------------------------------------------------------
DEFAULT_PYTHON_FILES = ["test_*.py", "*_test.py"]
DEFAULT_PYTHON_CLASSES = ["Test"]
DEFAULT_PYTHON_FUNCTIONS = ["test"]
DEFAULT_NORECURSEDIRS = [
    "*.egg",
    ".*",
    "_darcs",
    "build",
    "CVS",
    "dist",
    "node_modules",
    "venv",
    "{arch}",
]

TEST_MODULE = "def test_x():\n    assert True\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _norm(path: str) -> str:
    """Case- and separator-normalized form, so posix JSON == native header output."""
    return os.path.normcase(str(Path(path)))


def _run_real_pytest(cwd: Path, args: list[str]) -> tuple[str, str | None]:
    """Return ``(rootdir, configfile_or_None)`` as REAL pytest reports them."""
    env = dict(os.environ)
    for leak in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST"):
        env.pop(leak, None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:cacheprovider", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    rootdir: str | None = None
    configfile: str | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("rootdir:"):
            value = line[len("rootdir:") :].strip()
            # pytest < 7 put the config file on this same line.
            for legacy in (", configfile:", ", inifile:"):
                head, sep, tail = value.partition(legacy)
                if sep:
                    value, configfile = head.strip(), tail.strip()
            rootdir = value
        elif line.startswith("configfile:"):
            configfile = line[len("configfile:") :].strip()
    if rootdir is None:
        raise AssertionError(
            "no 'rootdir:' line in pytest output\n"
            f"cwd={cwd} args={args} rc={proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return rootdir, configfile


def _resolve(cwd: Path, args: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(rust.v2_resolve_config(str(cwd), args))
    return payload


def _assert_agrees_with_pytest(cwd: Path, args: list[str]) -> dict[str, Any]:
    """Run both oracles on the same layout and assert rootdir + config file agree."""
    pytest_rootdir, pytest_configfile = _run_real_pytest(cwd, args)
    resolved = _resolve(cwd, args)

    assert _norm(resolved["rootdir"]) == _norm(pytest_rootdir), (
        f"rootdir mismatch for cwd={cwd} args={args}: "
        f"rust={resolved['rootdir']!r} pytest={pytest_rootdir!r}"
    )

    rust_configfile: str | None = resolved["config_file"]
    if pytest_configfile is None:
        assert rust_configfile is None, (
            f"pytest found no config file for cwd={cwd} args={args}, "
            f"rust reported {rust_configfile!r}"
        )
    else:
        assert rust_configfile is not None, (
            f"pytest reported configfile {pytest_configfile!r} for cwd={cwd} args={args}, "
            "rust reported none"
        )
        # pytest's header shows the config file relative to rootdir (bestrelpath).
        relative = os.path.relpath(rust_configfile, resolved["rootdir"])
        assert _norm(relative) == _norm(pytest_configfile), (
            f"config file mismatch for cwd={cwd} args={args}: "
            f"rust={relative!r} pytest={pytest_configfile!r}"
        )
    return resolved


# --------------------------------------------------------------------------------------
# Layout (a): a bare directory -- no config file anywhere in the layout.
# --------------------------------------------------------------------------------------


def _layout_bare(tmp_path: Path) -> Path:
    root = tmp_path / "bare"
    _write(root / "test_bare.py", TEST_MODULE)
    return root


def test_bare_directory_rootdir_matches_pytest(tmp_path: Path) -> None:
    root = _layout_bare(tmp_path)
    resolved = _assert_agrees_with_pytest(root, [])
    # determine_setup's last fallback: no config, no setup.py -> the invocation dir.
    assert _norm(resolved["rootdir"]) == _norm(str(root))
    assert resolved["config_file"] is None


def test_bare_layout_reports_task3_extracted_defaults(tmp_path: Path) -> None:
    """The registered ini defaults, as extracted from the pytest source in Task 3.

    Sources (pytest 8.4.2, ``.venv/Lib/site-packages/_pytest/``):
    ``python.py::pytest_addoption`` for ``python_files`` (``["test_*.py", "*_test.py"]``),
    ``python_classes`` (``["Test"]``) and ``python_functions`` (``["test"]``);
    ``main.py::pytest_addoption`` for ``norecursedirs``. ``python_functions`` defaulting to
    the bare prefix ``test`` is the load-bearing one: it is why ``testfoo`` is a collected
    test (corpus case ``collection/naming-testfoo``).
    """
    root = _layout_bare(tmp_path)
    resolved = _resolve(root, [])

    # No config file was found, so what follows really is the *default* set.
    assert resolved["config_file"] is None
    assert resolved["python_files"] == DEFAULT_PYTHON_FILES
    assert resolved["python_classes"] == DEFAULT_PYTHON_CLASSES
    assert resolved["python_functions"] == DEFAULT_PYTHON_FUNCTIONS
    assert resolved["norecursedirs"] == DEFAULT_NORECURSEDIRS
    assert resolved["testpaths"] == []
    assert resolved["addopts"] == []
    assert resolved["markers"] == []


# --------------------------------------------------------------------------------------
# Layout (b): pytest.ini at the layout root, invoked from a nested tests dir.
# --------------------------------------------------------------------------------------


def _layout_pytest_ini(tmp_path: Path) -> Path:
    root = tmp_path / "ini_project"
    _write(root / "pytest.ini", "[pytest]\npython_classes = Check\nmarkers =\n    slow\n")
    _write(root / "tests" / "test_ini.py", TEST_MODULE)
    return root


def test_pytest_ini_rootdir_matches_pytest(tmp_path: Path) -> None:
    root = _layout_pytest_ini(tmp_path)
    resolved = _assert_agrees_with_pytest(root / "tests", [])
    # The upward search anchors on the nearest ancestor holding a config file.
    assert _norm(resolved["rootdir"]) == _norm(str(root))
    assert resolved["python_classes"] == ["Check"]
    assert resolved["markers"] == ["slow"]


# --------------------------------------------------------------------------------------
# Layout (c): pyproject.toml with [tool.pytest.ini_options] two levels up.
# --------------------------------------------------------------------------------------


def _layout_pyproject(tmp_path: Path) -> Path:
    root = tmp_path / "toml_project"
    _write(
        root / "pyproject.toml",
        '[project]\nname = "demo"\n\n'
        "[tool.pytest.ini_options]\n"
        'python_files = ["check_*.py"]\n'
        'testpaths = ["pkg/tests"]\n',
    )
    _write(root / "pkg" / "tests" / "check_toml.py", TEST_MODULE)
    return root


def test_pyproject_rootdir_matches_pytest(tmp_path: Path) -> None:
    root = _layout_pyproject(tmp_path)
    resolved = _assert_agrees_with_pytest(root / "pkg" / "tests", [])
    assert _norm(resolved["rootdir"]) == _norm(str(root))
    # TOML list values bypass shlex.split and arrive as-is.
    assert resolved["python_files"] == ["check_*.py"]
    assert resolved["testpaths"] == ["pkg/tests"]


# --------------------------------------------------------------------------------------
# Layout (d): tox.ini [pytest], invoked from the root with a relative directory arg.
# --------------------------------------------------------------------------------------


def _layout_tox_ini(tmp_path: Path) -> Path:
    root = tmp_path / "tox_project"
    _write(root / "tox.ini", "[tox]\nenvlist = py312\n\n[pytest]\naddopts = -ra --tb=short\n")
    _write(root / "pkg" / "tests" / "test_tox.py", TEST_MODULE)
    return root


def test_tox_ini_rootdir_matches_pytest(tmp_path: Path) -> None:
    root = _layout_tox_ini(tmp_path)
    arg = str(Path("pkg") / "tests")
    resolved = _assert_agrees_with_pytest(root, [arg])
    assert _norm(resolved["rootdir"]) == _norm(str(root))
    # `type="args"` ini values go through shlex.split.
    assert resolved["addopts"] == ["-ra", "--tb=short"]
