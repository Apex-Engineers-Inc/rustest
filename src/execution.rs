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

use crate::cache;
use crate::model::{
    invalid_test_definition, Fixture, FixtureScope, ParameterMap, PyRunReport, PyTestResult,
    RunConfiguration, TestCase, TestModule,
};
use crate::output::{OutputConfig, OutputRenderer, SpinnerDisplay};

/// Manages teardown for generator fixtures across different scopes.
struct TeardownCollector {
    session: Vec<Py<PyAny>>,
    module: Vec<Py<PyAny>>,
    class: Vec<Py<PyAny>>,
}

impl TeardownCollector {
    fn new() -> Self {
        Self {
            session: Vec::new(),
            module: Vec::new(),
            class: Vec::new(),
        }
    }
}

/// Manages fixture caches and teardowns for different scopes.
struct FixtureContext {
    session_cache: IndexMap<String, Py<PyAny>>,
    module_cache: IndexMap<String, Py<PyAny>>,
    class_cache: IndexMap<String, Py<PyAny>>,
    teardowns: TeardownCollector,
}

impl FixtureContext {
    fn new() -> Self {
        Self {
            session_cache: IndexMap::new(),
            module_cache: IndexMap::new(),
            class_cache: IndexMap::new(),
            teardowns: TeardownCollector::new(),
        }
    }
}

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

    // Create output renderer based on configuration
    let output_config = OutputConfig::from_run_config(config);
    let mut renderer: Box<dyn OutputRenderer> = Box::new(SpinnerDisplay::new(
        output_config.use_colors,
        output_config.ascii_mode,
    ));

    // Calculate totals for progress tracking
    let total_files = modules.len();
    let total_tests: usize = modules.iter().map(|m| m.tests.len()).sum();
    renderer.start_suite(total_files, total_tests);

    // Fixture context lives for the entire test run
    let mut context = FixtureContext::new();

    for module in modules.iter() {
        // Track per-file statistics
        let file_start = Instant::now();
        let mut file_passed = 0;
        let mut file_failed = 0;
        let mut file_skipped = 0;

        // Notify renderer that this file is starting
        renderer.start_file(module);

        // Reset module-scoped caches for this module
        context.module_cache.clear();

        // Group tests by class for class-scoped fixtures
        let mut tests_by_class: IndexMap<Option<String>, Vec<&TestCase>> = IndexMap::new();
        for test in module.tests.iter() {
            tests_by_class
                .entry(test.class_name.clone())
                .or_default()
                .push(test);
        }

        for (_class_name, tests) in tests_by_class {
            // Reset class-scoped cache for this class
            context.class_cache.clear();

            for test in tests {
                let result = run_single_test(py, module, test, config, &mut context)?;
                let is_failed = result.status == "failed";

                // Update global and per-file counters
                match result.status.as_str() {
                    "passed" => {
                        passed += 1;
                        file_passed += 1;
                    }
                    "failed" => {
                        failed += 1;
                        file_failed += 1;
                    }
                    "skipped" => {
                        skipped += 1;
                        file_skipped += 1;
                    }
                    _ => {
                        failed += 1;
                        file_failed += 1;
                    }
                }

                // Notify renderer of test completion
                renderer.test_completed(&result);

                results.push(result);

                // Check for fail-fast mode: exit immediately on first failure
                if config.fail_fast && is_failed {
                    // Clean up fixtures before returning early
                    finalize_generators(py, &mut context.teardowns.class);
                    finalize_generators(py, &mut context.teardowns.module);
                    finalize_generators(py, &mut context.teardowns.session);

                    let duration = start.elapsed();
                    let total = passed + failed + skipped;

                    // Notify renderer of early exit
                    renderer.finish_suite(total, passed, failed, skipped, duration);

                    let report = PyRunReport::new(
                        total,
                        passed,
                        failed,
                        skipped,
                        duration.as_secs_f64(),
                        results,
                    );

                    // Write cache before returning
                    write_failed_tests_cache(&report)?;

                    return Ok(report);
                }

                // If this is a plain function test (no class), clear class cache
                // Class-scoped fixtures should NOT be shared across plain function tests
                if test.class_name.is_none() {
                    context.class_cache.clear();
                    finalize_generators(py, &mut context.teardowns.class);
                }
            }

            // Class-scoped fixtures are dropped here - run teardowns
            finalize_generators(py, &mut context.teardowns.class);
        }

        // Module-scoped fixtures are dropped here - run teardowns
        finalize_generators(py, &mut context.teardowns.module);

        // Notify renderer that this file is complete
        let file_duration = file_start.elapsed();
        renderer.file_completed(
            &module.path.to_string_lossy(),
            file_duration,
            file_passed,
            file_failed,
            file_skipped,
        );
    }

    // Session-scoped fixtures are dropped here - run teardowns
    finalize_generators(py, &mut context.teardowns.session);

    let duration = start.elapsed();
    let total = passed + failed + skipped;

    // Notify renderer that the entire suite is complete
    renderer.finish_suite(total, passed, failed, skipped, duration);

    let report = PyRunReport::new(
        total,
        passed,
        failed,
        skipped,
        duration.as_secs_f64(),
        results,
    );

    // Write cache after all tests complete
    write_failed_tests_cache(&report)?;

    Ok(report)
}

