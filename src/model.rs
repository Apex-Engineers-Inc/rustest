//! Shared data structures used across the discovery and execution pipelines.
//!
//! The majority of the structs defined here are small value objects carrying
//! data between the different subsystems.  By keeping them in their own module
//! we ensure that the control flow is easy to follow for developers who may not
//! have much Rust experience yet.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use indexmap::IndexMap;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

/// Type alias to make signatures easier to read: parameter values are stored in
/// an ordered map so that we can preserve the parameter order when constructing
/// the argument list for a test function.
pub type ParameterMap = IndexMap<String, Py<PyAny>>;

/// Represents a single parameter value for a parametrized fixture.
#[derive(Debug)]
pub struct FixtureParam {
    pub id: String,
    pub value: Py<PyAny>,
}

impl FixtureParam {
    pub fn new(id: String, value: Py<PyAny>) -> Self {
        Self { id, value }
    }

    /// Clone the param with a Python context.
    pub fn clone_with_py(&self, py: Python<'_>) -> Self {
        Self {
            id: self.id.clone(),
            value: self.value.clone_ref(py),
        }
    }
}

/// The scope of a fixture determines when it is created and destroyed.
///
/// The order of variants matters for the derived `Ord` implementation:
/// Function < Class < Module < Package < Session
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Default)]
pub enum FixtureScope {
    /// Created once per test function (default).
    #[default]
    Function,
    /// Shared across all test methods in a class.
    Class,
    /// Shared across all tests in a module.
    Module,
    /// Shared across all tests in a package (directory with __init__.py).
    Package,
    /// Shared across all tests in the entire session.
    Session,
}

impl FixtureScope {
    /// Parse a scope string from Python.
    pub fn from_str(s: &str) -> Result<Self, String> {
        match s {
            "function" => Ok(FixtureScope::Function),
            "class" => Ok(FixtureScope::Class),
            "module" => Ok(FixtureScope::Module),
            "package" => Ok(FixtureScope::Package),
            "session" => Ok(FixtureScope::Session),
            _ => Err(format!("Invalid fixture scope: {}", s)),
        }
    }
}

/// Metadata describing a mark applied to a test function.
pub struct Mark {
    pub name: String,
    pub args: Py<PyList>,
    pub kwargs: Py<PyDict>,
}

impl Clone for Mark {
    fn clone(&self) -> Self {
        Python::attach(|py| self.clone_with_py(py))
    }
}

impl Mark {
    pub fn new(name: String, args: Py<PyList>, kwargs: Py<PyDict>) -> Self {
        Self { name, args, kwargs }
    }

    /// Clone the mark with a Python context.
    pub fn clone_with_py(&self, py: Python<'_>) -> Self {
        Self {
            name: self.name.clone(),
            args: self.args.clone_ref(py),
            kwargs: self.kwargs.clone_ref(py),
        }
    }

    /// Check if this mark has the given name.
    pub fn is_named(&self, name: &str) -> bool {
        self.name == name
    }

    /// Get a string argument from the mark args by position.
    #[allow(dead_code)]
    pub fn get_string_arg(&self, py: Python<'_>, index: usize) -> Option<String> {
        self.args
            .bind(py)
            .get_item(index)
            .ok()
            .and_then(|item| item.extract().ok())
    }

    /// Get a keyword argument from the mark kwargs.
    #[allow(dead_code)]
    pub fn get_kwarg(&self, py: Python<'_>, key: &str) -> Option<Py<PyAny>> {
        self.kwargs
            .bind(py)
            .get_item(key)
            .ok()
            .flatten()
            .map(|item| item.unbind())
    }

    /// Get a boolean from kwargs with a default value.
    #[allow(dead_code)]
    pub fn get_bool_kwarg(&self, py: Python<'_>, key: &str, default: bool) -> bool {
        self.get_kwarg(py, key)
            .and_then(|val| val.extract(py).ok())
            .unwrap_or(default)
    }
}

