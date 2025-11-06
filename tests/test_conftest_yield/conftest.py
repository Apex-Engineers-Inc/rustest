"""Conftest with yield fixtures for setup/teardown testing."""
import rustest as testlib

# Global list to track setup/teardown order
_lifecycle = []


@testlib.fixture
def simple_yield():
    """Simple yield fixture."""
    _lifecycle.append("simple_setup")
    yield "simple_value"
    _lifecycle.append("simple_teardown")


@testlib.fixture
def yield_with_cleanup():
    """Yield fixture with cleanup."""
    resource = {"status": "open"}
    _lifecycle.append("resource_setup")
    yield resource
    resource["status"] = "closed"
    _lifecycle.append("resource_teardown")


@testlib.fixture
def dependent_yield(simple_yield):
    """Yield fixture that depends on another yield fixture."""
    _lifecycle.append("dependent_setup")
    yield f"uses_{simple_yield}"
    _lifecycle.append("dependent_teardown")


@testlib.fixture(scope="module")
def module_yield():
    """Module-scoped yield fixture."""
    _lifecycle.append("module_yield_setup")
    yield "module_yield_value"
    _lifecycle.append("module_yield_teardown")


@testlib.fixture
def get_lifecycle():
    """Helper to access lifecycle for verification."""
    return _lifecycle.copy()


@testlib.fixture
def clear_lifecycle():
    """Helper to clear lifecycle before test."""
    _lifecycle.clear()
    yield
    # Don't clear on teardown so test can verify