/// Execute a single test case and convert the outcome into a [`PyTestResult`].
fn run_single_test(
    py: Python<'_>,
    module: &TestModule,
    test_case: &TestCase,
    config: &RunConfiguration,
    context: &mut FixtureContext,
) -> PyResult<PyTestResult> {
    if let Some(reason) = &test_case.skip_reason {
        return Ok(PyTestResult::skipped(
            test_case.display_name.clone(),
            test_case.path.display().to_string(),
            0.0,
            reason.clone(),
            test_case.mark_names(),
        ));
    }

    let start = Instant::now();
    let outcome = execute_test_case(py, module, test_case, config, context);
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
            test_case.mark_names(),
        )),
        Err(failure) => Ok(PyTestResult::failed(
            name,
            path,
            duration,
            failure.message,
            failure.stdout,
            failure.stderr,
            test_case.mark_names(),
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
    context: &mut FixtureContext,
) -> Result<TestCallSuccess, TestCallFailure> {
    let mut resolver = FixtureResolver::new(
        py,
        &module.fixtures,
        &test_case.parameter_values,
        &mut context.session_cache,
        &mut context.module_cache,
        &mut context.class_cache,
        &mut context.teardowns,
    );

    // Resolve autouse fixtures first
    if let Err(err) = resolver.resolve_autouse_fixtures() {
        let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
        return Err(TestCallFailure {
            message,
            stdout: None,
            stderr: None,
        });
    }

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
        let args_tuple = PyTuple::new(py, &call_args)?;
        let callable = test_case.callable.bind(py);
        callable.call1(args_tuple).map(|value| value.unbind())
    });

    let (result, stdout, stderr) = match call_result {
        Ok(value) => value,
        Err(err) => {
            // Clean up function-scoped fixtures before returning
            finalize_generators(py, &mut resolver.function_teardowns);
            return Err(TestCallFailure {
                message: err.to_string(),
                stdout: None,
                stderr: None,
            });
        }
    };

    // Clean up function-scoped fixtures after test completes
    finalize_generators(py, &mut resolver.function_teardowns);

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

/// Helper struct implementing fixture dependency resolver with scope support.
///
/// The resolver works with a cascading cache system:
/// - Session cache: shared across all tests
/// - Module cache: shared across all tests in a module
/// - Class cache: shared across all tests in a class
/// - Function cache: per-test, created fresh each time
///
/// When resolving a fixture, it checks caches in order based on the fixture's scope.
struct FixtureResolver<'py> {
    py: Python<'py>,
    fixtures: &'py IndexMap<String, Fixture>,
    session_cache: &'py mut IndexMap<String, Py<PyAny>>,
    module_cache: &'py mut IndexMap<String, Py<PyAny>>,
    class_cache: &'py mut IndexMap<String, Py<PyAny>>,
    function_cache: IndexMap<String, Py<PyAny>>,
    teardowns: &'py mut TeardownCollector,
    function_teardowns: Vec<Py<PyAny>>,
    stack: HashSet<String>,
    parameters: &'py ParameterMap,
}

