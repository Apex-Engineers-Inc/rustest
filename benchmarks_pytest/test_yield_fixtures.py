"""Benchmark: Tests using yield fixtures with setup/teardown."""

import pytest


# Simple yield fixtures
@pytest.fixture
def resource_with_cleanup():
    """Fixture that yields and performs cleanup."""
    # Setup
    resource = {"connection": "opened", "data": [1, 2, 3]}
    yield resource
    # Teardown
    resource["connection"] = "closed"


@pytest.fixture
def database_connection():
    """Simulates a database connection with setup/teardown."""
    # Setup phase
    db = {
        "host": "localhost",
        "port": 5432,
        "connected": True,
        "transactions": []
    }
    yield db
    # Teardown phase
    db["connected"] = False
    db["transactions"].clear()


@pytest.fixture
def file_handler():
    """Simulates a file handler with cleanup."""
    # Setup
    handler = {
        "filename": "test.txt",
        "mode": "r",
        "is_open": True,
        "content": "Hello, World!"
    }
    yield handler
    # Cleanup
    handler["is_open"] = False


@pytest.fixture
def api_client():
    """Simulates an API client with initialization and cleanup."""
    # Setup
    client = {
        "base_url": "https://api.example.com",
        "authenticated": True,
        "requests_made": 0,
        "cache": {}
    }
    yield client
    # Cleanup
    client["authenticated"] = False
    client["cache"].clear()


@pytest.fixture
def temp_directory():
    """Simulates a temporary directory creation and cleanup."""
    # Setup
    temp_dir = {
        "path": "/tmp/test_dir",
        "exists": True,
        "files": ["file1.txt", "file2.txt", "file3.txt"]
    }
    yield temp_dir
    # Cleanup
    temp_dir["files"].clear()
    temp_dir["exists"] = False


# Nested yield fixtures
@pytest.fixture
def base_resource():
    """Base resource for nested fixtures."""
    resource = {"value": 100, "initialized": True}
    yield resource
    resource["initialized"] = False


@pytest.fixture
def derived_resource(base_resource):
    """Derived resource that depends on base_resource."""
    # Setup - uses base_resource
    derived = {
        "base": base_resource,
        "multiplier": 2,
        "computed": base_resource["value"] * 2
    }
    yield derived
    # Cleanup
    derived["computed"] = 0


# Tests using yield fixtures
def test_resource_cleanup_1(resource_with_cleanup):
    assert resource_with_cleanup["connection"] == "opened"
    assert len(resource_with_cleanup["data"]) == 3


def test_resource_cleanup_2(resource_with_cleanup):
    assert "connection" in resource_with_cleanup
    assert isinstance(resource_with_cleanup["data"], list)


def test_database_connection_1(database_connection):
    assert database_connection["connected"] is True
    assert database_connection["host"] == "localhost"


def test_database_connection_2(database_connection):
    assert database_connection["port"] == 5432
    assert isinstance(database_connection["transactions"], list)


def test_database_connection_3(database_connection):
    database_connection["transactions"].append("SELECT * FROM users")
    assert len(database_connection["transactions"]) == 1


def test_file_handler_1(file_handler):
    assert file_handler["is_open"] is True
    assert file_handler["filename"] == "test.txt"


def test_file_handler_2(file_handler):
    assert file_handler["mode"] == "r"
    assert len(file_handler["content"]) > 0


def test_file_handler_3(file_handler):
    content = file_handler["content"]
    assert content.startswith("Hello")


def test_api_client_1(api_client):
    assert api_client["authenticated"] is True
    assert api_client["requests_made"] == 0


def test_api_client_2(api_client):
    api_client["requests_made"] += 1
    assert api_client["requests_made"] == 1


def test_api_client_3(api_client):
    api_client["cache"]["key1"] = "value1"
    assert "key1" in api_client["cache"]


def test_temp_directory_1(temp_directory):
    assert temp_directory["exists"] is True
    assert len(temp_directory["files"]) == 3


def test_temp_directory_2(temp_directory):
    assert temp_directory["path"] == "/tmp/test_dir"
    assert "file1.txt" in temp_directory["files"]


def test_nested_yield_fixtures_1(base_resource):
    assert base_resource["initialized"] is True
    assert base_resource["value"] == 100


def test_nested_yield_fixtures_2(derived_resource):
    assert derived_resource["computed"] == 200
    assert derived_resource["multiplier"] == 2


def test_nested_yield_fixtures_3(derived_resource, base_resource):
    assert derived_resource["base"] is base_resource
    assert derived_resource["computed"] == base_resource["value"] * 2


# Complex scenarios with multiple yield fixtures
def test_multiple_yield_fixtures_1(database_connection, file_handler):
    assert database_connection["connected"] is True
    assert file_handler["is_open"] is True


def test_multiple_yield_fixtures_2(api_client, temp_directory):
    assert api_client["authenticated"] is True
    assert temp_directory["exists"] is True


def test_multiple_yield_fixtures_3(resource_with_cleanup, database_connection, file_handler):
    assert resource_with_cleanup["connection"] == "opened"
    assert database_connection["connected"] is True
    assert file_handler["is_open"] is True
