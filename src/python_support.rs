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
use toml::Value;

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
pub(crate) fn find_basedir(path: &Path) -> PathBuf {
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
            return current
                .parent()
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
pub(crate) fn find_src_directory(base_path: &Path) -> Option<PathBuf> {
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

/// Find the project root by looking for a pyproject.toml file.
///
/// Walks up the directory tree from the given path until it finds a pyproject.toml file.
/// Returns the directory containing the pyproject.toml file.
pub(crate) fn find_project_root(path: &Path) -> Option<PathBuf> {
    let mut current = if path.is_file() {
        path.parent().unwrap_or(path)
    } else {
        path
    };

    loop {
        let pyproject_toml = current.join("pyproject.toml");
        if pyproject_toml.is_file() {
            return Some(current.to_path_buf());
        }

        match current.parent() {
            Some(parent) => current = parent,
            None => return None,
        }
    }
}

/// Read and parse pythonpath configuration from pyproject.toml.
///
/// Looks for `tool.pytest.ini_options.pythonpath` in the pyproject.toml file
/// and returns the list of paths configured there. These paths are relative to
/// the project root (the directory containing pyproject.toml).
pub(crate) fn read_pythonpath_from_pyproject(project_root: &Path) -> Option<Vec<PathBuf>> {
    let pyproject_path = project_root.join("pyproject.toml");

    // Read the file
    let contents = std::fs::read_to_string(&pyproject_path).ok()?;

    // Parse the TOML
    let config: Value = contents.parse().ok()?;

    // Navigate to tool.pytest.ini_options.pythonpath
    let pythonpath = config
        .get("tool")?
        .get("pytest")?
        .get("ini_options")?
        .get("pythonpath")?
        .as_array()?;

    // Convert the array of strings to PathBufs relative to the project root
    let paths: Vec<PathBuf> = pythonpath
        .iter()
        .filter_map(|v| v.as_str())
        .map(|s| project_root.join(s))
        .collect();

    if paths.is_empty() {
        None
    } else {
        Some(paths)
    }
}

/// Setup sys.path to enable imports, mimicking pytest's behavior.
///
/// This function automatically configures Python's module search path (`sys.path`)
/// to make your project's code importable from tests, without requiring manual
/// PYTHONPATH configuration.
///
/// ## How it works
///
/// For each test path provided:
///
/// 1. **Read pyproject.toml configuration**: Looks for `tool.pytest.ini_options.pythonpath`
///    in the project's pyproject.toml file. If found, those paths are added to `sys.path`.
///    This matches pytest's behavior exactly.
///
/// 2. **Find the project root**: Walks up the directory tree from your test file/directory
///    until it finds a directory without `__init__.py`. The parent of that directory is
///    considered the project root and added to `sys.path`.
///
/// 3. **Detect src-layout**: Checks if a `src/` directory exists at the project root
///    or any parent level. If found, it's also added to `sys.path`.
///
/// 4. **Prepend to sys.path**: Paths are inserted at the beginning of `sys.path`
///    (like pytest's prepend mode) so your project code takes precedence.
///
/// 5. **Avoid duplicates**: Checks if paths already exist in `sys.path` before adding.
///
/// ## Supported Project Layouts
///
/// **With pyproject.toml configuration** (recommended):
/// ```text
/// myproject/
///   pyproject.toml  # Contains: pythonpath = ["src"]
///   src/
///     mypackage/
///       __init__.py
///   tests/
///     test_something.py
/// ```
/// → Reads from pyproject.toml and adds `myproject/src/` to sys.path
///
/// **Src Layout** (auto-detected):
/// ```text
/// myproject/
///   src/
///     mypackage/
///       __init__.py
///   tests/
///     test_something.py
/// ```
/// → Adds `myproject/` and `myproject/src/` to sys.path
///
/// **Flat Layout**:
/// ```text
/// myproject/
///   mypackage/
///     __init__.py
///   tests/
///     test_something.py
/// ```
/// → Adds `myproject/` to sys.path
///
/// ## Example
///
/// With this automatic setup, your tests can simply:
/// ```python
/// from mypackage import some_function  # Just works!
/// ```
///
/// Instead of requiring:
/// ```bash
/// PYTHONPATH=src rustest tests/  # Not needed anymore!
/// ```
pub fn setup_python_path(py: Python<'_>, paths: &[PathBuf]) -> PyResult<()> {
    let sys = py.import("sys")?;
    let sys_path: Bound<'_, PyList> = sys.getattr("path")?.extract()?;

    // Track which paths we've already added to avoid duplicates
    let mut paths_to_add: HashSet<PathBuf> = HashSet::new();

    // First, check for pyproject.toml and read pythonpath configuration
    // Look for pyproject.toml in the first test path
    if let Some(first_path) = paths.first() {
        if let Some(project_root) = find_project_root(first_path) {
            // Always add the project root itself so top-level packages like `tests`
            // become importable. This mirrors pytest's behaviour where the
            // directory containing the configuration file is placed on
            // ``sys.path``. Without this, projects that rely on importing the
            // ``tests`` package (or other top-level modules) would fail when
            // intermediate directories lack ``__init__.py`` files.
            paths_to_add.insert(project_root.clone());

            // Read pythonpath from pyproject.toml if it exists
            if let Some(configured_paths) = read_pythonpath_from_pyproject(&project_root) {
                for path in configured_paths {
                    if path.is_dir() {
                        paths_to_add.insert(path);
                    }
                }
            }
        }
    }

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
        let already_exists = sys_path.iter().any(|item| {
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