impl<'py> FixtureResolver<'py> {
    fn new(
        py: Python<'py>,
        fixtures: &'py IndexMap<String, Fixture>,
        parameters: &'py ParameterMap,
        session_cache: &'py mut IndexMap<String, Py<PyAny>>,
        module_cache: &'py mut IndexMap<String, Py<PyAny>>,
        class_cache: &'py mut IndexMap<String, Py<PyAny>>,
        teardowns: &'py mut TeardownCollector,
    ) -> Self {
        Self {
            py,
            fixtures,
            session_cache,
            module_cache,
            class_cache,
            function_cache: IndexMap::new(),
            teardowns,
            function_teardowns: Vec::new(),
            stack: HashSet::new(),
            parameters,
        }
    }

    fn resolve_argument(&mut self, name: &str) -> PyResult<Py<PyAny>> {
        // First check if it's a parametrized value
        if let Some(value) = self.parameters.get(name) {
            return Ok(value.clone_ref(self.py));
        }

        // Check all caches in order: function -> class -> module -> session
        if let Some(value) = self.function_cache.get(name) {
            return Ok(value.clone_ref(self.py));
        }
        if let Some(value) = self.class_cache.get(name) {
            return Ok(value.clone_ref(self.py));
        }
        if let Some(value) = self.module_cache.get(name) {
            return Ok(value.clone_ref(self.py));
        }
        if let Some(value) = self.session_cache.get(name) {
            return Ok(value.clone_ref(self.py));
        }

        // Fixture not in any cache, need to execute it
        let fixture = self
            .fixtures
            .get(name)
            .ok_or_else(|| invalid_test_definition(format!("Unknown fixture '{}'.", name)))?;

        // Detect circular dependencies
        if !self.stack.insert(fixture.name.clone()) {
            return Err(PyRuntimeError::new_err(format!(
                "Detected recursive fixture dependency involving '{}'.",
                fixture.name
            )));
        }

        // Validate scope ordering: higher-scoped fixtures cannot depend on lower-scoped ones
        // This check happens during resolution of dependencies
        for param in fixture.parameters.iter() {
            if let Some(dep_fixture) = self.fixtures.get(param) {
                self.validate_scope_dependency(fixture, dep_fixture)?;
            }
        }

        // Resolve fixture dependencies recursively
        let mut args = Vec::new();
        for param in fixture.parameters.iter() {
            let value = self.resolve_argument(param)?;
            args.push(value);
        }

        // Execute the fixture
        let args_tuple = PyTuple::new(self.py, &args)?;
        let result = if fixture.is_generator {
            // For generator fixtures: call to get generator, then call next() to get yielded value
            let generator = fixture
                .callable
                .bind(self.py)
                .call1(args_tuple)
                .map(|value| value.unbind())?;

            // Call next() on the generator to get the yielded value
            let yielded_value = generator.bind(self.py).call_method0("__next__")?.unbind();

            // Store the generator in the appropriate teardown list
            match fixture.scope {
                FixtureScope::Session => {
                    self.teardowns.session.push(generator);
                }
                FixtureScope::Module => {
                    self.teardowns.module.push(generator);
                }
                FixtureScope::Class => {
                    self.teardowns.class.push(generator);
                }
                FixtureScope::Function => {
                    self.function_teardowns.push(generator);
                }
            }

            yielded_value
        } else {
            // For regular fixtures: call and use the return value directly
            fixture
                .callable
                .bind(self.py)
                .call1(args_tuple)
                .map(|value| value.unbind())?
        };

        self.stack.remove(&fixture.name);

        // Store in the appropriate cache based on scope
        match fixture.scope {
            FixtureScope::Session => {
                self.session_cache
                    .insert(fixture.name.clone(), result.clone_ref(self.py));
            }
            FixtureScope::Module => {
                self.module_cache
                    .insert(fixture.name.clone(), result.clone_ref(self.py));
            }
            FixtureScope::Class => {
                self.class_cache
                    .insert(fixture.name.clone(), result.clone_ref(self.py));
            }
            FixtureScope::Function => {
                self.function_cache
                    .insert(fixture.name.clone(), result.clone_ref(self.py));
            }
        }

        Ok(result)
    }

