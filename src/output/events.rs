//! Event types for streaming test execution updates to Python consumers
//!
//! These events are emitted during test execution and can be consumed by
//! multiple renderers (terminal, VS Code, JSON export, web UI, etc.)

use pyo3::prelude::*;
use pyo3::Py;

/// Event emitted when a test file starts execution
#[pyclass]
#[derive(Clone, Debug)]
pub struct FileStartedEvent {
    /// Relative path to the test file
    #[pyo3(get)]
    pub file_path: String,

    /// Total number of tests in this file
    #[pyo3(get)]
    pub total_tests: usize,

    /// Unix timestamp when file started
    #[pyo3(get)]
    pub timestamp: f64,
}

#[pymethods]
impl FileStartedEvent {
    fn __repr__(&self) -> String {
        format!(
            "FileStartedEvent(file_path='{}', total_tests={})",
            self.file_path, self.total_tests
        )
    }
}

/// Event emitted when an individual test completes
#[pyclass]
#[derive(Clone, Debug)]
pub struct TestCompletedEvent {
    /// Unique test identifier (e.g., "tests/test_foo.py::test_bar")
    #[pyo3(get)]
    pub test_id: String,

    /// File path (e.g., "tests/test_foo.py")
    #[pyo3(get)]
    pub file_path: String,

    /// Test name (e.g., "test_bar")
    #[pyo3(get)]
    pub test_name: String,

    /// Test status: "passed", "failed", "skipped"
    #[pyo3(get)]
    pub status: String,

    /// Test duration in seconds
    #[pyo3(get)]
    pub duration: f64,

    /// Optional error message (for failures)
    #[pyo3(get)]
    pub message: Option<String>,

    /// Unix timestamp when test completed
    #[pyo3(get)]
    pub timestamp: f64,
}

#[pymethods]
impl TestCompletedEvent {
    fn __repr__(&self) -> String {
        format!(
            "TestCompletedEvent(test_id='{}', status='{}')",
            self.test_id, self.status
        )
    }
}

/// Event emitted when a test file completes execution
#[pyclass]
#[derive(Clone, Debug)]
pub struct FileCompletedEvent {
    /// Relative path to the test file
    #[pyo3(get)]
    pub file_path: String,

    /// Duration in seconds for entire file
    #[pyo3(get)]
    pub duration: f64,

    /// Number of tests that passed
    #[pyo3(get)]
    pub passed: usize,

    /// Number of tests that failed
    #[pyo3(get)]
    pub failed: usize,

    /// Number of tests that were skipped
    #[pyo3(get)]
    pub skipped: usize,

    /// Unix timestamp when file completed
    #[pyo3(get)]
    pub timestamp: f64,
}

#[pymethods]
impl FileCompletedEvent {
    fn __repr__(&self) -> String {
        format!(
            "FileCompletedEvent(file_path='{}', passed={}, failed={}, skipped={})",
            self.file_path, self.passed, self.failed, self.skipped
        )
    }
}

/// Event emitted when test suite starts
#[pyclass]
#[derive(Clone, Debug)]
pub struct SuiteStartedEvent {
    /// Total number of files to execute
    #[pyo3(get)]
    pub total_files: usize,

    /// Total number of tests to execute
    #[pyo3(get)]
    pub total_tests: usize,

    /// Unix timestamp when suite started
    #[pyo3(get)]
    pub timestamp: f64,
}

#[pymethods]
impl SuiteStartedEvent {
    fn __repr__(&self) -> String {
        format!(
            "SuiteStartedEvent(total_files={}, total_tests={})",
            self.total_files, self.total_tests
        )
    }
}

/// Event emitted when entire test suite completes
#[pyclass]
#[derive(Clone, Debug)]
pub struct SuiteCompletedEvent {
    /// Total number of tests executed
    #[pyo3(get)]
    pub total: usize,

    /// Number of tests that passed
    #[pyo3(get)]
    pub passed: usize,

    /// Number of tests that failed
    #[pyo3(get)]
    pub failed: usize,

    /// Number of tests that were skipped
    #[pyo3(get)]
    pub skipped: usize,

    /// Number of collection errors
    #[pyo3(get)]
    pub errors: usize,

    /// Total duration in seconds
    #[pyo3(get)]
    pub duration: f64,

    /// Unix timestamp when suite completed
    #[pyo3(get)]
    pub timestamp: f64,
}

