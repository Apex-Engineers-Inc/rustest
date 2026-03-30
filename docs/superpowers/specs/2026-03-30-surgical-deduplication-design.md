# Surgical Deduplication of rustest

**Date:** 2026-03-30
**Status:** Approved
**Scope:** Source code simplification + test consolidation (no API surface changes)

## Context

rustest was largely vibe-coded and has accumulated significant duplication across ~14,600 lines of source code and ~16,000 lines of tests. This spec covers incremental, testable changes that reduce code without changing behavior or API surface.

## Out of Scope

- `_pytest_stub/` consolidation (investigated, savings too small to justify)
- CaptureFixture / LogCaptureFixture base class (classes are fundamentally different despite surface similarity)
- `py>=1.11` dependency removal (actively used by tmpdir fixtures)
- RichRenderer dispatch table (8-branch if-elif is readable enough)
- Structural changes to `TestExecutionUnit`, `FixtureResolver`, or parametrization pipeline architecture

## Changes

### Item 1: Extract Fixture Loading Helper (src/discovery.rs)

**Problem:** 4 functions contain an identical ~35-line block that extracts fixture metadata and creates `Fixture` objects:
- `load_pytest_plugins_fixtures()` (lines 648-683)
- `load_conftest_fixtures()` (lines 791-826)
- `load_builtin_fixtures()` (lines 894-929)
- `inspect_module()` (lines 1260-1295)

A 5th location (`discover_plain_class_tests_and_fixtures`, lines 1699-1729) is similar but intentionally omits `extract_fixture_params()` and filters `self` from parameters.

**Fix:** Extract a helper function:
```rust
fn build_fixture_from_value(
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
    name: &str,
    class_name: Option<&str>,
) -> PyResult<(String, Fixture)>
```

The 5th location (plain class fixtures) should NOT use this helper. It has three fundamental differences: (1) it skips `extract_fixture_params()` intentionally, (2) it filters `self` from parameters, and (3) it uses `create_plain_class_method_runner()` for the callable instead of `value.clone().unbind()`. These differences make it a distinct code path, not a parameterization of the same logic. Leave it as-is.

**Estimated savings:** ~140 lines
**Risk:** Low

### Item 2: Event Emission Macro (src/output/event_stream.rs, src/output/events.rs)

**Problem:** 6 `emit_*` methods in event_stream.rs (lines 33-91) are byte-for-byte identical except for the type parameter. 3 standalone `emit_collection_*` functions in events.rs repeat the same callback-invoke pattern.

**Fix:** Replace with a macro:
```rust
macro_rules! emit_event {
    ($self:expr, $event:expr) => {
        if let Some(callback) = &$self.callback {
            Python::attach(|py| {
                if let Err(e) = callback.call1(py, (Py::new(py, $event).unwrap(),)) {
                    eprintln!("Error in event callback: {}", e);
                }
            });
        }
    };
}
```

Remove individual `emit_*` methods and call the macro directly from `OutputRenderer` impl methods. The standalone functions in events.rs get the same macro treatment (they use a raw `&Py<PyAny>` callback, so they need a variant or separate macro).

**Estimated savings:** ~60 lines
**Risk:** Low

### Item 3: Teardown Cleanup Helpers (src/execution.rs)

**Problem:** The pattern `finalize_generators() + cache.clear() + close_event_loop()` repeats ~15 times across execution.rs. Not all sites are identical -- some skip `cache.clear()`.

**Fix:** Two methods on `FixtureContext`:
- `cleanup_scope(py, scope)` -- full cleanup (teardown + clear cache + close event loop)
- `teardown_scope(py, scope)` -- just finalize generators (no cache/loop changes)
- `cleanup_all(py)` -- iterates Class -> Module -> Package -> Session

The match arm in each method destructures the scope to get the right `(teardowns, cache, event_loop)` tuple.

**Estimated savings:** ~60-80 lines
**Risk:** Low-Medium (touches core execution, but the transformation is mechanical)

### Item 4: Color Formatting Helper (src/output/spinner_display.rs)

**Problem:** `if self.use_colors { style(x).color() } else { x }` pattern repeats ~15 times.

**Fix:** A `styled()` helper method:
```rust
fn styled<F>(&self, text: &str, styler: F) -> String
where
    F: FnOnce(StyledObject<&str>) -> StyledObject<&str>,
{
    if self.use_colors {
        format!("{}", styler(style(text)))
    } else {
        text.to_string()
    }
}
```

Usage: `self.styled("FAIL", |s| s.red())` or `self.styled(&text, |s| s.green().bold())`.

**Estimated savings:** ~30-40 lines
**Risk:** Low

### Item 5: Dead Code Removal (src/model.rs, src/cache.rs)

**Problem:** Multiple methods marked `#[allow(dead_code)]` are genuinely unused.

**Fix:**
- **Remove** 6 methods: `FixtureParam::get_string_arg()`, `Mark::get_kwarg()`, `Mark::get_bool_kwarg()`, `TestCase::find_mark()`, `TestCase::has_mark()`, `Fixture::is_parametrized()`
- **Remove** `clear_last_failed()` in cache.rs
- **Keep but remove false `#[allow(dead_code)]`**: `TestCase::name`, `TestModule::path`, `RunConfiguration::worker_count`

