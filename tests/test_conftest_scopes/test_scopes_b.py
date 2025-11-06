"""Second test module to verify cross-module scope behavior."""


def test_session_shared(scopes_session_fixture):
    """Session fixture should be the same across modules."""
    assert scopes_session_fixture == "session_value"


def test_module_fixture(module_fixture):
    """Module fixture should work."""
    assert module_fixture == "module_value"


def test_module_reused_in_same(module_fixture):
    """Module fixture should be reused within this module."""
    assert module_fixture == "module_value"


def test_all_scopes(function_fixture, module_fixture, scopes_session_fixture):
    """Test with all scope levels."""
    assert function_fixture == "function_value"
    assert module_fixture == "module_value"
    assert scopes_session_fixture == "session_value"


def test_class_fixture(class_fixture):
    """Test class-scoped fixture from conftest."""
    assert class_fixture == "class_value"
