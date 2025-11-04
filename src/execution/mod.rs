//! Execution pipeline for running collected tests.
//!
//! Even though the Python GIL prevents truly parallel execution, the code in
//! this module keeps the door open for future parallel strategies by isolating
//! the orchestration logic from the raw execution of tests.

use std::collections::HashSet;
use std::time::Instant;

use indexmap::IndexMap;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::PyAnyMethods;
use pyo3::prelude::*;
use pyo3::types::PyTuple;

use crate::model::{
    invalid_test_definition, Fixture, ParameterMap, PyRunReport, PyTestResult, RunConfiguration,
    TestCase, TestModule,
};

/// Run the collected test modules and return a report that mirrors pytest's
/// high-level summary information.
pub fn run_collected_tests(
    py: Python<'_>,
    modules: &[TestModule],
    config: &RunConfiguration,
) -> PyResult<PyRunReport> {
    let start = Instant::now();
    let mut results = Vec::new();
    let mut passed = 0;
    let mut failed = 0;
    let mut skipped = 0;

    for module in modules.iter() {
        for test in module.tests.iter() {
            let result = run_single_test(py, module, test, config)?;
            match result.status.as_str() {
                "passed" => passed += 1,
                "failed" => failed += 1,
                "skipped" => skipped += 1,
                _ => failed += 1,
            }
            results.push(result);
        }
    }

    let duration = start.elapsed().as_secs_f64();
    let total = passed + failed + skipped;
    Ok(PyRunReport::new(
        total, passed, failed, skipped, duration, results,
    ))
}

/// Execute a single test case and convert the outcome into a [`PyTestResult`].
fn run_single_test(
    py: Python<'_>,
    module: &TestModule,
    test_case: &TestCase,
    config: &RunConfiguration,
) -> PyResult<PyTestResult> {
    if let Some(reason) = &test_case.skip_reason {
        return Ok(PyTestResult::skipped(
            test_case.display_name.clone(),
            test_case.path.display().to_string(),
            0.0,
            reason.clone(),
        ));
    }

    let start = Instant::now();
    let outcome = execute_test_case(py, module, test_case, config);
    let duration = start.elapsed().as_secs_f64();
    let name = test_case.display_name.clone();
    let path = test_case.path.display().to_string();

    match outcome {
        Ok(success) => Ok(PyTestResult::passed(
            name,
            path,
            duration,
            success.stdout,
            success.stderr,
        )),
        Err(failure) => Ok(PyTestResult::failed(
            name,
            path,
            duration,
            failure.message,
            failure.stdout,
            failure.stderr,
        )),
    }
}

/// Successful execution details.
struct TestCallSuccess {
    stdout: Option<String>,
    stderr: Option<String>,
}

/// Failure details used to construct [`PyTestResult`].
struct TestCallFailure {
    message: String,
    stdout: Option<String>,
    stderr: Option<String>,
}

/// Execute a test case and return either success metadata or failure details.
fn execute_test_case(
    py: Python<'_>,
    module: &TestModule,
    test_case: &TestCase,
    config: &RunConfiguration,
) -> Result<TestCallSuccess, TestCallFailure> {
    let mut resolver = FixtureResolver::new(py, &module.fixtures, &test_case.parameter_values);
    let mut call_args = Vec::new();
    for param in &test_case.parameters {
        match resolver.resolve_argument(param) {
            Ok(value) => call_args.push(value),
            Err(err) => {
                let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
                return Err(TestCallFailure {
                    message,
                    stdout: None,
                    stderr: None,
                });
            }
        }
    }

    let call_result = call_with_capture(py, config.capture_output, || {
        let args_tuple = PyTuple::new_bound(py, &call_args);
        let callable = test_case.callable.bind(py);
        callable.call1(args_tuple).map(|value| value.to_object(py))
    });

    let (result, stdout, stderr) = match call_result {
        Ok(value) => value,
        Err(err) => {
            return Err(TestCallFailure {
                message: err.to_string(),
                stdout: None,
                stderr: None,
            })
        }
    };

    match result {
        Ok(_) => Ok(TestCallSuccess { stdout, stderr }),
        Err(err) => {
            let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
            Err(TestCallFailure {
                message,
                stdout,
                stderr,
            })
        }
    }
}

