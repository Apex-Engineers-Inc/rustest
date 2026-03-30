# Surgical Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove ~1,130-1,310 lines of duplicated and dead code across Rust source, Python source, and tests without changing API surface or behavior.

**Architecture:** Eight independent, incremental changes executed in risk-ascending order. Each change is verified by the full test suite before committing. Rust changes are rebuilt with `maturin develop` before running Python tests.

**Tech Stack:** Rust (PyO3 0.23+), Python 3.10+, cargo, maturin, pytest, rustest

---

## File Map

| Task | Files Modified | Files Deleted |
|------|---------------|---------------|
| 1 | `src/model.rs`, `src/cache.rs` | - |
| 2 | `src/output/event_stream.rs`, `src/output/events.rs` | - |
| 3 | `src/output/spinner_display.rs` | - |
| 4 | `src/discovery.rs` | - |
| 5 | `src/execution.rs` | - |
| 6 | `python/rustest/decorators.py` | - |
| 7 | `python/rustest/compat/pytest.py` | - |
| 8a | `tests/test_fixture_parametrization.py` | `tests/test_fixture_parametrization_pytest_compat.py` |
| 8b | `tests/test_parallel_async.py` | - |
| 8c | `tests/test_skip.py` | `tests/test_skip_simple.py`, `tests/test_skip_counting.py` |
| 8d | `tests/test_asyncio.py` | `tests/test_async_fixtures.py` |

---

### Task 1: Dead Code Removal (src/model.rs, src/cache.rs)

**Files:**
- Modify: `src/model.rs`
- Modify: `src/cache.rs`

- [ ] **Step 1: Remove unused methods from Mark**

In `src/model.rs`, delete the three dead methods on `Mark` (lines 107-134):

```rust
// DELETE these three methods entirely:

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
```

- [ ] **Step 2: Remove unused methods from TestCase**

In `src/model.rs`, delete `find_mark` and `has_mark` from `TestCase` (lines 260-270):

```rust
// DELETE these two methods entirely:

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
```

- [ ] **Step 3: Remove unused is_parametrized from Fixture**

In `src/model.rs`, delete `is_parametrized` from `Fixture` (lines 208-212):

```rust
// DELETE this method entirely:

    /// Check if this fixture is parametrized.
    #[allow(dead_code)]
    pub fn is_parametrized(&self) -> bool {
        self.params.is_some() && !self.params.as_ref().unwrap().is_empty()
    }
```

- [ ] **Step 4: Remove false #[allow(dead_code)] annotations**

In `src/model.rs`, remove the `#[allow(dead_code)]` annotations from fields that ARE used:

On `TestCase::name` (line 236):
```rust
// BEFORE:
    #[allow(dead_code)]
    pub name: String,
// AFTER:
    pub name: String,
```

On `TestModule::path` (line 280):
```rust
// BEFORE:
    #[allow(dead_code)]
    pub path: PathBuf,
// AFTER:
    pub path: PathBuf,
```

On `RunConfiguration::worker_count` (line 342):
```rust
// BEFORE:
    #[allow(dead_code)]
    pub worker_count: usize,
// AFTER:
    pub worker_count: usize,
```

- [ ] **Step 5: Remove clear_last_failed from cache.rs**

In `src/cache.rs`, delete the `clear_last_failed` function (lines 80-92):

```rust
// DELETE this function entirely:

/// Clear the last failed cache
#[allow(dead_code)]
pub fn clear_last_failed() -> PyResult<()> {
    let cache_path = get_last_failed_path();

    if cache_path.exists() {
        fs::remove_file(&cache_path).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Failed to clear cache: {}", e))
        })?;
    }

    Ok(())
}
```

- [ ] **Step 6: Verify Rust compiles and tests pass**

Run:
```bash
cargo fmt && cargo clippy --lib -- -D warnings && cargo test
```

Expected: All pass with no warnings. Clippy may now flag previously-suppressed warnings as resolved.

- [ ] **Step 7: Commit**

```bash
git add src/model.rs src/cache.rs
git commit -m "refactor: remove dead code from model.rs and cache.rs

Remove 6 unused methods (Mark::get_string_arg, get_kwarg, get_bool_kwarg,
TestCase::find_mark, has_mark, Fixture::is_parametrized) and
cache::clear_last_failed. Remove false #[allow(dead_code)] on 3 fields
that are actually used."
```

