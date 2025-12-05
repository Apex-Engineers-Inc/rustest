//! Execution pipeline for running collected tests.
//!
//! This module supports parallel async test execution within the same event loop scope.
//! Tests that share a loop scope (class, module, or session) can run concurrently
//! using asyncio.gather(), providing significant speedups for I/O-bound async tests.
//!
//! Key concepts:
//! - Tests with function loop scope run sequentially (each needs its own loop)
//! - Tests with class/module/session loop scope can batch within that scope
//! - Sync tests always run sequentially
//! - Fixture scopes are respected: shared fixtures resolve once, function fixtures per-test

use std::cell::RefCell;
use std::collections::HashSet;
use std::ffi::c_void;
use std::time::Instant;

use indexmap::IndexMap;
use pyo3::exceptions::{PyNotImplementedError, PyRuntimeError};
use pyo3::prelude::PyAnyMethods;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};

use crate::cache;
use crate::model::{
    invalid_test_definition, to_relative_path, CollectionError, Fixture, FixtureScope, Mark,
    ParameterMap, PyRunReport, PyTestResult, RunConfiguration, TestCase, TestModule,
};
use crate::output::{EventStreamRenderer, OutputConfig, OutputRenderer, SpinnerDisplay};

/// Represents a batch of async tests that can run in parallel.
/// All tests in a batch share the same event loop scope (class, module, or session).
struct AsyncBatch<'a> {
    /// The tests in this batch
    tests: Vec<&'a TestCase>,
    /// The loop scope shared by all tests in the batch
    loop_scope: FixtureScope,
}

/// Represents a test execution unit - either a single test or a batch of parallel async tests.
enum TestExecutionUnit<'a> {
    /// A single test to run sequentially
    Single(&'a TestCase),
    /// A batch of async tests to run in parallel
    Batch(AsyncBatch<'a>),
}

/// Determines if a test is async by checking its callable.
fn is_async_test(py: Python<'_>, test_case: &TestCase) -> bool {
    let inspect = match py.import("inspect") {
        Ok(m) => m,
        Err(_) => return false,
    };
    inspect
        .call_method1("iscoroutinefunction", (&test_case.callable.bind(py),))
        .map(|r| r.is_truthy().unwrap_or(false))
        .unwrap_or(false)
}

/// Partition tests into execution units for optimal async parallelization.
///
/// Tests are grouped based on their loop scope:
/// - Async tests with class/module/session loop scope are batched together
/// - Async tests with function loop scope run sequentially
/// - Sync tests always run sequentially
///
/// The function preserves test order within batches and relative to sequential tests.
fn partition_tests_for_parallel<'a>(
    py: Python<'_>,
    tests: &[&'a TestCase],
    fixtures: &IndexMap<String, Fixture>,
) -> Vec<TestExecutionUnit<'a>> {
    let mut units: Vec<TestExecutionUnit<'a>> = Vec::new();
    let mut current_batch: Option<AsyncBatch<'a>> = None;

    for test in tests {
        // Skip tests that are already marked as skipped
        if test.skip_reason.is_some() {
            // Flush any pending batch before adding a sequential test
            if let Some(batch) = current_batch.take() {
                if batch.tests.len() > 1 {
                    units.push(TestExecutionUnit::Batch(batch));
                } else if let Some(t) = batch.tests.into_iter().next() {
                    units.push(TestExecutionUnit::Single(t));
                }
            }
            units.push(TestExecutionUnit::Single(test));
            continue;
        }

        let is_async = is_async_test(py, test);
        let loop_scope = determine_test_loop_scope(py, test, fixtures);

        // Only batch async tests with non-function loop scope
        let can_batch = is_async && loop_scope > FixtureScope::Function;

        if can_batch {
            match &mut current_batch {
                Some(batch) if batch.loop_scope == loop_scope => {
                    // Same scope, add to current batch
                    batch.tests.push(test);
                }
                Some(batch) => {
                    // Different scope, flush current batch and start new one
                    if batch.tests.len() > 1 {
                        units.push(TestExecutionUnit::Batch(std::mem::replace(
                            batch,
                            AsyncBatch {
                                tests: vec![test],
                                loop_scope,
                            },
                        )));
                    } else {
                        // Single test batch becomes sequential
                        let old_batch = std::mem::replace(
                            batch,
                            AsyncBatch {
                                tests: vec![test],
                                loop_scope,
                            },
                        );
                        if let Some(t) = old_batch.tests.into_iter().next() {
                            units.push(TestExecutionUnit::Single(t));
                        }
                    }
                }
                None => {
                    // Start new batch
                    current_batch = Some(AsyncBatch {
                        tests: vec![test],
                        loop_scope,
                    });
                }
            }
        } else {
            // Flush any pending batch before adding a sequential test
            if let Some(batch) = current_batch.take() {
                if batch.tests.len() > 1 {
                    units.push(TestExecutionUnit::Batch(batch));
                } else if let Some(t) = batch.tests.into_iter().next() {
                    units.push(TestExecutionUnit::Single(t));
                }
            }
            units.push(TestExecutionUnit::Single(test));
        }
    }

    // Flush any remaining batch
    if let Some(batch) = current_batch.take() {
        if batch.tests.len() > 1 {
            units.push(TestExecutionUnit::Batch(batch));
        } else if let Some(t) = batch.tests.into_iter().next() {
            units.push(TestExecutionUnit::Single(t));
        }
    }

    units
}

// This thread-local stores a raw pointer to the currently active `FixtureResolver`.
// It lets Python's `request.getfixturevalue()` calls tunnel back into the Rust resolver
// without exposing the resolver publicly or cloning it.
//
// SAFETY INVARIANTS:
// 1. Pointers are only valid while `ResolverActivationGuard` is alive on the stack
// 2. The guard MUST be dropped before the resolver goes out of scope
// 3. Access is single-threaded (Python GIL ensures this)
// 4. The lifetime cast to 'static is a lie - we rely on stack discipline to ensure
//    the pointer is never dereferenced after the resolver is dropped
thread_local! {
    static ACTIVE_RESOLVER: RefCell<Vec<*mut c_void>> = const { RefCell::new(Vec::new()) };
}

struct ResolverActivationGuard {
    // Store the pointer to verify we pop the correct one
    ptr: *mut c_void,
}

impl ResolverActivationGuard {
    fn new(resolver: &mut FixtureResolver<'_>) -> Self {
        let ptr = resolver as *mut _ as *mut c_void;
        ACTIVE_RESOLVER.with(|cell| {
            cell.borrow_mut().push(ptr);
        });
        Self { ptr }
    }
}

impl Drop for ResolverActivationGuard {
    fn drop(&mut self) {
        ACTIVE_RESOLVER.with(|cell| {
            let mut slot = cell.borrow_mut();
            let popped = slot.pop();
            // Use assert! instead of debug_assert! to catch errors in release mode
            assert!(
                popped.is_some(),
                "BUG: resolver stack underflow - guard dropped without matching push"
            );
            // Verify we're popping the correct pointer (stack discipline)
            assert!(
                popped == Some(self.ptr),
                "BUG: resolver stack corruption - popped pointer doesn't match pushed pointer"
            );
        });
    }
}