/// Helper struct implementing a very small fixture dependency resolver.
struct FixtureResolver<'py> {
    py: Python<'py>,
    fixtures: &'py IndexMap<String, Fixture>,
    cache: IndexMap<String, PyObject>,
    stack: HashSet<String>,
    parameters: &'py ParameterMap,
}

impl<'py> FixtureResolver<'py> {
    fn new(
        py: Python<'py>,
        fixtures: &'py IndexMap<String, Fixture>,
        parameters: &'py ParameterMap,
    ) -> Self {
        Self {
            py,
            fixtures,
            cache: IndexMap::new(),
            stack: HashSet::new(),
            parameters,
        }
    }

    fn resolve_argument(&mut self, name: &str) -> PyResult<PyObject> {
        if let Some(value) = self.parameters.get(name) {
            return Ok(value.clone_ref(self.py));
        }

        if let Some(value) = self.cache.get(name) {
            return Ok(value.clone_ref(self.py));
        }

        let fixture = self
            .fixtures
            .get(name)
            .ok_or_else(|| invalid_test_definition(format!("Unknown fixture '{}'.", name)))?;

        if !self.stack.insert(fixture.name.clone()) {
            return Err(PyRuntimeError::new_err(format!(
                "Detected recursive fixture dependency involving '{}'.",
                fixture.name
            )));
        }

        let mut args = Vec::new();
        for param in fixture.parameters.iter() {
            let value = self.resolve_argument(param)?;
            args.push(value);
        }
        let args_tuple = PyTuple::new_bound(self.py, &args);
        let call_result = fixture
            .callable
            .bind(self.py)
            .call1(args_tuple)
            .map(|value| value.to_object(self.py));
        self.stack.remove(&fixture.name);
        let result = call_result?;
        self.cache
            .insert(fixture.name.clone(), result.clone_ref(self.py));
        Ok(result)
    }
}

/// Execute a callable while optionally capturing stdout/stderr.
fn call_with_capture<F>(
    py: Python<'_>,
    capture_output: bool,
    f: F,
) -> PyResult<(PyResult<PyObject>, Option<String>, Option<String>)>
where
    F: FnOnce() -> PyResult<PyObject>,
{
    if !capture_output {
        return Ok((f(), None, None));
    }

    let contextlib = py.import_bound("contextlib")?;
    let io = py.import_bound("io")?;
    let stdout_buffer = io.getattr("StringIO")?.call0()?;
    let stderr_buffer = io.getattr("StringIO")?.call0()?;
    let redirect_stdout = contextlib
        .getattr("redirect_stdout")?
        .call1((&stdout_buffer,))?;
    let redirect_stderr = contextlib
        .getattr("redirect_stderr")?
        .call1((&stderr_buffer,))?;
    let stack = contextlib.getattr("ExitStack")?.call0()?;
    stack.call_method1("enter_context", (&redirect_stdout,))?;
    stack.call_method1("enter_context", (&redirect_stderr,))?;

    let result = f();
    stack.call_method0("close")?;

    let stdout: String = stdout_buffer.call_method0("getvalue")?.extract()?;
    let stderr: String = stderr_buffer.call_method0("getvalue")?.extract()?;
    let stdout = if stdout.is_empty() {
        None
    } else {
        Some(stdout)
    };
    let stderr = if stderr.is_empty() {
        None
    } else {
        Some(stderr)
    };

    Ok((result, stdout, stderr))
}

/// Format a Python exception using `traceback.format_exception`.
fn format_pyerr(py: Python<'_>, err: &PyErr) -> PyResult<String> {
    let traceback = py.import_bound("traceback")?;
    let exc_type = err.get_type_bound(py).into_py(py);
    let exc_value = err.value_bound(py).into_py(py);
    let exc_tb = err
        .traceback_bound(py)
        .map(|tb| tb.into_py(py))
        .unwrap_or_else(|| py.None().into_py(py));
    let formatted: Vec<String> = traceback
        .call_method1("format_exception", (exc_type, exc_value, exc_tb))?
        .extract()?;
    Ok(formatted.join(""))
}
