"""Test file for nested fixture dependencies."""

from rustest import fixture


@fixture
def base_value():
    """Base fixture that returns a value."""
    return 10


@fixture
def doubled(base_value):
    """Fixture that depends on base_value."""
    return base_value * 2


@fixture
def tripled(base_value):
    """Another fixture that depends on base_value."""
    return base_value * 3


@fixture
def combined(doubled, tripled):
    """Fixture that depends on multiple other fixtures."""
    return doubled + tripled


def test_base_fixture(base_value):
    """Test using the base fixture."""
    assert base_value == 10


def test_doubled_fixture(doubled):
    """Test using the doubled fixture."""
    assert doubled == 20


def test_combined_fixture(combined):
    """Test using the combined fixture."""
    assert combined == 50


def test_multiple_fixtures(base_value, doubled, tripled):
    """Test using multiple fixtures."""
    assert base_value == 10
    assert doubled == 20
    assert tripled == 30
    assert doubled + tripled == 50