pub(crate) fn resolve_fixture_for_request(name: &str) -> PyResult<Py<PyAny>> {
    ACTIVE_RESOLVER.with(|cell| {
        let slot = cell.borrow();
        if let Some(&ptr) = slot.last() {
            // SAFETY: This is safe because:
            // 1. The pointer was pushed by ResolverActivationGuard::new() which holds a valid reference
            // 2. The guard is still on the stack (we haven't popped yet), so the resolver is still alive
            // 3. We're running under the Python GIL, so no concurrent access is possible
            // 4. The 'static lifetime is incorrect but we maintain stack discipline to ensure
            //    the pointer is never accessed after the resolver is dropped
            let resolver = unsafe { &mut *(ptr as *mut FixtureResolver<'static>) };
            resolver.resolve_for_request(name)
        } else {
            Err(PyRuntimeError::new_err(
                "request.getfixturevalue() can only run while rustest is executing a test. \
                 Call it from inside a test function (or inject the fixture directly) so rustest \
                 knows which resolver to use.",
            ))
        }
    })
}

/// Manages teardown for generator fixtures across different scopes.
struct TeardownCollector {
    session: Vec<Py<PyAny>>,
    package: Vec<Py<PyAny>>,
    module: Vec<Py<PyAny>>,
    class: Vec<Py<PyAny>>,
}

impl TeardownCollector {
    fn new() -> Self {
        Self {
            session: Vec::new(),
            package: Vec::new(),
            module: Vec::new(),
            class: Vec::new(),
        }
    }
}

/// Manages fixture caches and teardowns for different scopes.
struct FixtureContext {
    session_cache: IndexMap<String, Py<PyAny>>,
    package_cache: IndexMap<String, Py<PyAny>>,
    module_cache: IndexMap<String, Py<PyAny>>,
    class_cache: IndexMap<String, Py<PyAny>>,
    teardowns: TeardownCollector,
    /// Track the current package to detect package transitions
    current_package: Option<String>,
    /// Event loops for different scopes (for async fixtures)
    session_event_loop: Option<Py<PyAny>>,
    package_event_loop: Option<Py<PyAny>>,
    module_event_loop: Option<Py<PyAny>>,
    class_event_loop: Option<Py<PyAny>>,
}

impl FixtureContext {
    fn new() -> Self {
        Self {
            session_cache: IndexMap::new(),
            package_cache: IndexMap::new(),
            module_cache: IndexMap::new(),
            class_cache: IndexMap::new(),
            teardowns: TeardownCollector::new(),
            current_package: None,
            session_event_loop: None,
            package_event_loop: None,
            module_event_loop: None,
            class_event_loop: None,
        }
    }
}

/// Run the collected test modules and return a report that mirrors pytest's
/// high-level summary information.
pub fn run_collected_tests(
    py: Python<'_>,
    modules: &[TestModule],
    collection_errors: &[CollectionError],
    config: &RunConfiguration,
) -> PyResult<PyRunReport> {
    let start = Instant::now();
    let mut results = Vec::new();
    let mut passed = 0;
    let mut failed = 0;
    let mut skipped = 0;

    // Create output renderer based on configuration
    let output_config = OutputConfig::from_run_config(config);
    let mut renderer: Box<dyn OutputRenderer> = if let Some(ref callback) = config.event_callback {
        // Use event stream renderer when callback is provided
        let callback_clone = callback.clone_ref(py);
        Box::new(EventStreamRenderer::new(Some(callback_clone)))
    } else {
        // Fall back to default spinner display
        Box::new(SpinnerDisplay::new(
            output_config.use_colors,
            output_config.ascii_mode,
        ))
    };

    // Display collection errors before running tests (like pytest does)
    for error in collection_errors {
        renderer.collection_error(error);
    }

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

        // Check for package boundary transition
        let module_package = extract_package_name(&module.path);
        if context.current_package.as_ref() != Some(&module_package) {
            // Package changed - run teardowns and clear caches
            // Clear class cache first (narrowest scope teardowns first)
            finalize_generators(
                py,
                &mut context.teardowns.class,
                context.class_event_loop.as_ref(),
            );
            context.class_cache.clear();
            close_event_loop(py, &mut context.class_event_loop);

            // Then package teardowns
            finalize_generators(
                py,
                &mut context.teardowns.package,
                context.package_event_loop.as_ref(),
            );
            context.package_cache.clear();
            close_event_loop(py, &mut context.package_event_loop);
            context.current_package = Some(module_package);
        }

        // Reset module-scoped caches for this module
        context.module_cache.clear();
        close_event_loop(py, &mut context.module_event_loop);

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

            // Partition tests for optimal async parallelization
            let execution_units = partition_tests_for_parallel(py, &tests, &module.fixtures);

            for unit in execution_units {
                let (unit_results, is_plain_function_test): (Vec<PyTestResult>, bool) = match unit {
                    TestExecutionUnit::Single(test) => {
                        let result = run_single_test(py, module, test, config, &mut context)?;
                        let is_plain = test.class_name.is_none();
                        (vec![result], is_plain)
                    }
                    TestExecutionUnit::Batch(batch) => {
                        let batch_results =
                            run_async_batch(py, module, &batch, config, &mut context)?;
                        // For batches, check if any test is a plain function test
                        let any_plain = batch.tests.iter().any(|t| t.class_name.is_none());
                        (
                            batch_results.into_iter().map(|(_, r)| r).collect(),
                            any_plain,
                        )
                    }
                };

                let mut should_fail_fast = false;

                for result in unit_results {
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

                    // Check for fail-fast mode
                    if config.fail_fast && is_failed {
                        should_fail_fast = true;
                    }
                }

                // If this was a plain function test (no class), clear class cache
                // Class-scoped fixtures should NOT be shared across plain function tests
                if is_plain_function_test {
                    context.class_cache.clear();
                    finalize_generators(
                        py,
                        &mut context.teardowns.class,
                        context.class_event_loop.as_ref(),
                    );
                }

                // Handle fail-fast after processing all results in the unit
                if should_fail_fast {
                    // Clean up fixtures before returning early
                    finalize_generators(
                        py,
                        &mut context.teardowns.class,
                        context.class_event_loop.as_ref(),
                    );
                    close_event_loop(py, &mut context.class_event_loop);
                    finalize_generators(
                        py,
                        &mut context.teardowns.module,
                        context.module_event_loop.as_ref(),
                    );
                    close_event_loop(py, &mut context.module_event_loop);
                    finalize_generators(
                        py,
                        &mut context.teardowns.package,
                        context.package_event_loop.as_ref(),
                    );
                    close_event_loop(py, &mut context.package_event_loop);
                    finalize_generators(
                        py,
                        &mut context.teardowns.session,
                        context.session_event_loop.as_ref(),
                    );
                    close_event_loop(py, &mut context.session_event_loop);

                    let duration = start.elapsed();
                    let total = passed + failed + skipped;

                    // Notify renderer of early exit
                    renderer.finish_suite(
                        total,
                        passed,
                        failed,
                        skipped,
                        collection_errors.len(),
                        duration,
                    );

                    let report = PyRunReport::new(
                        total,
                        passed,
                        failed,
                        skipped,
                        duration.as_secs_f64(),
                        results,
                        collection_errors.to_vec(),
                    );

                    // Write cache before returning
                    write_failed_tests_cache(&report)?;

                    return Ok(report);
                }

                // Check for signals (like Ctrl+C) after each execution unit
                // This allows users to interrupt test runs with KeyboardInterrupt
                py.check_signals()?;
            }

            // Class-scoped fixtures are dropped here - run teardowns
            finalize_generators(
                py,
                &mut context.teardowns.class,
                context.class_event_loop.as_ref(),
            );
            close_event_loop(py, &mut context.class_event_loop);
        }

        // Module-scoped fixtures are dropped here - run teardowns
        finalize_generators(
            py,
            &mut context.teardowns.module,
            context.module_event_loop.as_ref(),
        );

        // Notify renderer that this file is complete
        let file_duration = file_start.elapsed();
        renderer.file_completed(
            &to_relative_path(&module.path),
            file_duration,
            file_passed,
            file_failed,
            file_skipped,
        );

        // Check for signals (like Ctrl+C) after each file/module
        // This allows users to interrupt test runs with KeyboardInterrupt
        py.check_signals()?;
    }

    // Package-scoped fixtures are dropped here - run teardowns for last package
    finalize_generators(
        py,
        &mut context.teardowns.package,
        context.package_event_loop.as_ref(),
    );
    close_event_loop(py, &mut context.package_event_loop);

    // Session-scoped fixtures are dropped here - run teardowns
    finalize_generators(
        py,
        &mut context.teardowns.session,
        context.session_event_loop.as_ref(),
    );
    close_event_loop(py, &mut context.session_event_loop);

    let duration = start.elapsed();
    let total = passed + failed + skipped;

    // Notify renderer that the entire suite is complete
    renderer.finish_suite(
        total,
        passed,
        failed,
        skipped,
        collection_errors.len(),
        duration,
    );

    let report = PyRunReport::new(
        total,
        passed,
        failed,
        skipped,
        duration.as_secs_f64(),
        results,
        collection_errors.to_vec(),
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
            to_relative_path(&test_case.path),
            0.0,
            reason.clone(),
            test_case.mark_names(),
        ));
    }

    let start = Instant::now();
    let outcome = execute_test_case(py, module, test_case, config, context);
    let duration = start.elapsed().as_secs_f64();
    let name = test_case.display_name.clone();
    let path = to_relative_path(&test_case.path);

    match outcome {
        Ok(success) => Ok(PyTestResult::passed(
            name,
            path,
            duration,
            success.stdout,
            success.stderr,
            test_case.mark_names(),
        )),
        Err(failure) => {
            // Check if this is a skip exception
            if is_skip_exception(&failure.message) {
                // Extract skip reason from the message
                let reason = extract_skip_reason(&failure.message);
                Ok(PyTestResult::skipped(
                    name,
                    path,
                    duration,
                    reason,
                    test_case.mark_names(),
                ))
            } else {
                Ok(PyTestResult::failed(
                    name,
                    path,
                    duration,
                    failure.message,
                    failure.stdout,
                    failure.stderr,
                    test_case.mark_names(),
                ))
            }
        }
    }
}

