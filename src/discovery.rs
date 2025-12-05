//! Test discovery pipeline.
//!
//! This module walks the file system, loads Python modules, and extracts both
//! fixtures and test functions.  The code heavily documents the involved steps
//! because the interaction with Python's reflection facilities can otherwise be
//! tricky to follow.

use std::collections::{HashMap, HashSet};
use std::ffi::CString;
use std::path::{Path, PathBuf};

use globset::{Glob, GlobSet, GlobSetBuilder};
use indexmap::IndexMap;
use pyo3::prelude::*;
use pyo3::prelude::{PyAnyMethods, PyDictMethods};
use pyo3::types::{PyAny, PyDict, PyList, PySequence, PyTuple};
use pyo3::Bound;
use rayon::prelude::*;
use walkdir::WalkDir;

use crate::cache;
use crate::mark_expr::MarkExpr;
use crate::model::{
    invalid_test_definition, to_relative_path, CollectionError, Fixture, FixtureParam,
    FixtureScope, LastFailedMode, Mark, ModuleIdGenerator, ParameterMap, RunConfiguration,
    TestCase, TestModule,
};
use crate::output::{emit_collection_completed, emit_collection_progress, emit_collection_started};
use crate::python_support::{setup_python_path, PyPaths};

/// Inject the pytest compatibility shim into sys.modules.
///
/// This allows existing pytest test files to work with rustest without any code changes.
/// When tests do `import pytest`, they'll get our compatibility shim which maps pytest's
/// API to rustest's implementations.
///
/// Note: The compatibility mode banner is printed by Python (using rich) before tests run.
fn inject_pytest_compat_shim(py: Python<'_>) -> PyResult<()> {
    // Import our compatibility modules
    let compat_module = py.import("rustest.compat.pytest")?;
    let pytest_asyncio_module = py.import("rustest.compat.pytest_asyncio")?;

    // Inject them into sys.modules
    let sys = py.import("sys")?;
    let sys_modules: Bound<'_, PyDict> = sys.getattr("modules")?.cast_into()?;
    sys_modules.set_item("pytest", compat_module)?;
    sys_modules.set_item("pytest_asyncio", pytest_asyncio_module)?;

    Ok(())
}

/// Check if a directory is a virtual environment by detecting marker files.
///
/// This mimics pytest's `_in_venv()` function, which checks for:
/// - `pyvenv.cfg`: Standard Python virtual environments (PEP 405)
/// - `conda-meta/history`: Conda environments
fn is_virtualenv(path: &Path) -> bool {
    path.join("pyvenv.cfg").is_file() || path.join("conda-meta").join("history").is_file()
}

/// Check if a directory basename matches a glob pattern.
///
/// This implements simplified fnmatch-style matching similar to pytest's fnmatch_ex.
/// If the pattern contains no path separator, it's matched against the basename only.
fn matches_pattern(basename: &str, pattern: &str) -> bool {
    // Common patterns that need exact or wildcard matching
    match pattern {
        // Match directories starting with dot (hidden directories)
        ".*" => basename.starts_with('.'),
        // Match directories ending with specific suffix
        "*.egg" => basename.ends_with(".egg"),
        // Exact matches
        _ => basename == pattern,
    }
}

/// Check if a directory should be excluded from test discovery.
///
/// This implements pytest's norecursedirs behavior with the default patterns:
/// ["*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}"]
///
/// Additionally checks for virtual environments via marker files (pyvenv.cfg).
fn should_exclude_dir(entry: &walkdir::DirEntry) -> bool {
    if !entry.file_type().is_dir() {
        return false;
    }

    let path = entry.path();
    let basename = entry.file_name().to_string_lossy();

    // First check if this is a virtual environment (pytest's _in_venv check)
    if is_virtualenv(path) {
        return true;
    }

    // Apply pytest's default norecursedirs patterns
    const NORECURSE_PATTERNS: &[&str] = &[
        "*.egg",
        ".*",
        "_darcs",
        "build",
        "CVS",
        "dist",
        "node_modules",
        "venv",
        "{arch}",
    ];

    NORECURSE_PATTERNS
        .iter()
        .any(|pattern| matches_pattern(&basename, pattern))
}

/// File type for collection.
#[derive(Clone)]
enum FileType {
    Python,
    Markdown,
}

/// Discover all test files in parallel using rayon.
///
/// This function walks the file system in parallel to collect all potential
/// test files before any Python imports happen. This is a significant
/// optimization because file system traversal is I/O-bound and can be
/// parallelized effectively.
fn discover_files_parallel(
    paths: &[PathBuf],
    py_glob: &GlobSet,
    md_glob: Option<&GlobSet>,
) -> Vec<(PathBuf, FileType)> {
    // First, collect all directories to walk
    let mut dirs_to_walk: Vec<PathBuf> = Vec::new();
    let mut direct_files: Vec<(PathBuf, FileType)> = Vec::new();

    for path in paths {
        if path.is_dir() {
            dirs_to_walk.push(path.clone());
        } else if path.is_file() {
            if py_glob.is_match(path) {
                direct_files.push((path.clone(), FileType::Python));
            } else if let Some(md_glob_set) = md_glob {
                if md_glob_set.is_match(path) {
                    direct_files.push((path.clone(), FileType::Markdown));
                }
            }
        }
    }

    // Walk directories in parallel using rayon
    let discovered_files: Vec<(PathBuf, FileType)> = dirs_to_walk
        .par_iter()
        .flat_map(|dir| {
            let mut files = Vec::new();
            for entry in WalkDir::new(dir)
                .into_iter()
                .filter_entry(|e| !should_exclude_dir(e))
                .filter_map(Result::ok)
            {
                let file = entry.into_path();
                if file.is_file() {
                    if py_glob.is_match(&file) {
                        files.push((file, FileType::Python));
                    } else if let Some(md_glob_set) = md_glob {
                        if md_glob_set.is_match(&file) {
                            files.push((file, FileType::Markdown));
                        }
                    }
                }
            }
            files
        })
        .collect();

    // Combine direct files with discovered files
    let mut all_files = direct_files;
    all_files.extend(discovered_files);
    all_files
}

/// Discover all conftest.py files in parallel.
///
/// This collects all conftest.py paths first using parallel file system traversal,
/// then loads them sequentially (Python imports require GIL).
fn discover_conftest_paths_parallel(paths: &[PathBuf]) -> HashSet<PathBuf> {
    let mut conftest_paths: HashSet<PathBuf> = HashSet::new();

    // Collect directories to walk
    let dirs_to_walk: Vec<&PathBuf> = paths.iter().filter(|p| p.is_dir()).collect();

    // Walk directories in parallel to find conftest.py files
    let discovered: Vec<PathBuf> = dirs_to_walk
        .par_iter()
        .flat_map(|dir| {
            let mut paths = Vec::new();
            for entry in WalkDir::new(dir)
                .into_iter()
                .filter_entry(|e| !should_exclude_dir(e))
                .filter_map(Result::ok)
            {
                let path = entry.path();
                if path.is_file() && path.file_name() == Some("conftest.py".as_ref()) {
                    if let Some(parent) = path.parent() {
                        paths.push(parent.to_path_buf());
                    }
                }
            }
            paths
        })
        .collect();

    conftest_paths.extend(discovered);

    // Also collect ancestor conftest directories for all input paths
    for path in paths {
        let start_dir = if path.is_dir() {
            Some(path.as_path())
        } else {
            path.parent()
        };

        let mut current = start_dir;
        while let Some(dir) = current {
            let conftest_path = dir.join("conftest.py");
            if conftest_path.is_file() {
                conftest_paths.insert(dir.to_path_buf());
            }
            current = dir.parent();
        }
    }

    conftest_paths
}

