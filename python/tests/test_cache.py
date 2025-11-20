"""Tests for cache fixture."""

from __future__ import annotations

from pathlib import Path
import tempfile
import shutil
import pytest

from rustest.builtin_fixtures import Cache


@pytest.fixture
def test_cache():
    """Provide a rustest Cache instance for testing."""
    cache_dir = Path(tempfile.mkdtemp()) / ".test_cache"
    try:
        yield Cache(cache_dir)
    finally:
        if cache_dir.exists():
            shutil.rmtree(cache_dir.parent)


def test_cache_get_set(test_cache):
    """Test basic get and set operations."""
    test_cache.set("test/key", "value")
    assert test_cache.get("test/key") == "value"


def test_cache_get_default(test_cache):
    """Test get with default value."""
    assert test_cache.get("nonexistent", "default") == "default"
    assert test_cache.get("nonexistent") is None


def test_cache_dict_access(test_cache):
    """Test dict-style access."""
    test_cache["test/key"] = "value"
    assert test_cache["test/key"] == "value"


def test_cache_contains(test_cache):
    """Test contains check."""
    test_cache.set("test/exists", True)
    assert "test/exists" in test_cache
    assert "test/notexists" not in test_cache


def test_cache_persistence():
    """Test that cache persists across instances."""
    # Create a temp directory for this test
    cache_dir = Path(tempfile.mkdtemp()) / ".test_cache"

    try:
        # Create first cache instance and set value
        cache1 = Cache(cache_dir)
        cache1.set("persist/test", "persisted_value")

        # Create second instance - should load the persisted value
        cache2 = Cache(cache_dir)
        assert cache2.get("persist/test") == "persisted_value"

    finally:
        # Cleanup
        if cache_dir.exists():
            shutil.rmtree(cache_dir.parent)


def test_cache_set_various_types(test_cache):
    """Test caching different data types."""
    test_cache.set("test/string", "hello")
    test_cache.set("test/int", 42)
    test_cache.set("test/float", 3.14)
    test_cache.set("test/bool", True)
    test_cache.set("test/list", [1, 2, 3])
    test_cache.set("test/dict", {"a": 1, "b": 2})
    test_cache.set("test/none", None)

    assert test_cache.get("test/string") == "hello"
    assert test_cache.get("test/int") == 42
    assert test_cache.get("test/float") == 3.14
    assert test_cache.get("test/bool") is True
    assert test_cache.get("test/list") == [1, 2, 3]
    assert test_cache.get("test/dict") == {"a": 1, "b": 2}
    assert test_cache.get("test/none") is None


def test_cache_overwrite(test_cache):
    """Test overwriting existing values."""
    test_cache.set("test/key", "value1")
    assert test_cache.get("test/key") == "value1"

    test_cache.set("test/key", "value2")
    assert test_cache.get("test/key") == "value2"


def test_cache_key_format(test_cache):
    """Test different key formats."""
    test_cache.set("simple", "value1")
    test_cache.set("with/slash", "value2")
    test_cache.set("multiple/levels/deep", "value3")

    assert test_cache.get("simple") == "value1"
    assert test_cache.get("with/slash") == "value2"
    assert test_cache.get("multiple/levels/deep") == "value3"


def test_cache_mkdir(test_cache):
    """Test creating directories in cache."""
    dir_path = test_cache.mkdir("testdir")
    assert dir_path.exists()
    assert dir_path.is_dir()
    assert dir_path.parent == test_cache._cache_dir


def test_cache_mkdir_nested(test_cache):
    """Test creating nested directories."""
    dir_path = test_cache.mkdir("nested/deep/directory")
    assert dir_path.exists()
    assert dir_path.is_dir()


def test_cache_mkdir_idempotent(test_cache):
    """Test that mkdir can be called multiple times."""
    dir1 = test_cache.mkdir("samedir")
    dir2 = test_cache.mkdir("samedir")
    assert dir1 == dir2
    assert dir1.exists()