/// Check if an error message indicates a skipped test.
///
/// Detects `rustest.decorators.Skipped`, `pytest.skip.Exception`, and common skip patterns.
fn is_skip_exception(message: &str) -> bool {
    // Check for the full module path in traceback
    message.contains("rustest.decorators.Skipped")
        || message.contains("pytest.skip.Exception")
        // Also check for the exception type at line start (common traceback format)
        || message.lines().any(|line| {
            let trimmed = line.trim();
            trimmed.starts_with("Skipped:") || trimmed.ends_with(".Skipped")
        })
}

/// Extract the skip reason from a skip exception message.
///
/// Parses the exception message to extract the reason text.
fn extract_skip_reason(message: &str) -> String {
    // Try to extract reason from exception message
    // Format: "rustest.decorators.Skipped: reason text"
    if let Some(pos) = message.find("Skipped: ") {
        let reason = &message[pos + 9..]; // Skip "Skipped: "
                                          // Take the first line of the reason
        reason.lines().next().unwrap_or(reason).to_string()
    } else if let Some(pos) = message.find("skip.Exception: ") {
        let reason = &message[pos + 16..]; // Skip "skip.Exception: "
        reason.lines().next().unwrap_or(reason).to_string()
    } else {
        // Fallback: use the entire message
        message.lines().next().unwrap_or(message).to_string()
    }
}

/// Run a batch of async tests in parallel using asyncio.gather().
///
/// This function:
/// 1. Sets up the shared event loop for the batch's loop scope
/// 2. Resolves shared fixtures (scopes >= loop_scope) once before the batch
/// 3. For each test: resolves function-scoped fixtures and creates the coroutine
/// 4. Runs all coroutines in parallel via Python's asyncio.gather()
/// 5. Returns results for each test
///
/// Returns a vector of (test_case, result) tuples in the same order as input.
fn run_async_batch<'a>(
    py: Python<'_>,
    module: &TestModule,
    batch: &AsyncBatch<'a>,
    config: &RunConfiguration,
    context: &mut FixtureContext,
) -> PyResult<Vec<(&'a TestCase, PyTestResult)>> {
    let mut results: Vec<(&TestCase, PyTestResult)> = Vec::with_capacity(batch.tests.len());

    // When fail-fast is enabled, fall back to sequential execution
    // (fail-fast semantics require stopping on first failure)
    // Note: Batches are guaranteed to have at least 2 tests by partition_tests_for_parallel
    if config.fail_fast {
        for test in &batch.tests {
            let result = run_single_test(py, module, test, config, context)?;
            let is_failed = result.status == "failed";
            results.push((test, result));
            if is_failed {
                break;
            }
        }
        return Ok(results);
    }

    // Prepare test execution data
    // We need to:
    // 1. Resolve shared fixtures once (these are cached by scope)
    // 2. Create a coroutine for each test with its resolved arguments
    // 3. Run all coroutines in parallel

    let mut test_coroutines: Vec<TestSpec> = Vec::new();
    let mut test_function_teardowns: Vec<(String, Vec<Py<PyAny>>)> = Vec::new();
    let mut preparation_errors: Vec<(String, String)> = Vec::new();

    // Get or create the event loop for this batch's scope
    let event_loop = get_or_create_context_event_loop(py, batch.loop_scope, context)?;

    for test in &batch.tests {
        let test_id = test.unique_id();

        // Validate loop scope compatibility
        if let Some(error_message) = validate_loop_scope_compatibility(py, test, &module.fixtures) {
            preparation_errors.push((
                test_id.clone(),
                format!("Loop scope validation error:\n{}", error_message),
            ));
            continue;
        }

        // Create a resolver for this test
        let test_display_name = test.display_name.clone();
        let test_nodeid = test.unique_id();
        let test_marks = test.marks.clone();

        let mut resolver = FixtureResolver::new(
            py,
            &module.fixtures,
            &test.parameter_values,
            &mut context.session_cache,
            &mut context.package_cache,
            &mut context.module_cache,
            &mut context.class_cache,
            &mut context.teardowns,
            &test.fixture_param_indices,
            &test.indirect_params,
            &mut context.session_event_loop,
            &mut context.package_event_loop,
            &mut context.module_event_loop,
            &mut context.class_event_loop,
            test.class_name.as_deref(),
            batch.loop_scope,
            test_display_name,
            test_nodeid,
            test_marks.clone(),
        );

        // Populate fixture registry
        if let Err(err) = populate_fixture_registry(py, &module.fixtures) {
            let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
            preparation_errors.push((
                test_id.clone(),
                format!("Fixture registry error:\n{}", message),
            ));
            continue;
        }

        // Resolve autouse fixtures
        {
            let _resolver_guard = ResolverActivationGuard::new(&mut resolver);
            if let Err(err) = resolver.resolve_autouse_fixtures() {
                let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
                preparation_errors.push((
                    test_id.clone(),
                    format!("Autouse fixture setup error:\n{}", message),
                ));
                continue;
            }

            if let Err(err) = resolver.apply_usefixtures_marks() {
                let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
                preparation_errors.push((
                    test_id.clone(),
                    format!("Usefixtures mark error:\n{}", message),
                ));
                continue;
            }

            // Resolve all arguments for this test
            let mut call_args = Vec::new();
            let mut resolution_failed = false;
            for param in &test.parameters {
                match resolver.resolve_argument(param) {
                    Ok(value) => call_args.push(value),
                    Err(err) => {
                        let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
                        preparation_errors.push((
                            test_id.clone(),
                            format!("Fixture '{}' resolution error:\n{}", param, message),
                        ));
                        resolution_failed = true;
                        break;
                    }
                }
            }

            if resolution_failed {
                // Clean up function teardowns for this test
                finalize_generators(
                    py,
                    &mut resolver.function_teardowns,
                    resolver.function_event_loop.as_ref(),
                );
                continue;
            }

            // Extract timeout from asyncio mark(s) if present
            // A test may have multiple asyncio marks (one with timeout, one from class decoration)
            let timeout = test_marks
                .iter()
                .filter(|m| m.is_named("asyncio"))
                .find_map(|m| {
                    m.kwargs
                        .bind(py)
                        .get_item("timeout")
                        .ok()
                        .flatten()
                        .and_then(|v| v.extract::<f64>().ok())
                });

            // Store the test's callable and args for parallel execution
            test_coroutines.push((
                test_id.clone(),
                test.callable.clone_ref(py),
                call_args,
                timeout,
            ));

            // Store function teardowns to run after all tests complete
            test_function_teardowns
                .push((test_id, resolver.function_teardowns.drain(..).collect()));
        }
    }

    // Add preparation errors as failed results
    for (test_id, error_message) in preparation_errors {
        if let Some(test) = batch.tests.iter().find(|t| t.unique_id() == test_id) {
            results.push((
                *test,
                PyTestResult::failed(
                    test.display_name.clone(),
                    to_relative_path(&test.path),
                    0.0,
                    error_message,
                    None,
                    None,
                    test.mark_names(),
                ),
            ));
        }
    }

    // If no tests to run in parallel, return early (but ensure teardowns run)
    if test_coroutines.is_empty() {
        // Run any pending teardowns from preparation phase
        for (_, mut teardowns) in test_function_teardowns {
            finalize_generators(py, &mut teardowns, Some(&event_loop));
        }
        return Ok(results);
    }

    // Run all test coroutines in parallel using Python's asyncio.gather
    // Use a closure to ensure teardowns run even if parallel execution fails
    let parallel_results =
        match run_coroutines_parallel(py, &event_loop, &test_coroutines, config.capture_output) {
            Ok(results) => results,
            Err(e) => {
                // Ensure teardowns run even on error
                for (_, mut teardowns) in test_function_teardowns {
                    finalize_generators(py, &mut teardowns, Some(&event_loop));
                }
                return Err(e);
            }
        };

    // Process results and run teardowns
    for ((test_id, _, _, _), result_dict) in test_coroutines.iter().zip(parallel_results.iter()) {
        // Find the corresponding test
        let test = batch.tests.iter().find(|t| t.unique_id() == *test_id);
        let test = match test {
            Some(t) => *t,
            None => continue,
        };

        // Find and run teardowns for this test
        if let Some((_, teardowns)) = test_function_teardowns
            .iter_mut()
            .find(|(id, _)| id == test_id)
        {
            finalize_generators(py, teardowns, Some(&event_loop));
        }

        // Extract result from dictionary
        let success: bool = result_dict
            .get_item("success")?
            .map(|v| v.extract().unwrap_or(false))
            .unwrap_or(false);
        let duration: f64 = result_dict
            .get_item("duration")?
            .map(|v| v.extract().unwrap_or(0.0))
            .unwrap_or(0.0);
        let error_message: Option<String> = result_dict
            .get_item("error_message")?
            .and_then(|v| v.extract().ok());
        let stdout: Option<String> = result_dict
            .get_item("stdout")?
            .and_then(|v| v.extract().ok());
        let stderr: Option<String> = result_dict
            .get_item("stderr")?
            .and_then(|v| v.extract().ok());

        let result = if success {
            PyTestResult::passed(
                test.display_name.clone(),
                to_relative_path(&test.path),
                duration,
                stdout,
                stderr,
                test.mark_names(),
            )
        } else {
            match error_message {
                Some(ref msg) if is_skip_exception(msg) => {
                    let reason = extract_skip_reason(msg);
                    PyTestResult::skipped(
                        test.display_name.clone(),
                        to_relative_path(&test.path),
                        duration,
                        reason,
                        test.mark_names(),
                    )
                }
                Some(msg) => PyTestResult::failed(
                    test.display_name.clone(),
                    to_relative_path(&test.path),
                    duration,
                    msg,
                    stdout,
                    stderr,
                    test.mark_names(),
                ),
                None => PyTestResult::failed(
                    test.display_name.clone(),
                    to_relative_path(&test.path),
                    duration,
                    "Unknown error".to_string(),
                    stdout,
                    stderr,
                    test.mark_names(),
                ),
            }
        };

        results.push((test, result));
    }

    Ok(results)
}

