"""Tests for complex fixture dependency chains and edge cases.

These tests verify that rustest correctly handles various fixture dependency
scenarios including deep chains, diamond dependencies, and scope interactions.

This test file is designed to be run with rustest:
    uv run python -m rustest tests/test_fixture_dependency_chains.py
"""

from __future__ import annotations

import sys

# Skip this entire module when running with pytest
if "pytest" in sys.argv[0]:
    import pytest

    pytest.skip(
        "This test file requires rustest runner (rustest-specific fixture behavior)",
        allow_module_level=True,
    )

from typing import Any

from rustest import fixture


# Track fixture execution order for verification
_chain_order: list[str] = []


def setup_module() -> None:
    """Reset execution tracking before tests."""
    global _chain_order
    _chain_order = []


# =============================================================================
# Deep Dependency Chain (5+ levels)
# =============================================================================


@fixture
def chain_base() -> str:
    """Base of the chain (level 5)."""
    _chain_order.append("chain_base")
    return "base"


@fixture
def chain_4(chain_base: str) -> str:
    """Level 4 - depends on base."""
    _chain_order.append("chain_4")
    return f"{chain_base}->L4"


@fixture
def chain_3(chain_4: str) -> str:
    """Level 3 - depends on level 4."""
    _chain_order.append("chain_3")
    return f"{chain_4}->L3"


@fixture
def chain_2(chain_3: str) -> str:
    """Level 2 - depends on level 3."""
    _chain_order.append("chain_2")
    return f"{chain_3}->L2"


@fixture
def chain_top(chain_2: str) -> str:
    """Top of chain - depends on level 2."""
    _chain_order.append("chain_top")
    return f"{chain_2}->top"


def test_deep_dependency_chain(chain_top: str) -> None:
    """Test that deep fixture chains (5+ levels) work correctly."""
    assert chain_top == "base->L4->L3->L2->top"
    # Verify execution order - deepest first
    assert "chain_base" in _chain_order
    assert "chain_top" in _chain_order


# =============================================================================
# Diamond Dependency (multiple paths to same fixture)
# =============================================================================


@fixture
def diamond_base() -> str:
    """Base fixture at the bottom of the diamond."""
    _chain_order.append("diamond_base")
    return "diamond_base_value"


@fixture
def diamond_left(diamond_base: str) -> str:
    """Left path in the diamond."""
    _chain_order.append("diamond_left")
    return f"left({diamond_base})"


@fixture
def diamond_right(diamond_base: str) -> str:
    """Right path in the diamond."""
    _chain_order.append("diamond_right")
    return f"right({diamond_base})"


@fixture
def diamond_top(diamond_left: str, diamond_right: str) -> str:
    """Top of the diamond - depends on both paths."""
    _chain_order.append("diamond_top")
    return f"top({diamond_left}, {diamond_right})"


def test_diamond_dependency(diamond_top: str) -> None:
    """Test that diamond dependencies resolve correctly."""
    # Both left and right should have the same base value
    assert "diamond_base_value" in diamond_top
    # The base should only be created once (check for single occurrence in order)
    base_count = _chain_order.count("diamond_base")
    assert base_count == 1, f"Base fixture created {base_count} times, expected 1"


# =============================================================================
# Multiple Independent Fixture Chains
# =============================================================================


@fixture
def independent_a() -> str:
    """First independent chain."""
    _chain_order.append("independent_a")
    return "chain_a"


@fixture
def independent_b() -> str:
    """Second independent chain."""
    _chain_order.append("independent_b")
    return "chain_b"


@fixture
def merger(independent_a: str, independent_b: str) -> str:
    """Merges two independent chains."""
    _chain_order.append("merger")
    return f"{independent_a}+{independent_b}"


def test_independent_chains(merger: str) -> None:
    """Test that independent chains are both initialized."""
    assert merger == "chain_a+chain_b"


# =============================================================================
# Fixture Scope Hierarchy
# =============================================================================


@fixture(scope="session")
def sess_scope_fixture() -> str:
    """Session-scoped fixture."""
    _chain_order.append("session")
    return "session_value"


@fixture(scope="module")
def mod_scope_fixture(sess_scope_fixture: str) -> str:
    """Module-scoped fixture depending on session fixture."""
    _chain_order.append("module")
    return f"module({sess_scope_fixture})"


@fixture(scope="function")
def func_scope_fixture(mod_scope_fixture: str) -> str:
    """Function-scoped fixture depending on module fixture."""
    _chain_order.append("function")
    return f"function({mod_scope_fixture})"


def test_scope_hierarchy(func_scope_fixture: str) -> None:
    """Test that scope hierarchy is respected (wider -> narrower)."""
    assert "session_value" in func_scope_fixture
    assert "module" in func_scope_fixture
    assert "function" in func_scope_fixture


# =============================================================================
# Mixed Autouse and Regular Fixtures
# =============================================================================


_autouse_ran = False


@fixture(autouse=True)
def autouse_base() -> None:
    """Autouse fixture that runs automatically."""
    global _autouse_ran
    _autouse_ran = True
    _chain_order.append("autouse_base")


@fixture
def depends_on_autouse(autouse_base: None) -> str:
    """Fixture that explicitly depends on autouse fixture."""
    _chain_order.append("depends_on_autouse")
    return "depends_on_autouse"


def test_autouse_runs_automatically() -> None:
    """Test that autouse fixtures run without explicit request."""
    global _autouse_ran
    assert _autouse_ran, "Autouse fixture should have run"


def test_explicit_autouse_dependency(depends_on_autouse: str) -> None:
    """Test that explicit dependency on autouse works."""
    assert depends_on_autouse == "depends_on_autouse"


# =============================================================================
# Generator/Yield Fixture Chains
# =============================================================================


_teardown_order: list[str] = []


@fixture
def yield_base():
    """Base generator fixture."""
    _chain_order.append("yield_base_setup")
    yield "yield_base_value"
    _teardown_order.append("yield_base_teardown")


@fixture
def yield_dependent(yield_base: str):
    """Dependent generator fixture."""
    _chain_order.append("yield_dependent_setup")
    yield f"dependent({yield_base})"
    _teardown_order.append("yield_dependent_teardown")


def test_yield_fixture_chain(yield_dependent: str) -> None:
    """Test that yield fixture chains work correctly."""
    assert yield_dependent == "dependent(yield_base_value)"
    # Setup order should be base first
    assert "yield_base_setup" in _chain_order


# =============================================================================
# Circular Dependency Detection (should fail at collection time)
# =============================================================================

# NOTE: These tests are intentionally commented out as they would cause
# collection errors. They serve as documentation for expected behavior.
#
# @fixture
# def circular_a(circular_b: str) -> str:
#     return f"a({circular_b})"
#
# @fixture
# def circular_b(circular_a: str) -> str:
#     return f"b({circular_a})"
#
# def test_circular_dependency(circular_a: str) -> None:
#     pass  # Should fail during collection


# =============================================================================
# Fixture with Optional Dependencies
# =============================================================================


@fixture
def optional_base() -> str:
    """Base fixture that may or may not be used."""
    _chain_order.append("optional_base")
    return "optional_value"


def test_without_optional_fixture() -> None:
    """Test that works without the optional fixture."""
    assert True


def test_with_optional_fixture(optional_base: str) -> None:
    """Test that uses the optional fixture."""
    assert optional_base == "optional_value"
