//! Execution pipeline for running collected tests.
//!
//! ## Parallelization Strategy
//!
//! Due to Python's GIL, true parallel execution within a single process is limited.
//! However, async I/O tests can run concurrently using `asyncio.gather()` since they
//! spend most of their time waiting on I/O rather than executing Python code.
//!
//! The execution strategy is:
//! 1. For each module, separate async tests from sync tests
//! 2. Run all async tests concurrently with `asyncio.gather()`
//! 3. Run sync tests sequentially
//!
//! This provides significant speedup for I/O-bound test suites without requiring
//! multi-process overhead.
//!
//! ## Fixture Scope Considerations
//!
//! - Session fixtures: Shared across all tests
//! - Package fixtures: Shared within package
//! - Module fixtures: Shared within file
//! - Function fixtures: Per-test (created fresh for each concurrent async test)

use std::collections::HashSet;
use std::time::Instant;

use indexmap::IndexMap;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::PyAnyMethods;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

use crate::cache;
use crate::model::{
    invalid_test_definition, to_relative_path, CollectionError, Fixture, FixtureScope, Mark,
    ParameterMap, PyRunReport, PyTestResult, RunConfiguration, TestCase, TestModule,
};
use crate::output::{EventStreamRenderer, OutputConfig, OutputRenderer, SpinnerDisplay};

/// Check if a test callable is an async function (coroutine function).
fn is_test_async(py: Python<'_>, test_case: &TestCase) -> bool {
    let inspect = match py.import("inspect") {
        Ok(m) => m,
        Err(_) => return false,
    };
    inspect
        .call_method1("iscoroutinefunction", (test_case.callable.bind(py),))
        .and_then(|r| r.extract::<bool>())
        .unwrap_or(false)
}

