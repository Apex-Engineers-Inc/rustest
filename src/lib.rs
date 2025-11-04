//! Top-level crate entry point for the `rustest` Python extension.
//!
//! The library is organised in a handful of small modules so that users who
//! are new to Rust can quickly orient themselves.  Each module focuses on a
//! specific concern (discovery, execution, modelling results, …) and exposes a
//! clean, well documented API.

mod discovery;
mod execution;
mod model;
mod python_support;

use discovery::discover_tests;
use execution::run_collected_tests;
use model::{PyRunReport, RunConfiguration};
use pyo3::prelude::*;
use python_support::PyPaths;

/// Entry point for the Python extension module.
#[pymodule]
fn _rust(py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyRunReport>()?;

    #[pyfn(m, "run")]
    #[pyo3(signature = (paths, pattern = None, workers = None, capture_output = true))]
    fn run_py(
        py: Python<'_>,
        paths: Vec<String>,
        pattern: Option<String>,
        workers: Option<usize>,
        capture_output: bool,
    ) -> PyResult<PyRunReport> {
        let config = RunConfiguration::new(pattern, workers, capture_output);
        let input_paths = PyPaths::from_vec(paths);
        let collected = discover_tests(py, &input_paths, &config)?;
        let report = run_collected_tests(py, collected, &config)?;
        Ok(report)
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use std::path::{Path, PathBuf};

    use crate::discovery::discover_tests;
    use crate::execution::run_collected_tests;
    use crate::model::RunConfiguration;
    use crate::python_support::PyPaths;
    use pyo3::types::{PyList, PyString};
    use pyo3::Python;

    fn ensure_python_package_on_path(py: Python<'_>) {
        let sys = py.import("sys").expect("failed to import sys");
        let path: &PyList = sys
            .getattr("path")
            .expect("missing sys.path")
            .downcast()
            .expect("sys.path is not a list");
        let package_root = Path::new(env!("CARGO_MANIFEST_DIR")).join("python");
        let package_root = package_root
            .to_str()
            .expect("python directory path is not valid unicode");
        let entry = PyString::new(py, package_root);
        path.insert(0, entry).expect("failed to insert python path");
    }

    fn sample_test_module(name: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("python_suite")
            .join(name)
    }

    fn run_discovery(py: Python<'_>, path: &Path) -> Vec<crate::model::TestModule> {
        let config = RunConfiguration::new(None, None, true);
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

            let config = RunConfiguration::new(None, None, true);
            let paths = PyPaths::from_vec(vec![file_path.to_string_lossy().into_owned()]);
            let modules = discover_tests(py, &paths, &config).expect("discovery should succeed");
            assert_eq!(modules.len(), 1);
            let report =
                run_collected_tests(py, modules, &config).expect("execution should succeed");
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

            let config = RunConfiguration::new(None, None, true);
            let paths = PyPaths::from_vec(vec![file_path.to_string_lossy().into_owned()]);
            let modules = discover_tests(py, &paths, &config).expect("discovery should succeed");
            let report = run_collected_tests(py, modules.clone(), &config)
                .expect("execution should succeed");

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
}
