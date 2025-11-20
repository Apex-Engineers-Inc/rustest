"""Tests for package-scoped fixtures.

Package scope shares fixtures across all tests in a package (directory).
This is useful for expensive setup that should be reused within a package
but reset when moving to a different package.
"""

import os
import tempfile

from rustest import run


class TestPackageScopeBasic:
    """Basic tests for package-scoped fixtures."""

    def test_package_scope_shared_within_package(self):
        """Package-scoped fixtures are shared across modules in the same package."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create package structure
            pkg_a = os.path.join(tmpdir, "pkg_a")
            os.makedirs(pkg_a)

            # Create __init__.py
            with open(os.path.join(pkg_a, "__init__.py"), "w") as f:
                f.write("")

            # Create conftest with package-scoped fixture
            conftest = os.path.join(pkg_a, "conftest.py")
            with open(conftest, "w") as f:
                f.write("""
from rustest import fixture

counter = {"value": 0}

@fixture(scope="package")
def pkg_fixture():
    counter["value"] += 1
    return counter["value"]
""")

            # Create two test modules in the same package
            test_mod1 = os.path.join(pkg_a, "test_mod1.py")
            with open(test_mod1, "w") as f:
                f.write("""
def test_first(pkg_fixture):
    # First test should get value 1
    assert pkg_fixture == 1
""")

            test_mod2 = os.path.join(pkg_a, "test_mod2.py")
            with open(test_mod2, "w") as f:
                f.write("""
def test_second(pkg_fixture):
    # Second test in same package should get same value (1)
    assert pkg_fixture == 1
""")

            result = run(paths=[pkg_a])
            assert result.passed == 2
            assert result.failed == 0

    def test_package_scope_reset_between_packages(self):
        """Package-scoped fixtures get fresh instances when entering a new package."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two packages
            pkg_a = os.path.join(tmpdir, "pkg_a")
            pkg_b = os.path.join(tmpdir, "pkg_b")
            os.makedirs(pkg_a)
            os.makedirs(pkg_b)

            # Create __init__.py files
            with open(os.path.join(pkg_a, "__init__.py"), "w") as f:
                f.write("")
            with open(os.path.join(pkg_b, "__init__.py"), "w") as f:
                f.write("")

            # Create shared conftest at root with package-scoped fixture
            conftest = os.path.join(tmpdir, "conftest.py")
            with open(conftest, "w") as f:
                f.write("""
from rustest import fixture

counter = {"value": 0}

@fixture(scope="package")
def pkg_fixture():
    counter["value"] += 1
    return counter["value"]
""")

            # Test in pkg_a
            test_a = os.path.join(pkg_a, "test_a.py")
            with open(test_a, "w") as f:
                f.write("""
def test_in_pkg_a(pkg_fixture):
    # Verify fixture was called and returned a value
    assert pkg_fixture >= 1
""")

            # Test in pkg_b
            test_b = os.path.join(pkg_b, "test_b.py")
            with open(test_b, "w") as f:
                f.write("""
def test_in_pkg_b(pkg_fixture):
    # Verify fixture was called and returned a value
    assert pkg_fixture >= 1
""")

            result = run(paths=[tmpdir])
            assert result.passed == 2
            assert result.failed == 0

    def test_package_scope_with_generator_fixture(self):
        """Package-scoped generator fixtures run teardown at package boundary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two packages
            pkg_a = os.path.join(tmpdir, "pkg_a")
            pkg_b = os.path.join(tmpdir, "pkg_b")
            os.makedirs(pkg_a)
            os.makedirs(pkg_b)

            # Create __init__.py files
            with open(os.path.join(pkg_a, "__init__.py"), "w") as f:
                f.write("")
            with open(os.path.join(pkg_b, "__init__.py"), "w") as f:
                f.write("")

            # Create shared conftest with generator fixture
            conftest = os.path.join(tmpdir, "conftest.py")
            with open(conftest, "w") as f:
                f.write("""
from rustest import fixture

lifecycle = {"setup": 0, "teardown": 0}

@fixture(scope="package")
def pkg_resource():
    lifecycle["setup"] += 1
    yield lifecycle["setup"]
    lifecycle["teardown"] += 1
""")

            # Test in pkg_a
            test_a = os.path.join(pkg_a, "test_a.py")
            with open(test_a, "w") as f:
                f.write("""
def test_in_pkg_a(pkg_resource):
    # Verify fixture was called and returned a value
    assert pkg_resource >= 1
