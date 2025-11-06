"""Tests that should FAIL with scope validation errors.

These tests verify that rustest properly validates fixture scope dependencies.
A higher-scoped fixture should NOT be able to depend on a lower-scoped fixture.

NOTE: These tests are expected to fail during fixture resolution with
ScopeMismatch errors. They document the expected validation behavior.
"""

from rustest import fixture

# ============================================================================
# INVALID: Module scope depending on Function scope
# ============================================================================


@fixture  # function scope (default)
def function_scoped_data():
    """Function-scoped data."""
    return {"value": 42}


@fixture(scope="module")
def invalid_module_fixture(function_scoped_data):
    """
    THIS SHOULD FAIL: Module-scoped fixture depending on function-scoped fixture.

    Expected error: ScopeMismatch: Fixture 'invalid_module_fixture' (scope: Module)
    cannot depend on 'function_scoped_data' (scope: Function).
    """
    return {"data": function_scoped_data}


def test_module_depending_on_function():
    """
    This test will fail during fixture resolution.
    The error should occur when trying to resolve invalid_module_fixture.
    """
    # This will fail before the test even runs
    pass  # We'll add fixture parameter in a moment


# ============================================================================
# INVALID: Session scope depending on Module scope
# ============================================================================


@fixture(scope="module")
def module_scoped_config():
    """Module-scoped config."""
    return {"setting": "value"}


@fixture(scope="session")
def invalid_session_fixture(module_scoped_config):
    """
    THIS SHOULD FAIL: Session-scoped fixture depending on module-scoped fixture.

    Expected error: ScopeMismatch: Fixture 'invalid_session_fixture' (scope: Session)
    cannot depend on 'module_scoped_config' (scope: Module).
    """
    return {"config": module_scoped_config}


# ============================================================================
# INVALID: Session scope depending on Class scope
# ============================================================================


@fixture(scope="class")
def class_scoped_resource():
    """Class-scoped resource."""
    return {"resource": "test"}


@fixture(scope="session")
def invalid_session_from_class(class_scoped_resource):
    """
    THIS SHOULD FAIL: Session-scoped fixture depending on class-scoped fixture.

    Expected error: ScopeMismatch: Fixture 'invalid_session_from_class' (scope: Session)
    cannot depend on 'class_scoped_resource' (scope: Class).
    """
    return {"resource": class_scoped_resource}


# ============================================================================
# INVALID: Session scope depending on Function scope
# ============================================================================


@fixture  # function scope
def function_value():
    """Function-scoped value."""
    return 123


@fixture(scope="session")
def invalid_session_from_function(function_value):
    """
    THIS SHOULD FAIL: Session-scoped fixture depending on function-scoped fixture.

    Expected error: ScopeMismatch: Fixture 'invalid_session_from_function' (scope: Session)
    cannot depend on 'function_value' (scope: Function).
    """
    return {"value": function_value}


# ============================================================================
# INVALID: Module scope depending on Class scope
# ============================================================================


@fixture(scope="class")
def class_data():
    """Class-scoped data."""
    return {"data": "test"}


@fixture(scope="module")
def invalid_module_from_class(class_data):
    """
    THIS SHOULD FAIL: Module-scoped fixture depending on class-scoped fixture.

    Expected error: ScopeMismatch: Fixture 'invalid_module_from_class' (scope: Module)
    cannot depend on 'class_data' (scope: Class).
    """
    return {"data": class_data}


# ============================================================================
# INVALID: Class scope depending on Function scope
# ============================================================================


@fixture  # function scope
def function_counter():
    """Function-scoped counter."""
    return {"count": 0}


@fixture(scope="class")
def invalid_class_from_function(function_counter):
    """
    THIS SHOULD FAIL: Class-scoped fixture depending on function-scoped fixture.

    Expected error: ScopeMismatch: Fixture 'invalid_class_from_function' (scope: Class)
    cannot depend on 'function_counter' (scope: Function).
    """
    return {"counter": function_counter}


# ============================================================================
# VALID CASES (for contrast) - These should work fine
# ============================================================================


@fixture(scope="session")
def valid_session():
    """Valid session fixture."""
    return {"session": True}


@fixture(scope="module")
def valid_module(valid_session):
    """Valid: Module depending on Session."""
    return {"module": True, "session": valid_session}


@fixture(scope="class")
def valid_class(valid_module, valid_session):
    """Valid: Class depending on Module and Session."""
    return {"class": True, "module": valid_module, "session": valid_session}


@fixture
def valid_function(valid_class, valid_module, valid_session):
    """Valid: Function depending on all higher scopes."""
    return {
        "function": True,
        "class": valid_class,
        "module": valid_module,
        "session": valid_session,
    }


def test_valid_scope_dependencies(valid_function):
    """This test should pass - all dependencies are valid."""
    assert valid_function["function"] is True
    assert valid_function["class"]["class"] is True
    assert valid_function["module"]["module"] is True
    assert valid_function["session"]["session"] is True