/// Get or create an event loop for the given scope from context.
fn get_or_create_context_event_loop(
    py: Python<'_>,
    scope: FixtureScope,
    context: &mut FixtureContext,
) -> PyResult<Py<PyAny>> {
    let event_loop_opt = match scope {
        FixtureScope::Session => &mut context.session_event_loop,
        FixtureScope::Package => &mut context.package_event_loop,
        FixtureScope::Module => &mut context.module_event_loop,
        FixtureScope::Class => &mut context.class_event_loop,
        FixtureScope::Function => {
            // Function scope doesn't make sense for batching, but handle it gracefully
            return Err(PyRuntimeError::new_err(
                "Cannot create shared event loop for function scope in batch execution",
            ));
        }
    };

    // Check if a loop already exists and is still open
    if let Some(ref loop_obj) = event_loop_opt {
        let is_closed = loop_obj
            .bind(py)
            .call_method0("is_closed")?
            .extract::<bool>()?;
        if !is_closed {
            return Ok(loop_obj.clone_ref(py));
        }
    }

    // Create a new event loop
    let asyncio = py.import("asyncio")?;
    let new_loop = asyncio.call_method0("new_event_loop")?.unbind();
    asyncio.call_method1("set_event_loop", (&new_loop.bind(py),))?;

    // Store it for reuse
    *event_loop_opt = Some(new_loop.clone_ref(py));

    Ok(new_loop)
}

/// A test specification for parallel execution: (test_id, callable, args, timeout).
type TestSpec = (String, Py<PyAny>, Vec<Py<PyAny>>, Option<f64>);

/// Run multiple test coroutines in parallel using Python's asyncio.gather.
///
/// This function:
/// 1. Creates coroutines by calling each test callable with its arguments
/// 2. Uses asyncio.gather to run them concurrently
/// 3. Wraps each coroutine to capture its result, stdout, stderr, and timing
fn run_coroutines_parallel<'py>(
    py: Python<'py>,
    event_loop: &Py<PyAny>,
    test_specs: &[TestSpec],
    capture_output: bool,
) -> PyResult<Vec<Bound<'py, PyDict>>> {
    // Import the async executor module
    let executor_module = py.import("rustest.async_executor")?;
    let run_parallel = executor_module.getattr("run_coroutines_parallel")?;

    // Create coroutines by calling each test callable
    let mut coroutines_list: Vec<(String, Py<PyAny>, Option<f64>)> = Vec::new();

    for (test_id, callable, args, timeout) in test_specs {
        let args_tuple = PyTuple::new(py, args)?;
        match callable.bind(py).call1(args_tuple) {
            Ok(coro) => {
                coroutines_list.push((test_id.clone(), coro.unbind(), *timeout));
            }
            Err(e) => {
                // Close any already-created coroutines to avoid "coroutine never awaited" warnings
                for (_, coro, _) in coroutines_list.drain(..) {
                    let _ = coro.bind(py).call_method0("close");
                }
                return Err(e);
            }
        }
    }

    // Convert to Python list of tuples (test_id, coro, timeout)
    let py_coroutines = PyList::new(
        py,
        coroutines_list.iter().map(|(id, coro, timeout)| {
            let timeout_py: Py<PyAny> = match timeout {
                Some(t) => t.into_pyobject(py).unwrap().into_any().unbind(),
                None => py.None(),
            };
            let tuple = PyTuple::new(
                py,
                [
                    id.as_str().into_pyobject(py).unwrap().into_any(),
                    coro.bind(py).clone().into_any(),
                    timeout_py.bind(py).clone().into_any(),
                ],
            )
            .unwrap();
            tuple
        }),
    )?;

    // Call the Python function
    let result = run_parallel.call1((event_loop, py_coroutines, capture_output))?;

    // Extract the list of result dictionaries
    let result_list = result.extract::<Vec<Bound<'py, PyDict>>>()?;

    Ok(result_list)
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

/// Populate the Python fixture registry for getfixturevalue() support.
///
/// This makes all fixtures available to the Python-side getfixturevalue() method
/// by registering them in a global registry that can be accessed from Python.
fn populate_fixture_registry(py: Python<'_>, fixtures: &IndexMap<String, Fixture>) -> PyResult<()> {
    let registry_module = py.import("rustest.fixture_registry")?;
    let register_fixtures = registry_module.getattr("register_fixtures")?;

    // Create a dictionary mapping fixture names to their callables
    let fixtures_dict = PyDict::new(py);
    for (name, fixture) in fixtures.iter() {
        let callable = fixture.callable.bind(py);
        fixtures_dict.set_item(name, callable)?;
    }

    // Register the fixtures
    register_fixtures.call1((fixtures_dict,))?;

    Ok(())
}

/// Extract the loop_scope from a test's asyncio mark(s), if present.
/// Returns Some(scope) if explicitly specified in any asyncio mark, None otherwise.
/// Note: A test may have multiple asyncio marks (e.g., one for timeout, one from class decoration).
fn get_explicit_loop_scope_from_marks(
    py: Python<'_>,
    test_case: &TestCase,
) -> Option<FixtureScope> {
    // Check all asyncio marks - a test might have multiple (one with timeout, one with loop_scope)
    for mark in &test_case.marks {
        if mark.is_named("asyncio") {
            if let Some(loop_scope_value) = mark.get_kwarg(py, "loop_scope") {
                if let Ok(loop_scope_str) = loop_scope_value.bind(py).extract::<String>() {
                    // Convert loop_scope string to FixtureScope
                    return Some(match loop_scope_str.as_str() {
                        "session" => FixtureScope::Session,
                        "package" => FixtureScope::Package,
                        "module" => FixtureScope::Module,
                        "class" => FixtureScope::Class,
                        _ => FixtureScope::Function,
                    });
                }
            }
            // This asyncio mark has no loop_scope, but keep checking other marks
        }
    }
    // No asyncio mark with loop_scope found
    None
}

