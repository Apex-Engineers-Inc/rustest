//! Helper utilities for bridging between Rust and the embedded Python runtime.
//!
//! The helpers in this module intentionally stay small and well commented so
//! that readers who are new to Rust can focus on the semantics instead of the
//! syntax.  They encapsulate the repetitive glue code that comes with
//! orchestrating Python objects from Rust.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use pyo3::prelude::*;
use pyo3::types::PyList;

/// Simple wrapper holding the user supplied paths.
///
/// Paths are normalised lazily; discovery operates on the canonicalised
/// [`PathBuf`] values to keep IO fallible in a controlled place.
#[derive(Debug, Clone)]
pub struct PyPaths {
    raw: Vec<String>,
}

impl PyPaths {
    /// Construct from a list of raw string paths coming from Python.
    pub fn from_vec(raw: Vec<String>) -> Self {
        Self { raw }
    }

    /// Convert the raw strings into canonicalised [`PathBuf`] values.
    pub fn materialise(&self) -> PyResult<Vec<PathBuf>> {
        self.raw
            .iter()
            .map(|value| {
                let path = Path::new(value);
                if path.exists() {
                    Ok(path.canonicalize()?)
                } else {
                    Err(pyo3::exceptions::PyFileNotFoundError::new_err(format!(
                        "Path '{}' does not exist",
                        value
                    )))
                }
            })
            .collect()
    }
}

/// Find the base directory for a test path, similar to pytest's behavior.
///
/// Walks up the directory tree from the given path until it finds the first
/// directory that does NOT contain an `__init__.py` file. Returns that directory's
/// parent (the project root) to make imports work for packages at the project level.
fn find_basedir(path: &Path) -> PathBuf {
    let mut current = if path.is_file() {
        path.parent().unwrap_or(path)
    } else {
        path
    };

    // Walk up until we find a directory without __init__.py
    loop {
        let init_py = current.join("__init__.py");
        if !init_py.exists() {
            // If the test directory doesn't have __init__.py, use its parent
            // as the basedir (the project root). This allows imports of packages
            // that are siblings to the test directory.
            return current.parent()
                .map(|p| p.to_path_buf())
                .unwrap_or_else(|| current.to_path_buf());
        }

        match current.parent() {
            Some(parent) => current = parent,
            None => return current.to_path_buf(),
        }
    }
}

/// Check if a `src/` directory exists in the given path or any of its parents.
///
/// This handles the common "src layout" where the package code lives in a `src/`
/// directory at the project root.
fn find_src_directory(base_path: &Path) -> Option<PathBuf> {
    let mut current = base_path;

    loop {
        let src_dir = current.join("src");
        if src_dir.is_dir() {
            return Some(src_dir);
        }

        match current.parent() {
            Some(parent) => current = parent,
            None => return None,
        }
    }
}

/// Setup sys.path to enable imports, mimicking pytest's behavior.
///
/// For each test path:
/// 1. Finds the "basedir" (first parent directory without __init__.py)
/// 2. Adds the basedir to sys.path if not already present
/// 3. Checks for a `src/` directory and adds it if found
///
/// This allows test code to import project modules without manually setting PYTHONPATH.
pub fn setup_python_path(py: Python<'_>, paths: &[PathBuf]) -> PyResult<()> {
    let sys = py.import("sys")?;
    let sys_path: Bound<'_, PyList> = sys.getattr("path")?.extract()?;

    // Track which paths we've already added to avoid duplicates
    let mut paths_to_add: HashSet<PathBuf> = HashSet::new();

    // Find basedirs and src directories for all test paths
    for path in paths {
        let basedir = find_basedir(path);
        paths_to_add.insert(basedir.clone());

        // Also check for src/ directory
        if let Some(src_dir) = find_src_directory(&basedir) {
            paths_to_add.insert(src_dir);
        }
    }

    // Add paths to sys.path if not already present
    for path in paths_to_add {
        let path_str = path.to_string_lossy();
        let path_str = path_str.as_ref();

        // Check if already in sys.path
        let already_exists = sys_path
            .iter()
            .any(|item| {
                item.extract::<String>()
                    .map(|s| s == path_str)
                    .unwrap_or(false)
            });

        if !already_exists {
            // Insert at the beginning like pytest does (prepend mode)
            sys_path.insert(0, path_str)?;
        }
    }

    Ok(())
}