/// Metadata describing a single fixture function.
pub struct Fixture {
    pub name: String,
    pub callable: Py<PyAny>,
    pub parameters: Vec<String>,
    pub scope: FixtureScope,
    pub is_generator: bool,
    pub is_async: bool,
    pub is_async_generator: bool,
    pub autouse: bool,
    /// Optional parametrization values for the fixture.
    pub params: Option<Vec<FixtureParam>>,
    /// Optional class name for class-based fixtures (to scope autouse fixtures correctly).
    pub class_name: Option<String>,
}

impl Fixture {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        name: String,
        callable: Py<PyAny>,
        parameters: Vec<String>,
        scope: FixtureScope,
        is_generator: bool,
        is_async: bool,
        is_async_generator: bool,
        autouse: bool,
        class_name: Option<String>,
    ) -> Self {
        Self {
            name,
            callable,
            parameters,
            scope,
            is_generator,
            is_async,
            is_async_generator,
            autouse,
            params: None,
            class_name,
        }
    }

    /// Create a fixture with parametrization.
    #[allow(clippy::too_many_arguments)]
    pub fn with_params(
        name: String,
        callable: Py<PyAny>,
        parameters: Vec<String>,
        scope: FixtureScope,
        is_generator: bool,
        is_async: bool,
        is_async_generator: bool,
        autouse: bool,
        params: Vec<FixtureParam>,
        class_name: Option<String>,
    ) -> Self {
        Self {
            name,
            callable,
            parameters,
            scope,
            is_generator,
            is_async,
            is_async_generator,
            autouse,
            params: Some(params),
            class_name,
        }
    }

    /// Check if this fixture is parametrized.
    #[allow(dead_code)]
    pub fn is_parametrized(&self) -> bool {
        self.params.is_some() && !self.params.as_ref().unwrap().is_empty()
    }

    /// Clone the fixture with a Python context.
    pub fn clone_with_py(&self, py: Python<'_>) -> Self {
        Self {
            name: self.name.clone(),
            callable: self.callable.clone_ref(py),
            parameters: self.parameters.clone(),
            scope: self.scope,
            is_generator: self.is_generator,
            is_async: self.is_async,
            is_async_generator: self.is_async_generator,
            autouse: self.autouse,
            params: self
                .params
                .as_ref()
                .map(|p| p.iter().map(|fp| fp.clone_with_py(py)).collect()),
            class_name: self.class_name.clone(),
        }
    }
}

/// Metadata describing a single test case.
pub struct TestCase {
    #[allow(dead_code)]
    pub name: String,
    pub display_name: String,
    pub path: PathBuf,
    pub callable: Py<PyAny>,
    pub parameters: Vec<String>,
    pub parameter_values: ParameterMap,
    pub skip_reason: Option<String>,
    pub marks: Vec<Mark>,
    /// The class name if this test is part of a test class (for class-scoped fixtures).
    pub class_name: Option<String>,
    /// Fixture parameter indices for parametrized fixtures.
    /// Maps fixture name to the parameter index to use.
    pub fixture_param_indices: IndexMap<String, usize>,
    /// Parameters that should be resolved as fixture references (indirect parametrization).
    /// Contains the parameter names that are marked as indirect.
    pub indirect_params: Vec<String>,
}

impl TestCase {
    pub fn unique_id(&self) -> String {
        format!("{}::{}", self.path.display(), self.display_name)
    }

    /// Find a mark by name.
    #[allow(dead_code)]
    pub fn find_mark(&self, name: &str) -> Option<&Mark> {
        self.marks.iter().find(|m| m.is_named(name))
    }

    /// Check if this test has a mark with the given name.
    #[allow(dead_code)]
    pub fn has_mark(&self, name: &str) -> bool {
        self.marks.iter().any(|m| m.is_named(name))
    }

    /// Get mark names as strings for reporting.
    pub fn mark_names(&self) -> Vec<String> {
        self.marks.iter().map(|m| m.name.clone()).collect()
    }
}

/// Collection of fixtures and test cases for a Python module.
pub struct TestModule {
    #[allow(dead_code)]
    pub path: PathBuf,
    pub fixtures: IndexMap<String, Fixture>,
    pub tests: Vec<TestCase>,
}

