"""
Tests demonstrating rustest_fixtures usage.

This test file uses fixtures loaded via the rustest_fixtures field
in conftest.py, which is the preferred rustest-native approach.
"""

import pytest


def test_rustest_native_fixture(rustest_native_fixture):
    """Test using fixture loaded via rustest_fixtures."""
    assert rustest_native_fixture == "rustest_native_value"


def test_database_fixture(database_fixture):
    """Test using database fixture from external module."""
    assert database_fixture["connected"] is True
    assert database_fixture["name"] == "test_db"


def test_shared_resource(shared_resource):
    """Test using module-scoped fixture from external module."""
    assert shared_resource == "shared_value"


def test_local_fixture(local_fixture):
    """Test using fixture from conftest.py."""
    assert local_fixture == "local_value"


def test_multiple_fixtures(rustest_native_fixture, database_fixture, local_fixture):
    """Test using multiple fixtures from different sources."""
    assert rustest_native_fixture == "rustest_native_value"
    assert database_fixture["connected"] is True
    assert local_fixture == "local_value"
