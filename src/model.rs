//! Shared data structures used across the discovery and execution pipelines.
//!
//! The majority of the structs defined here are small value objects carrying
//! data between the different subsystems.  By keeping them in their own module
//! we ensure that the control flow is easy to follow for developers who may not
//! have much Rust experience yet.

use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};

use indexmap::IndexMap;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

/// Type alias to make signatures easier to read: parameter values are stored in
/// an ordered map so that we can preserve the parameter order when constructing
/// the argument list for a test function.
pub type ParameterMap = IndexMap<String, Py<PyAny>>;

/// The scope of a fixture determines when it is created and destroyed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Default)]
pub enum FixtureScope {
    /// Created once per test function (default).
    #[default]
    Function,
    /// Shared across all test methods in a class.
    Class,
    /// Shared across all tests in a module.
    Module,
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
    pub autouse: bool,
}

impl Fixture {
    pub fn new(
        name: String,
        callable: Py<PyAny>,
        parameters: Vec<String>,
        scope: FixtureScope,
        is_generator: bool,
        autouse: bool,
    ) -> Self {
        Self {
            name,
            callable,
            parameters,
            scope,
            is_generator,
            autouse,
        }
    }

    /// Clone the fixture with a Python context.
    pub fn clone_with_py(&self, py: Python<'_>) -> Self {
        Self {
            name: self.name.clone(),
            callable: self.callable.clone_ref(py),
            parameters: self.parameters.clone(),
            scope: self.scope,
            is_generator: self.is_generator,
            autouse: self.autouse,
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
#[derive(Clone, Debug)]
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
}

impl PyRunReport {
    pub fn new(
        total: usize,
        passed: usize,
        failed: usize,
        skipped: usize,
        duration: f64,
        results: Vec<PyTestResult>,
    ) -> Self {
        Self {
            total,
            passed,
            failed,
            skipped,
            duration,
            results,
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