/// Check if an async test can be safely gathered with other async tests.
///
/// Tests are ONLY gathered if they can safely share an event loop. This means:
/// 1. Tests with explicit `loop_scope="function"` are NEVER gathered (they need isolation)
/// 2. Tests with `loop_scope="session"` or `"package"` are NEVER gathered (broader scope)
/// 3. Tests depending on session/package-scoped async fixtures are NEVER gathered
/// 4. Tests depending on session/package-scoped async autouse fixtures are NEVER gathered
///
/// Only tests with module or class scope (explicit or implicit) can be gathered,
/// since they're designed to share a loop within that boundary.
fn can_async_test_be_gathered(
    py: Python<'_>,
    fixtures: &IndexMap<String, Fixture>,
    test_case: &TestCase,
) -> bool {
    // Check explicit loop_scope from @mark.asyncio(loop_scope="...")
    if let Some(explicit_scope) = get_explicit_loop_scope_from_marks(py, test_case) {
        // Only module and class scopes can be gathered
        // - Function scope: explicitly requests a fresh loop per test (isolation)
        // - Session/package scopes: require a persistent loop across boundaries
        if !matches!(explicit_scope, FixtureScope::Module | FixtureScope::Class) {
            return false;
        }
    }

    // Check the required loop scope based on ALL async fixture dependencies
    // This includes explicit parameters, @mark.usefixtures, AND autouse fixtures
    let required_scope =
        detect_required_loop_scope_from_fixtures(py, fixtures, &test_case.parameters, test_case);

    // Only tests requiring module or class scope can be gathered
    // Function scope without explicit mark defaults to module for gathering purposes,
    // but if fixtures require session/package, we can't gather
    matches!(
        required_scope,
        FixtureScope::Function | FixtureScope::Module | FixtureScope::Class
    )
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
            // Package changed - run teardowns and clear cache
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

            // Partition tests into async (gatherable), async (sequential), and sync
            let mut gatherable_async_tests: Vec<&TestCase> = Vec::new();
            let mut sequential_async_tests: Vec<&TestCase> = Vec::new();
            let mut sync_tests: Vec<&TestCase> = Vec::new();

            for test in tests {
                if is_test_async(py, test) {
                    if can_async_test_be_gathered(py, &module.fixtures, test) {
                        gatherable_async_tests.push(test);
                    } else {
                        // Tests with session/package-scoped async fixtures run sequentially
                        sequential_async_tests.push(test);
                    }
                } else {
                    sync_tests.push(test);
                }
            }

            // Helper closure to process a test result
            let process_result =
                |result: PyTestResult,
                 results: &mut Vec<PyTestResult>,
                 passed: &mut usize,
                 failed: &mut usize,
                 skipped: &mut usize,
                 file_passed: &mut usize,
                 file_failed: &mut usize,
                 file_skipped: &mut usize,
                 renderer: &mut Box<dyn OutputRenderer>| {
                    match result.status.as_str() {
                        "passed" => {
                            *passed += 1;
                            *file_passed += 1;
                        }
                        "failed" => {
                            *failed += 1;
                            *file_failed += 1;
                        }
                        "skipped" => {
                            *skipped += 1;
                            *file_skipped += 1;
                        }
                        _ => {
                            *failed += 1;
                            *file_failed += 1;
                        }
                    }
                    renderer.test_completed(&result);
                    results.push(result);
                };

            // Run gatherable async tests concurrently with gather
            if !gatherable_async_tests.is_empty() {
                let async_results = run_async_tests_gathered(
                    py,
                    module,
                    &gatherable_async_tests,
                    config,
                    &mut context,
                )?;

                for (_test_case, result) in async_results {
                    let is_failed = result.status == "failed";
                    process_result(
                        result,
                        &mut results,
                        &mut passed,
                        &mut failed,
                        &mut skipped,
                        &mut file_passed,
                        &mut file_failed,
                        &mut file_skipped,
                        &mut renderer,
                    );

                    // Check for fail-fast mode after gathered results
                    if config.fail_fast && is_failed {
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

                        write_failed_tests_cache(&report)?;
                        return Ok(report);
                    }
                }
            }

            // Run sequential async tests (those with session/package-scoped async fixtures)
            for test in sequential_async_tests {
                let result = run_single_test(py, module, test, config, &mut context)?;
                let is_failed = result.status == "failed";

                process_result(
                    result,
                    &mut results,
                    &mut passed,
                    &mut failed,
                    &mut skipped,
                    &mut file_passed,
                    &mut file_failed,
                    &mut file_skipped,
                    &mut renderer,
                );

                // Check for fail-fast mode
                if config.fail_fast && is_failed {
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

                    write_failed_tests_cache(&report)?;
                    return Ok(report);
                }
            }

            // Run sync tests sequentially
            for test in sync_tests {
                let result = run_single_test(py, module, test, config, &mut context)?;
                let is_failed = result.status == "failed";

                process_result(
                    result,
                    &mut results,
                    &mut passed,
                    &mut failed,
                    &mut skipped,
                    &mut file_passed,
                    &mut file_failed,
                    &mut file_skipped,
                    &mut renderer,
                );

                // Check for fail-fast mode: exit immediately on first failure
                if config.fail_fast && is_failed {
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

                // If this is a plain function test (no class), clear class cache
                // Class-scoped fixtures should NOT be shared across plain function tests
                if test.class_name.is_none() {
                    context.class_cache.clear();
                    finalize_generators(
                        py,
                        &mut context.teardowns.class,
                        context.class_event_loop.as_ref(),
                    );
                }
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

/// Run multiple async tests concurrently using asyncio.gather().
///
/// This function:
/// 1. Prepares each test by resolving fixtures
/// 2. Calls each test to get its coroutine (without awaiting)
/// 3. Uses asyncio.gather() to run all coroutines concurrently
/// 4. Maps results back to PyTestResult
///
/// Returns a vector of (test_case, PyTestResult) pairs in order.
fn run_async_tests_gathered<'a>(
    py: Python<'_>,
    module: &TestModule,
    tests: &[&'a TestCase],
    config: &RunConfiguration,
    context: &mut FixtureContext,
) -> PyResult<Vec<(&'a TestCase, PyTestResult)>> {
    if tests.is_empty() {
        return Ok(Vec::new());
    }

    let start = Instant::now();
    let asyncio = py.import("asyncio")?;

    // Prepare all tests: resolve fixtures and get coroutines
    // Type alias to satisfy clippy::type_complexity
    type PreparedTest<'a> = (&'a TestCase, Py<PyAny>, Vec<Py<PyAny>>);
    let mut prepared_tests: Vec<PreparedTest<'_>> = Vec::new();
    let mut skip_results: Vec<(&TestCase, PyTestResult)> = Vec::new();
    let mut error_results: Vec<(&TestCase, PyTestResult)> = Vec::new();

    // Create a fresh event loop for the entire gather operation
    // This ensures all fixtures and tests use the same loop
    let event_loop = asyncio.call_method0("new_event_loop")?.unbind();
    asyncio.call_method1("set_event_loop", (&event_loop.bind(py),))?;

    // Store it in the context so fixtures use this same loop
    context.module_event_loop = Some(event_loop.clone_ref(py));

    for test_case in tests {
        // Handle skipped tests
        if let Some(reason) = &test_case.skip_reason {
            skip_results.push((
                test_case,
                PyTestResult::skipped(
                    test_case.display_name.clone(),
                    to_relative_path(&test_case.path),
                    0.0,
                    reason.clone(),
                    test_case.mark_names(),
                ),
            ));
            continue;
        }

        // Validate loop scope compatibility (catches mismatches between explicit scope and fixtures)
        if let Some(error_message) =
            validate_loop_scope_compatibility(py, test_case, &module.fixtures)
        {
            error_results.push((
                test_case,
                PyTestResult::failed(
                    test_case.display_name.clone(),
                    to_relative_path(&test_case.path),
                    0.0,
                    error_message,
                    None,
                    None,
                    test_case.mark_names(),
                ),
            ));
            continue;
        }

        // For gathered tests, all must use the same event loop (module scope)
        // This is required for asyncio.gather() to work correctly
        // Note: Tests with loop_scope="function" are excluded in can_async_test_be_gathered()
        let test_loop_scope = FixtureScope::Module;

        // Create a fresh function-scoped fixture context for each test
        let mut function_teardowns: Vec<Py<PyAny>> = Vec::new();

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
        );

        // Populate fixture registry
        if let Err(err) = populate_fixture_registry(py, &module.fixtures) {
            let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
            error_results.push((
                test_case,
                PyTestResult::failed(
                    test_case.display_name.clone(),
                    to_relative_path(&test_case.path),
                    0.0,
                    message,
                    None,
                    None,
                    test_case.mark_names(),
                ),
            ));
            continue;
        }

        // Resolve autouse fixtures
        if let Err(err) = resolver.resolve_autouse_fixtures() {
            let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
            error_results.push((
                test_case,
                PyTestResult::failed(
                    test_case.display_name.clone(),
                    to_relative_path(&test_case.path),
                    0.0,
                    message,
                    None,
                    None,
                    test_case.mark_names(),
                ),
            ));
            continue;
        }

        // Resolve fixtures requested via @mark.usefixtures
        if let Err(err) = resolver.resolve_usefixtures_marks(&test_case.marks) {
            let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
            error_results.push((
                test_case,
                PyTestResult::failed(
                    test_case.display_name.clone(),
                    to_relative_path(&test_case.path),
                    0.0,
                    message,
                    None,
                    None,
                    test_case.mark_names(),
                ),
            ));
            continue;
        }

        // Resolve fixture arguments
        let mut call_args = Vec::new();
        let mut fixture_error = None;
        for param in &test_case.parameters {
            match resolver.resolve_argument(param) {
                Ok(value) => call_args.push(value),
                Err(err) => {
                    let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
                    fixture_error = Some(message);
                    break;
                }
            }
        }

        if let Some(message) = fixture_error {
            error_results.push((
                test_case,
                PyTestResult::failed(
                    test_case.display_name.clone(),
                    to_relative_path(&test_case.path),
                    0.0,
                    message,
                    None,
                    None,
                    test_case.mark_names(),
                ),
            ));
            continue;
        }

        // Call the test function to get the coroutine (without awaiting)
        let args_tuple = PyTuple::new(py, &call_args)?;
        let callable = test_case.callable.bind(py);
        match callable.call1(args_tuple) {
            Ok(coroutine) => {
                // Store function teardowns for this test
                function_teardowns.append(&mut resolver.function_teardowns);
                prepared_tests.push((test_case, coroutine.unbind(), function_teardowns));
            }
            Err(err) => {
                let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());
                // Clean up function teardowns
                finalize_generators(py, &mut resolver.function_teardowns, Some(&event_loop));
                error_results.push((
                    test_case,
                    PyTestResult::failed(
                        test_case.display_name.clone(),
                        to_relative_path(&test_case.path),
                        0.0,
                        message,
                        None,
                        None,
                        test_case.mark_names(),
                    ),
                ));
            }
        }
    }

    // Now run all prepared coroutines with gather
    let mut results: Vec<(&TestCase, PyTestResult)> = Vec::new();
    results.extend(skip_results);
    results.extend(error_results);

    if !prepared_tests.is_empty() {
        // Create a tuple of coroutines for gather(*coros)
        let coroutines: Vec<Py<PyAny>> = prepared_tests
            .iter()
            .map(|(_, c, _)| c.clone_ref(py))
            .collect();
        let coro_tuple = PyTuple::new(py, coroutines.iter().map(|c| c.bind(py)))?;

        // Create gather with return_exceptions=True so one failure doesn't cancel others
        let kwargs = PyDict::new(py);
        kwargs.set_item("return_exceptions", true)?;
        let gather_coro = asyncio.getattr("gather")?.call(coro_tuple, Some(&kwargs))?;

        // Run the gathered coroutines with output capture
        // Note: Since tests run concurrently, we can only capture aggregate output
        let (gather_result, captured_stdout, captured_stderr) =
            call_with_capture(py, config.capture_output, || {
                event_loop
                    .bind(py)
                    .call_method1("run_until_complete", (gather_coro,))
                    .map(|r| r.unbind())
            })?;

        let gather_results: Vec<Py<PyAny>> = gather_result?.bind(py).extract()?;
        let elapsed = start.elapsed().as_secs_f64();
        let per_test_duration = elapsed / (prepared_tests.len() as f64);

        // Map results back to test cases
        for ((test_case, _coroutine, mut function_teardowns), result) in
            prepared_tests.into_iter().zip(gather_results)
        {
            let result_bound = result.bind(py);

            // Check if the result is an exception
            let is_exception =
                result_bound.is_instance(&py.get_type::<pyo3::exceptions::PyBaseException>())?;

            let test_result = if is_exception {
                // Extract exception info
                let err = PyErr::from_value(result_bound.clone());
                let message = format_pyerr(py, &err).unwrap_or_else(|_| err.to_string());

                // Check if it's a skip exception
                if is_skip_exception(&message) {
                    let reason = extract_skip_reason(&message);
                    PyTestResult::skipped(
                        test_case.display_name.clone(),
                        to_relative_path(&test_case.path),
                        per_test_duration,
                        reason,
                        test_case.mark_names(),
                    )
                } else {
                    // Include captured output with failed tests
                    // Note: Output is from all concurrent tests, not just this one
                    PyTestResult::failed(
                        test_case.display_name.clone(),
                        to_relative_path(&test_case.path),
                        per_test_duration,
                        message,
                        captured_stdout.clone(),
                        captured_stderr.clone(),
                        test_case.mark_names(),
                    )
                }
            } else {
                // Include captured output with passed tests too
                PyTestResult::passed(
                    test_case.display_name.clone(),
                    to_relative_path(&test_case.path),
                    per_test_duration,
                    captured_stdout.clone(),
                    captured_stderr.clone(),
                    test_case.mark_names(),
                )
            };

            // Clean up function-scoped fixtures for this test
            finalize_generators(py, &mut function_teardowns, Some(&event_loop));

            results.push((test_case, test_result));
        }
    }

    Ok(results)
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

/// Extract the loop_scope from a test's asyncio mark, if present.
/// Returns Some(scope) if explicitly specified, None otherwise.
fn get_explicit_loop_scope_from_marks(
    py: Python<'_>,
    test_case: &TestCase,
) -> Option<FixtureScope> {
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
            // asyncio mark found but no loop_scope specified
            return None;
        }
    }
    // No asyncio mark found
    None
}

