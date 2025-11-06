"""Test basic conftest.py fixture usage."""


def test_uses_conftest_fixture(basic_fixture):
    """Test that uses a fixture from conftest.py."""
    assert basic_fixture == "from_conftest"


def test_uses_conftest_value(conftest_value):
    """Test that uses another conftest fixture."""
    assert conftest_value == 42


def test_uses_conftest_dependency(conftest_with_dependency):
    """Test that uses a conftest fixture with dependencies."""
    assert conftest_with_dependency == "depends_on_from_conftest"


def test_uses_module_scoped(module_scoped_conftest):
    """Test that uses a module-scoped conftest fixture."""
    assert module_scoped_conftest == "module_conftest"


def test_uses_yield_fixture(conftest_yield):
    """Test that uses a yield fixture from conftest."""
    assert conftest_yield == "setup"