/// Analyze test's fixture dependencies to find the widest async fixture scope.
/// This enables automatic loop scope detection based on what fixtures the test uses.
///
/// Returns the widest scope of any async fixture used by the test, or Function if none.
fn detect_required_loop_scope_from_fixtures(
    fixtures: &IndexMap<String, Fixture>,
    test_params: &[String],
) -> FixtureScope {
    let mut widest_scope = FixtureScope::Function;
    let mut visited = HashSet::new();

    // Recursively analyze fixture dependencies
    for param in test_params {
        analyze_fixture_scope(fixtures, param, &mut widest_scope, &mut visited);
    }

    widest_scope
}

/// Recursively analyze a fixture and its dependencies to find async fixtures.
fn analyze_fixture_scope(
    fixtures: &IndexMap<String, Fixture>,
    fixture_name: &str,
    widest_scope: &mut FixtureScope,
    visited: &mut HashSet<String>,
) {
    // Avoid infinite recursion
    if visited.contains(fixture_name) {
        return;
    }
    visited.insert(fixture_name.to_string());

    if let Some(fixture) = fixtures.get(fixture_name) {
        // If this is an async fixture, check if its scope is wider
        if (fixture.is_async || fixture.is_async_generator)
            && is_scope_wider(&fixture.scope, widest_scope)
        {
            *widest_scope = fixture.scope;
        }

        // Recursively analyze this fixture's dependencies
        for dep in &fixture.parameters {
            analyze_fixture_scope(fixtures, dep, widest_scope, visited);
        }
    }
}

/// Check if scope_a is wider than scope_b.
fn is_scope_wider(scope_a: &FixtureScope, scope_b: &FixtureScope) -> bool {
    let order = |s: &FixtureScope| match s {
        FixtureScope::Function => 0,
        FixtureScope::Class => 1,
        FixtureScope::Module => 2,
        FixtureScope::Package => 3,
        FixtureScope::Session => 4,
    };
    order(scope_a) > order(scope_b)
}

/// Convert a FixtureScope to its string representation for error messages.
fn scope_to_string(scope: &FixtureScope) -> &'static str {
    match scope {
        FixtureScope::Function => "function",
        FixtureScope::Class => "class",
        FixtureScope::Module => "module",
        FixtureScope::Package => "package",
        FixtureScope::Session => "session",
    }
}

/// Validate that an explicit loop_scope is compatible with the test's fixture requirements.
///
/// Returns an error message if the explicit scope is too narrow for the fixtures used.
/// This helps users understand why they're getting "attached to a different loop" errors.
fn validate_loop_scope_compatibility(
    py: Python<'_>,
    test_case: &TestCase,
    fixtures: &IndexMap<String, Fixture>,
) -> Option<String> {
    // Only validate if there's an explicit loop_scope
    let explicit_scope = get_explicit_loop_scope_from_marks(py, test_case)?;

    // Detect what scope is required by fixtures
    let required_scope = detect_required_loop_scope_from_fixtures(fixtures, &test_case.parameters);

    // Check if explicit scope is narrower than required
    if is_scope_wider(&required_scope, &explicit_scope) {
        // Find the async fixture(s) that require the wider scope
        let mut problematic_fixtures = Vec::new();
        let mut visited = HashSet::new();
        for param in &test_case.parameters {
            find_async_fixtures_with_scope(
                fixtures,
                param,
                &required_scope,
                &mut problematic_fixtures,
                &mut visited,
            );
        }

        let fixture_list = if problematic_fixtures.is_empty() {
            "async fixtures".to_string()
        } else {
            problematic_fixtures
                .iter()
                .map(|s| format!("'{}'", s))
                .collect::<Vec<_>>()
                .join(", ")
        };

        let test_name = &test_case.name;
        let explicit_str = scope_to_string(&explicit_scope);
        let required_str = scope_to_string(&required_scope);

        return Some(format!(
            "Loop scope mismatch: Test '{}' uses @mark.asyncio(loop_scope=\"{}\") but depends on \
{}-scoped async fixture(s): {}.\n\n\
This will cause 'attached to a different loop' errors because the test creates a new event loop \
for each {} while the fixture expects to reuse the {} loop.\n\n\
To fix this, either:\n\
  1. Remove the explicit loop_scope to let rustest auto-detect it: @mark.asyncio\n\
  2. Use a wider loop_scope: @mark.asyncio(loop_scope=\"{}\")\n\
  3. Change the fixture scope to match your loop_scope",
            test_name,
            explicit_str,
            required_str,
            fixture_list,
            explicit_str,
            required_str,
            required_str,
        ));
    }

    None
}

/// Find async fixtures that have a specific scope, for error reporting.
fn find_async_fixtures_with_scope(
    fixtures: &IndexMap<String, Fixture>,
    fixture_name: &str,
    target_scope: &FixtureScope,
    found: &mut Vec<String>,
    visited: &mut HashSet<String>,
) {
    if visited.contains(fixture_name) {
        return;
    }
    visited.insert(fixture_name.to_string());

    if let Some(fixture) = fixtures.get(fixture_name) {
        // Check if this is the async fixture with the target scope
        if (fixture.is_async || fixture.is_async_generator) && fixture.scope == *target_scope {
            found.push(fixture_name.to_string());
        }

        // Recursively check dependencies
        for dep in &fixture.parameters {
            find_async_fixtures_with_scope(fixtures, dep, target_scope, found, visited);
        }
    }
}

/// Determine the appropriate loop scope for a test.
///
/// Strategy (matching pytest-asyncio with smart defaults):
/// 1. If @mark.asyncio(loop_scope="...") is explicit, use that
/// 2. Otherwise, analyze fixture dependencies to find widest async fixture scope
/// 3. Default to function scope if no async fixtures are used
///
/// This provides automatic compatibility: tests using session async fixtures
/// automatically share the session loop without explicit configuration.
fn determine_test_loop_scope(
    py: Python<'_>,
    test_case: &TestCase,
    fixtures: &IndexMap<String, Fixture>,
) -> FixtureScope {
    // Check for explicit loop_scope mark first
    if let Some(explicit_scope) = get_explicit_loop_scope_from_marks(py, test_case) {
        return explicit_scope;
    }

    // Analyze fixture dependencies to find required scope
    detect_required_loop_scope_from_fixtures(fixtures, &test_case.parameters)
}

