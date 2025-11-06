"""Benchmark: Tests using fixtures with different scopes."""

from rustest import fixture


# Function-scoped fixtures (default)
@fixture
def function_counter():
    """Function-scoped fixture - new instance for each test."""
    return {"count": 0, "calls": []}


@fixture(scope="function")
def function_resource():
    """Explicitly function-scoped fixture."""
    return {"id": id(object()), "data": [1, 2, 3]}


# Class-scoped fixtures
@fixture(scope="class")
def class_database():
    """Class-scoped fixture - shared across test class."""
    return {
        "connection": "db://test",
        "shared": True,
        "test_count": 0,
        "records": []
    }


@fixture(scope="class")
def class_config():
    """Class-scoped configuration."""
    return {
        "env": "test",
        "debug": True,
        "timeout": 30,
        "retries": 3
    }


# Module-scoped fixtures
@fixture(scope="module")
def module_cache():
    """Module-scoped fixture - shared across all tests in module."""
    return {
        "data": {},
        "hits": 0,
        "misses": 0,
        "size": 0
    }


@fixture(scope="module")
def module_api_client():
    """Module-scoped API client."""
    return {
        "base_url": "https://api.example.com",
        "token": "abc123",
        "requests": [],
        "initialized": True
    }


# Session-scoped fixtures
@fixture(scope="session")
def session_settings():
    """Session-scoped settings - shared across entire test session."""
    return {
        "app_name": "rustest",
        "version": "1.0.0",
        "global_counter": 0,
        "features": ["fixtures", "parametrize", "scopes"]
    }


@fixture(scope="session")
def session_metrics():
    """Session-scoped metrics collector."""
    return {
        "total_tests": 0,
        "total_time": 0.0,
        "test_names": []
    }


# Mixed scope fixtures - fixtures depending on different scopes
@fixture(scope="function")
def request_handler(module_cache, session_settings):
    """Function-scoped fixture depending on module and session fixtures."""
    return {
        "cache": module_cache,
        "settings": session_settings,
        "request_id": id(object())
    }


@fixture(scope="module")
def module_service(session_settings):
    """Module-scoped fixture depending on session fixture."""
    return {
        "settings": session_settings,
        "service_id": id(object()),
        "active": True
    }


# Tests using function-scoped fixtures
def test_function_scope_1(function_counter):
    assert function_counter["count"] == 0
    function_counter["count"] += 1


def test_function_scope_2(function_counter):
    # Should be a fresh instance
    assert function_counter["count"] == 0
    function_counter["count"] += 5


def test_function_scope_3(function_counter):
    # Should be a fresh instance
    assert function_counter["count"] == 0
    assert len(function_counter["calls"]) == 0


def test_function_resource_1(function_resource):
    assert len(function_resource["data"]) == 3
    function_resource["data"].append(4)


def test_function_resource_2(function_resource):
    # Should be a fresh instance
    assert len(function_resource["data"]) == 3


# Tests using class-scoped fixtures
def test_class_database_1(class_database):
    assert class_database["shared"] is True
    class_database["test_count"] += 1


def test_class_database_2(class_database):
    # Same instance within class
    class_database["test_count"] += 1
    class_database["records"].append("record1")


def test_class_database_3(class_database):
    # Same instance
    class_database["records"].append("record2")
    assert len(class_database["records"]) >= 1


def test_class_config_1(class_config):
    assert class_config["env"] == "test"
    assert class_config["debug"] is True


def test_class_config_2(class_config):
    assert class_config["timeout"] == 30
    class_config["retries"] = 5


def test_class_config_3(class_config):
    # Same instance, might have modified retries
    assert class_config["env"] == "test"


# Tests using module-scoped fixtures
def test_module_cache_1(module_cache):
    assert "data" in module_cache
    module_cache["hits"] += 1


def test_module_cache_2(module_cache):
    # Same instance across module
    module_cache["hits"] += 1
    module_cache["data"]["key1"] = "value1"


def test_module_cache_3(module_cache):
    # Same instance
    module_cache["misses"] += 1
    module_cache["size"] = len(module_cache["data"])


def test_module_api_client_1(module_api_client):
    assert module_api_client["initialized"] is True
    module_api_client["requests"].append("GET /users")


def test_module_api_client_2(module_api_client):
    # Same instance
    module_api_client["requests"].append("POST /users")
    assert len(module_api_client["requests"]) >= 1


def test_module_api_client_3(module_api_client):
    # Same instance
    assert module_api_client["base_url"] == "https://api.example.com"
    assert len(module_api_client["requests"]) >= 2


# Tests using session-scoped fixtures
def test_session_settings_1(session_settings):
    assert session_settings["app_name"] == "rustest"
    session_settings["global_counter"] += 1


def test_session_settings_2(session_settings):
    # Same instance across entire session
    session_settings["global_counter"] += 1
    assert "fixtures" in session_settings["features"]


def test_session_settings_3(session_settings):
    # Same instance
    assert session_settings["version"] == "1.0.0"
    assert session_settings["global_counter"] >= 2


def test_session_metrics_1(session_metrics):
    session_metrics["total_tests"] += 1
    session_metrics["test_names"].append("test_1")


def test_session_metrics_2(session_metrics):
    # Same instance
    session_metrics["total_tests"] += 1
    session_metrics["test_names"].append("test_2")


def test_session_metrics_3(session_metrics):
    # Same instance
    session_metrics["total_tests"] += 1
    assert len(session_metrics["test_names"]) >= 3


# Tests using mixed scope fixtures
def test_mixed_scopes_1(request_handler):
    assert request_handler["cache"] is not None
    assert request_handler["settings"] is not None
    assert request_handler["request_id"] > 0


def test_mixed_scopes_2(request_handler, module_cache, session_settings):
    # request_handler is function-scoped but has references to module/session fixtures
    assert request_handler["cache"] is module_cache
    assert request_handler["settings"] is session_settings


def test_mixed_scopes_3(module_service, session_settings):
    assert module_service["settings"] is session_settings
    assert module_service["active"] is True


# Tests combining multiple scope levels
def test_all_scopes_1(function_counter, module_cache, session_settings):
    assert function_counter["count"] == 0
    assert "data" in module_cache
    assert session_settings["app_name"] == "rustest"


def test_all_scopes_2(function_resource, class_config, module_api_client, session_metrics):
    assert len(function_resource["data"]) == 3
    assert class_config["env"] == "test"
    assert module_api_client["initialized"] is True
    assert "test_names" in session_metrics


def test_all_scopes_3(request_handler, class_database):
    assert request_handler["request_id"] > 0
    assert class_database["shared"] is True