/// Discover tests for the provided paths.
///
/// The return type is intentionally high level: the caller receives a list of
/// modules, each bundling the fixtures and tests that were defined in the
/// corresponding Python file.  This makes it straightforward for the execution
/// pipeline to run tests while still having quick access to fixtures.
///
/// Returns a tuple of (modules, collection_errors) where collection_errors
/// contains any errors that occurred during test collection (e.g., syntax errors).
pub fn discover_tests(
    py: Python<'_>,
    paths: &PyPaths,
    config: &RunConfiguration,
) -> PyResult<(Vec<TestModule>, Vec<CollectionError>)> {
    let collection_start = std::time::Instant::now();

    // Emit collection started event
    if let Some(ref callback) = config.event_callback {
        emit_collection_started(callback);
    }

    let canonical_paths = paths.materialise()?;

    // Setup sys.path to enable imports like pytest does
    setup_python_path(py, &canonical_paths)?;

    // If pytest compatibility mode is enabled, inject the pytest shim
    if config.pytest_compat {
        inject_pytest_compat_shim(py)?;
    }

    let py_glob = build_file_glob()?;
    // Disable markdown code blocks in pytest-compat mode by default
    // to avoid syntax errors from documentation examples
    let md_glob = if config.enable_codeblocks && !config.pytest_compat {
        Some(build_markdown_glob()?)
    } else {
        None
    };
    let mut modules = Vec::new();
    let mut collection_errors = Vec::new();
    let module_ids = ModuleIdGenerator::default();
    let mut files_collected: usize = 0;

    // OPTIMIZATION: Discover all conftest paths in parallel first
    let conftest_dirs = discover_conftest_paths_parallel(&canonical_paths);

    // Load conftest fixtures (must be sequential due to Python GIL)
    let mut conftest_fixtures: HashMap<PathBuf, IndexMap<String, Fixture>> = HashMap::new();
    for dir in &conftest_dirs {
        let conftest_path = dir.join("conftest.py");
        if conftest_path.is_file() && !conftest_fixtures.contains_key(dir) {
            let fixtures = load_conftest_fixtures(py, &conftest_path, &module_ids)?;
            conftest_fixtures.insert(dir.clone(), fixtures);
        }
    }

    // OPTIMIZATION: Discover all test files in parallel
    let test_files = discover_files_parallel(&canonical_paths, &py_glob, md_glob.as_ref());

    // Process test files sequentially (Python imports require GIL)
    for (file, file_type) in test_files {
        // Ensure parent conftest fixtures are loaded (they should already be, but check)
        discover_parent_conftest_files(py, &file, &mut conftest_fixtures, &module_ids)?;

        match file_type {
            FileType::Python => {
                match collect_from_file(py, &file, config, &module_ids, &conftest_fixtures) {
                    Ok(Some(module)) => {
                        let tests_in_file = module.tests.len();
                        modules.push(module);
                        files_collected += 1;
                        if let Some(ref callback) = config.event_callback {
                            emit_collection_progress(
                                callback,
                                to_relative_path(&file),
                                tests_in_file,
                                files_collected,
                            );
                        }
                    }
                    Ok(None) => {}
                    Err(err) => {
                        let error_msg = format_collection_error(py, &err);
                        collection_errors
                            .push(CollectionError::new(to_relative_path(&file), error_msg));
                    }
                }
            }
            FileType::Markdown => {
                match collect_from_markdown(py, &file, config, &conftest_fixtures) {
                    Ok(Some(module)) => {
                        let tests_in_file = module.tests.len();
                        modules.push(module);
                        files_collected += 1;
                        if let Some(ref callback) = config.event_callback {
                            emit_collection_progress(
                                callback,
                                to_relative_path(&file),
                                tests_in_file,
                                files_collected,
                            );
                        }
                    }
                    Ok(None) => {}
                    Err(err) => {
                        let error_msg = format_collection_error(py, &err);
                        collection_errors
                            .push(CollectionError::new(to_relative_path(&file), error_msg));
                    }
                }
            }
        }
    }

    // Apply last-failed filtering if configured
    if config.last_failed_mode != LastFailedMode::None {
        apply_last_failed_filter(&mut modules, config)?;
    }

    // Calculate total tests and emit collection completed event
    let total_tests: usize = modules.iter().map(|m| m.tests.len()).sum();
    let collection_duration = collection_start.elapsed().as_secs_f64();
    if let Some(ref callback) = config.event_callback {
        emit_collection_completed(callback, files_collected, total_tests, collection_duration);
    }

    Ok((modules, collection_errors))
}

/// Format a collection error for display.
fn format_collection_error(py: Python<'_>, error: &PyErr) -> String {
    // Try to get a formatted traceback using Python's traceback module
    let format_result: Result<String, _> = (|| {
        let traceback_module = py.import("traceback")?;
        let formatted = traceback_module.call_method1(
            "format_exception",
            (error.get_type(py), error.value(py), error.traceback(py)),
        )?;
        let lines: Vec<String> = formatted.extract()?;
        Ok(lines.join(""))
    })();

    format_result.unwrap_or_else(|_: PyErr| error.to_string())
}

/// Discover conftest.py files in parent directories when running a single test file.
///
/// When a user runs a test file in a nested directory structure like:
///   tests/test_api/test_nested.py
///
/// Pytest looks for conftest.py in all parent directories up to the project root:
///   tests/test_api/conftest.py
///   tests/conftest.py
///   conftest.py
///
/// This function walks UP the directory tree to find these conftest.py files,
/// ensuring session-scoped fixtures and other conftest fixtures are available
/// even when running deeply nested test files.
fn discover_parent_conftest_files(
    py: Python<'_>,
    test_file: &Path,
    conftest_map: &mut HashMap<PathBuf, IndexMap<String, Fixture>>,
    module_ids: &ModuleIdGenerator,
) -> PyResult<()> {
    // Start from the test file's parent directory
    let mut current_dir = match test_file.parent() {
        Some(dir) => dir,
        None => return Ok(()),
    };

    // Walk up the directory tree looking for conftest.py files
    loop {
        let conftest_path = current_dir.join("conftest.py");
        if conftest_path.is_file() {
            // Only load if we haven't already loaded it
            if !conftest_map.contains_key(current_dir) {
                let fixtures = load_conftest_fixtures(py, &conftest_path, module_ids)?;
                conftest_map.insert(current_dir.to_path_buf(), fixtures);
            }
        }

        // Move to parent directory
        match current_dir.parent() {
            Some(parent) => current_dir = parent,
            None => break, // Reached filesystem root
        }
    }

    Ok(())
}

/// Load fixtures from external modules (rustest_fixtures or pytest_plugins).
///
/// Rustest supports loading fixtures from external Python modules via:
/// - `rustest_fixtures` (preferred, rustest-specific, clear naming)
/// - `pytest_plugins` (pytest compatibility, despite the name it only loads fixture modules)
///
/// This is NOT about supporting pytest's plugin ecosystem (pluggy hooks, entry points, etc.)
/// - just importing Python modules and extracting their @fixture decorated functions.
///
/// Examples:
///   # conftest.py (rustest native - preferred)
///   rustest_fixtures = ["my_fixtures"]  # or "my_fixtures" as a string
///
///   # conftest.py (pytest compatibility)
///   pytest_plugins = ["my_fixtures"]  # works but confusing name
///
///   # my_fixtures.py
///   @pytest.fixture  # or @rustest.fixture
///   def my_fixture():
///       return "value"
fn load_pytest_plugins_fixtures(
    py: Python<'_>,
    pytest_plugins: &Bound<'_, PyAny>,
    _inspect: &Bound<'_, PyAny>,
    isfunction: &Bound<'_, PyAny>,
    fixtures: &mut IndexMap<String, Fixture>,
    conftest_dir: &Path,
) -> PyResult<()> {
    // Parse pytest_plugins - can be a string or list of strings
    let plugin_names: Vec<String> = if let Ok(plugin_str) = pytest_plugins.extract::<String>() {
        // Single string: pytest_plugins = "my_fixtures"
        vec![plugin_str]
    } else if let Ok(plugin_list) = pytest_plugins.extract::<Vec<String>>() {
        // List of strings: pytest_plugins = ["my_fixtures", "other_fixtures"]
        plugin_list
    } else {
        // Invalid format - skip silently for compatibility
        return Ok(());
    };

    // Temporarily add conftest directory to sys.path for importing modules
    // This allows importing modules from the same directory as conftest.py
    let sys = py.import("sys")?;
    let sys_path: Bound<'_, PyAny> = sys.getattr("path")?;
    let conftest_dir_str = conftest_dir.to_string_lossy().to_string();

    // Insert the conftest directory at the beginning of sys.path
    sys_path.call_method1("insert", (0, conftest_dir_str.clone()))?;

    // Import each module and extract fixtures
    let importlib = py.import("importlib")?;
    let import_module = importlib.getattr("import_module")?;

    for module_name in plugin_names {
        // Import the module
        let plugin_module = match import_module.call1((module_name.as_str(),)) {
            Ok(module) => module,
            Err(e) => {
                // If import fails, print a warning but continue
                eprintln!(
                    "Warning: Failed to import pytest plugin module '{}': {}",
                    module_name, e
                );
                continue;
            }
        };

        // Get the module's __dict__
        let plugin_dict: Bound<'_, PyDict> = match plugin_module.getattr("__dict__") {
            Ok(dict) => dict.cast_into()?,
            Err(_) => continue,
        };

        // Extract fixtures from the plugin module
        for (name_obj, value) in plugin_dict.iter() {
            let name: String = match name_obj.extract() {
                Ok(n) => n,
                Err(_) => continue,
            };

            // Check if it's a function and a fixture
            if isfunction.call1((&value,))?.is_truthy()? && is_fixture(&value)? {
                let scope = extract_fixture_scope(&value)?;
                let is_generator = is_generator_function(py, &value)?;
                let is_async = is_async_function(py, &value)?;
                let is_async_generator = is_async_generator_function(py, &value)?;
                let autouse = extract_fixture_autouse(&value)?;
                let params = extract_fixture_params(&value)?;
                let fixture_name = extract_fixture_name(&value, &name)?;

                let fixture = if let Some(params) = params {
                    Fixture::with_params(
                        fixture_name.clone(),
                        value.clone().unbind(),
                        extract_parameters(py, &value)?,
                        scope,
                        is_generator,
                        is_async,
                        is_async_generator,
                        autouse,
                        params,
                        None,
                    )
                } else {
                    Fixture::new(
                        fixture_name.clone(),
                        value.clone().unbind(),
                        extract_parameters(py, &value)?,
                        scope,
                        is_generator,
                        is_async,
                        is_async_generator,
                        autouse,
                        None,
                    )
                };
                fixtures.insert(fixture_name, fixture);
            }
        }
    }

    // Remove the conftest directory from sys.path
    sys_path.call_method1("remove", (conftest_dir_str,))?;

    Ok(())
}