/// Execute a test case and return either success metadata or failure details.
fn execute_test_case(
    py: Python<'_>,
    module: &TestModule,
    test_case: &TestCase,
    config: &RunConfiguration,
    context: &mut FixtureContext,
) -> Result<TestCallSuccess, TestCallFailure> {
    // Validate loop scope compatibility before running the test
    // This catches cases where explicit loop_scope is too narrow for the fixtures used
    if let Some(error_message) = validate_loop_scope_compatibility(py, test_case, &module.fixtures)
    {
        return Err(TestCallFailure {
            message: error_message,
            stdout: None,
            stderr: None,
        });
    }

    // Determine loop scope: explicit mark or smart detection based on fixture dependencies
    let test_loop_scope = determine_test_loop_scope(py, test_case, &module.fixtures);

    let test_display_name = test_case.display_name.clone();
    let test_nodeid = test_case.unique_id();
    let test_marks = test_case.marks.clone();

    let mut resolver = FixtureResolver::new(
        py,
        &module.fixtures,
        &test_case.parameter_values,
        &mut context.session_cache,
        &mut context.package_cache,
        &mut context.module_cache,
        &mut context.class_cache,
        &mut context.teardowns,
        &test_case.fixture_param_indices,
        &test_case.indirect_params,
        &mut context.session_event_loop,
        &mut context.package_event_loop,
        &mut context.module_event_loop,
        &mut context.class_event_loop,
        test_case.class_name.as_deref(),
        test_loop_scope,
        test_display_name,
        test_nodeid,
        test_marks.clone(),
    );

    let _resolver_guard = ResolverActivationGuard::new(&mut resolver);

    // Populate Python fixture registry for getfixturevalue() support
    if let Err(err) = populate_fixture_registry(py, &module.fixtures) {
        let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
        return Err(TestCallFailure {
            message,
            stdout: None,
            stderr: None,
        });
    }

    // Resolve autouse fixtures first
    if let Err(err) = resolver.resolve_autouse_fixtures() {
        let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
        return Err(TestCallFailure {
            message,
            stdout: None,
            stderr: None,
        });
    }

    if let Err(err) = resolver.apply_usefixtures_marks() {
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
        let result = callable.call1(args_tuple)?;

        // Check if the result is a coroutine (async test function)
        let inspect = py.import("inspect")?;
        let is_coroutine = inspect
            .call_method1("iscoroutine", (&result,))?
            .is_truthy()?;

        if is_coroutine {
            // Get or reuse the session event loop to ensure compatibility with async fixtures
            // This prevents "Task got Future attached to a different loop" errors
            let event_loop = resolver.get_or_create_test_event_loop()?;

            // Extract timeout from asyncio mark(s) if present
            let timeout: Option<f64> = test_marks
                .iter()
                .filter(|m| m.is_named("asyncio"))
                .find_map(|m| {
                    m.kwargs
                        .bind(py)
                        .get_item("timeout")
                        .ok()
                        .flatten()
                        .and_then(|v| v.extract::<f64>().ok())
                });

            // Apply timeout if specified
            let coro_to_run = if let Some(timeout_secs) = timeout {
                let asyncio = py.import("asyncio")?;
                asyncio.call_method1("wait_for", (&result, timeout_secs))?
            } else {
                result
            };

            Ok(event_loop
                .bind(py)
                .call_method1("run_until_complete", (&coro_to_run,))?
                .unbind())
        } else {
            Ok(result.unbind())
        }
    });

    let (result, stdout, stderr) = match call_result {
        Ok(value) => value,
        Err(err) => {
            // Clean up function-scoped fixtures before returning
            finalize_generators(
                py,
                &mut resolver.function_teardowns,
                resolver.function_event_loop.as_ref(),
            );
            return Err(TestCallFailure {
                message: err.to_string(),
                stdout: None,
                stderr: None,
            });
        }
    };

    // Clean up function-scoped fixtures after test completes
    finalize_generators(
        py,
        &mut resolver.function_teardowns,
        resolver.function_event_loop.as_ref(),
    );

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
/// - Package cache: shared across all tests in a package
/// - Module cache: shared across all tests in a module
/// - Class cache: shared across all tests in a class
/// - Function cache: per-test, created fresh each time
///
/// When resolving a fixture, it checks caches in order based on the fixture's scope.
struct FixtureResolver<'py> {
    py: Python<'py>,
    fixtures: &'py IndexMap<String, Fixture>,
    session_cache: &'py mut IndexMap<String, Py<PyAny>>,
    package_cache: &'py mut IndexMap<String, Py<PyAny>>,
    module_cache: &'py mut IndexMap<String, Py<PyAny>>,
    class_cache: &'py mut IndexMap<String, Py<PyAny>>,
    function_cache: IndexMap<String, Py<PyAny>>,
    teardowns: &'py mut TeardownCollector,
    function_teardowns: Vec<Py<PyAny>>,
    stack: HashSet<String>,
    parameters: &'py ParameterMap,
    /// Maps fixture name to the parameter index to use for parametrized fixtures.
    fixture_param_indices: &'py IndexMap<String, usize>,
    /// Current fixture param values being resolved, for request.param support.
    current_fixture_param: Option<Py<PyAny>>,
    /// Parameter names that should be resolved as fixture references (indirect parametrization).
    indirect_params: &'py [String],
    /// Event loops for different scopes (for async fixtures)
    session_event_loop: &'py mut Option<Py<PyAny>>,
    package_event_loop: &'py mut Option<Py<PyAny>>,
    module_event_loop: &'py mut Option<Py<PyAny>>,
    class_event_loop: &'py mut Option<Py<PyAny>>,
    function_event_loop: Option<Py<PyAny>>,
    /// Current test's class name (for filtering class-scoped autouse fixtures)
    test_class_name: Option<&'py str>,
    /// Loop scope for the current test (from @mark.asyncio(loop_scope="..."))
    test_loop_scope: FixtureScope,
    /// Display name for the current test (used for request.node.name)
    test_display_name: String,
    /// Fully qualified identifier for the current test (used for request.node.nodeid)
    test_nodeid: String,
    /// Marks attached to the current test
    test_marks: Vec<Mark>,
}