---

### Task 2: Event Emission Macro (src/output/event_stream.rs, src/output/events.rs)

**Files:**
- Modify: `src/output/event_stream.rs`
- Modify: `src/output/events.rs`

- [ ] **Step 1: Add emit_event macro and replace methods in event_stream.rs**

Replace the entire `EventStreamRenderer` impl block (lines 21-92) with:

```rust
impl EventStreamRenderer {
    /// Create a new event stream renderer
    pub fn new(callback: Option<Py<PyAny>>) -> Self {
        Self {
            callback,
            collection_errors: Vec::new(),
        }
    }
}

/// Emit a PyO3 event object to a Python callback, if present.
macro_rules! emit_event {
    ($callback:expr, $event:expr) => {
        if let Some(callback) = $callback {
            Python::attach(|py| {
                if let Err(e) = callback.call1(py, (Py::new(py, $event).unwrap(),)) {
                    eprintln!("Error in event callback: {}", e);
                }
            });
        }
    };
}
```

Then update the `OutputRenderer` impl (lines 94-189) to call the macro directly instead of the removed `emit_*` methods. Replace each method body:

`collection_error` — replace `self.emit_collection_error(event)` with `emit_event!(&self.callback, event)`:
```rust
    fn collection_error(&mut self, error: &CollectionError) {
        self.collection_errors.push(error.clone());
        let event = CollectionErrorEvent {
            path: error.path.clone(),
            message: error.message.clone(),
            timestamp: current_timestamp(),
        };
        emit_event!(&self.callback, event);
    }
```

`start_suite` — replace `self.emit_suite_started(event)` with `emit_event!(&self.callback, event)`:
```rust
    fn start_suite(&mut self, total_files: usize, total_tests: usize) {
        let event = SuiteStartedEvent {
            total_files,
            total_tests,
            timestamp: current_timestamp(),
        };
        emit_event!(&self.callback, event);
    }
```

`start_file` — replace `self.emit_file_started(event)` with `emit_event!(&self.callback, event)`:
```rust
    fn start_file(&mut self, module: &TestModule) {
        let event = FileStartedEvent {
            file_path: to_relative_path(&module.path),
            total_tests: module.tests.len(),
            timestamp: current_timestamp(),
        };
        emit_event!(&self.callback, event);
    }
```

`test_completed` — replace `self.emit_test_completed(event)` with `emit_event!(&self.callback, event)`:
```rust
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
        emit_event!(&self.callback, event);
    }
```

`file_completed` — replace `self.emit_file_completed(event)` with `emit_event!(&self.callback, event)`:
```rust
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
        emit_event!(&self.callback, event);
    }
```

`finish_suite` — replace `self.emit_suite_completed(event)` with `emit_event!(&self.callback, event)`:
```rust
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
        emit_event!(&self.callback, event);
    }
```

Leave `start_test` and `println` unchanged.

- [ ] **Step 2: Leave standalone emit functions in events.rs unchanged**

The 3 standalone functions in `events.rs` (lines 298-348) take `&Py<PyAny>` not `&Option<Py<PyAny>>` — the callback is always present (callers check before calling). They don't benefit from the macro. Leave them as-is.

- [ ] **Step 4: Verify Rust compiles and tests pass**

Run:
```bash
cargo fmt && cargo clippy --lib -- -D warnings && cargo test
```

Expected: All pass.

- [ ] **Step 5: Rebuild and verify Python tests**

Run:
```bash
uv run maturin develop && uv run pytest python/tests -v && uv run pytest tests/ examples/tests/ -v
```

Expected: All pass. Event streaming behavior is unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/output/event_stream.rs src/output/events.rs
git commit -m "refactor: replace 6 emit_* methods with emit_event macro

Consolidate identical event emission methods in EventStreamRenderer
into a single macro. Each OutputRenderer method now calls the macro
directly instead of going through a type-specific wrapper."
```

---

### Task 3: Color Formatting Helper (src/output/spinner_display.rs)

**Files:**
- Modify: `src/output/spinner_display.rs`

- [ ] **Step 1: Add styled() helper method to SpinnerDisplay**

Add this method inside the `impl SpinnerDisplay` block (after `spinner_style`, before `format_symbol`):

```rust
    /// Apply a style function to text only when colors are enabled.
    fn styled<F>(&self, text: &str, styler: F) -> String
    where
        F: FnOnce(console::StyledObject<&str>) -> console::StyledObject<&str>,
    {
        if self.use_colors {
            format!("{}", styler(style(text)))
        } else {
            text.to_string()
        }
    }
