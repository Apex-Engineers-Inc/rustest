"""Test yield fixtures in conftest."""


def test_simple_yield(simple_yield):
    """Test simple yield fixture."""
    assert simple_yield == "simple_value"


def test_yield_with_cleanup(yield_with_cleanup):
    """Test yield fixture with cleanup."""
    assert yield_with_cleanup["status"] == "open"


def test_dependent_yield(dependent_yield):
    """Test yield fixture with dependencies."""
    assert dependent_yield == "uses_simple_value"


def test_module_yield_first(module_yield):
    """First test using module-scoped yield fixture."""
    assert module_yield == "module_yield_value"


def test_module_yield_second(module_yield):
    """Second test using same module-scoped yield fixture."""
    assert module_yield == "module_yield_value"


def test_multiple_yields(simple_yield, yield_with_cleanup, module_yield):
    """Test using multiple yield fixtures."""
    assert simple_yield == "simple_value"
    assert yield_with_cleanup["status"] == "open"
    assert module_yield == "module_yield_value"