impl<'py> FixtureResolver<'py> {
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'py>,
        fixtures: &'py IndexMap<String, Fixture>,
        parameters: &'py ParameterMap,
        session_cache: &'py mut IndexMap<String, Py<PyAny>>,
        package_cache: &'py mut IndexMap<String, Py<PyAny>>,
        module_cache: &'py mut IndexMap<String, Py<PyAny>>,
        class_cache: &'py mut IndexMap<String, Py<PyAny>>,
        teardowns: &'py mut TeardownCollector,
        fixture_param_indices: &'py IndexMap<String, usize>,
        indirect_params: &'py [String],
        session_event_loop: &'py mut Option<Py<PyAny>>,
        package_event_loop: &'py mut Option<Py<PyAny>>,
        module_event_loop: &'py mut Option<Py<PyAny>>,
        class_event_loop: &'py mut Option<Py<PyAny>>,
        test_class_name: Option<&'py str>,
        test_loop_scope: FixtureScope,
        test_display_name: String,
        test_nodeid: String,
        test_marks: Vec<Mark>,
    ) -> Self {
        Self {
            py,
            fixtures,
            session_cache,
            package_cache,
            module_cache,
            class_cache,
            function_cache: IndexMap::new(),
            teardowns,
            function_teardowns: Vec::new(),
            stack: HashSet::new(),
            parameters,
            fixture_param_indices,
            current_fixture_param: None,
            indirect_params,
            session_event_loop,
            package_event_loop,
            module_event_loop,
            class_event_loop,
            function_event_loop: None,
            test_class_name,
            test_loop_scope,
            test_display_name,
            test_nodeid,
            test_marks,
        }
    }

    fn resolve_argument(&mut self, name: &str) -> PyResult<Py<PyAny>> {
        // First check if it's a parametrized value
        if let Some(value) = self.parameters.get(name) {
            // If this parameter is indirect, treat its value as a fixture name
            if self.indirect_params.contains(&name.to_string()) {
                // Extract the fixture name from the parameter value
                let fixture_name: String = value.bind(self.py).extract()?;
                // Resolve the fixture by its name (recursive call without the parameter)
                return self.resolve_argument(&fixture_name);
            }
            // Otherwise, return the value directly
            return Ok(value.clone_ref(self.py));
        }

        self.resolve_fixture_value(name)
    }

    fn resolve_fixture_value(&mut self, name: &str) -> PyResult<Py<PyAny>> {
        // Special handling for "request" fixture - create with current param value
        if name == "request" {
            return self.create_request_fixture();
        }

        // Check if this is a parametrized fixture and get the cache key
        let (cache_key, param_value) = if let Some(&param_idx) =
            self.fixture_param_indices.get(name)
        {
            if let Some(fixture) = self.fixtures.get(name) {
                if let Some(params) = &fixture.params {
                    // Bounds check to prevent panic on invalid param_idx
                    if param_idx >= params.len() {
                        return Err(invalid_test_definition(format!(
                                "Invalid parameter index {} for fixture '{}' which only has {} parameters. \
                                 This may indicate a mismatch between test parametrization and fixture definition.",
                                param_idx, name, params.len()
                            )));
                    }
                    let param = &params[param_idx];
                    // Use a cache key that includes the parameter index for parametrized fixtures
                    let key = format!("{}[{}]", name, param_idx);
                    (key, Some(param.value.clone_ref(self.py)))
                } else {
                    (name.to_string(), None)
                }
            } else {
                (name.to_string(), None)
            }
        } else {
            (name.to_string(), None)
        };

        // Check all caches in order: function -> class -> module -> package -> session
        if let Some(value) = self.function_cache.get(&cache_key) {
            return Ok(value.clone_ref(self.py));
        }
        if let Some(value) = self.class_cache.get(&cache_key) {
            return Ok(value.clone_ref(self.py));
        }
        if let Some(value) = self.module_cache.get(&cache_key) {
            return Ok(value.clone_ref(self.py));
        }
        if let Some(value) = self.package_cache.get(&cache_key) {
            return Ok(value.clone_ref(self.py));
        }
        if let Some(value) = self.session_cache.get(&cache_key) {
            return Ok(value.clone_ref(self.py));
        }

        // Fixture not in any cache, need to execute it
        let fixture = self.fixtures.get(name).ok_or_else(|| {
            let mut available: Vec<&str> = self.fixtures.keys().map(String::as_str).collect();
            available.sort();
            let available_list = available.join(", ");
            invalid_test_definition(format!(
                "Unknown fixture '{}'.\nAvailable fixtures: {}",
                name, available_list
            ))
        })?;

        // Set current fixture param for request.param access
        let previous_param = self.current_fixture_param.take();
        self.current_fixture_param = param_value;

        // Detect circular dependencies
        if !self.stack.insert(fixture.name.clone()) {
            return Err(PyRuntimeError::new_err(format!(
                "Detected recursive fixture dependency involving '{}'.",
                fixture.name
            )));
        }

        // Validate scope ordering: higher-scoped fixtures cannot depend on lower-scoped ones
        // This check happens during resolution of dependencies
        // Note: Skip validation for "request" as it's special and adapts to the requesting fixture's scope
        for param in fixture.parameters.iter() {
            if param == "request" {
                continue; // Skip scope validation for request fixture
            }
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
        let result = if fixture.is_async_generator {
            // For async generator fixtures: call to get async generator, then call anext() to get yielded value
            let async_generator = fixture
                .callable
                .bind(self.py)
                .call1(args_tuple)
                .map(|value| value.unbind())?;

            // Use the test's loop scope for all fixtures (pytest-asyncio behavior)
            // All async operations in a test (fixtures + test) share the same loop
            let event_loop = self.get_or_create_event_loop(self.test_loop_scope)?;

            // Call anext() on the async generator to get the yielded value
            let anext_builtin = self.py.import("builtins")?.getattr("anext")?;
            let coro = anext_builtin.call1((&async_generator.bind(self.py),))?;

            // Run the coroutine in the scoped event loop
            let yielded_value = event_loop
                .bind(self.py)
                .call_method1("run_until_complete", (coro,))?
                .unbind();

            // Store the async generator in the appropriate teardown list
            match fixture.scope {
                FixtureScope::Session => {
                    self.teardowns.session.push(async_generator);
                }
                FixtureScope::Package => {
                    self.teardowns.package.push(async_generator);
                }
                FixtureScope::Module => {
                    self.teardowns.module.push(async_generator);
                }
                FixtureScope::Class => {
                    self.teardowns.class.push(async_generator);
                }
                FixtureScope::Function => {
                    self.function_teardowns.push(async_generator);
                }
            }

            yielded_value
        } else if fixture.is_generator {
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
                FixtureScope::Package => {
                    self.teardowns.package.push(generator);
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
        } else if fixture.is_async {
            // For async fixtures: call to get coroutine, then await it using the scoped event loop
            let coro = fixture
                .callable
                .bind(self.py)
                .call1(args_tuple)
                .map(|value| value.unbind())?;

            // Use the test's loop scope for all fixtures (pytest-asyncio behavior)
            // All async operations in a test (fixtures + test) share the same loop
            let event_loop = self.get_or_create_event_loop(self.test_loop_scope)?;

            // Run the coroutine in the scoped event loop
            event_loop
                .bind(self.py)
                .call_method1("run_until_complete", (&coro.bind(self.py),))?
                .unbind()
        } else {
            // For regular fixtures: call and use the return value directly
            fixture
                .callable
                .bind(self.py)
                .call1(args_tuple)
                .map(|value| value.unbind())?
        };

        self.stack.remove(&fixture.name);

        // Restore previous fixture param
        self.current_fixture_param = previous_param;

        // Store in the appropriate cache based on scope
        // Use cache_key which includes param index for parametrized fixtures
        match fixture.scope {
            FixtureScope::Session => {
                self.session_cache
                    .insert(cache_key, result.clone_ref(self.py));
            }
            FixtureScope::Package => {
                self.package_cache
                    .insert(cache_key, result.clone_ref(self.py));
            }
            FixtureScope::Module => {
                self.module_cache
                    .insert(cache_key, result.clone_ref(self.py));
            }
            FixtureScope::Class => {
                self.class_cache
                    .insert(cache_key, result.clone_ref(self.py));
            }
            FixtureScope::Function => {
                self.function_cache
                    .insert(cache_key, result.clone_ref(self.py));
            }
        }

        Ok(result)
    }

    fn resolve_for_request(&mut self, name: &str) -> PyResult<Py<PyAny>> {
        if let Some(fixture) = self.fixtures.get(name) {
            if fixture.is_async || fixture.is_async_generator {
                return Err(PyNotImplementedError::new_err(async_getfixturevalue_error(
                    name,
                )));
            }
        }
        self.resolve_fixture_value(name)
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

    /// Apply @mark.usefixtures by eagerly resolving the referenced fixtures.
    ///
    /// Pytest treats `@mark.usefixtures("foo")` as if "foo" were listed in the test signature.
    /// Rather than mutating the signature, we simply resolve the fixtures up front so all
    /// registered setup/teardown behaviour still runs.
    fn apply_usefixtures_marks(&mut self) -> PyResult<()> {
        // Safely collect fixture names first so we can drop the immutable borrow on
        // `self.test_marks` before calling `resolve_fixture_value`.
        let mut names_to_resolve: Vec<String> = Vec::new();
        for mark in &self.test_marks {
            if !mark.is_named("usefixtures") {
                continue;
            }

            let args = mark.args.bind(self.py);
            for item in args.iter() {
                let fixture_name: String = item.extract()?;
                names_to_resolve.push(fixture_name);
            }
        }

        let mut resolved = HashSet::new();
        for fixture_name in names_to_resolve {
            if resolved.insert(fixture_name.clone()) {
                self.resolve_fixture_value(&fixture_name)?;
            }
        }

        Ok(())
    }

    /// Resolve all autouse fixtures appropriate for the current test.
    /// Autouse fixtures are automatically executed without needing to be explicitly requested.
    fn resolve_autouse_fixtures(&mut self) -> PyResult<()> {
        // Collect all autouse fixtures that match the current test's class
        let autouse_fixtures: Vec<String> = self
            .fixtures
            .iter()
            .filter(|(_, fixture)| {
                if !fixture.autouse {
                    return false;
                }
                // If fixture has a class_name, it should only run for tests in that class
                match (&fixture.class_name, self.test_class_name) {
                    (Some(fixture_class), Some(test_class)) => fixture_class.as_str() == test_class,
                    (None, _) => true, // Module-level autouse fixtures run for all tests
                    (Some(_), None) => false, // Class fixture shouldn't run for non-class tests
                }
            })
            .map(|(name, _)| name.clone())
            .collect();

        if std::env::var_os("RUSTEST_DEBUG_AUTOUSE").is_some() {
            eprintln!("[rustest-debug] autouse fixtures: {:?}", autouse_fixtures);
        }

        // Resolve each autouse fixture
        for name in autouse_fixtures {
            // Skip if already in cache (for higher-scoped autouse fixtures)
            if self.function_cache.contains_key(&name)
                || self.class_cache.contains_key(&name)
                || self.module_cache.contains_key(&name)
                || self.package_cache.contains_key(&name)
                || self.session_cache.contains_key(&name)
            {
                continue;
            }

            // Resolve the autouse fixture
            self.resolve_argument(&name)?;
        }

        Ok(())
    }

    /// Get or create an event loop for the given scope.
    ///
    /// This matches pytest-asyncio's behavior where each scope has its own event loop.
    /// - function scope: new loop for each test (default)
    /// - class scope: shared loop for all tests in a class
    /// - module scope: shared loop for all tests in a module
    /// - session scope: shared loop for entire test session
    ///
    /// The test's loop_scope (from @mark.asyncio) determines which loop is used.
    /// Async fixtures run in the same loop as the test resolving them.
    fn get_or_create_event_loop(&mut self, scope: FixtureScope) -> PyResult<Py<PyAny>> {
        // Get the appropriate event loop slot for this scope
        let event_loop_opt = match scope {
            FixtureScope::Session => &mut *self.session_event_loop,
            FixtureScope::Package => &mut *self.package_event_loop,
            FixtureScope::Module => &mut *self.module_event_loop,
            FixtureScope::Class => &mut *self.class_event_loop,
            FixtureScope::Function => &mut self.function_event_loop,
        };

        // Check if a loop already exists at this scope and is still open
        if let Some(ref loop_obj) = event_loop_opt {
            let is_closed = loop_obj
                .bind(self.py)
                .call_method0("is_closed")?
                .extract::<bool>()?;
            if !is_closed {
                return Ok(loop_obj.clone_ref(self.py));
            }
        }

        // Create a new event loop for this scope
        let asyncio = self.py.import("asyncio")?;
        let new_loop = asyncio.call_method0("new_event_loop")?.unbind();
        asyncio.call_method1("set_event_loop", (&new_loop.bind(self.py),))?;

        // Store it for reuse within this scope
        *event_loop_opt = Some(new_loop.clone_ref(self.py));

        Ok(new_loop)
    }

    /// Get or create an event loop for running async tests.
    ///
    /// Uses the test's loop_scope (from @mark.asyncio(loop_scope="...")) to determine
    /// which event loop to use. This matches pytest-asyncio's behavior.
    ///
    /// Default loop_scope is "function", which creates a new loop for each test.
    fn get_or_create_test_event_loop(&mut self) -> PyResult<Py<PyAny>> {
        // Use the test's specified loop_scope
        self.get_or_create_event_loop(self.test_loop_scope)
    }

    /// Create a request fixture with the current param value.
    fn create_request_fixture(&self) -> PyResult<Py<PyAny>> {
        // Import the FixtureRequest class from rustest.compat.pytest
        let compat = self.py.import("rustest.compat.pytest")?;
        let fixture_request_class = compat.getattr("FixtureRequest")?;

        // Create the FixtureRequest with the current param value
        let param = if let Some(ref param) = self.current_fixture_param {
            param.clone_ref(self.py)
        } else {
            self.py.None()
        };

        // Call FixtureRequest(param=param_value)
        let kwargs = pyo3::types::PyDict::new(self.py);
        kwargs.set_item("param", param)?;
        kwargs.set_item("node_name", &self.test_display_name)?;
        kwargs.set_item("nodeid", &self.test_nodeid)?;
        kwargs.set_item("node_markers", self.build_marker_list()?)?;
        let request = fixture_request_class.call((), Some(&kwargs))?;

        Ok(request.unbind())
    }

    fn build_marker_list(&self) -> PyResult<Py<PyList>> {
        let markers = PyList::empty(self.py);
        for mark in &self.test_marks {
            let marker_dict = PyDict::new(self.py);
            marker_dict.set_item("name", mark.name.clone())?;
            marker_dict.set_item("args", self.mark_args_as_tuple(mark)?)?;
            marker_dict.set_item("kwargs", mark.kwargs.clone_ref(self.py))?;
            markers.append(marker_dict)?;
        }
        Ok(markers.unbind())
    }

    fn mark_args_as_tuple(&self, mark: &Mark) -> PyResult<Py<PyAny>> {
        let builtins = self.py.import("builtins")?;
        let tuple_fn = builtins.getattr("tuple")?;
        let args_list = mark.args.bind(self.py);
        let tuple_obj = tuple_fn.call1((args_list,))?;
        Ok(tuple_obj.unbind())
    }
}

fn async_getfixturevalue_error(name: &str) -> String {
    format!(
        "\nCannot use async fixture '{name}' with request.getfixturevalue().\n\n\
Why this fails:\n\
  • getfixturevalue() is a synchronous function that returns values immediately\n\
  • Async fixtures must be awaited, but we can't await in a sync context\n\
  • Calling the async fixture returns a coroutine object, not the actual value\n\n\
Good news: Async fixtures work perfectly with normal injection!\n\n\
How to fix:\n\
  ❌ Don't use: request.getfixturevalue('{name}')\n\
  ✅ Instead use: def test_something({name}):\n\n\
Example:\n\
  # This works perfectly:\n\
  async def test_my_feature({name}):\n\
      assert {name} is not None\n"
    )
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
    py: Python<'_>,
    assertion: &str,
    locals: &pyo3::Bound<'_, pyo3::PyAny>,
) -> PyResult<Option<(String, String)>> {
    use regex::Regex;

    // Match patterns like: assert x == y, assert a != b, assert response.status_code == 404, etc.
    // Uses a more flexible pattern to capture attribute access and complex expressions
    let re = Regex::new(r"assert\s+(.+?)\s*(==|!=|>|<|>=|<=)\s*(.+)").unwrap();

    if let Some(caps) = re.captures(assertion) {
        let left_expr = caps[1].trim();
        let right_expr = caps[3].trim();
        let operator = &caps[2];

        // Try to evaluate both expressions in the locals context
        let eval_expr = |expr: &str| -> Option<String> {
            // First try direct variable lookup for simple cases
            if let Ok(true) = locals.contains(expr) {
                if let Ok(val) = locals.get_item(expr) {
                    return val.repr().ok().map(|r| r.to_string());
                }
            }

            // For complex expressions (e.g., response.status_code), try eval
            #[allow(deprecated)]
            let locals_dict: Option<&pyo3::Bound<'_, pyo3::types::PyDict>> = locals.downcast().ok();
            match locals_dict.and_then(|d| {
                py.eval(&std::ffi::CString::new(expr).ok()?, Some(d), None)
                    .ok()
            }) {
                Some(val) => val.repr().ok().map(|r| r.to_string()),
                None => None,
            }
        };

        // Try to evaluate both sides
        let left_val = eval_expr(left_expr);
        let right_val = eval_expr(right_expr);

        if let (Some(left_repr), Some(right_repr)) = (left_val, right_val) {
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
    }

    Ok(None)
}

/// Extract the package name from a test file path.
///
/// The package is determined by the parent directory of the test file.
/// For example:
/// - `tests/pkg_a/test_mod1.py` -> `tests/pkg_a`
/// - `tests/pkg_a/sub/test_mod2.py` -> `tests/pkg_a/sub`
/// - `test_root.py` -> `` (empty string for root level)
fn extract_package_name(path: &std::path::Path) -> String {
    path.parent()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default()
}

/// Finalize generator fixtures by running their teardown code.
/// This calls next() on each generator (or anext() for async generators),
/// which will execute the code after yield.
/// The generator will raise StopIteration (or StopAsyncIteration) when complete, which we catch and ignore.
/// For async generators, use the provided event loop if available; otherwise get the running loop or create one.
fn finalize_generators(
    py: Python<'_>,
    generators: &mut Vec<Py<PyAny>>,
    event_loop: Option<&Py<PyAny>>,
) {
    // Process generators in reverse order (LIFO) to match pytest behavior
    for generator in generators.drain(..).rev() {
        let gen_bound = generator.bind(py);

        // Check if this is an async generator by checking if it has __anext__ method
        let is_async_gen = gen_bound.hasattr("__anext__").unwrap_or(false);

        let result = if is_async_gen {
            // For async generators, use anext() with the scoped event loop
            match py.import("builtins").and_then(|builtins| {
                let anext = builtins.getattr("anext")?;
                let coro = anext.call1((gen_bound,))?;

                // Use the provided event loop or get/create one
                if let Some(loop_obj) = event_loop {
                    // Use the scoped event loop
                    loop_obj
                        .bind(py)
                        .call_method1("run_until_complete", (coro,))
                } else {
                    // Fallback to asyncio.run() if no event loop is provided
                    let asyncio = py.import("asyncio")?;
                    asyncio.call_method1("run", (coro,))
                }
            }) {
                Ok(_) => Ok(()),
                Err(err) => Err(err),
            }
        } else {
            // For sync generators, use __next__()
            gen_bound.call_method0("__next__").map(|_| ())
        };

        // Ignore StopIteration/StopAsyncIteration (expected) and log other errors
        if let Err(err) = result {
            // Check if it's StopIteration or StopAsyncIteration - that's expected and OK
            if !err.is_instance_of::<pyo3::exceptions::PyStopIteration>(py)
                && !err.is_instance_of::<pyo3::exceptions::PyStopAsyncIteration>(py)
            {
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

/// Close an event loop if it exists, properly cleaning up pending tasks.
fn close_event_loop(py: Python<'_>, event_loop: &mut Option<Py<PyAny>>) {
    if let Some(loop_obj) = event_loop.take() {
        let loop_bound = loop_obj.bind(py);

        // Check if loop is already closed
        let is_closed = loop_bound
            .call_method0("is_closed")
            .and_then(|v| v.extract::<bool>())
            .unwrap_or(true);

        if !is_closed {
            // Cancel pending tasks
            if let Ok(asyncio) = py.import("asyncio") {
                if let Ok(tasks) = asyncio.call_method1("all_tasks", (loop_bound,)) {
                    if let Ok(task_list) = tasks.extract::<Vec<Py<PyAny>>>() {
                        for task in task_list {
                            let _ = task.bind(py).call_method0("cancel");
                        }
                    }
                }
            }

            // Close the loop
            let _ = loop_bound.call_method0("close");
        }
    }
}