impl TestModule {
    pub fn new(path: PathBuf, fixtures: IndexMap<String, Fixture>, tests: Vec<TestCase>) -> Self {
        Self {
            path,
            fixtures,
            tests,
        }
    }
}

/// Mode for running last failed tests.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LastFailedMode {
    /// Don't filter based on last failed tests.
    None,
    /// Only run tests that failed in the last run.
    OnlyFailed,
    /// Run failed tests first, then all other tests.
    FailedFirst,
}

impl LastFailedMode {
    /// Parse from string (matches pytest's options).
    pub fn from_str(s: &str) -> Result<Self, String> {
        match s {
            "none" => Ok(LastFailedMode::None),
            "only" => Ok(LastFailedMode::OnlyFailed),
            "first" => Ok(LastFailedMode::FailedFirst),
            _ => Err(format!("Invalid last failed mode: {}", s)),
        }
    }
}

/// Configuration coming from Python.
#[derive(Debug)]
pub struct RunConfiguration {
    pub pattern: Option<String>,
    pub mark_expr: Option<String>,
    #[allow(dead_code)]
    pub worker_count: usize,
    pub capture_output: bool,
    pub enable_codeblocks: bool,
    pub last_failed_mode: LastFailedMode,
    pub fail_fast: bool,
    pub pytest_compat: bool,
    pub verbose: bool,
    pub ascii: bool,
    pub no_color: bool,
    pub event_callback: Option<pyo3::Py<pyo3::PyAny>>,
}

impl Clone for RunConfiguration {
    fn clone(&self) -> Self {
        Self {
            pattern: self.pattern.clone(),
            mark_expr: self.mark_expr.clone(),
            worker_count: self.worker_count,
            capture_output: self.capture_output,
            enable_codeblocks: self.enable_codeblocks,
            last_failed_mode: self.last_failed_mode,
            fail_fast: self.fail_fast,
            pytest_compat: self.pytest_compat,
            verbose: self.verbose,
            ascii: self.ascii,
            no_color: self.no_color,
            event_callback: self
                .event_callback
                .as_ref()
                .map(|cb| pyo3::Python::attach(|py| cb.clone_ref(py))),
        }
    }
}

impl RunConfiguration {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        pattern: Option<String>,
        mark_expr: Option<String>,
        workers: Option<usize>,
        capture_output: bool,
        enable_codeblocks: bool,
        last_failed_mode: LastFailedMode,
        fail_fast: bool,
        pytest_compat: bool,
        verbose: bool,
        ascii: bool,
        no_color: bool,
        event_callback: Option<pyo3::Py<pyo3::PyAny>>,
    ) -> Self {
        let worker_count = workers.unwrap_or_else(|| rayon::current_num_threads().max(1));
        Self {
            pattern,
            mark_expr,
            worker_count,
            capture_output,
            enable_codeblocks,
            last_failed_mode,
            fail_fast,
            pytest_compat,
            verbose,
            ascii,
            no_color,
            event_callback,
        }
    }
}

/// Public representation of the run summary exposed to Python.
#[pyclass(module = "rustest.rust")]
pub struct PyRunReport {
    #[pyo3(get)]
    pub total: usize,
    #[pyo3(get)]
    pub passed: usize,
    #[pyo3(get)]
    pub failed: usize,
    #[pyo3(get)]
    pub skipped: usize,
    #[pyo3(get)]
    pub duration: f64,
    #[pyo3(get)]
    pub results: Vec<PyTestResult>,
    #[pyo3(get)]
    pub collection_errors: Vec<CollectionError>,
}

impl PyRunReport {
    pub fn new(
        total: usize,
        passed: usize,
        failed: usize,
        skipped: usize,
        duration: f64,
        results: Vec<PyTestResult>,
        collection_errors: Vec<CollectionError>,
    ) -> Self {
        Self {
            total,
            passed,
            failed,
            skipped,
            duration,
            results,
            collection_errors,
        }
    }
}

