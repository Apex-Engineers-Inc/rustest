"""
Integration tests for different project layouts.

These tests create temporary project structures and verify that rustest
can correctly discover and run tests for each layout pattern.

NOTE: These tests use pytest fixtures and subprocess to test rustest externally.
They are automatically skipped when run with rustest (via conftest.py).
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def run_rustest(project_dir, *args):
    """Run rustest on a project directory and return result."""
    cmd = [
        sys.executable,
        "-m",
        "rustest",
        str(project_dir / "tests"),
        "--color",
        "never",
        *args,
    ]
    result = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True)
    return result


@pytest.fixture
def src_layout_project(tmp_path):
    """Create a project with src/ layout."""
    # Create structure
    src_dir = tmp_path / "src"
    pkg_dir = src_dir / "mypackage"
    pkg_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    # Create package files
    (pkg_dir / "__init__.py").write_text("""
def greet(name):
    return f"Hello, {name}!"

def add(a, b):
    return a + b
""")

    (pkg_dir / "utils.py").write_text("""
def multiply(a, b):
    return a * b
""")

    # Create test files
    (tests_dir / "test_basic.py").write_text("""
from mypackage import greet, add
from mypackage.utils import multiply

def test_greet():
    assert greet("World") == "Hello, World!"

def test_add():
    assert add(2, 3) == 5

def test_multiply():
    assert multiply(4, 5) == 20
""")

    return tmp_path


@pytest.fixture
def flat_layout_project(tmp_path):
    """Create a project with flat layout."""
    # Create structure
    pkg_dir = tmp_path / "mypackage"
    pkg_dir.mkdir()
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    # Create package files
    (pkg_dir / "__init__.py").write_text("""
def subtract(a, b):
    return a - b
""")

    # Create test files
    (tests_dir / "test_flat.py").write_text("""
from mypackage import subtract

def test_subtract():
    assert subtract(10, 3) == 7
    assert subtract(0, 5) == -5
""")

    return tmp_path


@pytest.fixture
def nested_package_project(tmp_path):
    """Create a project with nested packages."""
    # Create structure
    pkg_dir = tmp_path / "mypackage"
    pkg_dir.mkdir()
    sub_dir = pkg_dir / "subpackage"
    sub_dir.mkdir()
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    # Create package files
    (pkg_dir / "__init__.py").write_text("""
VERSION = "1.0.0"
""")

    (sub_dir / "__init__.py").write_text("""
def process(data):
    return data.upper()
""")

    # Create test files
    (tests_dir / "test_nested.py").write_text("""
from mypackage import VERSION
from mypackage.subpackage import process

def test_version():
    assert VERSION == "1.0.0"

def test_process():
    assert process("hello") == "HELLO"
""")

    return tmp_path


def test_src_layout_matches_pytest_on_the_default_engine(src_layout_project):
    """A bare ``src/`` layout is a **collection error** by default, exactly as under pytest.

    This is an adjudicated behaviour change at the v2 flip, not a regression left unnoticed.
    v1 silently inserted the project's ``src/`` directory into ``sys.path``, so
    ``from mypackage import ...`` resolved with no install, no ``PYTHONPATH`` and no ini.
    **pytest does not do that** -- probed on this exact layout, ``pytest tests`` reports
    ``ERROR collecting tests/test_basic.py: ModuleNotFoundError: No module named
    'mypackage'`` and exits 2 -- and the default engine's contract is pytest's behaviour.

    ``--v1`` kept the old convenience while the legacy engine existed, and the second half
    of this test used to pin that. Phase 4 Task 2 deleted the engine, so the escape hatch is
    gone and the *supported* answer is the one the next test pins: pytest's ``pythonpath``
    ini, implemented in Phase 4 Task 1.
    """
    result = run_rustest(src_layout_project)
    assert result.returncode == 2, f"expected pytest's collection-error exit: {result.stderr}"
    assert "No module named 'mypackage'" in result.stdout + result.stderr, result.stderr


def test_src_layout_works_once_the_pythonpath_ini_is_set(src_layout_project):
    """...and the *supported* way to make it importable is pytest's ``pythonpath`` ini.

    Implemented in Phase 4 Task 1 (`_pytest/config/__init__.py::Config._configure_python_path`,
    l. 1316-1319; the option is declared at l. 1258-1260 with ``type="paths"``). The entry is
    resolved against the **config file's** directory, prepended to the worker's ``sys.path``
    before any import, and answered by ``request.config.getini("pythonpath")`` as a list of
    ``Path`` objects the way pytest answers it.

    This restores what v1 did through ``src/python_support.rs::read_pythonpath_from_pyproject``
    -- but only what pytest itself does: the project root and the auto-detected ``src/`` that
    v1 also injected are deliberately *not* added back, because an import that succeeds under
    rustest and fails under pytest is the worse of the two bugs.
    """
    (src_layout_project / "pytest.ini").write_text("[pytest]\npythonpath = src\n")

    result = run_rustest(src_layout_project)

    assert result.returncode == 0, f"pythonpath ini not honoured: {result.stdout}{result.stderr}"
    assert "3 passed" in result.stderr, result.stderr


def test_flat_layout(flat_layout_project):
    """Test that flat layout works without PYTHONPATH."""
    result = run_rustest(flat_layout_project)

    assert result.returncode == 0, f"rustest failed: {result.stderr}"
    assert "1 passed" in result.stderr, f"Expected 1 test to pass: {result.stderr}"


def test_nested_packages(nested_package_project):
    """Test that nested package structures work correctly."""
    result = run_rustest(nested_package_project)

    assert result.returncode == 0, f"rustest failed: {result.stderr}"
    assert "2 passed" in result.stderr, f"Expected 2 tests to pass: {result.stderr}"