```

- [ ] **Step 2: Simplify format_symbol**

Replace the `format_symbol` method (lines 77-101) with:

```rust
    /// Format a symbol based on status
    fn format_symbol(&self, failed: usize) -> String {
        let (text, color): (&str, fn(console::StyledObject<&str>) -> console::StyledObject<&str>) = if failed > 0 {
            (if self.ascii_mode { "FAIL" } else { "✗" }, |s| s.red())
        } else {
            (if self.ascii_mode { "PASS" } else { "✓" }, |s| s.green())
        };
        self.styled(text, color)
    }
```

- [ ] **Step 3: Simplify format_duration**

Replace the standalone `format_duration` function (lines 15-28) with:

```rust
/// Format duration with appropriate units (ms or s) and optional color
fn format_duration(duration: Duration, use_colors: bool) -> String {
    let millis = duration.as_millis();
    let duration_str = if millis < 1000 {
        format!("({}ms)", millis)
    } else {
        format!("({:.2}s)", duration.as_secs_f64())
    };

    if use_colors {
        format!("{}", style(duration_str).dim())
    } else {
        duration_str
    }
}
```

Note: `format_duration` is a standalone function (not a method on `SpinnerDisplay`), so it cannot use `self.styled()`. Leave it unchanged — it's already concise.

- [ ] **Step 4: Simplify file_completed status parts**

In the `file_completed` method, replace the status_parts building (lines 168-182) with:

```rust
            let mut status_parts = Vec::new();
            if passed > 0 {
                status_parts.push(self.styled(&format!("{} passing", passed), |s| s.green()));
            }
            if failed > 0 {
                status_parts.push(self.styled(&format!("{} failed", failed), |s| s.red()));
            }
```

- [ ] **Step 5: Simplify finish_suite ERRORS and FAILURES headers**

In the `finish_suite` method, replace the ERRORS header (lines 209-213) with:

```rust
            eprintln!("{}", self.styled("ERRORS", |s| s.red().bold()));
```

Replace the FAILURES header (lines 233-237) with:

```rust
            eprintln!("{}", self.styled("FAILURES", |s| s.red().bold()));
```

- [ ] **Step 6: Simplify finish_suite summary parts**

Replace the summary parts building (lines 251-282) with:

```rust
        let mut parts = Vec::new();
        if passed > 0 {
            parts.push(self.styled(&format!("{} passing", passed), |s| s.green()));
        }
        if failed > 0 {
            parts.push(self.styled(&format!("{} failed", failed), |s| s.red()));
        }
        if skipped > 0 {
            parts.push(self.styled(&format!("{} skipped", skipped), |s| s.yellow()));
        }
        if errors > 0 {
            parts.push(self.styled(&format!("{} error", errors), |s| s.red()));
        }
```

- [ ] **Step 7: Simplify finish_suite final symbol**

Replace the final symbol block (lines 291-301) with:

```rust
        let symbol = if failed > 0 || errors > 0 {
            self.styled("✗", |s| s.red())
        } else {
            self.styled("✓", |s| s.green())
        };
```

- [ ] **Step 8: Verify Rust compiles and tests pass**

Run:
```bash
cargo fmt && cargo clippy --lib -- -D warnings && cargo test
```

Expected: All pass.

- [ ] **Step 9: Commit**

```bash
git add src/output/spinner_display.rs
git commit -m "refactor: extract styled() helper to reduce color conditional duplication