def test_cache_session_scope(test_cache):
    """Test that cache works across operations."""
    test_cache.set("session/test", "value")
    assert test_cache.get("session/test") == "value"


def test_cache_with_complex_data(test_cache):
    """Test caching complex nested structures."""
    complex_data = {
        "users": [
            {"id": 1, "name": "Alice", "tags": ["admin", "user"]},
            {"id": 2, "name": "Bob", "tags": ["user"]},
        ],
        "settings": {"theme": "dark", "notifications": True},
        "version": "1.0.0",
    }

    test_cache.set("app/data", complex_data)
    retrieved = test_cache.get("app/data")

    assert retrieved == complex_data
    assert retrieved["users"][0]["name"] == "Alice"
    assert retrieved["settings"]["theme"] == "dark"


def test_cache_update_workflow(test_cache):
    """Test a typical cache update workflow."""
    # First run - compute and cache
    if "compute/result" not in test_cache:
        result = 42  # Expensive computation
        test_cache.set("compute/result", result)

    # Second run - use cached value
    assert test_cache.get("compute/result") == 42


def test_cache_version_tracking(test_cache):
    """Test using cache for version tracking."""
    current_version = "2.0.0"
    cached_version = test_cache.get("app/version", "1.0.0")

    if cached_version != current_version:
        test_cache.set("app/version", current_version)

    assert test_cache.get("app/version") >= "1.0.0"


def test_cache_counter(test_cache):
    """Test using cache as a counter."""
    count = test_cache.get("test/counter", 0)
    count += 1
    test_cache.set("test/counter", count)

    assert test_cache.get("test/counter") == 1

    # Increment again
    count = test_cache.get("test/counter", 0)
    count += 1
    test_cache.set("test/counter", count)

    assert test_cache.get("test/counter") == 2


def test_cache_corrupted_file_recovery():
    """Test that cache recovers from corrupted cache file."""
    cache_dir = Path(tempfile.mkdtemp()) / ".test_cache"

    try:
        # Create cache and set value
        cache1 = Cache(cache_dir)
        cache1.set("test/key", "value")

        # Corrupt the cache file
        cache_file = cache_dir / "cache.json"
        with open(cache_file, "w") as f:
            f.write("{ invalid json content")

        # Create new cache - should recover with empty cache
        cache2 = Cache(cache_dir)
        assert cache2.get("test/key") is None  # Data lost, but no crash

        # Should be able to set new values
        cache2.set("test/new", "works")
        assert cache2.get("test/new") == "works"

    finally:
        if cache_dir.exists():
            shutil.rmtree(cache_dir.parent)


def test_cache_empty_key(test_cache):
    """Test cache with empty string key."""
    test_cache.set("", "empty_key_value")
    assert test_cache.get("") == "empty_key_value"


def test_cache_special_characters(test_cache):
    """Test cache keys with special characters."""
    # Note: Keys should generally use alphanumeric and /
    # but the cache should handle other characters
    test_cache.set("test-key", "value1")
    test_cache.set("test_key", "value2")
    test_cache.set("test.key", "value3")

    assert test_cache.get("test-key") == "value1"
    assert test_cache.get("test_key") == "value2"
    assert test_cache.get("test.key") == "value3"


def test_cache_large_value(test_cache):
    """Test caching large values."""
    large_list = list(range(10000))
    test_cache.set("test/large", large_list)
    assert test_cache.get("test/large") == large_list
    assert len(test_cache.get("test/large")) == 10000


def test_cache_multiple_keys(test_cache):
    """Test multiple independent cache keys."""
    keys_values = {
        "app/version": "1.0.0",
        "app/config/theme": "dark",
        "app/config/language": "en",
        "test/results/last_run": {"passed": 42, "failed": 3},
        "test/results/previous_run": {"passed": 40, "failed": 5},
    }

    for key, value in keys_values.items():
        test_cache.set(key, value)

    for key, expected_value in keys_values.items():
        assert test_cache.get(key) == expected_value
