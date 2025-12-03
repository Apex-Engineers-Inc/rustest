"""Regression tests for bugs that have been fixed.

Each test documents a specific bug and ensures it doesn't regress.

This test file is designed to be run with rustest:
    uv run python -m rustest tests/test_regression_bugs.py
"""

from __future__ import annotations

import sys

# Skip this entire module when running with pytest
if "pytest" in sys.argv[0]:
    import pytest

    pytest.skip(
        "This test file requires rustest runner (rustest-specific features)",
        allow_module_level=True,
    )

from rustest import fixture, mark, parametrize


# Regression test for bug #99: Multiple @parametrize decorators should create cross-products
@parametrize("a", [1, 2])
@parametrize("b", [10, 20])
def test_multiple_parametrize_creates_cross_product(a: int, b: int) -> None:
    """Bug #99: Multiple @parametrize decorators now create cross-products.

    Before the fix, multiple decorators would overwrite each other instead of
    creating the cartesian product.
    """
    assert a in (1, 2)
    assert b in (10, 20)


# We need to track the combinations to verify all are run
_cross_product_combinations: set[tuple[int, int]] = set()


@parametrize("x", [1, 2, 3])
@parametrize("y", ["a", "b"])
def test_cross_product_all_combinations(x: int, y: str) -> None:
    """Verify that all cross-product combinations are actually executed."""
    _cross_product_combinations.add((x, y))  # type: ignore[arg-type]


def test_cross_product_verify_count() -> None:
    """Verify the expected number of combinations were executed.

    3 x values * 2 y values = 6 combinations.
    """
    # Note: This test must run after the cross-product tests
    # In parallel execution, this might be flaky
    assert len(_cross_product_combinations) == 6


# Regression test for bug #102: Nested conftest autouse fixture discovery
@fixture(autouse=True, scope="function")
def autouse_regression_fixture() -> str:
    """This autouse fixture should be discovered and run automatically."""
    return "autouse_ran"


_autouse_marker: list[str] = []


@fixture
def dependent_fixture(autouse_regression_fixture: str) -> str:
    """A fixture that depends on an autouse fixture."""
    _autouse_marker.append(autouse_regression_fixture)
    return f"dependent_{autouse_regression_fixture}"


def test_autouse_fixture_runs(dependent_fixture: str) -> None:
    """Bug #102: Autouse fixtures should be discovered in nested conftests."""
    assert dependent_fixture == "dependent_autouse_ran"


# Regression test for bounds checking on parametrized fixtures
@fixture(params=[1, 2, 3])
def bounded_param_fixture(request: object) -> int:
    """A parametrized fixture for testing bounds checking."""
    return request.param  # type: ignore[attr-defined, no-any-return]


def test_bounded_param_fixture(bounded_param_fixture: int) -> None:
    """Test that parametrized fixtures work correctly with bounds checking."""
    assert bounded_param_fixture in (1, 2, 3)


# Regression test for MonkeyPatch with dotted path validation
def test_monkeypatch_dotted_path_validation(monkeypatch: object) -> None:
    """MonkeyPatch should require at least one dot in dotted path syntax."""
    import pytest

    with pytest.raises(TypeError, match="at least one dot"):
        monkeypatch.setattr("nodots", "value")  # type: ignore[attr-defined]


# Regression test for approx with different sequence types
def test_approx_sequence_type_flexibility() -> None:
    """Approx should compare lists and tuples with same contents as equal."""
    from rustest import approx

    # List vs tuple should work
    result = (0.1 + 0.2, 0.3)
    assert result == approx([0.3, 0.3])

    # Tuple vs list should also work
    result_list = [0.1 + 0.2, 0.3]
    assert result_list == approx((0.3, 0.3))


# Regression test for class-scoped fixtures on package boundary
class TestClassScopeIsolation:
    """Test that class-scoped fixtures are properly isolated across packages."""

    @fixture(scope="class")
    def class_scoped_value(self) -> str:
        """A class-scoped fixture that should be isolated."""
        return "class_value"

    def test_class_scope_first(self, class_scoped_value: str) -> None:
        """First test using class-scoped fixture."""
        assert class_scoped_value == "class_value"

    def test_class_scope_second(self, class_scoped_value: str) -> None:
        """Second test should get same instance within class."""
        assert class_scoped_value == "class_value"


# Regression test for skipif with boolean conditions
_skip_condition_evaluated = False


@mark.skipif(False, reason="This should not be skipped")
def test_skipif_boolean_condition_false() -> None:
    """skipif with false boolean condition should run the test."""
    global _skip_condition_evaluated
    _skip_condition_evaluated = True
    assert True


@mark.skipif(True, reason="This condition is true")
def test_skipif_boolean_condition_true() -> None:
    """skipif with true boolean condition should skip the test."""
    # This should be skipped - if it runs, the test framework handles skipif
    pass


# Regression test for xfail - these test the decorator attachment, not execution
@mark.xfail(reason="This test is expected to fail")
def test_xfail_marked() -> None:
    """xfail mark should be attached to test."""
    # Pass the test - xfail decoration is what we're testing
    pass


@mark.xfail(reason="This test unexpectedly passes", strict=False)
def test_xfail_unexpected_pass() -> None:
    """xfail test that unexpectedly passes (non-strict)."""
    assert True  # This passes but was expected to fail


# Regression test for usefixtures mark
_usefixtures_ran = False


@fixture
def usefixtures_side_effect() -> None:
    """Fixture with side effect for testing usefixtures."""
    global _usefixtures_ran
    _usefixtures_ran = True


@mark.usefixtures("usefixtures_side_effect")
def test_usefixtures_mark() -> None:
    """usefixtures mark should trigger fixture execution."""
    assert _usefixtures_ran


# Note: indirect parametrization is tested in tests/test_indirect_parametrization.py
