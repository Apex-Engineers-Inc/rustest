//! Top-level crate entry point for the `rustest` Python extension.
//!
//! The library is organised in a handful of small modules so that users who
//! are new to Rust can quickly orient themselves.  Each module focuses on a
//! specific concern (discovery, execution, modelling results, …) and exposes a
//! clean, well documented API.

#![allow(clippy::useless_conversion)]

mod cache;
mod discovery;
mod execution;
mod mark_expr;
mod model;
mod output;
mod python_support;

#[cfg(test)]
mod model_tests;
#[cfg(test)]
mod python_support_tests;

use discovery::discover_tests;
use execution::run_collected_tests;
use model::{LastFailedMode, PyRunReport, RunConfiguration};
use pyo3::prelude::*;
use pyo3::wrap_pyfunction;
use python_support::PyPaths;

#[pyfunction(signature = (paths, pattern = None, mark_expr = None, workers = None, capture_output = true, enable_codeblocks = true, last_failed_mode = "none", fail_fast = false, pytest_compat = false, verbose = false, ascii = false, no_color = false))]
#[allow(clippy::too_many_arguments)]
fn run(
    py: Python<'_>,
    paths: Vec<String>,
    pattern: Option<String>,
    mark_expr: Option<String>,
    workers: Option<usize>,
    capture_output: bool,
    enable_codeblocks: bool,
    last_failed_mode: &str,
    fail_fast: bool,
    pytest_compat: bool,
    verbose: bool,
    ascii: bool,
    no_color: bool,
) -> PyResult<PyRunReport> {
    let last_failed_mode = LastFailedMode::from_str(last_failed_mode)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;

    let config = RunConfiguration::new(
        pattern,
        mark_expr,
        workers,
        capture_output,
        enable_codeblocks,
        last_failed_mode,
        fail_fast,
        pytest_compat,
        verbose,
        ascii,
        no_color,
    );
    let input_paths = PyPaths::from_vec(paths);
    let collected = discover_tests(py, &input_paths, &config)?;
    let report = run_collected_tests(py, &collected, &config)?;
    Ok(report)
}