/// Individual test result exposed to Python callers.
#[pyclass(module = "rustest.rust")]
#[derive(Clone)]
pub struct PyTestResult {
    #[pyo3(get)]
    pub name: String,
    #[pyo3(get)]
    pub path: String,
    #[pyo3(get)]
    pub status: String,
    #[pyo3(get)]
    pub duration: f64,
    #[pyo3(get)]
    pub message: Option<String>,
    #[pyo3(get)]
    pub stdout: Option<String>,
    #[pyo3(get)]
    pub stderr: Option<String>,
    #[pyo3(get)]
    pub marks: Vec<String>,
}

impl PyTestResult {
    /// Get the unique identifier for this test result.
    pub fn unique_id(&self) -> String {
        format!("{}::{}", self.path, self.name)
    }

    pub fn passed(
        name: String,
        path: String,
        duration: f64,
        stdout: Option<String>,
        stderr: Option<String>,
        marks: Vec<String>,
    ) -> Self {
        Self {
            name,
            path,
            status: "passed".to_string(),
            duration,
            message: None,
            stdout,
            stderr,
            marks,
        }
    }

    pub fn skipped(
        name: String,
        path: String,
        duration: f64,
        reason: String,
        marks: Vec<String>,
    ) -> Self {
        Self {
            name,
            path,
            status: "skipped".to_string(),
            duration,
            message: Some(reason),
            stdout: None,
            stderr: None,
            marks,
        }
    }

    pub fn failed(
        name: String,
        path: String,
        duration: f64,
        message: String,
        stdout: Option<String>,
        stderr: Option<String>,
        marks: Vec<String>,
    ) -> Self {
        Self {
            name,
            path,
            status: "failed".to_string(),
            duration,
            message: Some(message),
            stdout,
            stderr,
            marks,
        }
    }
}

/// Represents an error that occurred during test collection.
///
/// This is used to report errors that prevented tests from being collected,
/// such as syntax errors in Python files or markdown code blocks. Unlike test
/// failures, collection errors prevent the test from even being defined.
#[pyclass(module = "rustest.rust")]
#[derive(Clone)]
pub struct CollectionError {
    #[pyo3(get)]
    pub path: String,
    #[pyo3(get)]
    pub message: String,
}

impl CollectionError {
    pub fn new(path: String, message: String) -> Self {
        Self { path, message }
    }
}

/// Light-weight helper used to generate monotonically increasing identifiers
/// for dynamically generated module names.
#[derive(Default)]
pub struct ModuleIdGenerator {
    counter: AtomicUsize,
}

impl ModuleIdGenerator {
    pub fn next(&self) -> usize {
        self.counter.fetch_add(1, Ordering::Relaxed)
    }
}

/// Convenience wrapper that converts a raw Python exception into a structured
/// message.  We expose this via [`PyValueError`] for ergonomics on the Python
/// side.
pub fn invalid_test_definition(message: impl Into<String>) -> PyErr {
    PyValueError::new_err(message.into())
}

/// Convert an absolute path to a relative path from the current working directory.
///
/// This makes the output more readable by showing paths relative to the project root
/// instead of full absolute paths like `\\?\C:\Users\...`.
pub fn to_relative_path(path: &Path) -> String {
    // Normalize the path - handle Windows extended-length path prefix (\\?\)
    let path_str = path.to_string_lossy();
    let normalized_path = if let Some(stripped) = path_str.strip_prefix(r"\\?\") {
        // Remove the \\?\ prefix for Windows extended-length paths
        PathBuf::from(stripped)
    } else {
        path.to_path_buf()
    };

    if let Ok(cwd) = std::env::current_dir() {
        // Also normalize the cwd for Windows
        let cwd_str = cwd.to_string_lossy();
        let normalized_cwd = if let Some(stripped) = cwd_str.strip_prefix(r"\\?\") {
            PathBuf::from(stripped)
        } else {
            cwd
        };

        if let Ok(relative) = normalized_path.strip_prefix(&normalized_cwd) {
            let relative_str = relative.to_string_lossy();
            if relative_str.is_empty() {
                return ".".to_string();
            }
            // Return without leading separator for cleaner display
            return relative_str.to_string();
        }
    }
    // Fallback to normalized path (without \\?\ prefix)
    normalized_path.to_string_lossy().to_string()
}