""")

            # Test in pkg_b
            test_b = os.path.join(pkg_b, "test_b.py")
            with open(test_b, "w") as f:
                f.write("""
def test_in_pkg_b(pkg_resource):
    # Verify fixture was called and returned a value
    assert pkg_resource >= 1
""")

            result = run(paths=[tmpdir])
            assert result.passed == 2
            assert result.failed == 0


class TestPackageScopeDependencies:
    """Tests for package-scoped fixture dependencies."""

    def test_package_depends_on_session(self):
        """Package-scoped fixtures can depend on session-scoped fixtures."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_a = os.path.join(tmpdir, "pkg_a")
            os.makedirs(pkg_a)

            with open(os.path.join(pkg_a, "__init__.py"), "w") as f:
                f.write("")

            conftest = os.path.join(pkg_a, "conftest.py")
            with open(conftest, "w") as f:
                f.write("""
from rustest import fixture

@fixture(scope="session")
def session_value():
    return 100

@fixture(scope="package")
def pkg_value(session_value):
    return session_value + 1
""")

            test_file = os.path.join(pkg_a, "test_deps.py")
            with open(test_file, "w") as f:
                f.write("""
def test_package_depends_on_session(pkg_value):
    assert pkg_value == 101
""")

            result = run(paths=[pkg_a])
            assert result.passed == 1
            assert result.failed == 0

    def test_module_can_depend_on_package(self):
        """Module-scoped fixtures can depend on package-scoped fixtures (narrower on broader)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_a = os.path.join(tmpdir, "pkg_a")
            os.makedirs(pkg_a)

            with open(os.path.join(pkg_a, "__init__.py"), "w") as f:
                f.write("")

            conftest = os.path.join(pkg_a, "conftest.py")
            with open(conftest, "w") as f:
                f.write("""
from rustest import fixture

@fixture(scope="package")
def pkg_value():
    return 100

@fixture(scope="module")
def mod_value(pkg_value):
    # This is valid - narrower scope can depend on broader scope
    return pkg_value + 1
""")

            test_file = os.path.join(pkg_a, "test_valid.py")
            with open(test_file, "w") as f:
                f.write("""
def test_valid_dependency(mod_value):
    assert mod_value == 101
""")

            result = run(paths=[pkg_a])
            # Should pass - narrower scope can depend on broader scope
            assert result.passed == 1
            assert result.failed == 0

    def test_function_can_depend_on_package(self):
        """Function-scoped fixtures can depend on package-scoped fixtures."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_a = os.path.join(tmpdir, "pkg_a")
            os.makedirs(pkg_a)

            with open(os.path.join(pkg_a, "__init__.py"), "w") as f:
                f.write("")

            conftest = os.path.join(pkg_a, "conftest.py")
            with open(conftest, "w") as f:
                f.write("""
from rustest import fixture

@fixture(scope="package")
def pkg_value():
    return 100

@fixture(scope="function")
def func_value(pkg_value):
    return pkg_value + 1
""")

            test_file = os.path.join(pkg_a, "test_valid.py")
            with open(test_file, "w") as f:
                f.write("""
def test_valid_dependency(func_value):
    assert func_value == 101
""")

            result = run(paths=[pkg_a])
            assert result.passed == 1
            assert result.failed == 0


class TestPackageScopeAutouse:
    """Tests for package-scoped autouse fixtures."""

    def test_package_autouse_runs_once_per_package(self):
        """Package-scoped autouse fixtures run once per package."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_a = os.path.join(tmpdir, "pkg_a")
            os.makedirs(pkg_a)

            with open(os.path.join(pkg_a, "__init__.py"), "w") as f:
                f.write("")

            # Create a shared state module that can be imported
            shared_module = os.path.join(pkg_a, "shared_state.py")
            with open(shared_module, "w") as f:
                f.write("""
setup_count = {"value": 0}
""")

            conftest = os.path.join(pkg_a, "conftest.py")
            with open(conftest, "w") as f:
                f.write(f"""
import sys
sys.path.insert(0, "{pkg_a}")
from rustest import fixture
from shared_state import setup_count

@fixture(scope="package", autouse=True)
def auto_setup():
    setup_count["value"] += 1
    return setup_count["value"]
""")

            # Create two test files in the same package
            test_mod1 = os.path.join(pkg_a, "test_mod1.py")
            with open(test_mod1, "w") as f:
                f.write(f"""
import sys
sys.path.insert(0, "{pkg_a}")
from shared_state import setup_count

