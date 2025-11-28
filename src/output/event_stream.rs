//! Event stream renderer that emits events to Python consumers
//!
//! This renderer converts test execution events into Python objects
//! and calls a Python callback function. This allows Python code to
//! consume events and render them using rich, export to VS Code, etc.

use super::events::*;
use super::renderer::OutputRenderer;
use crate::model::{to_relative_path, CollectionError, PyTestResult, TestCase, TestModule};
use pyo3::prelude::*;
use std::time::Duration;

/// Renderer that emits events to a Python callback
pub struct EventStreamRenderer {
    /// Python callback function to invoke for each event
    callback: Option<Py<PyAny>>,
    /// Store collection errors to defer them
    collection_errors: Vec<CollectionError>,
}

impl EventStreamRenderer {
    /// Create a new event stream renderer
    pub fn new(callback: Option<Py<PyAny>>) -> Self {
        Self {
            callback,
            collection_errors: Vec::new(),
        }
    }

    /// Emit an event to the Python callback
    ///
    /// Events are PyO3 classes, so they can be passed directly to Python
    fn emit_file_started(&self, event: FileStartedEvent) {
        if let Some(callback) = &self.callback {
            Python::attach(|py| {
                if let Err(e) = callback.call1(py, (Py::new(py, event).unwrap(),)) {
                    eprintln!("Error in event callback: {}", e);
                }
            });
        }
    }

    fn emit_test_completed(&self, event: TestCompletedEvent) {
        if let Some(callback) = &self.callback {
            Python::attach(|py| {
                if let Err(e) = callback.call1(py, (Py::new(py, event).unwrap(),)) {
                    eprintln!("Error in event callback: {}", e);
                }
            });
        }
    }

    fn emit_file_completed(&self, event: FileCompletedEvent) {
        if let Some(callback) = &self.callback {
            Python::attach(|py| {
                if let Err(e) = callback.call1(py, (Py::new(py, event).unwrap(),)) {
                    eprintln!("Error in event callback: {}", e);
                }
            });
        }
    }

    fn emit_suite_started(&self, event: SuiteStartedEvent) {
        if let Some(callback) = &self.callback {
            Python::attach(|py| {
                if let Err(e) = callback.call1(py, (Py::new(py, event).unwrap(),)) {
                    eprintln!("Error in event callback: {}", e);
                }
            });
        }
    }

    fn emit_suite_completed(&self, event: SuiteCompletedEvent) {
        if let Some(callback) = &self.callback {
            Python::attach(|py| {
                if let Err(e) = callback.call1(py, (Py::new(py, event).unwrap(),)) {
                    eprintln!("Error in event callback: {}", e);
                }
            });
        }
    }

    fn emit_collection_error(&self, event: CollectionErrorEvent) {
        if let Some(callback) = &self.callback {
            Python::attach(|py| {
                if let Err(e) = callback.call1(py, (Py::new(py, event).unwrap(),)) {
                    eprintln!("Error in event callback: {}", e);
                }
            });
        }
    }
}

impl OutputRenderer for EventStreamRenderer {
    fn collection_error(&mut self, error: &CollectionError) {
        // Store collection errors to emit at the end (like pytest)
        self.collection_errors.push(error.clone());

        // Also emit event immediately for real-time consumers
        let event = CollectionErrorEvent {
            path: error.path.clone(),
            message: error.message.clone(),
            timestamp: current_timestamp(),
        };
        self.emit_collection_error(event);
    }

    fn start_suite(&mut self, total_files: usize, total_tests: usize) {
        let event = SuiteStartedEvent {
            total_files,
            total_tests,
            timestamp: current_timestamp(),
        };
        self.emit_suite_started(event);
    }

    fn start_file(&mut self, module: &TestModule) {
        let event = FileStartedEvent {
            file_path: to_relative_path(&module.path),
            total_tests: module.tests.len(),
            timestamp: current_timestamp(),
        };
        self.emit_file_started(event);
    }

    fn start_test(&mut self, _test: &TestCase) {
        // Not emitting individual test start events for now
        // Can add TestStartedEvent later if needed for verbose mode
    }

    fn test_completed(&mut self, result: &PyTestResult) {
        let event = TestCompletedEvent {
            test_id: format!("{}::{}", result.path, result.name),
            file_path: result.path.clone(),
            test_name: result.name.clone(),
            status: result.status.clone(),
            duration: result.duration,
            message: result.message.clone(),
            timestamp: current_timestamp(),
        };
        self.emit_test_completed(event);
    }

    fn file_completed(
        &mut self,
        path: &str,
        duration: Duration,
        passed: usize,
        failed: usize,
        skipped: usize,
    ) {
        let event = FileCompletedEvent {
            file_path: path.to_string(),
            duration: duration.as_secs_f64(),
            passed,
            failed,
            skipped,
            timestamp: current_timestamp(),
        };
        self.emit_file_completed(event);
    }

    fn finish_suite(
        &mut self,
        total: usize,
        passed: usize,
        failed: usize,
        skipped: usize,
        errors: usize,
        duration: Duration,
    ) {
        let event = SuiteCompletedEvent {
            total,
            passed,
            failed,
            skipped,
            errors,
            duration: duration.as_secs_f64(),
            timestamp: current_timestamp(),
        };
        self.emit_suite_completed(event);
    }

    fn println(&self, message: &str) {
        // For now, just print directly
        // Could emit a MessageEvent later if needed
        eprintln!("{}", message);
    }
}