Replace ~15 instances of if use_colors { style(x).color() } else { x }
with calls to a single styled() helper method."
```

---

### Task 4: Extract Fixture Loading Helper (src/discovery.rs)

**Files:**
- Modify: `src/discovery.rs`

- [ ] **Step 1: Add build_fixture_from_value helper function**

Add this function near the other helper functions in discovery.rs (e.g., after `extract_fixture_name` or near the end of the helper functions section). Read the file first to find the right location — it should be near the other `extract_*` helpers.

```rust
/// Build a Fixture from a Python callable that has been identified as a fixture.
///
/// This consolidates the repeated pattern of extracting fixture metadata and
/// creating a Fixture object. Used by load_conftest_fixtures, load_builtin_fixtures,
/// load_pytest_plugins_fixtures, and inspect_module.
fn build_fixture_from_value(
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
    name: &str,
    class_name: Option<&str>,
) -> PyResult<(String, Fixture)> {
    let scope = extract_fixture_scope(value)?;
    let is_generator = is_generator_function(py, value)?;
    let is_async = is_async_function(py, value)?;
    let is_async_generator = is_async_generator_function(py, value)?;
    let autouse = extract_fixture_autouse(value)?;
    let params = extract_fixture_params(value)?;
    let fixture_name = extract_fixture_name(value, name)?;

    let fixture = if let Some(params) = params {
        Fixture::with_params(
            fixture_name.clone(),
            value.clone().unbind(),
            extract_parameters(py, value)?,
            scope,
            is_generator,
            is_async,
            is_async_generator,
            autouse,
            params,
            class_name.map(|s| s.to_string()),
        )
    } else {
        Fixture::new(
            fixture_name.clone(),
            value.clone().unbind(),
            extract_parameters(py, value)?,
            scope,
            is_generator,
            is_async,
            is_async_generator,
            autouse,
            class_name.map(|s| s.to_string()),
        )
    };
    Ok((fixture_name, fixture))
}
```

- [ ] **Step 2: Replace fixture extraction in load_pytest_plugins_fixtures**

In `load_pytest_plugins_fixtures`, replace the body of the `if is_function(...) && is_fixture(...)` block (approximately lines 648-683) with:

```rust
            if is_function(&value, function_type)? && is_fixture(&value)? {
                let (fixture_name, fixture) = build_fixture_from_value(py, &value, &name, None)?;
                fixtures.insert(fixture_name, fixture);
            }