/// Load fixtures from a conftest.py file.
fn load_conftest_fixtures(
    py: Python<'_>,
    path: &Path,
    module_ids: &ModuleIdGenerator,
) -> PyResult<IndexMap<String, Fixture>> {
    let (module_name, package_name) = infer_module_names(path, module_ids.next());
    let module = load_python_module(py, path, &module_name, package_name.as_deref())?;
    let module_dict: Bound<'_, PyDict> = module.getattr("__dict__")?.cast_into()?;

    let inspect = py.import("inspect")?;
    let isfunction = inspect.getattr("isfunction")?;
    let mut fixtures = IndexMap::new();

    // Load fixtures from external modules
    // Priority: rustest_fixtures (preferred) > pytest_plugins (compatibility)
    let fixture_modules = if let Ok(Some(modules)) = module_dict.get_item("rustest_fixtures") {
        // Preferred: rustest_fixtures (clear, explicit naming)
        Some(modules)
    } else if let Ok(Some(modules)) = module_dict.get_item("pytest_plugins") {
        // Fallback: pytest_plugins (for pytest compatibility)
        // Note: Despite the name, this only loads fixture modules, not actual pytest plugins
        Some(modules)
    } else {
        None
    };

    if let Some(plugins) = fixture_modules {
        // Get the conftest directory for importing modules
        let conftest_dir = path.parent().unwrap_or(path);
        load_pytest_plugins_fixtures(
            py,
            &plugins,
            &inspect,
            &isfunction,
            &mut fixtures,
            conftest_dir,
        )?;
    }

    // Then load fixtures from the conftest module itself
    for (name_obj, value) in module_dict.iter() {
        let name: String = name_obj.extract()?;

        // Check if it's a function and a fixture
        if isfunction.call1((&value,))?.is_truthy()? && is_fixture(&value)? {
            let scope = extract_fixture_scope(&value)?;
            let is_generator = is_generator_function(py, &value)?;
            let is_async = is_async_function(py, &value)?;
            let is_async_generator = is_async_generator_function(py, &value)?;
            let autouse = extract_fixture_autouse(&value)?;
            let params = extract_fixture_params(&value)?;
            let fixture_name = extract_fixture_name(&value, &name)?;

            let fixture = if let Some(params) = params {
                Fixture::with_params(
                    fixture_name.clone(),
                    value.clone().unbind(),
                    extract_parameters(py, &value)?,
                    scope,
                    is_generator,
                    is_async,
                    is_async_generator,
                    autouse,
                    params,
                    None,
                )
            } else {
                Fixture::new(
                    fixture_name.clone(),
                    value.clone().unbind(),
                    extract_parameters(py, &value)?,
                    scope,
                    is_generator,
                    is_async,
                    is_async_generator,
                    autouse,
                    None,
                )
            };
            fixtures.insert(fixture_name, fixture);
        }
    }

    Ok(fixtures)
}

/// Merge conftest fixtures for a test file with the file's own fixtures.
/// Conftest fixtures from parent directories are merged from farthest to nearest,
/// and the test file's own fixtures override any conftest fixtures with the same name.
fn merge_conftest_fixtures(
    py: Python<'_>,
    test_path: &Path,
    module_fixtures: IndexMap<String, Fixture>,
    conftest_map: &HashMap<PathBuf, IndexMap<String, Fixture>>,
) -> PyResult<IndexMap<String, Fixture>> {
    let mut merged = IndexMap::new();

    // Start with built-in fixtures so user-defined ones can override them.
    for (name, fixture) in load_builtin_fixtures(py)? {
        merged.insert(name, fixture);
    }

    // Collect all parent directories from farthest to nearest
    let mut parent_dirs = Vec::new();
    if let Some(mut parent) = test_path.parent() {
        loop {
            parent_dirs.push(parent.to_path_buf());
            if let Some(next_parent) = parent.parent() {
                parent = next_parent;
            } else {
                break;
            }
        }
    }
    parent_dirs.reverse(); // Process from farthest to nearest

    // Merge conftest fixtures from farthest to nearest
    for dir in parent_dirs {
        if let Some(fixtures) = conftest_map.get(&dir) {
            for (name, fixture) in fixtures {
                merged.insert(name.clone(), fixture.clone_with_py(py));
            }
        }
    }

    // Module's own fixtures override conftest fixtures
    for (name, fixture) in module_fixtures {
        merged.insert(name, fixture);
    }

    Ok(merged)
}

/// Load the built-in fixtures bundled with rustest.
fn load_builtin_fixtures(py: Python<'_>) -> PyResult<IndexMap<String, Fixture>> {
    let module = py.import("rustest.builtin_fixtures")?;
    let module_dict: Bound<'_, PyDict> = module.getattr("__dict__")?.cast_into()?;

    let inspect = py.import("inspect")?;
    let isfunction = inspect.getattr("isfunction")?;
    let mut fixtures = IndexMap::new();

    for (name_obj, value) in module_dict.iter() {
        let name: String = name_obj.extract()?;

        if isfunction.call1((&value,))?.is_truthy()? && is_fixture(&value)? {
            let scope = extract_fixture_scope(&value)?;
            let is_generator = is_generator_function(py, &value)?;
            let is_async = is_async_function(py, &value)?;
            let is_async_generator = is_async_generator_function(py, &value)?;
            let autouse = extract_fixture_autouse(&value)?;
            let params = extract_fixture_params(&value)?;
            let fixture_name = extract_fixture_name(&value, &name)?;

            let fixture = if let Some(params) = params {
                Fixture::with_params(
                    fixture_name.clone(),
                    value.clone().unbind(),
                    extract_parameters(py, &value)?,
                    scope,
                    is_generator,
                    is_async,
                    is_async_generator,
                    autouse,
                    params,
                    None,
                )
            } else {
                Fixture::new(
                    fixture_name.clone(),
                    value.clone().unbind(),
                    extract_parameters(py, &value)?,
                    scope,
                    is_generator,
                    is_async,
                    is_async_generator,
                    autouse,
                    None,
                )
            };
            fixtures.insert(fixture_name, fixture);
        }
    }

    Ok(fixtures)
}

/// Build the default glob set matching `test_*.py` and `*_test.py` files.
fn build_file_glob() -> PyResult<GlobSet> {
    let mut builder = GlobSetBuilder::new();
    builder.add(
        Glob::new("**/test_*.py")
            .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))?,
    );
    builder.add(
        Glob::new("**/*_test.py")
            .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))?,
    );
    builder
        .build()
        .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))
}

/// Build the glob set matching markdown files (*.md).
fn build_markdown_glob() -> PyResult<GlobSet> {
    let mut builder = GlobSetBuilder::new();
    builder.add(
        Glob::new("**/*.md")
            .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))?,
    );
    builder
        .build()
        .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))
}

/// Load a module from `path` and extract fixtures and tests.
fn collect_from_file(
    py: Python<'_>,
    path: &Path,
    config: &RunConfiguration,
    module_ids: &ModuleIdGenerator,
    conftest_map: &HashMap<PathBuf, IndexMap<String, Fixture>>,
) -> PyResult<Option<TestModule>> {
    let (module_name, package_name) = infer_module_names(path, module_ids.next());
    let module = load_python_module(py, path, &module_name, package_name.as_deref())?;
    let module_dict: Bound<'_, PyDict> = module.getattr("__dict__")?.cast_into()?;

    let (module_fixtures, tests) = inspect_module(py, path, &module_dict, config.pytest_compat)?;

    // Merge conftest fixtures with the module's own fixtures
    let fixtures = merge_conftest_fixtures(py, path, module_fixtures, conftest_map)?;

    // Expand tests for parametrized fixtures
    let mut tests = expand_tests_for_parametrized_fixtures(py, tests, &fixtures)?;

    if let Some(pattern) = &config.pattern {
        tests.retain(|case| test_matches_pattern(case, pattern));
    }

    // Apply mark filtering if specified
    if let Some(mark_expr_str) = &config.mark_expr {
        let mark_expr = MarkExpr::parse(mark_expr_str)
            .map_err(|e| invalid_test_definition(format!("Invalid mark expression: {}", e)))?;
        tests.retain(|case| mark_expr.matches(&case.marks));
    }

    if tests.is_empty() {
        return Ok(None);
    }

    Ok(Some(TestModule::new(path.to_path_buf(), fixtures, tests)))
}