    /// Validate that a fixture's scope is compatible with its dependency's scope.
    ///
    /// The rule is: a fixture can only depend on fixtures with equal or broader scope.
    /// - Session fixtures can depend on: session only
    /// - Module fixtures can depend on: session, module
    /// - Class fixtures can depend on: session, module, class
    /// - Function fixtures can depend on: session, module, class, function
    fn validate_scope_dependency(&self, fixture: &Fixture, dependency: &Fixture) -> PyResult<()> {
        // Check if dependency scope is narrower than fixture scope
        if fixture.scope > dependency.scope {
            return Err(invalid_test_definition(format!(
                "ScopeMismatch: Fixture '{}' (scope: {:?}) cannot depend on '{}' (scope: {:?}). \
                 A fixture can only depend on fixtures with equal or broader scope.",
                fixture.name, fixture.scope, dependency.name, dependency.scope
            )));
        }
        Ok(())
    }

    /// Resolve all autouse fixtures appropriate for the current test.
    /// Autouse fixtures are automatically executed without needing to be explicitly requested.
    fn resolve_autouse_fixtures(&mut self) -> PyResult<()> {
        // Collect all autouse fixtures
        let autouse_fixtures: Vec<String> = self
            .fixtures
            .iter()
            .filter(|(_, fixture)| fixture.autouse)
            .map(|(name, _)| name.clone())
            .collect();

        // Resolve each autouse fixture
        for name in autouse_fixtures {
            // Skip if already in cache (for higher-scoped autouse fixtures)
            if self.function_cache.contains_key(&name)
                || self.class_cache.contains_key(&name)
                || self.module_cache.contains_key(&name)
                || self.session_cache.contains_key(&name)
            {
                continue;
            }

            // Resolve the autouse fixture
            self.resolve_argument(&name)?;
        }

        Ok(())
    }
}

/// Result type for test execution with optional stdout/stderr capture.
type CallResult = (PyResult<Py<PyAny>>, Option<String>, Option<String>);

