"""Integration tests for underscore-prefixed fixture discovery."""

from pathlib import Path

from rustest.core import run


def test_class_private_fixture(tmp_path: Path) -> None:
    """Test that class-level private fixtures are discovered and work."""
    test_file = tmp_path / "test_class_private.py"
    test_file.write_text(
        """
from rustest import fixture

class TestPrivateFixtures:
    @fixture
    def _private_helper(self):
        '''Private fixture for class internal use.'''
        return "private_data"

    def test_uses_private(self, _private_helper):
        '''Test can use private fixture.'''
        assert _private_helper == "private_data"

    def test_also_uses_private(self, _private_helper):
        '''Another test using the same private fixture.'''
        assert _private_helper == "private_data"
"""
    )

    report = run(paths=[str(tmp_path)])
    assert report.total == 2
    assert report.passed == 2, f"Both tests should pass. Failures: {report.failed}"


def test_conftest_underscore_fixture(tmp_path: Path) -> None:
    """Test that underscore fixtures in conftest.py are discovered."""
    conftest = tmp_path / "conftest.py"
    conftest.write_text(
        """
from rustest import fixture

@fixture
def _internal_helper():
    '''Internal helper fixture.'''
    return "internal"

@fixture
def public_fixture(_internal_helper):
    '''Public fixture that depends on internal helper.'''
    return f"public_uses_{_internal_helper}"
"""
    )

    test_file = tmp_path / "test_conftest_underscore.py"
    test_file.write_text(
        """
def test_public_fixture(public_fixture):
    '''Test using public fixture that uses internal helper.'''
    assert public_fixture == "public_uses_internal"

def test_direct_internal(_internal_helper):
    '''Test directly using internal helper.'''
    assert _internal_helper == "internal"
"""
    )

    report = run(paths=[str(tmp_path)])
    assert report.total == 2
    assert report.passed == 2


def test_module_level_underscore_fixture(tmp_path: Path) -> None:
    """Test underscore fixtures at module level."""
    test_file = tmp_path / "test_module_underscore.py"
    test_file.write_text(
        """
from rustest import fixture

@fixture
def _module_helper():
    return {"key": "value"}

@fixture
def derived_fixture(_module_helper):
    return _module_helper["key"]

def test_derived(derived_fixture):
    assert derived_fixture == "value"

def test_direct(_module_helper):
    assert _module_helper == {"key": "value"}
"""
    )

    report = run(paths=[str(tmp_path)])
    assert report.total == 2
    assert report.passed == 2


def test_single_underscore_fixture_name(tmp_path: Path) -> None:
    """Test fixture named just '_' (single underscore)."""
    test_file = tmp_path / "test_single_underscore.py"
    test_file.write_text(
        """
from rustest import fixture

@fixture
def _():
    '''Fixture with just underscore name.'''
    return "underscore"

def test_uses_underscore(_):
    assert _ == "underscore"
"""
    )

    report = run(paths=[str(tmp_path)])
    assert report.total == 1
    assert report.passed == 1


def test_double_underscore_fixture(tmp_path: Path) -> None:
    """Test fixture with double underscore prefix (but not dunder)."""
    test_file = tmp_path / "test_double_underscore.py"
    test_file.write_text(
        """
from rustest import fixture

@fixture
def __helper():
    '''Fixture starting with double underscore.'''
    return "double"

def test_double(__helper):
    assert __helper == "double"
"""
    )

    report = run(paths=[str(tmp_path)])
    assert report.total == 1
    assert report.passed == 1


def test_underscore_fixture_with_custom_name(tmp_path: Path) -> None:
    """Test underscore fixture with custom name via @fixture(name=...)."""
    test_file = tmp_path / "test_custom_name.py"
    test_file.write_text(
        """
from rustest import fixture

@fixture(name="public_name")
def _private_implementation():
    '''Private impl with public name.'''
    return "renamed"

def test_custom_name(public_name):
    # Access via public name
    assert public_name == "renamed"
"""
    )

    report = run(paths=[str(tmp_path)])
    assert report.total == 1
    assert report.passed == 1


def test_conftest_underscore_fixture_inheritance(tmp_path: Path) -> None:
    """Test underscore fixtures are inherited from parent conftest."""
    # Create parent conftest
    conftest = tmp_path / "conftest.py"
    conftest.write_text(
        """
from rustest import fixture

@fixture
def _parent_helper():
    return "parent"
"""
    )

    # Create subdirectory with test
    subdir = tmp_path / "subdir"
    subdir.mkdir()

    test_file = subdir / "test_inherit.py"
    test_file.write_text(
        """
def test_inherited(_parent_helper):
    '''Should inherit _parent_helper from parent conftest.'''
    assert _parent_helper == "parent"
"""
    )

    report = run(paths=[str(tmp_path)])
    assert report.total == 1
    assert report.passed == 1


def test_click_style_underscore_fixture(tmp_path: Path) -> None:
    """Test fixture pattern similar to click's _patch_for_completion."""
    test_file = tmp_path / "test_click_pattern.py"
    test_file.write_text(
        """
from rustest import fixture
import sys

@fixture
def _patch_for_completion():
    '''Mimics click's internal fixture pattern.'''
    original_argv = sys.argv.copy()
    sys.argv = ["prog", "arg1", "arg2"]
    yield sys.argv
    sys.argv = original_argv

def test_patched_argv(_patch_for_completion):
    '''Test using click-style internal fixture.'''
    assert "arg1" in _patch_for_completion
    assert "arg2" in _patch_for_completion
"""
    )

    report = run(paths=[str(tmp_path)])
    assert report.total == 1
    assert report.passed == 1


def test_underscore_fixture_in_pytest_compat_mode(tmp_path: Path) -> None:
    """Test underscore fixtures work in pytest-compat mode."""
    conftest = tmp_path / "conftest.py"
    conftest.write_text(
        """
import pytest

@pytest.fixture
def _compat_helper():
    return "compat"
"""
    )

    test_file = tmp_path / "test_compat.py"
    test_file.write_text(
        """
def test_compat_underscore(_compat_helper):
    assert _compat_helper == "compat"
"""
    )

    report = run(paths=[str(tmp_path)], pytest_compat=True)
    assert report.total == 1
    assert report.passed == 1
