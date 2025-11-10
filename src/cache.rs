use pyo3::PyResult;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;

const CACHE_DIR: &str = ".rustest_cache";
const LAST_FAILED_FILE: &str = "lastfailed";

#[derive(Debug, Serialize, Deserialize)]
struct LastFailedCache {
    failed: HashSet<String>,
}

/// Get the path to the cache directory
fn get_cache_dir() -> PathBuf {
    PathBuf::from(CACHE_DIR)
}

/// Get the path to the last failed cache file
fn get_last_failed_path() -> PathBuf {
    get_cache_dir().join(LAST_FAILED_FILE)
}

/// Ensure the cache directory exists
fn ensure_cache_dir() -> std::io::Result<()> {
    let cache_dir = get_cache_dir();
    if !cache_dir.exists() {
        fs::create_dir_all(&cache_dir)?;
    }
    Ok(())
}

/// Read the last failed tests from cache
/// Returns a set of test IDs that failed in the last run
pub fn read_last_failed() -> PyResult<HashSet<String>> {
    let cache_path = get_last_failed_path();

    if !cache_path.exists() {
        return Ok(HashSet::new());
    }

    let content = fs::read_to_string(&cache_path).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to read cache: {}", e))
    })?;

    if content.trim().is_empty() {
        return Ok(HashSet::new());
    }

    let cache: LastFailedCache = serde_json::from_str(&content).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("Failed to parse cache: {}", e))
    })?;

    Ok(cache.failed)
}

/// Write the failed tests to cache
/// Takes a set of test IDs that failed in this run
pub fn write_last_failed(failed_tests: &HashSet<String>) -> PyResult<()> {
    ensure_cache_dir().map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to create cache directory: {}", e))
    })?;

    let cache = LastFailedCache {
        failed: failed_tests.clone(),
    };

    let content = serde_json::to_string_pretty(&cache).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("Failed to serialize cache: {}", e))
    })?;

    fs::write(get_last_failed_path(), content).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to write cache: {}", e))
    })?;

    Ok(())
}

/// Clear the last failed cache
#[allow(dead_code)]
pub fn clear_last_failed() -> PyResult<()> {
    let cache_path = get_last_failed_path();

    if cache_path.exists() {
        fs::remove_file(&cache_path).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Failed to clear cache: {}", e))
        })?;
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cache_roundtrip() {
        let mut failed = HashSet::new();
        failed.insert("test_foo.py::test_bar".to_string());
        failed.insert("test_baz.py::test_qux[param1]".to_string());

        write_last_failed(&failed).unwrap();
        let read_failed = read_last_failed().unwrap();

        assert_eq!(failed, read_failed);
    }
}