#[pymethods]
impl SuiteCompletedEvent {
    fn __repr__(&self) -> String {
        format!(
            "SuiteCompletedEvent(total={}, passed={}, failed={}, skipped={})",
            self.total, self.passed, self.failed, self.skipped
        )
    }
}

/// Event emitted when a collection error occurs
#[pyclass]
#[derive(Clone, Debug)]
pub struct CollectionErrorEvent {
    /// Path where error occurred
    #[pyo3(get)]
    pub path: String,

    /// Error message
    #[pyo3(get)]
    pub message: String,

    /// Unix timestamp when error occurred
    #[pyo3(get)]
    pub timestamp: f64,
}

#[pymethods]
impl CollectionErrorEvent {
    fn __repr__(&self) -> String {
        format!("CollectionErrorEvent(path='{}')", self.path)
    }
}

/// Event emitted when test collection starts
#[pyclass]
#[derive(Clone, Debug)]
pub struct CollectionStartedEvent {
    /// Unix timestamp when collection started
    #[pyo3(get)]
    pub timestamp: f64,
}

#[pymethods]
impl CollectionStartedEvent {
    fn __repr__(&self) -> String {
        "CollectionStartedEvent()".to_string()
    }
}

/// Event emitted when a file is collected during test discovery
#[pyclass]
#[derive(Clone, Debug)]
pub struct CollectionProgressEvent {
    /// Path to the file being collected
    #[pyo3(get)]
    pub file_path: String,

    /// Number of tests collected from this file
    #[pyo3(get)]
    pub tests_collected: usize,

    /// Total files collected so far
    #[pyo3(get)]
    pub files_collected: usize,

    /// Unix timestamp when file was collected
    #[pyo3(get)]
    pub timestamp: f64,
}

#[pymethods]
impl CollectionProgressEvent {
    fn __repr__(&self) -> String {
        format!(
            "CollectionProgressEvent(file_path='{}', tests_collected={}, files_collected={})",
            self.file_path, self.tests_collected, self.files_collected
        )
    }
}

/// Event emitted when test collection completes
#[pyclass]
#[derive(Clone, Debug)]
pub struct CollectionCompletedEvent {
    /// Total number of test files collected
    #[pyo3(get)]
    pub total_files: usize,

    /// Total number of tests collected
    #[pyo3(get)]
    pub total_tests: usize,

    /// Duration of collection in seconds
    #[pyo3(get)]
    pub duration: f64,

    /// Unix timestamp when collection completed
    #[pyo3(get)]
    pub timestamp: f64,
}

#[pymethods]
impl CollectionCompletedEvent {
    fn __repr__(&self) -> String {
        format!(
            "CollectionCompletedEvent(total_files={}, total_tests={})",
            self.total_files, self.total_tests
        )
    }
}

/// Helper to get current Unix timestamp
pub fn current_timestamp() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs_f64()
}

/// Emit a CollectionStartedEvent to the callback
pub fn emit_collection_started(callback: &Py<PyAny>) {
    let event = CollectionStartedEvent {
        timestamp: current_timestamp(),
    };
    Python::attach(|py| {
        if let Err(e) = callback.call1(py, (Py::new(py, event).unwrap(),)) {
            eprintln!("Error in event callback: {}", e);
        }
    });
}

/// Emit a CollectionProgressEvent to the callback
pub fn emit_collection_progress(
    callback: &Py<PyAny>,
    file_path: String,
    tests_collected: usize,
    files_collected: usize,
) {
    let event = CollectionProgressEvent {
        file_path,
        tests_collected,
        files_collected,
        timestamp: current_timestamp(),
    };
    Python::attach(|py| {
        if let Err(e) = callback.call1(py, (Py::new(py, event).unwrap(),)) {
            eprintln!("Error in event callback: {}", e);
        }
    });
}

/// Emit a CollectionCompletedEvent to the callback
pub fn emit_collection_completed(
    callback: &Py<PyAny>,
    total_files: usize,
    total_tests: usize,
    duration: f64,
) {
    let event = CollectionCompletedEvent {
        total_files,
        total_tests,
        duration,
        timestamp: current_timestamp(),
    };
    Python::attach(|py| {
        if let Err(e) = callback.call1(py, (Py::new(py, event).unwrap(),)) {
            eprintln!("Error in event callback: {}", e);
        }
    });
}
