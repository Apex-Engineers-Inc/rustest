"""Differential tests: the v2 config subsystem vs. REAL pytest.

Every assertion here is a diff against pytest itself, not against remembered behaviour.
Four ``tmp_path`` layouts are built, real pytest is run in each via ``subprocess``, and its
answers are compared with what ``rust.resolve_config`` (the Rust port of
``_pytest/config/findpaths.py::determine_setup``) produces for the same layout. Any
disagreement is a bug in the port.

Two things are diffed, both read out of the *same* subprocess run:

* **rootdir / config file** -- parsed from the session header (``rootdir:`` and
  ``configfile:`` lines).
* **the resolved ini values** -- a ``conftest.py`` dropped into each layout implements
  ``pytest_report_header`` and dumps ``config.getini(...)`` for the keys v2 models, so the
  comparison is against *pytest's own resolved values* rather than against constants
  transcribed by hand from the same reading of the pytest source. Hand constants cannot
  catch a misreading and rot silently on a pytest upgrade; this can.

Header-parsing notes (measured, not assumed):

* The task brief suggested ``pytest --collect-only -q``, but ``-q`` drives verbosity to
  ``-1`` and ``_pytest/terminal.py::TerminalReporter.showheader`` is ``self.verbosity >= 0``,
  so the header -- and with it the ``rootdir:`` line -- is suppressed entirely. These tests
  run ``--collect-only`` at default verbosity.
* pytest 8.4.2 puts ``configfile:`` on its own line right after ``rootdir:``; older pytest
  appended ``, inifile: ...`` to the ``rootdir:`` line itself, so both shapes are handled.
* conftest ``pytest_report_header`` results are printed *before* the core ``rootdir:`` line.
  Parsing is scoped to the header block (from the ``test session starts`` separator to the
  first blank line) so nothing in collected output or a traceback can be mistaken for it.

Windows note: the JSON carries posix separators and pytest's header prints native ones, so
every path comparison goes through ``Path`` + ``os.path.normcase``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

from rustest import rust

# ini keys whose resolved values v2 models and pytest can report back to us. `markers` is
# dumped too but is only subset-checked: plugins append to it via `addinivalue_line`.
INI_KEYS = [
    "python_files",
    "python_classes",
    "python_functions",
    "norecursedirs",
    "addopts",
    "testpaths",
]

# `type="paths"` ini keys: pytest's `getini` answers a list of `Path` objects
# (`Config._getini` l. 1659-1666 returns `[dp / x for x in ...]`) and v2 answers absolute
# posix strings, so they are dumped as `str` and compared through `_norm`.
PATH_INI_KEYS = ["pythonpath"]

CONFTEST = f'''\
"""Reports pytest's own resolved ini values so the Rust port can be diffed against them."""

import json

INI_KEYS = {INI_KEYS + ["markers"]!r}
PATH_INI_KEYS = {PATH_INI_KEYS!r}


def pytest_report_header(config):
    dump = {{key: list(config.getini(key)) for key in INI_KEYS}}
    dump.update({{key: [str(v) for v in config.getini(key)] for key in PATH_INI_KEYS}})
    return "inidump: " + json.dumps(dump)