```

- [ ] **Step 3: Replace fixture extraction in load_conftest_fixtures**

In `load_conftest_fixtures`, replace the body of the `if is_function(...) && is_fixture(...)` block (approximately lines 791-826) with:

```rust
                if is_function(&value, &function_type)? && is_fixture(&value)? {
                    let (fixture_name, fixture) = build_fixture_from_value(py, &value, &name, None)?;
                    fixtures.insert(fixture_name, fixture);
```

Keep the `continue;` if present after the insertion.

- [ ] **Step 4: Replace fixture extraction in load_builtin_fixtures**

In `load_builtin_fixtures`, replace the body of the `if is_function(...) && is_fixture(...)` block (approximately lines 894-929) with:

```rust
            if is_function(&value, &function_type)? && is_fixture(&value)? {
                let (fixture_name, fixture) = build_fixture_from_value(py, &value, &name, None)?;
                fixtures.insert(fixture_name, fixture);
            }
```

- [ ] **Step 5: Replace fixture extraction in inspect_module**

In `inspect_module`, replace the body of the `if is_fixture(...)` block inside the `if is_function(...)` check (approximately lines 1260-1295) with:

```rust
                if is_fixture(&value)? {
                    let (fixture_name, fixture) = build_fixture_from_value(py, &value, &name, None)?;
                    fixtures.insert(fixture_name, fixture);
                    continue;
                }
```

- [ ] **Step 6: Leave discover_plain_class_tests_and_fixtures unchanged**

Do NOT touch the fixture extraction in `discover_plain_class_tests_and_fixtures` (lines 1699-1729). It intentionally: (1) skips `extract_fixture_params()`, (2) filters `self` from parameters, (3) uses `create_plain_class_method_runner()` for the callable. These differences make it a distinct code path.

- [ ] **Step 7: Verify Rust compiles and tests pass**

Run:
```bash
cargo fmt && cargo clippy --lib -- -D warnings && cargo test
```

Expected: All pass.

- [ ] **Step 8: Rebuild and run full test suite**

Run:
```bash
uv run maturin develop && uv run pytest python/tests -v && uv run pytest tests/ examples/tests/ -v && uv run python -m rustest tests/ examples/tests/ -v
```

Expected: All pass. Fixture discovery behavior is unchanged.

- [ ] **Step 9: Commit**

```bash
git add src/discovery.rs
git commit -m "refactor: extract build_fixture_from_value to deduplicate fixture loading

Consolidate identical 35-line fixture extraction blocks from 4 functions
(load_pytest_plugins_fixtures, load_conftest_fixtures, load_builtin_fixtures,
inspect_module) into a single helper function."
```

---

### Task 5: Teardown Cleanup Helpers (src/execution.rs)

**Files:**
- Modify: `src/execution.rs`

- [ ] **Step 1: Read execution.rs and map all cleanup sites**

Read the full file. Identify every call to `finalize_generators()` paired with `cache.clear()` and/or `close_event_loop()`. The exploration found 13 non-function-scope instances. Map them by context:

**Full cleanup sites (finalize + clear + close):**
- Package boundary transitions (class scope, package scope)
- Fail-fast exits (class, module, package, session)
- End-of-run (package, session)

**Partial cleanup sites (finalize + close, no clear):**
- End of class tests
- Module completion

**Cache-only sites:**
- Per-class reset (just `class_cache.clear()`)
- Module start (just `module_cache.clear()` + close loop)

- [ ] **Step 2: Add cleanup methods to FixtureContext**

Add these methods to the `impl FixtureContext` block:

```rust
    /// Run teardowns and close event loop for a specific scope. Does NOT clear cache.
    fn teardown_scope(&mut self, py: Python<'_>, scope: FixtureScope) {
        let (teardowns, event_loop) = match scope {
            FixtureScope::Class => (&mut self.teardowns.class, &mut self.class_event_loop),
            FixtureScope::Module => (&mut self.teardowns.module, &mut self.module_event_loop),
            FixtureScope::Package => (&mut self.teardowns.package, &mut self.package_event_loop),
            FixtureScope::Session => (&mut self.teardowns.session, &mut self.session_event_loop),
            FixtureScope::Function => return,
        };
        finalize_generators(py, teardowns, event_loop.as_ref());
        close_event_loop(py, event_loop);
    }

    /// Run teardowns, clear cache, and close event loop for a specific scope.
    fn cleanup_scope(&mut self, py: Python<'_>, scope: FixtureScope) {
        let (teardowns, cache, event_loop) = match scope {
            FixtureScope::Class => (
                &mut self.teardowns.class,
                &mut self.class_cache,
                &mut self.class_event_loop,
            ),
            FixtureScope::Module => (
                &mut self.teardowns.module,
                &mut self.module_cache,
                &mut self.module_event_loop,
            ),
            FixtureScope::Package => (
                &mut self.teardowns.package,
                &mut self.package_cache,
                &mut self.package_event_loop,
            ),
            FixtureScope::Session => (
                &mut self.teardowns.session,
                &mut self.session_cache,
                &mut self.session_event_loop,
            ),
            FixtureScope::Function => return,
        };
        finalize_generators(py, teardowns, event_loop.as_ref());
        cache.clear();
        close_event_loop(py, event_loop);
    }

    /// Clean up all scopes from narrowest to widest.
    fn cleanup_all(&mut self, py: Python<'_>) {
        for scope in [
            FixtureScope::Class,
            FixtureScope::Module,
            FixtureScope::Package,
            FixtureScope::Session,
        ] {
            self.teardown_scope(py, scope);
        }
    }
```

- [ ] **Step 3: Replace fail-fast cleanup block**

Find the fail-fast block that cleans up all 4 scopes sequentially (approximately lines 446-469). Replace the 4 consecutive blocks with:

```rust
                        context.cleanup_all(py);
```

- [ ] **Step 4: Replace package boundary transition cleanups**

Find where package boundary transitions trigger class + package cleanup (approximately lines 338-353). Replace with:

```rust
                    context.cleanup_scope(py, FixtureScope::Class);
                    context.cleanup_scope(py, FixtureScope::Package);
```

- [ ] **Step 5: Replace end-of-class and end-of-module cleanups**

For end-of-class (approximately lines 506-511), replace with:
```rust
                context.teardown_scope(py, FixtureScope::Class);
```

For end-of-module (approximately lines 515-519), replace with:
```rust
            context.teardown_scope(py, FixtureScope::Module);
```

Note: These use `teardown_scope` (no cache clear) because caches were handled separately.

- [ ] **Step 6: Replace end-of-run cleanups**

For end-of-run package + session cleanup (approximately lines 537-550), replace with:
```rust
        context.teardown_scope(py, FixtureScope::Package);
        context.teardown_scope(py, FixtureScope::Session);
```

- [ ] **Step 7: Leave partial/unique sites unchanged**

Some cleanup sites have unique patterns that don't fit the helpers:
- Module start cache reset (`module_cache.clear()` + `close_event_loop`) — leave as-is
- Per-class cache reset (just `class_cache.clear()`) — leave as-is
- Plain function test cleanup (clear + finalize, no close) — leave as-is
- Function-scope teardowns in async batch — leave as-is (they operate on local `Vec`, not `FixtureContext` fields)

Do NOT force these into the helpers. The goal is to consolidate the obvious duplicates, not every mention of `finalize_generators`.

- [ ] **Step 8: Verify Rust compiles and tests pass**

Run:
```bash
cargo fmt && cargo clippy --lib -- -D warnings && cargo test
```

Expected: All pass.

- [ ] **Step 9: Rebuild and run full test suite**

Run:
```bash
uv run maturin develop && uv run pytest python/tests -v && uv run pytest tests/ examples/tests/ -v && uv run python -m rustest tests/ examples/tests/ -v
```

Expected: All pass. Fixture teardown behavior is unchanged.

- [ ] **Step 10: Commit**

```bash
git add src/execution.rs
git commit -m "refactor: extract teardown/cleanup helpers on FixtureContext

Add cleanup_scope(), teardown_scope(), and cleanup_all() to consolidate
the repeated finalize_generators + cache.clear + close_event_loop pattern."
```

---

### Task 6: Parameter ID Generation Consolidation (python/rustest/decorators.py)

**Files:**
- Modify: `python/rustest/decorators.py`

- [ ] **Step 1: Extract _resolve_case_id helper**

Add this function after `_generate_param_id` (after line 236):

```python
def _resolve_case_id(
    *,
    param_set_id: str | None,
    ids: Sequence[str] | Callable[[Any], str | None] | None,
    ids_is_callable: bool,
    value: Any,
    index: int,
) -> str:
    """Resolve the ID for a parametrization case.

    Priority: ParameterSet.id > callable ids > explicit ids list > auto-generated.
    """
    if param_set_id is not None:
        return param_set_id
    if ids is None:
        return _generate_param_id(value, index)
    if ids_is_callable:
        generated_id = ids(value)
        return str(generated_id) if generated_id is not None else _generate_param_id(value, index)
    return ids[index]
```

- [ ] **Step 2: Update _build_fixture_params to use _resolve_case_id**

In `_build_fixture_params`, replace the case ID generation block (approximately lines 175-190) with:

```python
        case_id = _resolve_case_id(
            param_set_id=param_set_id,
            ids=ids,
            ids_is_callable=ids_is_callable,
            value=actual_value,
            index=index,
        )
```

- [ ] **Step 3: Update _build_cases to use _resolve_case_id**

In `_build_cases`, replace the case ID generation block (approximately lines 457-468) with:

```python
        case_id = _resolve_case_id(
            param_set_id=param_set_id,
            ids=ids,
            ids_is_callable=ids_is_callable,
            value=actual_case,
            index=index,
        )
```

This changes `_build_cases` from using `case_{index}` as its fallback to using `_generate_param_id(actual_case, index)`, which produces readable type-aware IDs like `True`, `hello`, `1-2-3` instead of opaque `case_0`, `case_1`.

- [ ] **Step 4: Verify Python tests pass**

Run:
```bash
uv run ruff format python && uv run ruff check python && uv run basedpyright python
```

Expected: All pass.

Then run tests:
```bash
uv run pytest python/tests -v && uv run pytest tests/ examples/tests/ -v
```

Expected: All pass. Some test IDs may now be more readable (e.g., `test_foo[True]` instead of `test_foo[case_0]`). If any tests assert exact test IDs that included `case_N`, they will need updating.

- [ ] **Step 5: Fix any test ID assertion failures**

If any tests assert exact test IDs containing `case_0`, `case_1` etc., update them to match the new readable IDs produced by `_generate_param_id`. Search for `case_` in test assertions:

```bash
grep -r "case_[0-9]" python/tests/ tests/
```

Update any assertions to match the new IDs.

- [ ] **Step 6: Commit**

```bash
git add python/rustest/decorators.py
git commit -m "refactor: consolidate parameter ID generation into _resolve_case_id

Extract shared priority logic (ParameterSet.id > callable ids > explicit
ids > auto-generated) into a single helper. _build_cases now uses
_generate_param_id for readable test names instead of opaque case_N."
```

---

### Task 7: Docstring Trimming (python/rustest/compat/pytest.py)

**Files:**
- Modify: `python/rustest/compat/pytest.py`

- [ ] **Step 1: Trim Node docstring**

Replace the Node docstring (lines 137-161) with:

```python
class Node:
    """Pytest-compatible Node representing a test or collection node.

    Supports: name, nodeid, get_closest_marker(), add_marker(), keywords, config.
    Not implemented: parent, session (always None).
    """
```

- [ ] **Step 2: Trim Config docstring**

Replace the Config docstring (lines 300-325) with:

```python
class Config:
    """Pytest-compatible Config for accessing test configuration.

    Supports: getoption(), getini(), rootpath, inipath, pluginmanager, option.
    """
```

- [ ] **Step 3: Trim FixtureRequest docstring**

Replace the FixtureRequest docstring (lines 461-514) with:

```python
class FixtureRequest:
    """Pytest-compatible FixtureRequest for fixture parametrization.

    Supports: param, scope, node, config, getfixturevalue().
    Not implemented: function, cls, module, fixturename (always None),
    addfinalizer() (raises NotImplementedError).
    """
```

- [ ] **Step 4: Verify Python linting passes**

Run:
```bash
uv run ruff format python && uv run ruff check python && uv run basedpyright python
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add python/rustest/compat/pytest.py
git commit -m "refactor: trim verbose docstrings in compat/pytest.py

Reduce Node, Config, and FixtureRequest docstrings from 25-50 lines
to 3-5 lines each. Migration guides belong in docs, not docstrings."
```

---

### Task 8a: Merge Fixture Parametrization Test Files

**Files:**
- Modify: `tests/test_fixture_parametrization.py`
- Delete: `tests/test_fixture_parametrization_pytest_compat.py`

- [ ] **Step 1: Read both files completely**

Read `tests/test_fixture_parametrization.py` and `tests/test_fixture_parametrization_pytest_compat.py` to identify:
- Tests that are unique to each file
- Tests that overlap (testing the same scenario with different API)
- The pytest-compat file's unique tests that need to be preserved

- [ ] **Step 2: Append pytest-compat unique tests to main file**

Add a clearly separated section at the end of `tests/test_fixture_parametrization.py`:

```python
# --- pytest-compat API tests ---
# These tests verify fixture parametrization works through the pytest compatibility layer.
```

Copy over tests from the pytest-compat file that test scenarios NOT already covered in the main file. Common patterns to merge:
- `pytest.param()` usage (unique to compat file)
- Complex object params with custom IDs (unique to compat file)
- Autouse fixtures with params (unique to compat file)

For tests that overlap (e.g., basic fixture params, scoped fixtures, yield fixtures), keep only the rustest-API version since the underlying mechanism is the same.

- [ ] **Step 3: Delete the pytest-compat file**

```bash
git rm tests/test_fixture_parametrization_pytest_compat.py
```

- [ ] **Step 4: Verify tests pass**

Run:
```bash
uv run pytest tests/test_fixture_parametrization.py -v && uv run python -m rustest tests/test_fixture_parametrization.py -v
```

Expected: All pass with both runners.

- [ ] **Step 5: Commit**

```bash
git add tests/test_fixture_parametrization.py
git commit -m "refactor: merge fixture parametrization test files

Consolidate test_fixture_parametrization_pytest_compat.py into
test_fixture_parametrization.py, keeping unique pytest-compat scenarios
and removing overlapping tests."
```

---

### Task 8b: Parametrize Scope Tests in test_parallel_async.py

**Files:**
- Modify: `tests/test_parallel_async.py`

- [ ] **Step 1: Read and identify repeated scope patterns**

Read `tests/test_parallel_async.py`. Identify test groups that repeat the same test body for different scopes (module, class, session). Common patterns:
- Resource fixture creation (same pattern, different scope)
- Parallel test execution verification (same assertions, different loop_scope)
- Timing verification (same sleep + assert, different scope)

- [ ] **Step 2: Consolidate repeated scope groups where possible**

For test groups where ONLY the scope differs, convert to parametrized tests or shared helpers. For example, if there are 3 groups testing "parallel execution with shared resource" at module/class/session scope:

```python
# Instead of 3 separate fixture+test groups, use a helper:
async def _verify_parallel_execution(resource_value):
    """Shared assertion logic for parallel execution tests."""
    assert resource_value is not None
    await asyncio.sleep(0.01)
    assert resource_value is not None
```

Keep scope-specific fixtures (they must be defined separately since `@fixture(scope=...)` is declarative), but share test logic.

- [ ] **Step 3: Keep genuinely unique tests unchanged**

Tests that are specific to one scope (e.g., class-scoped tests that verify `self` access, session-scoped tests that verify cross-module sharing) should NOT be parametrized. Only consolidate tests where the body is identical across scopes.

- [ ] **Step 4: Verify tests pass**

Run:
```bash
uv run pytest tests/test_parallel_async.py -v && uv run python -m rustest tests/test_parallel_async.py -v
```

Expected: All pass with both runners.

- [ ] **Step 5: Commit**

```bash
git add tests/test_parallel_async.py
git commit -m "refactor: reduce duplication in test_parallel_async.py

Extract shared test logic from scope-specific test groups. Tests that
were identical except for scope now share helper functions."
```

---

### Task 8c: Consolidate Skip Tests

**Files:**
- Modify: `tests/test_skip.py`
- Delete: `tests/test_skip_simple.py`, `tests/test_skip_counting.py`

- [ ] **Step 1: Read all three skip test files**

Read `tests/test_skip.py` (28 lines), `tests/test_skip_simple.py` (20 lines), and `tests/test_skip_counting.py` (52 lines).

- [ ] **Step 2: Merge into tests/test_skip.py**

Append the content from both files into `tests/test_skip.py` with clear section comments:

```python
# --- Existing content from test_skip.py ---
# (keep as-is)

# --- Dynamic skip tests (from test_skip_simple.py) ---
# (paste test functions)

# --- Skip counting tests (from test_skip_counting.py) ---
# (paste test functions and classes)
```

Deduplicate any test functions that appear in multiple files (e.g., simple passing tests used as baselines).

- [ ] **Step 3: Delete merged files**

```bash
git rm tests/test_skip_simple.py tests/test_skip_counting.py
```

- [ ] **Step 4: Verify tests pass**

Run:
```bash
uv run pytest tests/test_skip.py -v && uv run python -m rustest tests/test_skip.py -v
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_skip.py
git commit -m "refactor: consolidate skip tests into single file

Merge test_skip_simple.py and test_skip_counting.py into test_skip.py."
```

---

### Task 8d: Consolidate Async Test Files

**Files:**
- Modify: `tests/test_asyncio.py`
- Delete: `tests/test_async_fixtures.py`
- Keep: `tests/test_async_fixture_regression.py`, `tests/test_async_fixture_event_loop_issue.py`

- [ ] **Step 1: Read all async test files**

Read `tests/test_asyncio.py`, `tests/test_async_fixtures.py`, `tests/test_async_fixture_regression.py`, and `tests/test_async_fixture_event_loop_issue.py`.

- [ ] **Step 2: Merge test_async_fixtures.py into test_asyncio.py**

Append the async fixture tests into `test_asyncio.py` with a section comment:

```python
# --- Async fixture tests (from test_async_fixtures.py) ---
```

These test the same feature area (async execution) and share the same module-level skip pattern. Merging them reduces file count without losing coverage.

- [ ] **Step 3: Keep regression files separate**

`test_async_fixture_regression.py` and `test_async_fixture_event_loop_issue.py` test specific bugs with dedicated setup scenarios. Merging them would make it harder to understand what each regression test validates. Leave them as separate files.

- [ ] **Step 4: Delete merged file**

```bash
git rm tests/test_async_fixtures.py
```

- [ ] **Step 5: Verify tests pass**

Run:
```bash
uv run pytest tests/test_asyncio.py -v && uv run python -m rustest tests/test_asyncio.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_asyncio.py
git commit -m "refactor: merge test_async_fixtures.py into test_asyncio.py

Consolidate async fixture tests into the main async test file.
Keep regression-specific files separate for clarity."
```
