"""
Pytest compatibility shim for rustest.

This module provides a pytest-compatible API that translates to rustest
under the hood. It allows users to run existing pytest test suites with
rustest by using: rustest --pytest-compat tests/

Supported pytest features:
- @pytest.fixture() with scopes (function/class/module/session)
- @pytest.mark.* decorators
- @pytest.mark.parametrize()
- @pytest.mark.skip() and @pytest.mark.skipif()
- @pytest.mark.asyncio (from pytest-asyncio plugin)
- pytest.raises()
- pytest.approx()
- Built-in fixtures: tmp_path, tmp_path_factory, tmpdir, tmpdir_factory, monkeypatch

Not supported (with clear error messages):
- Fixture params (@pytest.fixture(params=[...]))
- request fixture
- Some built-in fixtures (capsys, capfd, caplog, etc.)
- Assertion rewriting
- Other pytest plugins

Usage:
    # Instead of modifying your tests, just run:
    $ rustest --pytest-compat tests/

    # Your existing pytest tests will run with rustest:
    import pytest  # This gets intercepted

    @pytest.fixture
    def database():
        return Database()

    @pytest.mark.parametrize("value", [1, 2, 3])
    def test_values(value):
        assert value > 0
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

# Import rustest's actual implementations
from rustest.decorators import (
    fixture as _rustest_fixture,
    parametrize as _rustest_parametrize,
    skip as _rustest_skip,
    mark as _rustest_mark,
    raises as _rustest_raises,
)
from rustest.approx import approx as _rustest_approx

__all__ = [
    "fixture",
    "parametrize",
    "mark",
    "skip",
    "raises",
    "approx",
    "param",
    "warns",
    "deprecated_call",
]

# Type variable for generic functions
F = TypeVar("F", bound=Callable[..., Any])


def fixture(
    func: F | None = None,
    *,
    scope: str = "function",
    params: Any = None,
    autouse: bool = False,
    ids: Any = None,
    name: str | None = None,
) -> F | Callable[[F], F]:
    """
    Pytest-compatible fixture decorator.

    Maps to rustest.fixture with validation for unsupported features.

    Supported:
        - scope: function/class/module/session
        - autouse: True/False

    Not supported (will raise NotImplementedError):
        - params: Use @pytest.mark.parametrize on the test instead
        - ids: Not needed without params
        - name: Use function name

    Examples:
        @pytest.fixture
        def simple_fixture():
            return 42

        @pytest.fixture(scope="module")
        def database():
            db = Database()
            yield db
            db.close()

        @pytest.fixture(autouse=True)
        def setup():
            setup_environment()
    """
    # Validate unsupported parameters
    unsupported = []
    if params is not None:
        unsupported.append("params")
    if ids is not None and params is None:
        # ids without params doesn't make sense anyway
        pass
    elif ids is not None:
        unsupported.append("ids")
    if name is not None:
        unsupported.append("name")

    if unsupported:
        features = ", ".join(unsupported)
        msg = (
            f"rustest --pytest-compat mode doesn't support fixture {features}.\n"
            f"\n"
            f"Workarounds:\n"
            f"  - params: Use @pytest.mark.parametrize() on your test function instead\n"
            f"  - name: Just use the function name\n"
            f"\n"
            f"Note: Built-in fixtures (tmp_path, tmpdir, monkeypatch) are fully supported!\n"
            f"\n"
            f"To use full rustest features, change 'import pytest' to 'from rustest import fixture, mark, ...'."
        )
        raise NotImplementedError(msg)

    # Map to rustest fixture - handle both @pytest.fixture and @pytest.fixture()
    if func is not None:
        # Called as @pytest.fixture (without parentheses)
        return _rustest_fixture(func, scope=scope, autouse=autouse)
    else:
        # Called as @pytest.fixture(...) (with parentheses)
        return _rustest_fixture(scope=scope, autouse=autouse)  # type: ignore[return-value]


# Direct mappings - these already have identical signatures
parametrize = _rustest_parametrize
raises = _rustest_raises
approx = _rustest_approx
skip = _rustest_skip


class _PytestMarkCompat:
    """
    Compatibility wrapper for pytest.mark.

    Provides the same interface as pytest.mark by delegating to rustest.mark.

    Examples:
        @pytest.mark.slow
        @pytest.mark.integration
        def test_expensive():
            pass

        @pytest.mark.skipif(sys.platform == "win32", reason="Unix only")
        def test_unix():
            pass
    """

    def __getattr__(self, name: str) -> Any:
        """Delegate all mark.* access to rustest.mark.*"""
        return getattr(_rustest_mark, name)

    # Explicitly expose common marks for better IDE support
    @property
    def parametrize(self) -> Any:
        """Alias for @pytest.mark.parametrize (same as top-level parametrize)."""
        return _rustest_mark.parametrize

    def skip(self, reason: str | None = None) -> Callable[[F], F]:
        """Mark test as skipped.

        This is the @pytest.mark.skip() decorator which should skip the test.
        Maps to rustest's skip() decorator.
        """
        return _rustest_skip(reason=reason)  # type: ignore[return-value]

    @property
    def skipif(self) -> Any:
        """Conditional skip decorator."""
        return _rustest_mark.skipif

    @property
    def xfail(self) -> Any:
        """Mark test as expected to fail."""
        return _rustest_mark.xfail

    @property
    def asyncio(self) -> Any:
        """Mark async test to run with asyncio."""
        return _rustest_mark.asyncio


# Create the mark instance
mark = _PytestMarkCompat()


def param(*values: Any, **kwargs: Any) -> Any:
    """
    Pytest's param() for parametrize is not directly supported.

    In pytest, you can do:
        @pytest.mark.parametrize("x", [
            pytest.param(1, marks=pytest.mark.skip),
            pytest.param(2),
        ])

    Rustest doesn't support per-parameter marks yet. This function
    raises a helpful error.
    """
    msg = (
        "rustest --pytest-compat mode doesn't support pytest.param() yet.\n"
        "\n"
        "This feature allows marking individual parameter sets, which\n"
        "rustest doesn't support yet. You can work around this by:\n"
        "  1. Splitting into separate test functions\n"
        "  2. Using conditional skips inside the test\n"
        "\n"
        "To use full rustest features, change 'import pytest' to 'from rustest import fixture, mark, ...'."
    )
    raise NotImplementedError(msg)


def warns(*args: Any, **kwargs: Any) -> Any:
    """
    Pytest's warns() context manager is not supported.

    Rustest focuses on test failures and exceptions. Warning capture
    is not currently supported.
    """
    msg = (
        "rustest --pytest-compat mode doesn't support pytest.warns().\n"
        "\n"
        "Use Python's warnings module directly if needed:\n"
        "  import warnings\n"
        "  with warnings.catch_warnings(record=True) as w:\n"
        "      # your code\n"
    )
    raise NotImplementedError(msg)


def deprecated_call(*args: Any, **kwargs: Any) -> Any:
    """Pytest's deprecated_call() is not supported."""
    msg = "rustest --pytest-compat mode doesn't support pytest.deprecated_call()."
    raise NotImplementedError(msg)


# Module-level version to match pytest
__version__ = "rustest-compat"