/// Parse markdown file and extract Python code blocks as tests.
fn collect_from_markdown(
    py: Python<'_>,
    path: &Path,
    config: &RunConfiguration,
    conftest_map: &HashMap<PathBuf, IndexMap<String, Fixture>>,
) -> PyResult<Option<TestModule>> {
    // Read the markdown file
    let content = std::fs::read_to_string(path).map_err(|e| {
        invalid_test_definition(format!("Failed to read {}: {}", path.display(), e))
    })?;

    // Parse Python code blocks
    let mut tests = Vec::new();
    let code_blocks = extract_python_code_blocks(&content);

    for (index, (code, line_number, should_skip)) in code_blocks.into_iter().enumerate() {
        // Create a test name that includes the line number for clarity
        let test_name = format!("codeblock_{}_line_{}", index, line_number);
        // Create a display name that looks like: path.md::codeblock_0::line_15
        let display_name = format!(
            "{}::codeblock_{}::line_{}",
            path.display(),
            index,
            line_number
        );

        // Create a Python callable that executes the code block
        let callable = create_codeblock_callable(py, &code, path, line_number)?;

        // Create codeblock mark
        let codeblock_mark = Mark::new(
            "codeblock".to_string(),
            PyList::empty(py).unbind(),
            PyDict::new(py).unbind(),
        );

        // Set skip reason if marker was present
        let skip_reason = if should_skip {
            Some("Skipped via HTML comment marker".to_string())
        } else {
            None
        };

        tests.push(TestCase {
            name: test_name.clone(),
            display_name,
            path: path.to_path_buf(),
            callable,
            parameters: Vec::new(),
            parameter_values: ParameterMap::new(),
            skip_reason,
            marks: vec![codeblock_mark],
            class_name: None,
            fixture_param_indices: IndexMap::new(),
            indirect_params: Vec::new(),
        });
    }

    // Apply pattern filtering if specified
    if let Some(pattern) = &config.pattern {
        tests.retain(|case| test_matches_pattern(case, pattern));
    }

    // Apply mark filtering if specified
    if let Some(mark_expr_str) = &config.mark_expr {
        let mark_expr = MarkExpr::parse(mark_expr_str)
            .map_err(|e| invalid_test_definition(format!("Invalid mark expression: {}", e)))?;
        tests.retain(|case| mark_expr.matches(&case.marks));
    }

    if tests.is_empty() {
        return Ok(None);
    }

    // Merge conftest fixtures for the markdown file
    let fixtures = merge_conftest_fixtures(py, path, IndexMap::new(), conftest_map)?;

    Ok(Some(TestModule::new(path.to_path_buf(), fixtures, tests)))
}

/// Extract Python code blocks from markdown content.
/// Returns a vector of tuples containing (code, line_number, should_skip) where:
/// - code: the Python code
/// - line_number: the line where the code block starts (the ``` line)
/// - should_skip: true if <!--pytest.mark.skip--> appears before the block
fn extract_python_code_blocks(content: &str) -> Vec<(String, usize, bool)> {
    let mut code_blocks = Vec::new();
    let mut in_code_block = false;
    let mut current_block = String::new();
    let mut block_language = String::new();
    let mut block_start_line = 0;
    let mut has_skip_marker = false;
    let mut last_line_was_comment = false;

    for (line_num, line) in content.lines().enumerate() {
        let trimmed = line.trim();

        // Check for skip marker before code blocks
        // Support both rustest and pytest variants for compatibility
        if !in_code_block
            && (trimmed.contains("<!--rustest.mark.skip-->")
                || trimmed.contains("<!--pytest.mark.skip-->")
                || trimmed.contains("<!--pytest-codeblocks:skip-->"))
        {
            has_skip_marker = true;
            last_line_was_comment = true;
            continue;
        }

        if let Some(stripped) = trimmed.strip_prefix("```") {
            if in_code_block {
                // End of code block
                if block_language == "python" {
                    code_blocks.push((current_block.clone(), block_start_line, has_skip_marker));
                }
                current_block.clear();
                block_language.clear();
                in_code_block = false;
                has_skip_marker = false;
            } else {
                // Start of code block (line numbers are 1-based)
                in_code_block = true;
                block_start_line = line_num + 1;
                // Extract the language identifier
                block_language = stripped.trim().to_lowercase();
            }
        } else if in_code_block {
            // Add line to current block
            if !current_block.is_empty() {
                current_block.push('\n');
            }
            current_block.push_str(line);
        } else if !last_line_was_comment {
            // Reset skip marker if we encounter a non-comment, non-code-block line
            has_skip_marker = false;
        }

        last_line_was_comment = false;
    }

    code_blocks
}

/// Create a Python callable that executes a code block.
fn create_codeblock_callable(
    py: Python<'_>,
    code: &str,
    file_path: &Path,
    line_number: usize,
) -> PyResult<Py<PyAny>> {
    // Create a wrapper function that executes the code block
    // Include the original markdown location as a comment for better error messages
    let wrapper_code = format!(
        r#"
# Code block from {} (line {})
def run_codeblock():
{}
"#,
        file_path.display(),
        line_number,
        // Indent the code block by 4 spaces
        code.lines()
            .map(|line| format!("    {}", line))
            .collect::<Vec<_>>()
            .join("\n")
    );

    let namespace = PyDict::new(py);

    // Use compile with a descriptive filename for better tracebacks
    let filename = format!("{}:L{}", file_path.display(), line_number);
    let compile_result =
        py.import("builtins")?
            .getattr("compile")?
            .call1((wrapper_code, filename, "exec"))?;

    py.import("builtins")?
        .getattr("exec")?
        .call1((compile_result, &namespace))?;

    let run_codeblock = namespace
        .get_item("run_codeblock")?
        .ok_or_else(|| invalid_test_definition("Failed to create codeblock callable"))?;

    Ok(run_codeblock.unbind())
}

/// Determine whether a test case should be kept for the provided pattern.
fn test_matches_pattern(test_case: &TestCase, pattern: &str) -> bool {
    let pattern_lower = pattern.to_ascii_lowercase();
    test_case
        .display_name
        .to_ascii_lowercase()
        .contains(&pattern_lower)
        || test_case
            .path
            .display()
            .to_string()
            .to_ascii_lowercase()
            .contains(&pattern_lower)
}

/// Inspect the module dictionary and extract fixtures/tests.
fn inspect_module(
    py: Python<'_>,
    path: &Path,
    module_dict: &Bound<'_, PyDict>,
    pytest_compat: bool,
) -> PyResult<(IndexMap<String, Fixture>, Vec<TestCase>)> {
    let inspect = py.import("inspect")?;
    let isfunction = inspect.getattr("isfunction")?;
    let isclass = inspect.getattr("isclass")?;
    let mut fixtures = IndexMap::new();
    let mut tests = Vec::new();

    for (name_obj, value) in module_dict.iter() {
        let name: String = name_obj.extract()?;

        // Check if it's a function
        if isfunction.call1((&value,))?.is_truthy()? {
            if is_fixture(&value)? {
                let scope = extract_fixture_scope(&value)?;
                let is_generator = is_generator_function(py, &value)?;
                let is_async = is_async_function(py, &value)?;
                let is_async_generator = is_async_generator_function(py, &value)?;
                let autouse = extract_fixture_autouse(&value)?;
                let params = extract_fixture_params(&value)?;
                let fixture_name = extract_fixture_name(&value, &name)?;

                let fixture = if let Some(params) = params {
                    Fixture::with_params(
                        fixture_name.clone(),
                        value.clone().unbind(),
                        extract_parameters(py, &value)?,
                        scope,
                        is_generator,
                        is_async,
                        is_async_generator,
                        autouse,
                        params,
                        None,
                    )
                } else {
                    Fixture::new(
                        fixture_name.clone(),
                        value.clone().unbind(),
                        extract_parameters(py, &value)?,
                        scope,
                        is_generator,
                        is_async,
                        is_async_generator,
                        autouse,
                        None,
                    )
                };
                fixtures.insert(fixture_name, fixture);
                continue;
            }

            if !name.starts_with("test") {
                continue;
            }

            let parameters = extract_parameters(py, &value)?;
            let mut skip_reason = string_attribute(&value, "__rustest_skip__")?;

            // Check for @patch decorator if not already skipped
            if skip_reason.is_none() {
                skip_reason = check_for_patch_decorator(py, &value, pytest_compat)?;
            }

            // Check for @pytest.mark.skip decorator if not already skipped
            if skip_reason.is_none() {
                skip_reason = check_for_pytest_skip_mark(py, &value)?;
            }

            let param_cases = collect_parametrization(py, &value)?;
            let marks = collect_marks(&value)?;
            let indirect_params = extract_indirect_params(&value)?;

            if param_cases.is_empty() {
                tests.push(TestCase {
                    name: name.clone(),
                    display_name: name.clone(),
                    path: path.to_path_buf(),
                    callable: value.clone().unbind(),
                    parameters: parameters.clone(),
                    parameter_values: ParameterMap::new(),
                    skip_reason: skip_reason.clone(),
                    marks: marks.clone(),
                    class_name: None,
                    fixture_param_indices: IndexMap::new(),
                    indirect_params: indirect_params.clone(),
                });
            } else {
                for (case_id, values) in param_cases {
                    let display_name = format!("{}[{}]", name, case_id);
                    tests.push(TestCase {
                        name: name.clone(),
                        display_name,
                        path: path.to_path_buf(),
                        callable: value.clone().unbind(),
                        parameters: parameters.clone(),
                        parameter_values: values,
                        skip_reason: skip_reason.clone(),
                        marks: marks.clone(),
                        class_name: None,
                        fixture_param_indices: IndexMap::new(),
                        indirect_params: indirect_params.clone(),
                    });
                }
            }
        }
        // Check if it's a class (both unittest.TestCase and plain test classes)
        else if isclass.call1((&value,))?.is_truthy()? {
            if is_test_case_class(py, &value)? {
                // unittest.TestCase support
                let class_tests = discover_unittest_class_tests(py, path, &name, &value)?;
                tests.extend(class_tests);
            } else if is_plain_test_class(&name) {
                // Plain pytest-style test class support
                // Extract both test methods and fixture methods from the class
                let (class_fixtures, class_tests) = discover_plain_class_tests_and_fixtures(
                    py,
                    path,
                    &name,
                    &value,
                    pytest_compat,
                )?;
                // Merge class fixtures into module fixtures
                for (fixture_name, fixture) in class_fixtures {
                    fixtures.insert(fixture_name, fixture);
                }
                tests.extend(class_tests);
            }
        }
    }

    Ok((fixtures, tests))
}

