#!/usr/bin/env python3
"""
Integration tests for different project layouts.

This test creates temporary project structures and verifies that rustest
can correctly discover and run tests for each layout pattern.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def create_src_layout_project(base_dir):
    """Create a project with src/ layout."""
    # Create structure
    src_dir = base_dir / "src"
    pkg_dir = src_dir / "mypackage"
    pkg_dir.mkdir(parents=True)
    tests_dir = base_dir / "tests"
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

    return base_dir


def create_flat_layout_project(base_dir):
    """Create a project with flat layout."""
    # Create structure
    pkg_dir = base_dir / "mypackage"
    pkg_dir.mkdir()
    tests_dir = base_dir / "tests"
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

    return base_dir


def create_nested_package_project(base_dir):
    """Create a project with nested packages."""
    # Create structure
    pkg_dir = base_dir / "mypackage"
    pkg_dir.mkdir()
    sub_dir = pkg_dir / "subpackage"
    sub_dir.mkdir()
    tests_dir = base_dir / "tests"
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

    return base_dir


def run_rustest(project_dir):
    """Run rustest on a project directory and return result."""
    cmd = [sys.executable, "-m", "rustest", str(project_dir / "tests")]
    result = subprocess.run(
        cmd,
        cwd=project_dir,
        capture_output=True,
        text=True
    )
    return result


def main():
    """Run integration tests for all project layouts."""
    print("Testing project layout support...")
    print("=" * 60)

    all_passed = True

    # Test src layout
    print("\n1. Testing src/ layout...")
    with tempfile.TemporaryDirectory(prefix="rustest_integration_src_") as tmpdir:
        project = create_src_layout_project(Path(tmpdir))
        result = run_rustest(project)

        if result.returncode == 0 and "3 passed" in result.stdout:
            print("   ✓ Src layout: PASSED")
        else:
            print(f"   ✗ Src layout: FAILED")
            print(f"   stdout: {result.stdout}")
            print(f"   stderr: {result.stderr}")
            all_passed = False

    # Test flat layout
    print("\n2. Testing flat layout...")
    with tempfile.TemporaryDirectory(prefix="rustest_integration_flat_") as tmpdir:
        project = create_flat_layout_project(Path(tmpdir))
        result = run_rustest(project)

        if result.returncode == 0 and "1 passed" in result.stdout:
            print("   ✓ Flat layout: PASSED")
        else:
            print(f"   ✗ Flat layout: FAILED")
            print(f"   stdout: {result.stdout}")
            print(f"   stderr: {result.stderr}")
            all_passed = False

    # Test nested packages
    print("\n3. Testing nested packages...")
    with tempfile.TemporaryDirectory(prefix="rustest_integration_nested_") as tmpdir:
        project = create_nested_package_project(Path(tmpdir))
        result = run_rustest(project)

        if result.returncode == 0 and "2 passed" in result.stdout:
            print("   ✓ Nested packages: PASSED")
        else:
            print(f"   ✗ Nested packages: FAILED")
            print(f"   stdout: {result.stdout}")
            print(f"   stderr: {result.stderr}")
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("✓ All layout integration tests PASSED!")
        return 0
    else:
        print("✗ Some layout integration tests FAILED!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
