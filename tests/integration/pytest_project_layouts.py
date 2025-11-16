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


def run_rustest(project_dir):
    """Run rustest on a project directory and return result."""
    cmd = [sys.executable, "-m", "rustest", str(project_dir / "tests")]
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


def test_src_layout(src_layout_project):
    """Test that src/ layout works without PYTHONPATH."""
    result = run_rustest(src_layout_project)

    assert result.returncode == 0, f"rustest failed: {result.stderr}"
    assert "3 passed" in result.stderr, f"Expected 3 tests to pass: {result.stderr}"


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
