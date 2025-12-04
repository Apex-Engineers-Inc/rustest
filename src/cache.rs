use pyo3::PyResult;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

const CACHE_DIR: &str = ".rustest_cache";
const LAST_FAILED_FILE: &str = "lastfailed";
const COLLECTION_CACHE_FILE: &str = "collection";

/// Cached metadata for a single test function.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CachedTestInfo {
    pub name: String,
    pub display_name: String,
    pub parameters: Vec<String>,
    pub skip_reason: Option<String>,
    pub mark_names: Vec<String>,
    pub class_name: Option<String>,
    /// Parametrization cases: (id, param_names)
    pub parametrization: Vec<CachedParameterCase>,
    /// Names of parameters that are indirect (fixture references)
    pub indirect_params: Vec<String>,
}

/// A single parametrization case.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CachedParameterCase {
    pub id: String,
    pub param_names: Vec<String>,
}

/// Cached metadata for a single fixture.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CachedFixtureInfo {
    pub name: String,
    pub parameters: Vec<String>,
    pub scope: String,
    pub is_generator: bool,
    pub is_async: bool,
    pub is_async_generator: bool,
    pub autouse: bool,
    pub class_name: Option<String>,
    /// Parametrization IDs if the fixture is parametrized.
    pub param_ids: Vec<String>,
}

/// Cached metadata for a Python module (test file).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CachedModuleInfo {
    /// File modification time as seconds since epoch.
    pub mtime_secs: u64,
    /// File size in bytes.
    pub size: u64,
    /// Tests defined in this module.
    pub tests: Vec<CachedTestInfo>,
    /// Fixtures defined in this module.
    pub fixtures: Vec<CachedFixtureInfo>,
}

/// The complete collection cache.
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct CollectionCache {
    /// Version for cache invalidation on format changes.
    pub version: u32,
    /// Map from canonical file path to cached module info.
    pub modules: HashMap<String, CachedModuleInfo>,
}

impl CollectionCache {
    /// Current cache format version. Bump this when the cache format changes.
    pub const CURRENT_VERSION: u32 = 1;

    /// Create a new empty cache.
    pub fn new() -> Self {
        Self {
            version: Self::CURRENT_VERSION,
            modules: HashMap::new(),
        }
    }

    /// Check if a file's cache entry is still valid.
    pub fn is_valid(&self, path: &Path) -> bool {
        let key = path.to_string_lossy().to_string();
        if let Some(cached) = self.modules.get(&key) {
            if let Ok(metadata) = fs::metadata(path) {
                if let Ok(mtime) = metadata.modified() {
                    if let Ok(duration) = mtime.duration_since(SystemTime::UNIX_EPOCH) {
                        let current_mtime = duration.as_secs();
                        let current_size = metadata.len();
                        return cached.mtime_secs == current_mtime && cached.size == current_size;
                    }
                }
            }
        }
        false
    }

    /// Get cached module info if valid.
    pub fn get(&self, path: &Path) -> Option<&CachedModuleInfo> {
        if self.is_valid(path) {
            let key = path.to_string_lossy().to_string();
            self.modules.get(&key)
        } else {
            None
        }
    }

    /// Insert or update a module's cached info.
    pub fn insert(&mut self, path: &Path, info: CachedModuleInfo) {
        let key = path.to_string_lossy().to_string();
        self.modules.insert(key, info);
    }

    /// Remove stale entries (files that no longer exist).
    pub fn cleanup(&mut self) {
        self.modules.retain(|path, _| Path::new(path).exists());
    }
}

/// Get the path to the collection cache file.
fn get_collection_cache_path() -> PathBuf {
    get_cache_dir().join(COLLECTION_CACHE_FILE)
}

/// Read the collection cache from disk.
pub fn read_collection_cache() -> CollectionCache {
    let cache_path = get_collection_cache_path();

    if !cache_path.exists() {
        return CollectionCache::new();
    }

    let content = match fs::read_to_string(&cache_path) {
        Ok(c) => c,
        Err(_) => return CollectionCache::new(),
    };

    if content.trim().is_empty() {
        return CollectionCache::new();
    }

    match serde_json::from_str::<CollectionCache>(&content) {
        Ok(cache) if cache.version == CollectionCache::CURRENT_VERSION => cache,
        _ => CollectionCache::new(), // Version mismatch or parse error
    }
}

/// Write the collection cache to disk.
pub fn write_collection_cache(cache: &CollectionCache) -> PyResult<()> {
    ensure_cache_dir().map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to create cache directory: {}", e))
    })?;

    let content = serde_json::to_string(cache).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!(
            "Failed to serialize collection cache: {}",
            e
        ))
    })?;

    fs::write(get_collection_cache_path(), content).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to write collection cache: {}", e))
    })?;

    Ok(())
}

/// Clear the collection cache.
#[allow(dead_code)]
pub fn clear_collection_cache() -> PyResult<()> {
    let cache_path = get_collection_cache_path();

    if cache_path.exists() {
        fs::remove_file(&cache_path).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Failed to clear collection cache: {}", e))
        })?;
    }

    Ok(())
}

/// Create CachedModuleInfo for a file.
pub fn create_cached_module_info(path: &Path) -> Option<CachedModuleInfo> {
    let metadata = fs::metadata(path).ok()?;
    let mtime = metadata.modified().ok()?;
    let duration = mtime.duration_since(SystemTime::UNIX_EPOCH).ok()?;

    Some(CachedModuleInfo {
        mtime_secs: duration.as_secs(),
        size: metadata.len(),
        tests: Vec::new(),
        fixtures: Vec::new(),
    })
}

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