/// Analyze test's fixture dependencies to find the widest async fixture scope.
/// This enables automatic loop scope detection based on what fixtures the test uses.
///
/// This function checks:
/// 1. Explicit fixture parameters requested by the test
/// 2. Fixtures requested via @mark.usefixtures decorator
/// 3. Autouse fixtures that apply to this test (based on class scope)
/// 4. Recursive dependencies of all the above
///
/// Returns the widest scope of any async fixture used by the test, or Function if none.
fn detect_required_loop_scope_from_fixtures(
    py: Python<'_>,
    fixtures: &IndexMap<String, Fixture>,
    test_params: &[String],
    test_case: &TestCase,
) -> FixtureScope {
    let mut widest_scope = FixtureScope::Function;
    let mut visited = HashSet::new();

    // Analyze explicit fixture parameters
    for param in test_params {
        analyze_fixture_scope(fixtures, param, &mut widest_scope, &mut visited);
    }

    // Also analyze fixtures from @mark.usefixtures decorator
    // These are implicitly injected even though not in the parameter list
    for mark in &test_case.marks {
        if mark.is_named("usefixtures") {
            // usefixtures passes fixture names as positional args
            for arg in mark.args.bind(py).iter() {
                if let Ok(fixture_name) = arg.extract::<String>() {
                    analyze_fixture_scope(fixtures, &fixture_name, &mut widest_scope, &mut visited);
                }
            }
        }
    }

    // Also analyze autouse fixtures that apply to this test
    // These are implicitly executed even if not in the parameter list
    for (name, fixture) in fixtures.iter() {
        if !fixture.autouse {
            continue;
        }

        // Check if autouse fixture applies to this test
        let applies = match (&fixture.class_name, &test_case.class_name) {
            // Class-scoped autouse: only applies to tests in that class
            (Some(fixture_class), Some(test_class)) => fixture_class == test_class,
            // Module-level autouse: applies to all tests
            (None, _) => true,
            // Class fixture shouldn't run for non-class tests
            (Some(_), None) => false,
        };

        if applies {
            analyze_fixture_scope(fixtures, name, &mut widest_scope, &mut visited);
        }
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

    // Detect what scope is required by fixtures (including usefixtures and autouse)
    let required_scope =
        detect_required_loop_scope_from_fixtures(py, fixtures, &test_case.parameters, test_case);

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

    // Analyze fixture dependencies to find required scope (including usefixtures and autouse)
    detect_required_loop_scope_from_fixtures(py, fixtures, &test_case.parameters, test_case)
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
    );

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

    // Resolve fixtures requested via @mark.usefixtures
    if let Err(err) = resolver.resolve_usefixtures_marks(&test_case.marks) {
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
            Ok(event_loop
                .bind(py)
                .call_method1("run_until_complete", (&result,))?
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
            // Close the function-scoped event loop to prevent resource warnings
            close_event_loop(py, &mut resolver.function_event_loop);
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
    // Close the function-scoped event loop to prevent resource warnings
    close_event_loop(py, &mut resolver.function_event_loop);

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

        // Special handling for "request" fixture - create with current param value
        if name == "request" {
            return self.create_request_fixture();
        }

        // Check if this is a parametrized fixture and get the cache key
        let (cache_key, param_value) =
            if let Some(&param_idx) = self.fixture_param_indices.get(name) {
                if let Some(fixture) = self.fixtures.get(name) {
                    if let Some(params) = &fixture.params {
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
        let fixture = self
            .fixtures
            .get(name)
            .ok_or_else(|| invalid_test_definition(format!("Unknown fixture '{}'.", name)))?;

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

    /// Resolve fixtures requested via @mark.usefixtures decorator.
    /// These fixtures are executed but their values are NOT passed to the test function.
    fn resolve_usefixtures_marks(&mut self, marks: &[Mark]) -> PyResult<()> {
        for mark in marks {
            if mark.is_named("usefixtures") {
                // usefixtures passes fixture names as positional args
                for arg in mark.args.bind(self.py).iter() {
                    if let Ok(fixture_name) = arg.extract::<String>() {
                        // Skip if already in cache
                        if self.function_cache.contains_key(&fixture_name)
                            || self.class_cache.contains_key(&fixture_name)
                            || self.module_cache.contains_key(&fixture_name)
                            || self.package_cache.contains_key(&fixture_name)
                            || self.session_cache.contains_key(&fixture_name)
                        {
                            continue;
                        }

                        // Resolve the fixture (but don't pass it to the test)
                        self.resolve_argument(&fixture_name)?;
                    }
                }
            }
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
        let request = fixture_request_class.call((), Some(&kwargs))?;

        Ok(request.unbind())
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