/// Execute a callable while optionally capturing stdout/stderr.
fn call_with_capture<F>(py: Python<'_>, capture_output: bool, f: F) -> PyResult<CallResult>
where
    F: FnOnce() -> PyResult<Py<PyAny>>,
{
    if !capture_output {
        return Ok((f(), None, None));
    }

    let contextlib = py.import("contextlib")?;
    let io = py.import("io")?;
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
/// For AssertionErrors, also attempts to extract the actual vs expected values
/// from the local scope.
fn format_pyerr(py: Python<'_>, err: &PyErr) -> PyResult<String> {
    let traceback = py.import("traceback")?;
    let exc_type: Py<PyAny> = err.get_type(py).unbind().into();
    let exc_value: Py<PyAny> = err.value(py).clone().unbind().into();
    let exc_tb: Py<PyAny> = err
        .traceback(py)
        .map(|tb| tb.clone().unbind().into())
        .unwrap_or_else(|| py.None());
    let formatted: Vec<String> = traceback
        .call_method1("format_exception", (exc_type, exc_value, exc_tb))?
        .extract()?;

    let mut result = formatted.join("");

    // For AssertionError, try to extract comparison values from the frame
    if err.is_instance_of::<pyo3::exceptions::PyAssertionError>(py) {
        if let Some(tb) = err.traceback(py) {
            if let Ok(enriched) = enrich_assertion_error(py, &tb, &result) {
                result = enriched;
            }
        }
    }

    Ok(result)
}

/// Attempt to enrich an AssertionError with actual vs expected values
/// by inspecting the local variables in the frame where the assertion failed.
fn enrich_assertion_error(
    py: Python<'_>,
    tb: &pyo3::Bound<'_, pyo3::types::PyTraceback>,
    formatted: &str,
) -> PyResult<String> {
    // Get the frame from the traceback
    let frame = tb.getattr("tb_frame")?;
    let locals = frame.getattr("f_locals")?;

    // Try to extract the failing line from the formatted traceback
    // Look for lines containing "assert"
    for line in formatted.lines() {
        if line.trim().starts_with("assert ") {
            // Parse the assertion to find variable names
            let assertion = line.trim();

            // Try to extract comparison values
            if let Some(values) = extract_comparison_values(py, assertion, &locals)? {
                // Append the extracted values to the formatted traceback
                return Ok(format!(
                    "{}\n__RUSTEST_ASSERTION_VALUES__\nExpected: {}\nReceived: {}",
                    formatted, values.0, values.1
                ));
            }
            break;
        }
    }

    Ok(formatted.to_string())
}

/// Extract the actual comparison values from local variables
fn extract_comparison_values(
    _py: Python<'_>,
    assertion: &str,
    locals: &pyo3::Bound<'_, pyo3::PyAny>,
) -> PyResult<Option<(String, String)>> {
    use regex::Regex;

    // Match patterns like: assert x == y, assert a != b, etc.
    let re = Regex::new(r"assert\s+(\w+)\s*(==|!=|>|<|>=|<=)\s+(\w+)").unwrap();

    if let Some(caps) = re.captures(assertion) {
        let left_var = &caps[1];
        let right_var = &caps[3];
        let operator = &caps[2];

        // Try to get the values from locals
        // Check if the variables exist, then get their values
        if locals.contains(left_var).unwrap_or(false) && locals.contains(right_var).unwrap_or(false)
        {
            match (locals.get_item(left_var), locals.get_item(right_var)) {
                (Ok(left), Ok(right)) => {
                    let left_repr = left.repr()?.to_string();
                    let right_repr = right.repr()?.to_string();

                    // For == comparisons, left is actual, right is expected (by convention)
                    // For comparison operators (>, <, >=, <=), left is the value being tested,
                    // right is the threshold/expected value
                    return Ok(match operator {
                        "==" => Some((right_repr, left_repr)), // (expected, actual)
                        "!=" => Some((left_repr, right_repr)), // Show both sides
                        ">=" | "<=" | ">" | "<" => Some((right_repr, left_repr)), // (threshold, actual)
                        _ => Some((left_repr, right_repr)),
                    });
                }
                _ => {
                    // Could not get the values
                }
            }
        }
    }

    Ok(None)
}

/// Finalize generator fixtures by running their teardown code.
/// This calls next() on each generator, which will execute the code after yield.
/// The generator will raise StopIteration when complete, which we catch and ignore.
fn finalize_generators(py: Python<'_>, generators: &mut Vec<Py<PyAny>>) {
    // Process generators in reverse order (LIFO) to match pytest behavior
    for generator in generators.drain(..).rev() {
        let result = generator.bind(py).call_method0("__next__");
        // Ignore StopIteration (expected) and log other errors
        if let Err(err) = result {
            // Check if it's StopIteration - that's expected and OK
            if !err.is_instance_of::<pyo3::exceptions::PyStopIteration>(py) {
                // For other exceptions, we could log them, but for now we'll ignore
                // to avoid breaking the test run. In pytest, teardown errors are collected
                // but don't stop other teardowns from running.
                eprintln!("Warning: Error during fixture teardown: {}", err);
            }
        }
    }
}

/// Write the cache of failed tests for the --lf and --ff options.
fn write_failed_tests_cache(report: &PyRunReport) -> PyResult<()> {
    let mut failed_tests = HashSet::new();

    // Collect all failed test IDs
    for result in &report.results {
        if result.status == "failed" {
            failed_tests.insert(result.unique_id());
        }
    }

    // Write to cache
    cache::write_last_failed(&failed_tests)?;

    Ok(())
}
