"""Unit tests for the fixture decorator with scope support."""

import pytest

from rustest import fixture


def test_fixture_default_scope():
    """Test that fixture has default function scope."""

    @fixture
    def my_fixture():
        return 42

    assert hasattr(my_fixture, "__rustest_fixture__")
    assert my_fixture.__rustest_fixture__ is True
    assert hasattr(my_fixture, "__rustest_fixture_scope__")
    assert my_fixture.__rustest_fixture_scope__ == "function"


def test_fixture_explicit_function_scope():
    """Test explicit function scope."""

    @fixture(scope="function")
    def my_fixture():
        return 42

    assert my_fixture.__rustest_fixture__ is True
    assert my_fixture.__rustest_fixture_scope__ == "function"


def test_fixture_class_scope():
    """Test class scope."""

    @fixture(scope="class")
    def my_fixture():
        return 42

    assert my_fixture.__rustest_fixture__ is True
    assert my_fixture.__rustest_fixture_scope__ == "class"


def test_fixture_module_scope():
    """Test module scope."""

    @fixture(scope="module")
    def my_fixture():
        return 42

    assert my_fixture.__rustest_fixture__ is True
    assert my_fixture.__rustest_fixture_scope__ == "module"


def test_fixture_session_scope():
    """Test session scope."""

    @fixture(scope="session")
    def my_fixture():
        return 42

    assert my_fixture.__rustest_fixture__ is True
    assert my_fixture.__rustest_fixture_scope__ == "session"


def test_fixture_invalid_scope():
    """Test that invalid scope raises ValueError."""
    with pytest.raises(ValueError, match="Invalid fixture scope 'invalid'"):

        @fixture(scope="invalid")
        def my_fixture():
            return 42


def test_fixture_invalid_scope_message():
    """Test that error message lists valid scopes."""
    with pytest.raises(ValueError, match="Must be one of: class, function, module, session"):

        @fixture(scope="wrong")
        def my_fixture():
            return 42


def test_fixture_preserves_function_name():
    """Test that decorator preserves function name and docstring."""

    @fixture(scope="module")
    def my_special_fixture():
        """This is a special fixture."""
        return 42

    assert my_special_fixture.__name__ == "my_special_fixture"
    assert my_special_fixture.__doc__ == "This is a special fixture."


def test_fixture_with_parameters():
    """Test fixture decorator on function with parameters."""

    @fixture(scope="session")
    def my_fixture(other_fixture):
        return other_fixture * 2

    assert my_fixture.__rustest_fixture__ is True
    assert my_fixture.__rustest_fixture_scope__ == "session"
    # Function should still have its parameter
    assert my_fixture.__code__.co_varnames[0] == "other_fixture"


def test_fixture_multiple_decorators():
    """Test that fixture decorator can be combined with other decorators."""
    from rustest import mark

    @fixture(scope="module")
    @mark.slow
    def my_fixture():
        return 42

    assert my_fixture.__rustest_fixture__ is True
    assert my_fixture.__rustest_fixture_scope__ == "module"
    assert hasattr(my_fixture, "__rustest_marks__")


def test_fixture_without_parentheses():
    """Test that @fixture works without parentheses."""

    @fixture
    def my_fixture():
        return 42

    assert my_fixture.__rustest_fixture__ is True
    assert my_fixture.__rustest_fixture_scope__ == "function"


def test_fixture_with_parentheses_default():
    """Test that @fixture() works with parentheses and default scope."""

    @fixture()
    def my_fixture():
        return 42

    assert my_fixture.__rustest_fixture__ is True
    assert my_fixture.__rustest_fixture_scope__ == "function"


def test_fixture_scope_case_sensitive():
    """Test that scope is case-sensitive."""
    with pytest.raises(ValueError, match="Invalid fixture scope 'Function'"):

        @fixture(scope="Function")
        def my_fixture():
            return 42


def test_fixture_scope_whitespace():
    """Test that scope doesn't accept whitespace variants."""
    with pytest.raises(ValueError, match="Invalid fixture scope ' function'"):

        @fixture(scope=" function")
        def my_fixture():
            return 42


def test_all_valid_scopes():
    """Test that all documented scopes are valid."""
    from rustest.decorators import VALID_SCOPES

    assert VALID_SCOPES == frozenset(["function", "class", "module", "session"])

    for scope in VALID_SCOPES:

        @fixture(scope=scope)
        def test_fixture():
            return 42

        assert test_fixture.__rustest_fixture_scope__ == scope
