# Rustest Indicatif Examples

This directory contains proof-of-concept examples demonstrating different UI approaches using the `indicatif` crate for real-time test progress visualization.

## Overview

These examples simulate how rustest could provide live feedback during test execution, rather than waiting until all tests complete.

## Running the Examples

Make sure you have the dev dependencies installed:

```bash
cargo build --examples
```

### 1. File-Level Spinners

Shows a spinner next to each test file as it runs, updating to a status symbol when complete.

```bash
cargo run --example indicatif_poc
```

**What you'll see:**
- Each file gets its own spinner
- Spinners animate while tests run
- Completed files show ✅ or ❌ with timing
- Works well with parallel execution

**Best for:** Small to medium test suites (< 50 files)

---

### 2. Hierarchical Display

Shows both files and individual tests in a nested structure with live updates.

```bash
cargo run --example indicatif_hierarchical_poc
```

**What you'll see:**
- File-level progress bars
- Nested test-level spinners
- Individual test results as they complete
- Full hierarchy visible

**Best for:** Verbose mode, debugging slow tests

---

### 3. Progress Bar with Stats

Shows a single progress bar with running statistics for large test suites.

```bash
cargo run --example indicatif_progress_bar_poc
```

**What you'll see:**
- Overall progress bar filling up
- Live counts: passed, failed, skipped
- Current test name scrolling
- Compact, clean display

**Best for:** Large test suites (100+ tests), CI environments

---

## Comparison with Current Behavior

### Current Rustest (v0.10.0)

```
..F.......s....
```
*(printed after all tests complete)*

### With Indicatif (File Spinners)

```
⠂ tests/test_auth.py (5 tests)
⠁ tests/test_database.py (8 tests)
✅ tests/test_utils.py (7 tests, 0.12s)
```
*(updating in real-time)*

### With Indicatif (Progress Bar)

```
━━━━━━━━━━━━━━━━╸─────── 234/500 (46%)
✅ 180  ❌ 12  ⊘ 42  |  tests/test_models.py::test_validation
```
*(updating in real-time)*

---

## Implementation Notes

These examples simulate test execution with `thread::sleep()`. In the real rustest implementation:

1. **File spinners** would be created in `src/execution.rs` when processing each module
2. **Test spinners** would be added/updated as individual tests start/complete
3. **Progress bars** would increment as tests finish
4. **MultiProgress** would handle concurrent updates from parallel test execution

## Next Steps

See `INDICATIF_EXPLORATION.md` in the project root for:
- Full analysis of implementation approaches
- Trade-offs between different UI styles
- Migration paths from Python to Rust output
- Performance considerations
- API design proposals

## Dependencies

These examples use:
- `indicatif = "0.17"` - Progress bars and spinners
- Standard Rust threading primitives

The real implementation would integrate with:
- `src/execution.rs` - Test execution loop
- `src/model.rs` - Test result data structures
- `rayon` - Parallel execution (already used by rustest)