/// Recursively collect all parametrized fixtures in a fixture's dependency chain.
fn collect_parametrized_fixtures<'a>(
    name: &str,
    fixtures: &'a IndexMap<String, Fixture>,
    param_fixtures: &mut Vec<(&'a String, &'a Vec<FixtureParam>)>,
    visited: &mut HashSet<String>,
) {
    // Skip if already visited (avoid infinite loops)
    if visited.contains(name) {
        return;
    }
    visited.insert(name.to_string());

    if let Some(fixture) = fixtures.get(name) {
        // If this fixture is parametrized, add it to the list
        if fixture.params.is_some() {
            // Get a stable reference to the fixture name from the map
            for (fixture_name, f) in fixtures.iter() {
                if fixture_name == name {
                    if let Some(p) = &f.params {
                        param_fixtures.push((fixture_name, p));
                    }
                    break;
                }
            }
        }

        // Recursively check this fixture's dependencies
        for dep_name in &fixture.parameters {
            collect_parametrized_fixtures(dep_name, fixtures, param_fixtures, visited);
        }
    }
}

/// Expand tests based on parametrized fixtures.
/// For each test that uses a parametrized fixture, create multiple test cases -
/// one for each parameter value.
fn expand_tests_for_parametrized_fixtures(
    py: Python<'_>,
    tests: Vec<TestCase>,
    fixtures: &IndexMap<String, Fixture>,
) -> PyResult<Vec<TestCase>> {
    let mut expanded_tests = Vec::new();

    for test in tests {
        // Find all parametrized fixtures in the dependency chain (recursive)
        let mut param_fixtures: Vec<(&String, &Vec<FixtureParam>)> = Vec::new();
        let mut visited: HashSet<String> = HashSet::new();

        // Collect all parametrized fixtures from test parameters and their dependencies
        for param_name in &test.parameters {
            collect_parametrized_fixtures(param_name, fixtures, &mut param_fixtures, &mut visited);
        }

        if param_fixtures.is_empty() {
            // No parametrized fixtures, keep the test as-is
            expanded_tests.push(test);
            continue;
        }

        // Generate the cartesian product of all parametrized fixture values
        let combinations = generate_param_combinations(&param_fixtures);

        for (combo_ids, combo_indices) in combinations {
            // Build the new display name
            let fixture_id_suffix = combo_ids.join("-");
            let new_display_name = if test.display_name.contains('[') {
                // Already has test parametrization, append fixture params
                let base = test.display_name.trim_end_matches(']');
                format!("{}-{}]", base, fixture_id_suffix)
            } else {
                format!("{}[{}]", test.display_name, fixture_id_suffix)
            };

            // Build fixture_param_indices map
            let mut fixture_param_indices = test.fixture_param_indices.clone();
            for (fixture_name, param_idx) in combo_indices {
                fixture_param_indices.insert(fixture_name, param_idx);
            }

            // Clone parameter_values with Python context
            let mut cloned_param_values = ParameterMap::new();
            for (key, value) in &test.parameter_values {
                cloned_param_values.insert(key.clone(), value.clone_ref(py));
            }

            expanded_tests.push(TestCase {
                name: test.name.clone(),
                display_name: new_display_name,
                path: test.path.clone(),
                callable: test.callable.clone_ref(py),
                parameters: test.parameters.clone(),
                parameter_values: cloned_param_values,
                skip_reason: test.skip_reason.clone(),
                marks: test.marks.iter().map(|m| m.clone_with_py(py)).collect(),
                class_name: test.class_name.clone(),
                fixture_param_indices,
                indirect_params: test.indirect_params.clone(),
            });
        }
    }

    Ok(expanded_tests)
}

/// Type alias for parameter combinations: (IDs, fixture indices).
type ParamCombination = (Vec<String>, Vec<(String, usize)>);

/// Generate the cartesian product of parametrized fixture values.
/// Returns a vector of (ids, indices) tuples.
fn generate_param_combinations(
    param_fixtures: &[(&String, &Vec<FixtureParam>)],
) -> Vec<ParamCombination> {
    if param_fixtures.is_empty() {
        return vec![(Vec::new(), Vec::new())];
    }

    let mut result = vec![(Vec::new(), Vec::new())];

    for (fixture_name, params) in param_fixtures {
        let mut new_result = Vec::new();
        for (ids, indices) in &result {
            for (param_idx, param) in params.iter().enumerate() {
                let mut new_ids = ids.clone();
                new_ids.push(param.id.clone());

                let mut new_indices = indices.clone();
                new_indices.push(((*fixture_name).clone(), param_idx));

                new_result.push((new_ids, new_indices));
            }
        }
        result = new_result;
    }

    result
}

/// Check if a class name follows the pytest-style test class naming convention.
/// A plain test class starts with "Test" (capital T).
fn is_plain_test_class(name: &str) -> bool {
    name.starts_with("Test")
}

/// Check if a class is a unittest.TestCase subclass.
fn is_test_case_class(py: Python<'_>, cls: &Bound<'_, PyAny>) -> PyResult<bool> {
    let unittest = py.import("unittest")?;
    let test_case = unittest.getattr("TestCase")?;

    // Use issubclass to check inheritance
    let builtins = py.import("builtins")?;
    let issubclass_fn = builtins.getattr("issubclass")?;

    match issubclass_fn.call1((cls, &test_case)) {
        Ok(result) => Ok(result.is_truthy()?),
        Err(_) => Ok(false),
    }
}

/// Discover test methods in a unittest.TestCase class.
fn discover_unittest_class_tests(
    py: Python<'_>,
    path: &Path,
    class_name: &str,
    cls: &Bound<'_, PyAny>,
) -> PyResult<Vec<TestCase>> {
    let mut tests = Vec::new();
    let inspect = py.import("inspect")?;

    // Get all members of the class
    let members = inspect.call_method1("getmembers", (cls,))?;

    for member in members.try_iter()? {
        let member = member?;

        // Each member is a tuple (name, value)
        let name: String = member.get_item(0)?.extract()?;
        let method = member.get_item(1)?;

        // Check if it's a method and starts with "test"
        if name.starts_with("test") && is_callable(&method)? {
            let display_name = format!("{}::{}", class_name, name);

            // Create a callable that properly instantiates and runs the test
            let test_callable = create_unittest_method_runner(py, cls, &name)?;

            tests.push(TestCase {
                name: name.clone(),
                display_name,
                path: path.to_path_buf(),
                callable: test_callable,
                parameters: Vec::new(),
                parameter_values: ParameterMap::new(),
                skip_reason: None,
                marks: Vec::new(),
                class_name: Some(class_name.to_string()),
                fixture_param_indices: IndexMap::new(),
                indirect_params: Vec::new(),
            });
        }
    }

    Ok(tests)
}