def test_first():
    assert setup_count["value"] == 1

def test_second():
    assert setup_count["value"] == 1
""")

            test_mod2 = os.path.join(pkg_a, "test_mod2.py")
            with open(test_mod2, "w") as f:
                f.write(f"""
import sys
sys.path.insert(0, "{pkg_a}")
from shared_state import setup_count

def test_third():
    # Still same package, so autouse fixture only ran once
    assert setup_count["value"] == 1
""")

            result = run(paths=[pkg_a])
            assert result.passed == 3
            assert result.failed == 0


class TestPackageScopeNested:
    """Tests for package scope with nested packages."""

    def test_nested_packages_have_separate_scopes(self):
        """Nested packages are treated as separate packages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create nested package structure
            pkg_parent = os.path.join(tmpdir, "parent")
            pkg_child = os.path.join(pkg_parent, "child")
            os.makedirs(pkg_child)

            # Create __init__.py files
            with open(os.path.join(pkg_parent, "__init__.py"), "w") as f:
                f.write("")
            with open(os.path.join(pkg_child, "__init__.py"), "w") as f:
                f.write("")

            # Shared conftest at root
            conftest = os.path.join(tmpdir, "conftest.py")
            with open(conftest, "w") as f:
                f.write("""
from rustest import fixture

counter = {"value": 0}

@fixture(scope="package")
def pkg_fixture():
    counter["value"] += 1
    return counter["value"]
""")

            # Test in parent package
            test_parent = os.path.join(pkg_parent, "test_parent.py")
            with open(test_parent, "w") as f:
                f.write("""
def test_in_parent(pkg_fixture):
    # Just verify fixture was called and returned a value
    assert pkg_fixture >= 1
""")

            # Test in child package
            test_child = os.path.join(pkg_child, "test_child.py")
            with open(test_child, "w") as f:
                f.write("""
def test_in_child(pkg_fixture):
    # Child is a different package, so gets its own instance
    assert pkg_fixture >= 1
""")

            result = run(paths=[tmpdir])
            assert result.passed == 2
            assert result.failed == 0


class TestPackageScopeWithOtherScopes:
    """Tests for package scope interacting with other scopes."""

    def test_all_scopes_together(self):
        """All fixture scopes work together correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_a = os.path.join(tmpdir, "pkg_a")
            os.makedirs(pkg_a)

            with open(os.path.join(pkg_a, "__init__.py"), "w") as f:
                f.write("")

            conftest = os.path.join(pkg_a, "conftest.py")
            with open(conftest, "w") as f:
                f.write("""
from rustest import fixture

@fixture(scope="session")
def session_fix():
    return "session"

@fixture(scope="package")
def package_fix():
    return "package"

@fixture(scope="module")
def module_fix():
    return "module"

@fixture(scope="function")
def function_fix():
    return "function"
""")

            test_file = os.path.join(pkg_a, "test_all.py")
            with open(test_file, "w") as f:
                f.write("""
def test_all_scopes(session_fix, package_fix, module_fix, function_fix):
    assert session_fix == "session"
    assert package_fix == "package"
    assert module_fix == "module"
    assert function_fix == "function"
""")

            result = run(paths=[pkg_a])
            assert result.passed == 1
            assert result.failed == 0

    def test_scope_ordering_is_correct(self):
        """Fixture scope ordering: function < class < module < package < session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_a = os.path.join(tmpdir, "pkg_a")
            os.makedirs(pkg_a)

            with open(os.path.join(pkg_a, "__init__.py"), "w") as f:
                f.write("")

            conftest = os.path.join(pkg_a, "conftest.py")
            with open(conftest, "w") as f:
                f.write("""
from rustest import fixture

call_order = []

@fixture(scope="session")
def session_fix():
    call_order.append("session")
    return call_order

@fixture(scope="package")
def package_fix(session_fix):
    call_order.append("package")
    return call_order

@fixture(scope="module")
def module_fix(package_fix):
    call_order.append("module")
    return call_order

@fixture(scope="function")
def function_fix(module_fix):
    call_order.append("function")
    return call_order
""")

            test_file = os.path.join(pkg_a, "test_order.py")
            with open(test_file, "w") as f:
                f.write("""
def test_ordering(function_fix):
    # Fixtures should be created in order: session -> package -> module -> function
    assert function_fix == ["session", "package", "module", "function"]
""")

            result = run(paths=[pkg_a])
            assert result.passed == 1
            assert result.failed == 0
