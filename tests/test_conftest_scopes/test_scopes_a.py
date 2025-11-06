"""First test module to verify conftest fixture scopes."""


def test_function_scope_1(function_fixture, scopes_session_fixture):
    """Each test should get a function-scoped fixture."""
    assert function_fixture == "function_value"
    assert scopes_session_fixture == "session_value"


def test_function_scope_2(function_fixture, scopes_session_fixture):
    """Second test in same module."""
    assert function_fixture == "function_value"
    assert scopes_session_fixture == "session_value"


def test_module_scope(module_fixture, scopes_session_fixture):
    """Module fixture should work."""
    assert module_fixture == "module_value"
    assert scopes_session_fixture == "session_value"


def test_module_scope_reused(module_fixture, scopes_session_fixture):
    """Module fixture should work in same module."""
    assert module_fixture == "module_value"
    assert scopes_session_fixture == "session_value"


def test_fixture_dependency(fixture_with_dep):
    """Test fixture that depends on another conftest fixture."""
    assert fixture_with_dep == "depends_on_session_value"