/// Combine class-level and method-level parametrizations.
///
/// When both class and method have parametrizations, this creates the Cartesian product
/// of all parameter combinations, matching pytest's behavior.
///
/// # Examples:
///
/// - Class params: [(x=1), (x=2)]
/// - Method params: [(y=10), (y=20)]
/// - Result: [(x=1,y=10), (x=1,y=20), (x=2,y=10), (x=2,y=20)]
fn combine_parametrizations(
    py: Python<'_>,
    class_params: &[(String, ParameterMap)],
    method_params: &[(String, ParameterMap)],
) -> PyResult<Vec<(String, ParameterMap)>> {
    // If neither has parametrizations, return empty
    if class_params.is_empty() && method_params.is_empty() {
        return Ok(Vec::new());
    }

    // If only class has parametrizations, return them
    if method_params.is_empty() {
        let mut result = Vec::new();
        for (class_id, class_values) in class_params {
            let mut cloned_values = ParameterMap::new();
            for (key, value) in class_values {
                cloned_values.insert(key.clone(), value.clone_ref(py));
            }
            result.push((class_id.clone(), cloned_values));
        }
        return Ok(result);
    }

    // If only method has parametrizations, return them
    if class_params.is_empty() {
        let mut result = Vec::new();
        for (method_id, method_values) in method_params {
            let mut cloned_values = ParameterMap::new();
            for (key, value) in method_values {
                cloned_values.insert(key.clone(), value.clone_ref(py));
            }
            result.push((method_id.clone(), cloned_values));
        }
        return Ok(result);
    }

    // Both have parametrizations - create Cartesian product
    let mut result = Vec::new();
    for (class_id, class_values) in class_params {
        for (method_id, method_values) in method_params {
            // Combine the parameter values
            let mut combined_values = ParameterMap::new();
            for (key, value) in class_values {
                combined_values.insert(key.clone(), value.clone_ref(py));
            }
            for (key, value) in method_values {
                combined_values.insert(key.clone(), value.clone_ref(py));
            }

            // Combine the IDs
            let combined_id = format!("{}-{}", class_id, method_id);
            result.push((combined_id, combined_values));
        }
    }

    Ok(result)
}

/// Discover test methods and fixture methods in a plain pytest-style test class.
/// Returns both fixtures defined in the class and the test cases.
fn discover_plain_class_tests_and_fixtures(
    py: Python<'_>,
    path: &Path,
    class_name: &str,
    cls: &Bound<'_, PyAny>,
    pytest_compat: bool,
) -> PyResult<(IndexMap<String, Fixture>, Vec<TestCase>)> {
    let mut fixtures = IndexMap::new();
    let mut tests = Vec::new();
    let inspect = py.import("inspect")?;

    // Extract class-level parametrization (if any)
    let class_param_cases = collect_parametrization(py, cls)?;

    // Process all members
    let members = inspect.call_method1("getmembers", (cls,))?;

    for member in members.try_iter()? {
        let member = member?;

        // Each member is a tuple (name, value)
        let name: String = member.get_item(0)?.extract()?;
        let method = member.get_item(1)?;

        // Skip special methods (like __init__, __str__, etc.)
        if name.starts_with("__") {
            continue;
        }

        // Check if it's a fixture method
        if is_callable(&method)? && is_fixture(&method)? {
            // Extract fixture metadata
            let scope = extract_fixture_scope(&method)?;
            let is_generator = is_generator_function(py, &method)?;
            let is_async = is_async_function(py, &method)?;
            let is_async_generator = is_async_generator_function(py, &method)?;
            let autouse = extract_fixture_autouse(&method)?;
            let fixture_name = extract_fixture_name(&method, &name)?;

            // Extract parameters (excluding 'self')
            let all_params = extract_parameters(py, &method)?;
            let parameters: Vec<String> = all_params.into_iter().filter(|p| p != "self").collect();

            // Create a wrapper that instantiates the class and calls the fixture method
            let fixture_callable = create_plain_class_method_runner(py, cls, &name)?;

            fixtures.insert(
                fixture_name.clone(),
                Fixture::new(
                    fixture_name,
                    fixture_callable,
                    parameters,
                    scope,
                    is_generator,
                    is_async,
                    is_async_generator,
                    autouse,
                    Some(class_name.to_string()),
                ),
            );
            continue;
        }

        // Check if it's a test method
        if name.starts_with("test") && is_callable(&method)? {
            let display_name = format!("{}::{}", class_name, name);

            // Extract parameters (excluding 'self')
            let all_params = extract_parameters(py, &method)?;
            let parameters: Vec<String> = all_params.into_iter().filter(|p| p != "self").collect();

            // Extract metadata
            let mut skip_reason = string_attribute(&method, "__rustest_skip__")?;

            // Check for @patch decorator if not already skipped
            if skip_reason.is_none() {
                skip_reason = check_for_patch_decorator(py, &method, pytest_compat)?;
            }

            // Check for @pytest.mark.skip decorator if not already skipped
            if skip_reason.is_none() {
                skip_reason = check_for_pytest_skip_mark(py, &method)?;
            }

            let marks = collect_marks(&method)?;
            let method_param_cases = collect_parametrization(py, &method)?;
            let indirect_params = extract_indirect_params(&method)?;

            // Combine class-level and method-level parametrization
            let combined_param_cases =
                combine_parametrizations(py, &class_param_cases, &method_param_cases)?;

            // Create a callable that instantiates the class and calls the method
            // Autouse fixtures will be resolved by the fixture system
            let test_callable = create_plain_class_method_runner(py, cls, &name)?;

            if combined_param_cases.is_empty() {
                tests.push(TestCase {
                    name: name.clone(),
                    display_name,
                    path: path.to_path_buf(),
                    callable: test_callable,
                    parameters,
                    parameter_values: ParameterMap::new(),
                    skip_reason,
                    marks,
                    class_name: Some(class_name.to_string()),
                    fixture_param_indices: IndexMap::new(),
                    indirect_params: indirect_params.clone(),
                });
            } else {
                // Handle parametrized test methods
                for (case_id, values) in combined_param_cases {
                    let param_display_name = format!("{}::{}[{}]", class_name, name, case_id);
                    tests.push(TestCase {
                        name: name.clone(),
                        display_name: param_display_name,
                        path: path.to_path_buf(),
                        callable: test_callable.clone_ref(py),
                        parameters: parameters.clone(),
                        parameter_values: values,
                        skip_reason: skip_reason.clone(),
                        marks: marks.clone(),
                        class_name: Some(class_name.to_string()),
                        fixture_param_indices: IndexMap::new(),
                        indirect_params: indirect_params.clone(),
                    });
                }
            }
        }
    }

    Ok((fixtures, tests))
}

/// Check if an object is callable.
fn is_callable(obj: &Bound<'_, PyAny>) -> PyResult<bool> {
    let builtins = obj.py().import("builtins")?;
    let callable_fn = builtins.getattr("callable")?;
    callable_fn.call1((obj,))?.is_truthy()
}

/// Create a callable that instantiates a unittest.TestCase and runs a specific test method.
/// This follows unittest's pattern of instantiating with the method name.
fn create_unittest_method_runner(
    py: Python<'_>,
    cls: &Bound<'_, PyAny>,
    method_name: &str,
) -> PyResult<Py<PyAny>> {
    // Create a wrapper function that instantiates the test class and runs the method
    // This will properly invoke setUp, the test method, and tearDown
    let code = format!(
        r#"
def run_test():
    test_instance = test_class('{}')
    test_instance()
"#,
        method_name
    );

    let namespace = PyDict::new(py);
    namespace.set_item("test_class", cls)?;

    let code_cstr = CString::new(code).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("Invalid code string: {}", e))
    })?;
    // Use the same dict for both globals and locals to ensure proper variable resolution
    py.run(&code_cstr, Some(&namespace), Some(&namespace))?;
    let run_test = namespace.get_item("run_test")?.unwrap();

    Ok(run_test.unbind())
}

/// Create a callable that instantiates a plain test class and runs a specific test method.
/// This wrapper will receive fixtures as arguments and pass them to the method.
fn create_plain_class_method_runner(
    py: Python<'_>,
    cls: &Bound<'_, PyAny>,
    method_name: &str,
) -> PyResult<Py<PyAny>> {
    // Create a wrapper function that:
    // 1. Instantiates the test class (without arguments)
    // 2. Gets the test method
    // 3. Calls the method with provided fixtures (as *args)
    let code = format!(
        r#"
def run_test(*args, **kwargs):
    test_instance = test_class()
    test_method = getattr(test_instance, '{}')
    return test_method(*args, **kwargs)
"#,
        method_name
    );

    let namespace = PyDict::new(py);
    namespace.set_item("test_class", cls)?;

    let code_cstr = CString::new(code).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("Invalid code string: {}", e))
    })?;
    // Use the same dict for both globals and locals to ensure proper variable resolution
    py.run(&code_cstr, Some(&namespace), Some(&namespace))?;
    let run_test = namespace.get_item("run_test")?.unwrap();

    Ok(run_test.unbind())
}

/// Determine whether a Python object has been marked as a fixture.
fn is_fixture(value: &Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(match value.getattr("__rustest_fixture__") {
        Ok(flag) => flag.is_truthy()?,
        Err(_) => false,
    })
}

// Python code object flag constants (from CPython's code.h)
const CO_GENERATOR: u32 = 0x20;
const CO_COROUTINE: u32 = 0x80;
const CO_ASYNC_GENERATOR: u32 = 0x200;