**Estimated savings:** ~30 lines
**Risk:** Very Low

### Item 6: Parameter ID Generation Consolidation (python/rustest/decorators.py)

**Problem:** Three separate implementations of parameter ID logic:
- `_generate_param_id()` (lines 197-236): type-aware, produces readable names like `True`, `hello`, `None`, `1-2-3`
- `_build_fixture_params()` (lines 143-194): uses `_generate_param_id()` with priority logic
- `_build_cases()` (lines 406-471): has its own inline ID logic that falls back to `case_{index}` instead of `_generate_param_id()`

**Fix:**
1. `_build_cases()` uses `_generate_param_id()` instead of `case_{index}` fallback. This *improves* readability -- parametrize test names become type-aware instead of opaque `case_0`, `case_1`.
2. Extract shared priority logic (ParameterSet.id > callable ids > explicit ids list > auto-generated) into `_resolve_case_id()` used by both `_build_fixture_params()` and `_build_cases()`.

**Important:** `_generate_param_id()` must remain the canonical source of readable test names. The type-aware logic (None, bool, int/float, truncated strings, sequence representations, dict summaries) is critical for test output readability.

**Estimated savings:** ~30 lines
**Risk:** Low

### Item 7: Docstring Trimming (python/rustest/compat/pytest.py)

**Problem:** Several classes have 25-50 line docstrings with full migration guides, multiple examples, and repeated supported/not-supported lists.

**Fix:** Trim to essentials:
- `FixtureRequest` docstring (50 lines -> ~10)
- `Node` docstring (25 lines -> ~5)
- `Config` docstring (25 lines -> ~5)
- Various method docstrings with redundant examples

Keep the information useful but cut verbosity. Migration guides belong in docs, not in docstrings.

**Estimated savings:** ~100-150 lines
**Risk:** Very Low

### Item 8: Test Consolidation

#### 8a. Merge Fixture Parametrization Test Files

**Problem:** `tests/test_fixture_parametrization.py` (375 lines) and `tests/test_fixture_parametrization_pytest_compat.py` (238 lines) test the same feature in two modes.

**Fix:** Merge into one file. Use a fixture or shared helper to toggle between rustest and pytest-compat modes where the tests differ.

**Estimated savings:** ~150-200 lines

#### 8b. Parametrize Scope Tests in test_parallel_async.py

**Problem:** `tests/test_parallel_async.py` (1,087 lines) has nearly identical test methods repeated for module/class/session scope.

**Fix:** Parametrize over scopes instead of duplicating test bodies. Target: ~650 lines.

**Estimated savings:** ~400 lines

#### 8c. Consolidate Skip Tests

**Problem:** Three tiny files testing skip behavior:
- `tests/test_skip.py` (28 lines)
- `tests/test_skip_simple.py` (20 lines)
- `tests/test_skip_counting.py` (52 lines)

**Fix:** Merge into one `tests/test_skip.py` with clear sections.

**Estimated savings:** ~30 lines (mostly file overhead)

#### 8d. Consolidate Async Test Files

**Problem:** 4 fragmented async test files in `tests/`:
- `test_asyncio.py` (258 lines)
- `test_async_fixtures.py` (330 lines)
- `test_async_fixture_regression.py`
- `test_async_fixture_event_loop_issue.py`

**Fix:** Merge feature tests into one file. Keep regression-specific files only if they test genuinely different scenarios that would be confusing mixed together.

**Estimated savings:** ~100-150 lines

## Execution Order

1. **Item 5:** Dead code removal (safest, pure deletion)
2. **Item 2:** Event emission macro (isolated Rust module)
3. **Item 4:** Color formatting helper (isolated Rust module)
4. **Item 1:** Fixture extraction helper (larger Rust change in discovery.rs)
5. **Item 3:** Teardown cleanup helpers (most complex Rust change, touches execution.rs)
6. **Item 6:** Param ID consolidation (Python, low risk)
7. **Item 7:** Docstring trimming (Python, very low risk)
8. **Item 8a-d:** Test consolidation (tests, low risk)

**After each item:** Run full verification:
```bash
# Rust
cargo fmt && cargo clippy --lib -- -D warnings && cargo test

# Rebuild bridge
uv run maturin develop

# Python
uv run pytest python/tests -v
uv run pytest tests/ examples/tests/ -v
uv run python -m rustest tests/ examples/tests/ -v
```

## Estimated Total Impact

| Item | Description | Lines Saved | Risk |
|------|-------------|-------------|------|
| 1 | Fixture extraction helper | ~140 | Low |
| 2 | Event emission macro | ~60 | Low |
| 3 | Teardown cleanup helpers | ~60-80 | Low-Medium |
| 4 | Color formatting helper | ~30-40 | Low |
| 5 | Dead code removal | ~30 | Very Low |
| 6 | Param ID consolidation | ~30 | Low |
| 7 | Docstring trimming | ~100-150 | Very Low |
| 8 | Test consolidation | ~680-780 | Low |
| **Total** | | **~1,130-1,310** | |