/// Entry point for the Python extension module.
#[pymodule]
fn rust(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyRunReport>()?;
    m.add_function(wrap_pyfunction!(run, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::path::{Path, PathBuf};

    use crate::discovery::discover_tests;
    use crate::execution::run_collected_tests;
    use crate::model::{LastFailedMode, RunConfiguration};
    use crate::python_support::PyPaths;
    use pyo3::prelude::PyAnyMethods;
    use pyo3::types::PyList;
    use pyo3::Bound;
    use pyo3::Python;

    fn ensure_python_package_on_path(py: Python<'_>) {
        let sys = py.import("sys").expect("failed to import sys");
        let path = sys.getattr("path").expect("missing sys.path");
        let path: Bound<'_, PyList> = path.cast_into().expect("sys.path is not a list");
        let package_root = Path::new(env!("CARGO_MANIFEST_DIR")).join("python");
        let package_root = package_root
            .to_str()
            .expect("python directory path is not valid unicode");
        path.call_method1("insert", (0, package_root))
            .expect("failed to insert python path");
    }

    fn sample_test_module(name: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join(name)
    }

    fn run_discovery(py: Python<'_>, path: &Path) -> Vec<crate::model::TestModule> {
        let config = RunConfiguration::new(
            None,
            None,
            None,
            true,
            true,
            LastFailedMode::None,
            false,
            false,
            false,
            false,
            false,
        );
        let paths = PyPaths::from_vec(vec![path.to_string_lossy().into_owned()]);
        discover_tests(py, &paths, &config).expect("discovery should succeed")
    }

    #[test]
    fn discovers_basic_test_functions() {
        Python::with_gil(|py| {
            ensure_python_package_on_path(py);
            let file_path = sample_test_module("test_basic.py");

            let modules = run_discovery(py, &file_path);
            assert_eq!(modules.len(), 1);
            let module = &modules[0];
            assert_eq!(module.tests.len(), 1);
            assert_eq!(module.tests[0].display_name, "test_example");
        });
    }

    #[test]
    fn executes_tests_that_use_fixtures() {
        Python::with_gil(|py| {
            ensure_python_package_on_path(py);
            let file_path = sample_test_module("test_fixtures.py");

            let config = RunConfiguration::new(
                None,
                None,
                None,
                true,
                true,
                LastFailedMode::None,
                false,
                false,
                false,
                false,
                false,
            );
            let paths = PyPaths::from_vec(vec![file_path.to_string_lossy().into_owned()]);
            let modules = discover_tests(py, &paths, &config).expect("discovery should succeed");
            assert_eq!(modules.len(), 1);
            let report =
                run_collected_tests(py, &modules, &config).expect("execution should succeed");
            assert_eq!(report.total, 1);
            assert_eq!(report.passed, 1);
            assert_eq!(report.failed, 0);
            assert_eq!(report.skipped, 0);
            assert_eq!(report.results.len(), 1);
            assert_eq!(report.results[0].status, "passed");
        });
    }

    #[test]
    fn expands_parametrized_tests_into_multiple_cases() {
        Python::with_gil(|py| {
            ensure_python_package_on_path(py);
            let file_path = sample_test_module("test_parametrized.py");

            let config = RunConfiguration::new(
                None,
                None,
                None,
                true,
                true,
                LastFailedMode::None,
                false,
                false,
                false,
                false,
                false,
            );
            let paths = PyPaths::from_vec(vec![file_path.to_string_lossy().into_owned()]);
            let modules = discover_tests(py, &paths, &config).expect("discovery should succeed");
            let report =
                run_collected_tests(py, &modules, &config).expect("execution should succeed");

            assert_eq!(report.total, 3);
            assert_eq!(report.passed, 3);
            let discovered_names: Vec<_> = modules
                .into_iter()
                .flat_map(|module| module.tests.into_iter().map(|case| case.display_name))
                .collect();
            assert_eq!(
                discovered_names,
                vec![
                    "test_power[double]".to_string(),
                    "test_power[triple]".to_string(),
                    "test_power[quad]".to_string(),
                ]
            );
            let result_names: Vec<_> = report
                .results
                .iter()
                .map(|result| result.name.clone())
                .collect();
            assert_eq!(
                result_names,
                vec![
                    "test_power[double]".to_string(),
                    "test_power[triple]".to_string(),
                    "test_power[quad]".to_string(),
                ]
            );
        });
    }

    #[test]
    fn test_pattern_filtering() {
        Python::with_gil(|py| {
            ensure_python_package_on_path(py);
            let file_path = sample_test_module("test_basic.py");

            let config = RunConfiguration::new(
                Some("nonexistent".to_string()),
                None,
                None,
                true,
                true,
                LastFailedMode::None,
                false,
                false,
                false,
                false,
                false,
            );
            let paths = PyPaths::from_vec(vec![file_path.to_string_lossy().into_owned()]);
            let modules = discover_tests(py, &paths, &config).expect("discovery should succeed");

            // No modules should match the pattern
            assert_eq!(modules.len(), 0);
        });
    }

    #[test]
    fn test_discovery_with_directory() {
        Python::with_gil(|py| {
            ensure_python_package_on_path(py);
            let dir_path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests");

            let modules = run_discovery(py, &dir_path);
            // Should discover all test files in the directory
            assert!(modules.len() >= 3);
        });
    }

    #[test]
    fn test_execution_with_capture_output_disabled() {
        Python::with_gil(|py| {
            ensure_python_package_on_path(py);
            let file_path = sample_test_module("test_basic.py");

            let config = RunConfiguration::new(
                None,
                None,
                None,
                false,
                true,
                LastFailedMode::None,
                false,
                false,
                false,
                false,
                false,
            );
            let paths = PyPaths::from_vec(vec![file_path.to_string_lossy().into_owned()]);
            let modules = discover_tests(py, &paths, &config).expect("discovery should succeed");
            let report =
                run_collected_tests(py, &modules, &config).expect("execution should succeed");

            // Output should not be captured
            assert_eq!(report.results[0].stdout, None);
            assert_eq!(report.results[0].stderr, None);
        });
    }

    #[test]
    fn test_empty_directory_discovery() {
        Python::with_gil(|py| {
            ensure_python_package_on_path(py);

            // Create a temporary empty directory
            let temp_dir = std::env::temp_dir().join("rustest_empty");
            std::fs::create_dir_all(&temp_dir).unwrap();

            let modules = run_discovery(py, &temp_dir);
            assert_eq!(modules.len(), 0);

            // Cleanup
            std::fs::remove_dir(&temp_dir).ok();
        });
    }

    #[test]
    fn test_nonexistent_path_error() {
        Python::with_gil(|py| {
            ensure_python_package_on_path(py);
            let config = RunConfiguration::new(
                None,
                None,
                None,
                true,
                true,
                LastFailedMode::None,
                false,
                false,
                false,
                false,
                false,
            );
            let paths = PyPaths::from_vec(vec!["/nonexistent/path".to_string()]);
            let result = discover_tests(py, &paths, &config);

            assert!(result.is_err());
        });
    }

    #[test]
    fn test_run_report_statistics() {
        Python::with_gil(|py| {
            ensure_python_package_on_path(py);
            let file_path = sample_test_module("test_parametrized.py");

            let config = RunConfiguration::new(
                None,
                None,
                None,
                true,
                true,
                LastFailedMode::None,
                false,
                false,
                false,
                false,
                false,
            );
            let paths = PyPaths::from_vec(vec![file_path.to_string_lossy().into_owned()]);
            let modules = discover_tests(py, &paths, &config).expect("discovery should succeed");
            let report =
                run_collected_tests(py, &modules, &config).expect("execution should succeed");

            // Verify statistics are consistent
            assert_eq!(report.total, report.passed + report.failed + report.skipped);
            assert!(report.duration >= 0.0);
            assert_eq!(report.results.len(), report.total);
        });
    }

    #[test]
    fn test_worker_count_configuration() {
        let config1 = RunConfiguration::new(
            None,
            None,
            Some(1),
            true,
            true,
            LastFailedMode::None,
            false,
            false,
            false,
            false,
            false,
        );
        assert_eq!(config1.worker_count, 1);

        let config2 = RunConfiguration::new(
            None,
            None,
            Some(8),
            true,
            true,
            LastFailedMode::None,
            false,
            false,
            false,
            false,
            false,
        );
        assert_eq!(config2.worker_count, 8);

        let config3 = RunConfiguration::new(
            None,
            None,
            None,
            true,
            true,
            LastFailedMode::None,
            false,
            false,
            false,
            false,
            false,
        );
        assert!(config3.worker_count >= 1);
    }
}