'''

TEST_MODULE = "def test_x():\n    assert True\n"

# The one value still asserted against a hand constant, because no differential can reach
# it: a differential proves we agree with pytest, never that pytest still says what the
# Phase 0 audit recorded. Source: `_pytest/python.py::pytest_addoption` --
# `addini("python_functions", type="args", default=["test"])`. The default is the bare
# prefix `test`, NOT `test_*`, which is why `testfoo` is collected (corpus case
# `collection/naming-testfoo`). If a pytest upgrade ever changes this, that corpus case and
# `src/engine/config.rs::DEFAULT_PYTHON_FUNCTIONS` must be re-audited -- so it should fail loudly.
PYTEST_SOURCE_PYTHON_FUNCTIONS_DEFAULT = ["test"]


class PytestAnswer(NamedTuple):
    """What REAL pytest reported for a layout."""

    rootdir: str
    configfile: str | None
    ini: dict[str, list[str]]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _norm(path: str) -> str:
    """Case- and separator-normalized form, so posix JSON == native header output."""
    return os.path.normcase(str(Path(path)))


def _header_lines(stdout: str) -> list[str]:
    """The session header block only: separator line .. first blank line."""
    lines = stdout.splitlines()
    start = next((i for i, line in enumerate(lines) if "test session starts" in line), None)
    if start is None:
        raise AssertionError(f"no 'test session starts' separator in pytest output:\n{stdout}")
    header: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        header.append(line)
    return header


def _run_real_pytest(cwd: Path, args: list[str]) -> PytestAnswer:
    """Run REAL pytest in `cwd`; read rootdir, config file and ini values off its header."""
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
    context = (
        f"cwd={cwd} args={args} rc={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    assert proc.returncode == 0, f"pytest did not exit cleanly\n{context}"

    rootdir: str | None = None
    configfile: str | None = None
    ini: dict[str, list[str]] | None = None
    for line in _header_lines(proc.stdout):
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
        elif line.startswith("inidump:"):
            ini = json.loads(line[len("inidump:") :])
    if rootdir is None:
        raise AssertionError(f"no 'rootdir:' line in the pytest header\n{context}")
    if ini is None:
        raise AssertionError(f"no 'inidump:' line in the header (conftest unloaded?)\n{context}")
    return PytestAnswer(rootdir, configfile, ini)


def _resolve(cwd: Path, args: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(rust.resolve_config(str(cwd), args))
    return payload


def _assert_agrees_with_pytest(cwd: Path, args: list[str]) -> dict[str, Any]:
    """Run both oracles once on the same layout and diff rootdir, config file and ini."""
    oracle = _run_real_pytest(cwd, args)
    resolved = _resolve(cwd, args)
    where = f"cwd={cwd} args={args}"

    # Messages are built before the assert so that both the repo's ruff (0.14) and the
    # pre-commit-pinned ruff (0.8) agree on the formatting; they wrap `assert cond, msg`
    # differently and would otherwise fight over this file.
    detail = f"rust={resolved['rootdir']!r} pytest={oracle.rootdir!r}"
    assert _norm(resolved["rootdir"]) == _norm(oracle.rootdir), f"rootdir {where}: {detail}"

    rust_configfile: str | None = resolved["config_file"]
    if oracle.configfile is None:
        detail = f"pytest found no config file, rust reported {rust_configfile!r}"
        assert rust_configfile is None, f"config file {where}: {detail}"
    else:
        detail = f"pytest reported {oracle.configfile!r}, rust reported none"
        assert rust_configfile is not None, f"config file {where}: {detail}"
        # pytest's header shows the config file relative to rootdir (bestrelpath).
        relative = os.path.relpath(rust_configfile, resolved["rootdir"])
        detail = f"rust={relative!r} pytest={oracle.configfile!r}"
        assert _norm(relative) == _norm(oracle.configfile), f"config file {where}: {detail}"

    for key in INI_KEYS:
        detail = f"rust={resolved[key]!r} pytest={oracle.ini[key]!r}"
        assert resolved[key] == oracle.ini[key], f"ini {key!r} {where}: {detail}"
    for key in PATH_INI_KEYS:
        rust_paths = [_norm(value) for value in resolved[key]]
        pytest_paths = [_norm(value) for value in oracle.ini[key]]
        detail = f"rust={rust_paths!r} pytest={pytest_paths!r}"
        assert rust_paths == pytest_paths, f"ini {key!r} {where}: {detail}"
    # Plugins append to `markers` via `addinivalue_line`, so pytest's list is a superset of
    # what the ini file declares; v2 reports only the ini's own lines.
    extra = [m for m in resolved["markers"] if m not in oracle.ini["markers"]]
    assert not extra, f"markers not known to pytest for {where}: {extra!r}"

    return resolved


# --------------------------------------------------------------------------------------
# Layout (a): a bare directory -- no config file anywhere in the layout.
# --------------------------------------------------------------------------------------


def _layout_bare(tmp_path: Path) -> Path:
    root = tmp_path / "bare"
    _write(root / "test_bare.py", TEST_MODULE)
    _write(root / "conftest.py", CONFTEST)
    return root


def test_bare_directory_rootdir_matches_pytest(tmp_path: Path) -> None:
    root = _layout_bare(tmp_path)
    resolved = _assert_agrees_with_pytest(root, [])
    # determine_setup's last fallback: no config, no setup.py -> the invocation dir.
    assert _norm(resolved["rootdir"]) == _norm(str(root))
    assert resolved["config_file"] is None


def test_bare_layout_ini_defaults_match_pytests_own_getini(tmp_path: Path) -> None:
    """With no config file in play, the diffed ini values *are* the registered defaults.

    ``_assert_agrees_with_pytest`` does the work: every key in ``INI_KEYS`` is compared
    against ``config.getini(key)`` from pytest itself, so this pins v2's defaults to pytest's
    without transcribing them. The lone hand constant is ``python_functions == ["test"]``
    (see ``PYTEST_SOURCE_PYTHON_FUNCTIONS_DEFAULT``), which a differential structurally
    cannot check: it guards against pytest *changing*, not against v2 diverging.
    """
    root = _layout_bare(tmp_path)
    resolved = _assert_agrees_with_pytest(root, [])

    # No config file was found, so what the differential just compared really are defaults.
    assert resolved["config_file"] is None
    assert resolved["python_functions"] == PYTEST_SOURCE_PYTHON_FUNCTIONS_DEFAULT
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
    _write(root / "tests" / "conftest.py", CONFTEST)
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
    _write(root / "pkg" / "tests" / "conftest.py", CONFTEST)
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
    _write(root / "conftest.py", CONFTEST)
    return root


def test_tox_ini_rootdir_matches_pytest(tmp_path: Path) -> None:
    root = _layout_tox_ini(tmp_path)
    arg = str(Path("pkg") / "tests")
    resolved = _assert_agrees_with_pytest(root, [arg])
    assert _norm(resolved["rootdir"]) == _norm(str(root))
    # `type="args"` ini values go through shlex.split.
    assert resolved["addopts"] == ["-ra", "--tb=short"]


# --------------------------------------------------------------------------------------
# Layout (e): `pythonpath`, the one `type="paths"` ini v2 models.
# --------------------------------------------------------------------------------------


def _layout_pythonpath(tmp_path: Path, value: str) -> Path:
    root = tmp_path / "pythonpath_project"
    _write(root / "pytest.ini", f"[pytest]\npythonpath = {value}\n")
    _write(root / "src" / "mylib" / "__init__.py", "VALUE = 1\n")
    _write(root / "vendor" / "other" / "__init__.py", "VALUE = 2\n")
    _write(root / "tests" / "test_pp.py", TEST_MODULE)
    _write(root / "conftest.py", CONFTEST)
    return root


def test_pythonpath_entries_match_pytests_own_resolution(tmp_path: Path) -> None:
    """`type="paths"` resolves each entry against the **config file's** directory.

    Not against rootdir, and not against the invocation directory when a config file
    exists: `Config._getini` (l. 1659-1666) uses
    `dp = self.inipath.parent if self.inipath is not None else self.invocation_params.dir`.
    The differential is what proves it -- the layout is invoked from a *subdirectory*, so a
    port that used the invocation dir would produce `<root>/tests/src` and be caught.
    """
    root = _layout_pythonpath(tmp_path, "src vendor")
    resolved = _assert_agrees_with_pytest(root / "tests", ["."])
    assert [_norm(p) for p in resolved["pythonpath"]] == [
        _norm(str(root / "src")),
        _norm(str(root / "vendor")),
    ]


def test_pythonpath_absent_is_an_empty_list_on_both_sides(tmp_path: Path) -> None:
    root = _layout_tox_ini(tmp_path)
    resolved = _assert_agrees_with_pytest(root, [str(Path("pkg") / "tests")])
    assert resolved["pythonpath"] == []


def test_pythonpath_as_a_toml_list_bypasses_shlex(tmp_path: Path) -> None:
    root = tmp_path / "toml_pythonpath"
    _write(
        root / "pyproject.toml",
        '[tool.pytest.ini_options]\npythonpath = ["src dir", "vendor"]\n',
    )
    _write(root / "tests" / "test_pp.py", TEST_MODULE)
    _write(root / "conftest.py", CONFTEST)
    resolved = _assert_agrees_with_pytest(root, ["tests"])
    # A list value is used verbatim, so the space in "src dir" is part of one entry.
    assert [_norm(p) for p in resolved["pythonpath"]] == [
        _norm(str(root / "src dir")),
        _norm(str(root / "vendor")),
    ]


def test_an_absolute_pythonpath_entry_replaces_the_base(tmp_path: Path) -> None:
    """`dp / x` is `pathlib` division: an absolute `x` discards `dp` entirely."""
    absolute = tmp_path / "elsewhere"
    _write(absolute / "pkg" / "__init__.py", "VALUE = 3\n")
    root = tmp_path / "abs_pythonpath"
    _write(root / "pytest.ini", f"[pytest]\npythonpath = {absolute.as_posix()}\n")
    _write(root / "tests" / "test_pp.py", TEST_MODULE)
    _write(root / "conftest.py", CONFTEST)
    resolved = _assert_agrees_with_pytest(root, ["tests"])
    assert [_norm(p) for p in resolved["pythonpath"]] == [_norm(str(absolute))]


# --------------------------------------------------------------------------------------
# The other half of `pythonpath`: what the *runner* puts on `sys.path`.
# --------------------------------------------------------------------------------------


def test_pythonpath_reaches_sys_path_with_the_same_spelling_pytest_uses(tmp_path: Path) -> None:
    """Differential on `sys.path[0]` and on the imported module's `__file__`.

    pytest inserts `str(path)` for a `pathlib.Path` (`Config._configure_python_path`), i.e.
    **native** separators. rustest carries the entry over the worker protocol as posix like
    every other path, and inserting that verbatim imports fine on Windows but leaves every
    module found through it with a forward-slash `__file__` -- which then compares unequal
    to the same path under pytest for any `relative_to`/string check a suite does. Silent,
    and it was live on the acceptance target until Phase 4 Task 1's review.
    """
    root = tmp_path / "sep_project"
    _write(root / "pytest.ini", "[pytest]\npythonpath = src\n")
    _write(root / "src" / "seplib" / "__init__.py", "VALUE = 1\n")
    _write(
        root / "tests" / "test_sep.py",
        'import seplib\n\n\ndef test_sep():\n    print("SEPPROBE", repr(seplib.__file__))\n',
    )

    def _probe(argv: list[str]) -> str:
        proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True, check=False)
        for line in (proc.stdout + proc.stderr).splitlines():
            if line.strip().startswith("SEPPROBE"):
                return line.split("SEPPROBE", 1)[1].strip()
        raise AssertionError(f"probe printed nothing: {proc.stdout}{proc.stderr}")

    oracle = _probe([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-s"])
    ours = _probe([sys.executable, "-m", "rustest", "-q", "-s"])

    assert _norm(oracle.strip("'\"")) == _norm(ours.strip("'\"")), f"{oracle} vs {ours}"
    assert (os.sep in ours) or ("/" not in ours), ours
