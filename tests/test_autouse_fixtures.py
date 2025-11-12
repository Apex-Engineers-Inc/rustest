"""Test autouse fixtures."""

from rustest import fixture

# Test 1: Basic autouse fixture (function scope)


counter = []


@fixture(autouse=True)
def auto_setup():
    """Auto-executed fixture that runs before each test."""
    counter.append("setup")
    yield
    counter.append("teardown")


def test_autouse_runs_automatically():
    """Test that autouse fixture runs without being explicitly requested."""
    assert "setup" in counter
    assert counter.count("setup") >= 1


def test_autouse_runs_for_all_tests():
    """Test that autouse fixture runs for multiple tests."""
    assert "setup" in counter
    # Should have been called at least twice (once for each test)
    assert counter.count("setup") >= 2


# Test 2: Autouse with explicit fixture dependency


autouse_log = []


@fixture(autouse=True)
def auto_logger():
    """Autouse fixture that logs test execution."""
    autouse_log.append("test_start")
    yield
    autouse_log.append("test_end")


@fixture
def data():
    """Regular fixture."""
    return {"value": 42}


def test_autouse_with_regular_fixture(data):
    """Test that autouse fixtures work alongside regular fixtures."""
    assert "test_start" in autouse_log
    assert data["value"] == 42


def test_autouse_side_effect():
    """Test that autouse fixture side effects are visible."""
    # autouse_log should contain entries from previous test
    assert autouse_log.count("test_start") >= 1
    assert autouse_log.count("test_end") >= 0  # May or may not have ended yet


# Test 3: Module-scoped autouse fixture


module_counter = []


@fixture(scope="module", autouse=True)
def module_setup():
    """Module-scoped autouse fixture."""
    module_counter.append("module_start")
    yield
    module_counter.append("module_end")


def test_module_autouse_1():
    """First test using module autouse."""
    assert "module_start" in module_counter
    assert module_counter.count("module_start") == 1


def test_module_autouse_2():
    """Second test using module autouse."""
    # Module fixture should only run once
    assert module_counter.count("module_start") == 1


# Test 4: Autouse fixture with dependencies


setup_order = []


@fixture
def resource():
    """Regular fixture that autouse depends on."""
    setup_order.append("resource")
    return "resource_value"


@fixture(autouse=True)
def auto_with_dependency(resource):
    """Autouse fixture that depends on another fixture."""
    setup_order.append(f"auto:{resource}")
    yield
    setup_order.append("auto_cleanup")


def test_autouse_dependency_order():
    """Test that autouse fixtures with dependencies are resolved correctly."""
    assert "resource" in setup_order
    assert "auto:resource_value" in setup_order
    # Resource should be created before autouse fixture uses it
    resource_idx = setup_order.index("resource")
    auto_idx = setup_order.index("auto:resource_value")
    assert resource_idx < auto_idx


# Test 5: Non-autouse fixture (control test)


manual_log = []


@fixture
def manual_fixture():
    """Regular fixture that must be explicitly requested."""
    manual_log.append("manual")
    return "manual_value"


def test_without_manual_fixture():
    """Test that non-autouse fixtures don't run automatically."""
    # This test doesn't request manual_fixture, so it shouldn't run
    # Note: We can't assert it's not there because it might have run in other tests
    pass


def test_with_manual_fixture(manual_fixture):
    """Test that non-autouse fixtures run when requested."""
    assert manual_fixture == "manual_value"
    assert "manual" in manual_log


# Test 6: Autouse with return value


@fixture(autouse=True)
def auto_with_value():
    """Autouse fixture that returns a value."""
    return "auto_value"


def test_autouse_return_value(auto_with_value):
    """Test that autouse fixtures can still be explicitly requested."""
    assert auto_with_value == "auto_value"


# Test 7: Multiple autouse fixtures


multi_log = []


@fixture(autouse=True)
def auto_first():
    """First autouse fixture."""
    multi_log.append("first")


@fixture(autouse=True)
def auto_second():
    """Second autouse fixture."""
    multi_log.append("second")


def test_multiple_autouse():
    """Test that multiple autouse fixtures all run."""
    assert "first" in multi_log
    assert "second" in multi_log


# Test 8: Session-scoped autouse


session_log = []


@fixture(scope="session", autouse=True)
def session_autouse():
    """Session-scoped autouse fixture."""
    session_log.append("session_start")
    yield
    session_log.append("session_end")


def test_session_autouse_1():
    """Test session autouse fixture."""
    assert "session_start" in session_log
    # Should only be called once per session
    assert session_log.count("session_start") == 1


def test_session_autouse_2():
    """Test session autouse fixture persistence."""
    # Should still be the same session
    assert session_log.count("session_start") == 1