/// Check if a function is a generator function (contains yield).
///
/// OPTIMIZATION: Uses __code__.co_flags directly instead of inspect.isgeneratorfunction()
/// which is significantly faster.
fn is_generator_function(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<bool> {
    // Fast path: check co_flags directly
    if let Ok(code) = value.getattr("__code__") {
        if let Ok(flags) = code.getattr("co_flags") {
            let flags: u32 = flags.extract()?;
            return Ok((flags & CO_GENERATOR) != 0);
        }
    }

    // Fallback for edge cases
    let inspect = py.import("inspect")?;
    let is_gen = inspect.call_method1("isgeneratorfunction", (value,))?;
    is_gen.is_truthy()
}

/// Check if a function is an async coroutine function.
///
/// OPTIMIZATION: Uses __code__.co_flags directly instead of inspect.iscoroutinefunction()
/// which is significantly faster.
fn is_async_function(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<bool> {
    // Fast path: check co_flags directly
    if let Ok(code) = value.getattr("__code__") {
        if let Ok(flags) = code.getattr("co_flags") {
            let flags: u32 = flags.extract()?;
            return Ok((flags & CO_COROUTINE) != 0);
        }
    }

    // Fallback for edge cases
    let inspect = py.import("inspect")?;
    let is_coro = inspect.call_method1("iscoroutinefunction", (value,))?;
    is_coro.is_truthy()
}

/// Check if a function is an async generator function (contains async + yield).
///
/// OPTIMIZATION: Uses __code__.co_flags directly instead of inspect.isasyncgenfunction()
/// which is significantly faster.
fn is_async_generator_function(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<bool> {
    // Fast path: check co_flags directly
    if let Ok(code) = value.getattr("__code__") {
        if let Ok(flags) = code.getattr("co_flags") {
            let flags: u32 = flags.extract()?;
            return Ok((flags & CO_ASYNC_GENERATOR) != 0);
        }
    }

    // Fallback for edge cases
    let inspect = py.import("inspect")?;
    let is_async_gen = inspect.call_method1("isasyncgenfunction", (value,))?;
    is_async_gen.is_truthy()
}

/// Extract the scope of a fixture, defaulting to "function" if not specified.
fn extract_fixture_scope(value: &Bound<'_, PyAny>) -> PyResult<FixtureScope> {
    match string_attribute(value, "__rustest_fixture_scope__")? {
        Some(scope_str) => FixtureScope::from_str(&scope_str).map_err(invalid_test_definition),
        None => Ok(FixtureScope::default()),
    }
}

/// Extract the autouse flag of a fixture, defaulting to false if not specified.
fn extract_fixture_autouse(value: &Bound<'_, PyAny>) -> PyResult<bool> {
    match value.getattr("__rustest_fixture_autouse__") {
        Ok(flag) => flag.is_truthy(),
        Err(_) => Ok(false),
    }
}

/// Extract the fixture name from __rustest_fixture_name__ attribute,
/// falling back to the provided default name if not specified.
fn extract_fixture_name(value: &Bound<'_, PyAny>, default_name: &str) -> PyResult<String> {
    match string_attribute(value, "__rustest_fixture_name__")? {
        Some(name) => Ok(name),
        None => Ok(default_name.to_string()),
    }
}

/// Extract fixture parametrization values, if any.
fn extract_fixture_params(value: &Bound<'_, PyAny>) -> PyResult<Option<Vec<FixtureParam>>> {
    let Ok(attr) = value.getattr("__rustest_fixture_params__") else {
        return Ok(None);
    };

    let sequence: Bound<'_, PySequence> = attr.cast_into()?;
    let mut params = Vec::new();

    for element in sequence.try_iter()? {
        let element = element?;
        let param_dict: Bound<'_, PyDict> = element.cast_into()?;

        let id = param_dict
            .get_item("id")?
            .ok_or_else(|| invalid_test_definition("Missing id in fixture param"))?;
        let id: String = id.extract()?;

        let value = param_dict
            .get_item("value")?
            .ok_or_else(|| invalid_test_definition("Missing value in fixture param"))?;

        params.push(FixtureParam::new(id, value.unbind()));
    }

    if params.is_empty() {
        Ok(None)
    } else {
        Ok(Some(params))
    }
}

/// Extract a string attribute from the object, if present.
fn string_attribute(value: &Bound<'_, PyAny>, attr: &str) -> PyResult<Option<String>> {
    match value.getattr(attr) {
        Ok(obj) => {
            if obj.is_none() {
                Ok(None)
            } else {
                Ok(Some(obj.extract()?))
            }
        }
        Err(_) => Ok(None),
    }
}

/// Check if a test function uses @patch decorator from unittest.mock.
///
/// Returns a skip reason if @patch is detected, None otherwise.
/// This check runs in all modes (not just pytest-compat) to prevent
/// confusing "Unknown fixture" errors.
fn check_for_patch_decorator(
    _py: Python<'_>,
    func: &Bound<'_, PyAny>,
    _pytest_compat: bool,
) -> PyResult<Option<String>> {
    // Check if the function has __wrapped__ attribute (indicates decorators)
    // The @patch decorator from unittest.mock wraps functions
    let mut current = func.clone();
    let mut depth = 0;
    const MAX_DEPTH: usize = 10; // Prevent infinite loops

    while depth < MAX_DEPTH {
        // Check if this is a patch object by looking for common patch attributes
        if let Ok(patch_attribute) = current.getattr("attribute") {
            // Has 'attribute' - likely a patch object
            if let Ok(target) = current.getattr("target") {
                // Has both 'attribute' and 'target' - definitely a patch mock
                let _ = (patch_attribute, target); // Use variables
                return Ok(Some(
                    "@patch decorator not supported. Use monkeypatch fixture instead. \
                     See documentation for migration examples."
                        .to_string(),
                ));
            }
        }

        // Check for patchings attribute (used by unittest.mock)
        if let Ok(patchings) = current.getattr("patchings") {
            // Check if patchings is a non-empty list
            if let Ok(list) = patchings.cast_into::<PyList>() {
                if !list.is_empty() {
                    return Ok(Some(
                        "@patch decorator not supported. Use monkeypatch fixture instead. \
                         See documentation for migration examples."
                            .to_string(),
                    ));
                }
            }
        }

        // Try to unwrap to next level
        match current.getattr("__wrapped__") {
            Ok(wrapped) => {
                current = wrapped;
                depth += 1;
            }
            Err(_) => break,
        }
    }

    Ok(None)
}

/// Check if a test function has @pytest.mark.skip decorator.
///
/// Returns a skip reason if @pytest.mark.skip is detected, None otherwise.
/// This handles the case where tests use pytest marks directly without
/// --pytest-compat mode.
fn check_for_pytest_skip_mark(
    _py: Python<'_>,
    func: &Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    // Check for pytestmark attribute (set by pytest decorators)
    let Ok(pytestmark) = func.getattr("pytestmark") else {
        return Ok(None);
    };

    // pytestmark can be a single mark or a list of marks
    let marks: Vec<Bound<'_, PyAny>> = if pytestmark.is_instance_of::<PyList>() {
        pytestmark.extract()?
    } else {
        vec![pytestmark]
    };

    // Check each mark
    for mark in marks {
        // Get mark name
        if let Ok(name) = mark.getattr("name") {
            if let Ok(name_str) = name.extract::<String>() {
                if name_str == "skip" {
                    // Extract reason from kwargs
                    if let Ok(kwargs) = mark.getattr("kwargs") {
                        if let Ok(reason) = kwargs.get_item("reason") {
                            if let Ok(reason_str) = reason.extract::<String>() {
                                return Ok(Some(reason_str));
                            }
                        }
                    }
                    // No reason provided, use default
                    return Ok(Some("Skipped via pytest.mark.skip".to_string()));
                }
            }
        }
    }

    Ok(None)
}

/// Extract the parameter names from a Python callable.
///
/// OPTIMIZATION: Uses __code__.co_varnames directly instead of inspect.signature()
/// which is significantly faster. Falls back to inspect.signature() for edge cases
/// like built-in functions or wrapped callables that don't have __code__.
fn extract_parameters(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<Vec<String>> {
    // Fast path: try to get parameters directly from __code__ object
    // This is much faster than inspect.signature() for regular Python functions
    if let Ok(code) = value.getattr("__code__") {
        if let (Ok(varnames), Ok(argcount)) =
            (code.getattr("co_varnames"), code.getattr("co_argcount"))
        {
            let argcount: usize = argcount.extract()?;
            let varnames_tuple: Bound<'_, PyTuple> = varnames.cast_into()?;
            let mut names = Vec::with_capacity(argcount);
            for i in 0..argcount {
                if let Ok(name) = varnames_tuple.get_item(i) {
                    names.push(name.extract()?);
                }
            }
            return Ok(names);
        }
    }

    // Fallback: use inspect.signature() for edge cases (built-ins, C extensions, etc.)
    let inspect = py.import("inspect")?;
    let signature = inspect.call_method1("signature", (value,))?;
    let params = signature.getattr("parameters")?;
    let mut names = Vec::new();
    for key in params.call_method0("keys")?.try_iter()? {
        let key = key?;
        names.push(key.extract()?);
    }
    Ok(names)
}

/// Collect parameterisation information attached to a test function.
fn collect_parametrization(
    _py: Python<'_>,
    value: &Bound<'_, PyAny>,
) -> PyResult<Vec<(String, ParameterMap)>> {
    let mut parametrized = Vec::new();
    let Ok(attr) = value.getattr("__rustest_parametrization__") else {
        return Ok(parametrized);
    };
    let sequence: Bound<'_, PySequence> = attr.cast_into()?;
    for element in sequence.try_iter()? {
        let element = element?;
        let case: Bound<'_, PyDict> = element.cast_into()?;
        let case_id = case
            .get_item("id")?
            .ok_or_else(|| invalid_test_definition("Missing id in parametrization metadata"))?;
        let case_id: String = case_id.extract()?;
        let values = case
            .get_item("values")?
            .ok_or_else(|| invalid_test_definition("Missing values in parametrization metadata"))?;
        let values: Bound<'_, PyDict> = values.cast_into()?;
        let mut parameters = ParameterMap::new();
        for (key, value) in values.iter() {
            let key: String = key.extract()?;
            parameters.insert(key, value.unbind());
        }
        parametrized.push((case_id, parameters));
    }
    Ok(parametrized)
}

/// Extract the list of indirect parameters from a test function.
/// Returns parameter names that should be resolved as fixture references.
fn extract_indirect_params(value: &Bound<'_, PyAny>) -> PyResult<Vec<String>> {
    let Ok(attr) = value.getattr("__rustest_parametrization_indirect__") else {
        return Ok(Vec::new());
    };
    let sequence: Bound<'_, PySequence> = attr.cast_into()?;
    let mut indirect_params = Vec::new();
    for element in sequence.try_iter()? {
        let element = element?;
        let param_name: String = element.extract()?;
        indirect_params.push(param_name);
    }
    Ok(indirect_params)
}

/// Collect mark information attached to a test function.
fn collect_marks(value: &Bound<'_, PyAny>) -> PyResult<Vec<Mark>> {
    let Ok(attr) = value.getattr("__rustest_marks__") else {
        return Ok(Vec::new());
    };
    let sequence: Bound<'_, PySequence> = attr.cast_into()?;
    let mut marks = Vec::new();
    for element in sequence.try_iter()? {
        let element = element?;
        let mark_dict: Bound<'_, PyDict> = element.cast_into()?;

        // Extract name
        let name = mark_dict
            .get_item("name")?
            .ok_or_else(|| invalid_test_definition("Missing name in mark metadata"))?;
        let name: String = name.extract()?;

        // Extract args (default to empty list if not present)
        // Convert tuple to list if necessary, since Python decorators store args as tuples
        let args_raw = mark_dict
            .get_item("args")?
            .unwrap_or_else(|| PyList::empty(value.py()).into_any());
        let args: Py<PyList> = if args_raw.is_instance_of::<pyo3::types::PyTuple>() {
            let tuple: Bound<'_, pyo3::types::PyTuple> = args_raw.cast_into()?;
            PyList::new(value.py(), tuple.iter())?.unbind()
        } else {
            args_raw.extract()?
        };

        // Extract kwargs (default to empty dict if not present)
        let kwargs = mark_dict
            .get_item("kwargs")?
            .unwrap_or_else(|| PyDict::new(value.py()).into_any());
        let kwargs: Py<PyDict> = kwargs.extract()?;

        marks.push(Mark::new(name, args, kwargs));
    }
    Ok(marks)
}

/// Load parent __init__.py files to ensure package structure is initialized.
/// This is necessary for relative imports to work correctly.
fn ensure_parent_packages_loaded(py: Python<'_>, path: &Path) -> PyResult<()> {
    let mut parent = path.parent();
    let mut package_path = Vec::new();

    // Collect all parent directories with __init__.py files
    while let Some(dir) = parent {
        let init_file = dir.join("__init__.py");
        if init_file.exists() {
            if let Some(name) = dir.file_name().and_then(|value| value.to_str()) {
                package_path.push((name.to_string(), init_file));
            }
            parent = dir.parent();
        } else {
            break;
        }
    }

    // Reverse to load from top-level package down to nearest parent
    package_path.reverse();

    let sys = py.import("sys")?;
    let modules: Bound<'_, PyDict> = sys.getattr("modules")?.cast_into()?;
    let importlib = py.import("importlib.util")?;

    // Build and load each parent package
    let mut current_package = Vec::new();
    for (name, init_path) in package_path {
        current_package.push(name.clone());
        let package_name = current_package.join(".");

        // Check if package is already loaded
        if modules.contains(&package_name)? {
            continue;
        }

        // Load the __init__.py file for this package
        let path_str = init_path.to_string_lossy();
        let spec = importlib.call_method1(
            "spec_from_file_location",
            (package_name.as_str(), path_str.as_ref()),
        )?;
        let loader = spec.getattr("loader")?;

        if !loader.is_none() {
            let module = importlib.call_method1("module_from_spec", (&spec,))?;

            // Set __package__ for the __init__.py module
            if current_package.len() > 1 {
                let parent_package = current_package[..current_package.len() - 1].join(".");
                module.setattr("__package__", parent_package)?;
            } else {
                module.setattr("__package__", package_name.as_str())?;
            }

            // Add to sys.modules before executing
            modules.set_item(package_name.as_str(), &module)?;

            // Execute the __init__.py file
            loader.call_method1("exec_module", (&module,))?;
        }
    }

    Ok(())
}

/// Load the Python module from disk.
fn load_python_module<'py>(
    py: Python<'py>,
    path: &Path,
    module_name: &str,
    package: Option<&str>,
) -> PyResult<Bound<'py, PyAny>> {
    // Ensure parent packages are loaded for relative imports to work
    ensure_parent_packages_loaded(py, path)?;

    let importlib = py.import("importlib.util")?;
    let path_str = path.to_string_lossy();
    let spec =
        importlib.call_method1("spec_from_file_location", (module_name, path_str.as_ref()))?;
    let loader = spec.getattr("loader")?;
    if loader.is_none() {
        return Err(invalid_test_definition(format!(
            "Unable to load module for {}",
            path.display()
        )));
    }
    let module = importlib.call_method1("module_from_spec", (&spec,))?;
    if let Some(package_name) = package {
        module.setattr("__package__", package_name)?;
    }
    let sys = py.import("sys")?;
    let modules: Bound<'_, PyDict> = sys.getattr("modules")?.cast_into()?;
    modules.set_item(module_name, &module)?;
    loader.call_method1("exec_module", (&module,))?;
    Ok(module)
}

/// Compute a stable module and package name for the test file.
fn infer_module_names(path: &Path, fallback_id: usize) -> (String, Option<String>) {
    let stem = path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("rustest_module");

    let mut components = vec![stem.to_string()];
    let mut parent = path.parent();

    while let Some(dir) = parent {
        let init_file = dir.join("__init__.py");
        if init_file.exists() {
            if let Some(name) = dir.file_name().and_then(|value| value.to_str()) {
                components.push(name.to_string());
            }
            parent = dir.parent();
        } else {
            break;
        }
    }

    if components.len() == 1 {
        // Fall back to a generated name when no package structure exists.
        return (format!("rustest_module_{}", fallback_id), None);
    }

    components.reverse();
    let package_components = components[..components.len() - 1].to_vec();
    let module_name = components.join(".");
    let package_name = if package_components.is_empty() {
        None
    } else {
        Some(package_components.join("."))
    };

    (module_name, package_name)
}

/// Apply last-failed filtering to the collected test modules.
/// This modifies the modules in place, filtering or reordering tests based on the last failed cache.
fn apply_last_failed_filter(
    modules: &mut Vec<TestModule>,
    config: &RunConfiguration,
) -> PyResult<()> {
    // Read the last failed test IDs from cache
    let failed_ids = cache::read_last_failed()?;

    // If the cache is empty and we're in OnlyFailed mode, return empty modules
    if failed_ids.is_empty() && config.last_failed_mode == LastFailedMode::OnlyFailed {
        modules.clear();
        return Ok(());
    }

    // Process each module
    for module in modules.iter_mut() {
        let mut failed_tests = Vec::new();
        let mut other_tests = Vec::new();

        // Separate tests into failed and non-failed
        for test in module.tests.drain(..) {
            let test_id = test.unique_id();
            if failed_ids.contains(&test_id) {
                failed_tests.push(test);
            } else {
                other_tests.push(test);
            }
        }

        // Apply the filtering/ordering based on mode
        match config.last_failed_mode {
            LastFailedMode::None => {
                // This should not happen as we check this before calling this function
                module.tests = failed_tests;
                module.tests.extend(other_tests);
            }
            LastFailedMode::OnlyFailed => {
                // Only include failed tests
                module.tests = failed_tests;
            }
            LastFailedMode::FailedFirst => {
                // Include failed tests first, then other tests
                module.tests = failed_tests;
                module.tests.extend(other_tests);
            }
        }
    }

    // Remove modules that have no tests (only relevant in OnlyFailed mode)
    modules.retain(|m| !m.tests.is_empty());

    Ok(())
}
