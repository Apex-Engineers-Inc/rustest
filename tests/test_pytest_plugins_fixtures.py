# tests/test_pytest_plugins_fixtures.py
"""End-to-end: a conftest's ``pytest_plugins`` declaration registers its fixtures.

This replaces `tests/test_rustest_fixtures/`, which exercised a `rustest_fixtures`
conftest field that never existed in the shipped engine. That suite could not pass: its
guard was ``skipif "_pytest" in sys.modules``, and the compat shim is always installed, so
it skipped under rustest as well as under pytest. Removing the guard would have failed,
because nothing reads the field.

The mechanism that does exist is pytest's own ``pytest_plugins``, read by
`_worker.py::_register_declared_plugins`. Both spellings pytest accepts are supported.

The suite is built in ``tmp_path`` and run through a subprocess rather than declared in
this directory, for a reason worth stating: pytest **rejects** ``pytest_plugins`` in a
conftest that is not at the rootdir ("Defining 'pytest_plugins' in a non-top-level conftest
is no longer supported"). A `tests/test_pytest_plugins/conftest.py` declaring it would
therefore break `pytest tests/`, which CI runs. Keeping the declaration inside a throwaway
rootdir isolates it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FIXTURE_MODULE = """\
from rustest import fixture


@fixture
def from_plugin_module():
    return "plugin_value"


@fixture(scope="module")
def shared_from_plugin():
    return "shared_value"
"""

SECOND_MODULE = """\
from rustest import fixture


@fixture
def from_second_module():
    return "second_value"
"""


def _build(tmp_path: Path, declaration: str, *, second: bool = False) -> Path:
    """Write a throwaway rootdir whose conftest names its fixture modules."""
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "plugin_fixtures.py").write_text(FIXTURE_MODULE)
    if second:
        (tmp_path / "more_fixtures.py").write_text(SECOND_MODULE)
    (tmp_path / "conftest.py").write_text(declaration)
    return tmp_path


def _run(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # rustest writes its human summary to stderr so stdout stays clean for --llm JSONL,
    # so callers assert against the combined streams.
    return subprocess.run(
        [sys.executable, "-m", "rustest", str(target), "-q", *args],
        capture_output=True,
        text=True,
        cwd=str(target),
    )


def test_pytest_plugins_as_a_list(tmp_path: Path) -> None:
    """The list spelling registers every named module's fixtures."""
    root = _build(tmp_path, 'pytest_plugins = ["plugin_fixtures"]\n')
    (root / "test_uses.py").write_text(
        "def test_from_plugin(from_plugin_module):\n"
        "    assert from_plugin_module == 'plugin_value'\n"
        "def test_module_scope(shared_from_plugin):\n"
        "    assert shared_from_plugin == 'shared_value'\n"
    )

    proc = _run(root)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "2 passed" in proc.stdout + proc.stderr


def test_pytest_plugins_as_a_bare_string(tmp_path: Path) -> None:
    """pytest accepts a single module name unwrapped, and so does rustest."""
    root = _build(tmp_path, 'pytest_plugins = "plugin_fixtures"\n')
    (root / "test_uses.py").write_text(
        "def test_from_plugin(from_plugin_module):\n"
        "    assert from_plugin_module == 'plugin_value'\n"
    )

    proc = _run(root)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 passed" in proc.stdout + proc.stderr


def test_several_modules_register_together(tmp_path: Path) -> None:
    """Fixtures from every named module are visible to one test."""
    root = _build(
        tmp_path,
        'pytest_plugins = ["plugin_fixtures", "more_fixtures"]\n',
        second=True,
    )
    (root / "test_uses.py").write_text(
        "def test_both(from_plugin_module, from_second_module):\n"
        "    assert from_plugin_module == 'plugin_value'\n"
        "    assert from_second_module == 'second_value'\n"
    )

    proc = _run(root)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 passed" in proc.stdout + proc.stderr


def test_declared_fixtures_are_visible_outside_the_declaring_directory(
    tmp_path: Path,
) -> None:
    """A named module is registered with no nodeid, so its fixtures are run-wide.

    This is the property `user_guide/fixtures.md` documents: unlike a fixture defined in
    the conftest itself, a `pytest_plugins` module's fixtures are not scoped below the
    conftest that named it.
    """
    root = _build(tmp_path, 'pytest_plugins = ["plugin_fixtures"]\n')
    nested = root / "nested"
    nested.mkdir()
    (nested / "test_deep.py").write_text(
        "def test_reaches_here(from_plugin_module):\n"
        "    assert from_plugin_module == 'plugin_value'\n"
    )

    proc = _run(root)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 passed" in proc.stdout + proc.stderr


def test_an_unimportable_module_is_a_loud_error(tmp_path: Path) -> None:
    """A name that will not import fails the run rather than silently registering nothing."""
    root = _build(tmp_path, 'pytest_plugins = ["no_such_module"]\n')
    (root / "test_uses.py").write_text("def test_anything():\n    assert True\n")

    proc = _run(root)

    assert proc.returncode != 0, proc.stdout + proc.stderr
    combined = proc.stdout + proc.stderr
    assert "no_such_module" in combined
