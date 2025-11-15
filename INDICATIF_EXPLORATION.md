# Indicatif Integration Exploration for Rustest

This document explores ideas for enhancing rustest's UI using the `indicatif` crate and discusses the possibility of moving output rendering from Python to Rust.

## Table of Contents
- [Current Architecture Overview](#current-architecture-overview)
- [Indicatif Capabilities](#indicatif-capabilities)
- [UI Enhancement Ideas](#ui-enhancement-ideas)
- [Moving Output to Rust](#moving-output-to-rust)
- [Implementation Approaches](#implementation-approaches)
- [Recommendations](#recommendations)

---

## Current Architecture Overview

### Current Output Flow
```
Rust (Execution) → Python (Data Structures) → Python (Formatting & Display)
```

**Key Points:**
- **Rust handles:** Test discovery, execution, fixture resolution, data collection
- **Python handles:** ALL output formatting, colors, progress indicators, error formatting
- **Data bridge:** `PyRunReport` and `PyTestResult` structs passed via PyO3

### Current Display Modes

#### 1. Default Mode (pytest-style)
- Progress indicators printed **after all tests complete**
- Symbols: `.` (pass), `F` (fail), `s` (skip) or `✓✗⊘` in Unicode mode
- Failure details printed at the end
- **No real-time feedback during execution**

**File:** `python/rustest/cli.py:213-256`

#### 2. Verbose Mode (vitest-style)
- Hierarchical structure by file → class → test
- Indented display with timing information
- Still prints **after completion**, not during execution
- Shows test tree structure

**File:** `python/rustest/cli.py:258-333`

### Key Limitation
**Tests execute, then results are printed in batch.** There's no live progress indication while tests are running.

---

## Indicatif Capabilities

### Core Features

1. **ProgressBar** - Bounded progress indicators
   - Template-based styling with custom formats
   - Supports elapsed time, ETA, speed, custom messages
   - Can transition to completion states

2. **Spinner** - Unbounded progress for indefinite operations
   - Multiple spinner styles (Braille, dots, lines, etc.)
   - Custom tick characters
   - Perfect for "running..." states

3. **MultiProgress** - Concurrent progress management
   - Manages multiple progress bars/spinners simultaneously
   - Thread-safe (Sync + Send)
   - Allows inserting/removing bars dynamically
   - Supports nested displays

4. **Styling**
   - Template syntax: `{prefix} {spinner} {wide_msg} {elapsed}`
   - Custom tick characters for spinners
   - Integration with ANSI colors
   - Progress bar characters (filled, half, empty)

5. **Terminal Integration**
   - Automatic terminal size detection
   - Proper handling of stdout/stderr
   - `.println()` for logging without disrupting bars
   - Clean cleanup on completion

### Example Spinner Styles

```rust
// Braille spinner (smooth animation)
.tick_chars("⠁⠂⠄⡀⢀⠠⠐⠈ ")

// Dots
.tick_chars("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ ")

// Line spinner
.tick_chars("⎯\\|/")

// Arrow
.tick_chars("▹▸▹◂◃◂")
```

### Key APIs

```rust
let multi = MultiProgress::new();
let pb = multi.add(ProgressBar::new_spinner());
pb.set_style(ProgressStyle::with_template("{spinner} {msg}").unwrap());
pb.set_message("Running tests...");
pb.inc(1); // Update progress
pb.finish_with_message("✓ Done");
```

---

## UI Enhancement Ideas

### Idea 1: File-Level Spinners (Minimal Enhancement)

**Concept:** Show a spinner next to each file as it's being tested.

**Visual Example:**
```
⠁ tests/test_auth.py
⠂ tests/test_database.py
✓ tests/test_utils.py (0.15s)
```

**Implementation:**
- Use `MultiProgress` with one spinner per file
- As each file starts, add a spinner
- When file completes, replace with status symbol (✓/✗/⊘)
- Show file-level timing

**Pros:**
- Clear real-time feedback
- Shows which files are currently running
- Minimal overhead
- Works well with parallel execution

**Cons:**
- Only file-level granularity
- Large test files might appear "stuck" on one spinner

**Rust Changes Required:**
- Modify `run_collected_tests()` to emit events when files start/finish
- Stream results instead of batching

---

### Idea 2: File + Test Spinners (Verbose Mode)

**Concept:** Hierarchical spinners showing both files and individual tests.

**Visual Example:**
```
tests/test_auth.py
  ✓ test_login (0.05s)
  ⠂ test_logout
  • test_register
```

**Implementation:**
- File-level spinner as parent
- Nested test-level spinners that appear as tests start
- Completed tests remain visible with status
- Use indentation for hierarchy

**Pros:**
- Maximum visibility into test execution
- Users see exactly what's running
- Great for debugging slow tests
- Hierarchical structure matches verbose mode

**Cons:**
- Can be overwhelming for large test suites
- More terminal updates = higher overhead
- May need smart collapsing for 100+ test files

**Rust Changes Required:**
- Stream test start/finish events individually
- Maintain proper ordering for display

---

### Idea 3: Progress Bar + Stats (Overview Mode)

**Concept:** Single progress bar with running statistics.

**Visual Example:**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 234/500  46%
✓ 180  ✗ 12  ⊘ 42  ⏱  15s  tests/test_database.py::test_query_timeout
```

**Implementation:**
- One progress bar showing overall completion
- Live stats: passed, failed, skipped counts
- Current test name in message area
- Updates on each test completion

**Pros:**
- Clean, compact display
- Works for very large test suites
- Shows overall progress at a glance
- Less visual noise

**Cons:**
- Less detail about what's happening
- Harder to identify which file is slow
- Single-threaded appearance (even if parallel)

**Rust Changes Required:**
- Stream test completions with counts
- Calculate total test count upfront

---

### Idea 4: Grouped Spinners (File Status Grid)

**Concept:** Show all files with status indicators, updating in place.

**Visual Example:**
```
━━━━━━━━━━━━━━━ Test Execution ━━━━━━━━━━━━━━━

tests/
  ✓ test_auth.py        (15 tests, 0.3s)
  ⠂ test_database.py    (8/23 tests)
  • test_api.py         (pending)
  ✓ test_utils.py       (7 tests, 0.1s)

Running: 2 files  |  ✓ 22  ✗ 0  ⊘ 1  |  0.4s elapsed
```

**Implementation:**
- List all discovered test files upfront
- Show status: pending (•), running (spinner), done (✓/✗)
- Update counts in place
- Summary line at bottom

**Pros:**
- Great overview of entire test suite
- See which files haven't started yet
- Clear visual hierarchy
- Works well for medium-sized projects

**Cons:**
- Doesn't scale to 100+ files (need scrolling or grouping)
- Initial discovery delay to list files
- Complex state management

**Rust Changes Required:**
- Two-phase: discovery, then execution
- Emit file-level progress events
- Track per-file test counts

---

### Idea 5: Adaptive Display (Smart Switching)

**Concept:** Automatically choose display mode based on test suite size.

**Behavior:**
- **< 10 files:** Hierarchical spinners (Idea 2)
- **10-50 files:** File-level spinners (Idea 1)
- **> 50 files:** Progress bar + stats (Idea 3)

**Implementation:**
- Detect test suite size during discovery
- Select appropriate renderer
- Could add `--ui=auto|detailed|compact` flag

**Pros:**
- Best experience for all project sizes
- Smart defaults
- Users can override if desired

**Cons:**
- More complexity in implementation
- Need to maintain multiple renderers

---

### Idea 6: Live Failure Reporting

**Concept:** Show failures immediately as they occur, not just at the end.

**Visual Example:**
```
⠁ tests/test_auth.py (12/15)
⠂ tests/test_database.py (3/23)

━━━━━━━━━━━━━━━ FAILURE ━━━━━━━━━━━━━━━
tests/test_auth.py::test_login

    def test_login():
→       assert user.is_authenticated == True
        AssertionError: Expected True, got False

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✓ 8  ✗ 1  ⊘ 0  |  0.3s
```

**Implementation:**
- Use `multi.println()` to insert failure details
- Failure formatting happens in real-time
- Progress indicators continue above/below
- Summary updates live

**Pros:**
- Immediate feedback on failures
- No waiting until the end to see what failed
- Developers can start debugging while tests continue
- Matches pytest-xdist behavior

**Cons:**
- Can clutter the display
- May interleave failures from parallel runs
- Complex to format nicely

**Rust Changes Required:**
- Stream failures immediately
- Need error formatting in Rust (or callback to Python)

---

## Moving Output to Rust

### Current Python-Rust Boundary

**Python Side:**
- `cli.py`: Argument parsing, report printing
- `error_formatter.py`: 599 lines of error formatting logic
- `reporting.py`: Data classes

**Rust Side:**
- `lib.rs`, `execution.rs`, `discovery.rs`: Core logic
- `model.rs`: Data structures (PyRunReport, PyTestResult)

### Why Move to Rust?

#### Advantages

1. **Real-Time Streaming**
   - Currently: Rust collects all results → returns to Python → Python prints
   - With Rust: Rust can print **as tests run**
   - Enables live progress indicators naturally

2. **Performance**
   - No serialization overhead for large test suites
   - Faster string formatting (though negligible impact)
   - Tighter integration with indicatif

3. **Reduced Complexity**
   - Simpler data flow: Rust → Terminal (no Python hop)
   - One less layer to maintain
   - Better type safety throughout

4. **Easier Parallel Updates**
   - indicatif is `Sync + Send`, works great with rayon
   - Can update progress from any thread
   - Less Python GIL contention

#### Disadvantages

1. **Error Formatter Complexity**
   - `error_formatter.py` is 599 lines of intricate logic
   - Parses tracebacks, reads source files, extracts values
   - Would need to be rewritten in Rust
   - Uses Python's `traceback` module

2. **Loss of Python Ecosystem**
   - Python has rich terminal libraries (rich, colorama, etc.)
   - Rust has indicatif, console, but ecosystem is smaller
   - Harder to experiment with UI changes

3. **Debugging Difficulty**
   - Python output code is easier to iterate on
   - Rust compilation is slower for quick tweaks
   - Users can't easily monkey-patch Rust output

4. **Breaking Changes**
   - Any programmatic use of `rustest.core.run()` would change
   - API stability concerns
   - Would need careful migration path

### Hybrid Approach: Event Streaming

**Concept:** Rust streams events to Python, Python handles rendering.

```rust
// Rust side
pub enum TestEvent {
    FileStarted { path: String, count: usize },
    TestStarted { name: String, path: String },
    TestCompleted { result: PyTestResult },
    FileCompleted { path: String, duration: f64 },
}

// Stream events to Python callback
for event in test_runner.run() {
    py_callback.call1((event,))?;
}
```

```python
# Python side
def handle_event(event):
    if isinstance(event, FileStarted):
        spinners[event.path] = multi.add(ProgressBar.new_spinner())
    elif isinstance(event, TestCompleted):
        # Update spinner
```

**Pros:**
- Real-time updates without full Rust rewrite
- Python keeps rendering flexibility
- Incremental migration path
- Best of both worlds?

**Cons:**
- More complex than pure Rust or pure Python
- Callback overhead on every test (though minimal)
- Two codebases to maintain

---

## Implementation Approaches

### Approach A: Pure Rust Output (Full Migration)

**Steps:**
1. Add `indicatif` dependency to `Cargo.toml`
2. Create `src/output.rs` module
3. Port error formatter to Rust (or simplify it)
4. Replace `PyRunReport` return with direct terminal output
5. Remove Python CLI output code

**Timeline:** 2-3 weeks
**Risk:** High (large change, error formatter complexity)
**Benefit:** Clean architecture, best performance

---

### Approach B: Hybrid Event Streaming

**Steps:**
1. Add event enum to `src/model.rs`
2. Modify `run_collected_tests()` to yield events
3. Update Python CLI to accept event callback
4. Create indicatif-based event handler in Python
5. Keep error formatter in Python

**Timeline:** 1 week
**Risk:** Medium (API changes, but contained)
**Benefit:** Real-time updates, maintains Python flexibility

---

### Approach C: Rust Output with Python Fallback

**Steps:**
1. Implement Rust output in parallel to existing Python output
2. Add `--rust-output` experimental flag
3. Gradual feature parity
4. Switch default when stable
5. Deprecate Python output

**Timeline:** 3-4 weeks (phased)
**Risk:** Low (gradual, reversible)
**Benefit:** Safe migration, user testing, easy rollback

---

### Approach D: Python with Indicatif-Like Library

**Steps:**
1. Keep output in Python
2. Use `rich.progress` or `alive-progress` (Python equivalents)
3. Modify `run()` to yield results via generator
4. Update CLI to consume generator and update progress

**Timeline:** 3-5 days
**Risk:** Low (stays in Python)
**Benefit:** Fast implementation, familiar ecosystem

**Note:** This approach doesn't move to Rust, but achieves similar UX

---

## Recommendations

### Short Term (1-2 weeks): Hybrid Event Streaming + File Spinners

**Why:**
- Achieves the primary goal: real-time progress indication
- Minimal risk, incremental change
- Keeps proven error formatter in Python
- Can add indicatif later without architectural changes

**Implementation:**
```rust
// src/execution.rs
pub enum TestEvent {
    FileStarted { path: String, test_count: usize },
    TestCompleted { result: PyTestResult },
}

// Yield events instead of collecting
```

```python
# python/rustest/cli.py
from rich.progress import Progress, SpinnerColumn

def run_with_progress(paths, config):
    with Progress() as progress:
        tasks = {}

        def on_event(event):
            if isinstance(event, FileStarted):
                tasks[event.path] = progress.add_task(event.path, total=event.test_count)
            elif isinstance(event, TestCompleted):
                progress.update(tasks[event.result.path], advance=1)

        core.run(paths, config, callback=on_event)
```

**Deliverables:**
- Live file-level spinners
- Real-time test counts
- Minimal code changes
- No breaking changes

---

### Medium Term (1-2 months): Full Rust Output with Indicatif

**Why:**
- Better architectural alignment
- Simpler codebase (one language for output)
- Full control over rendering
- Better performance at scale

**Prerequisites:**
- Port error formatter to Rust (or simplify)
  - Could use `miette` crate for beautiful errors
  - Or keep basic traceback formatting
- Design clean terminal output API
- Add comprehensive tests

**Implementation Plan:**
1. Create `src/output/` module with renderers
2. Port error formatting (simplified version)
3. Implement file-level spinner mode
4. Implement verbose hierarchical mode
5. Add progress bar mode
6. Replace Python CLI calls

**Migration Path:**
- Add `--experimental-rust-output` flag
- Run both side-by-side for validation
- Switch default after testing period
- Remove Python output in next major version

---

### Long Term Vision: Pluggable Renderers

**Concept:** Multiple output renderers users can choose from.

```rust
pub trait TestRenderer {
    fn on_file_started(&mut self, path: &str, count: usize);
    fn on_test_completed(&mut self, result: &TestResult);
    fn on_suite_completed(&mut self, report: &RunReport);
}

// Implementations
struct SpinnerRenderer { ... }
struct ProgressBarRenderer { ... }
struct QuietRenderer { ... }
struct JsonRenderer { ... }  // For CI tools
struct JunitXmlRenderer { ... }
```

**Benefits:**
- Users choose their preferred UX
- Easy to add CI-friendly formats (JSON, JUnit XML)
- Plugin system for custom renderers
- A/B test different UIs

---

## Proof of Concept Code

### Minimal File Spinner (Rust)

```rust
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use std::time::Duration;

pub struct TestSpinnerDisplay {
    multi: MultiProgress,
    spinners: HashMap<String, ProgressBar>,
}

impl TestSpinnerDisplay {
    pub fn new() -> Self {
        Self {
            multi: MultiProgress::new(),
            spinners: HashMap::new(),
        }
    }

    pub fn start_file(&mut self, path: String, count: usize) {
        let pb = self.multi.add(ProgressBar::new_spinner());
        pb.set_style(
            ProgressStyle::with_template("{spinner} {msg}")
                .unwrap()
                .tick_chars("⠁⠂⠄⡀⢀⠠⠐⠈ ")
        );
        pb.set_message(format!("{} ({} tests)", path, count));
        pb.enable_steady_tick(Duration::from_millis(100));
        self.spinners.insert(path, pb);
    }

    pub fn finish_file(&mut self, path: &str, passed: usize, failed: usize, duration: f64) {
        if let Some(pb) = self.spinners.get(path) {
            let symbol = if failed == 0 { "✓" } else { "✗" };
            let color = if failed == 0 { "\x1b[92m" } else { "\x1b[91m" };
            pb.finish_with_message(format!(
                "{}{}{} {} ({} passed, {} failed, {:.2}s)",
                color, symbol, "\x1b[0m", path, passed, failed, duration
            ));
        }
    }
}
```

### Integration Point in execution.rs

```rust
// src/execution.rs
pub fn run_collected_tests(
    modules: Vec<TestModule>,
    config: &Config,
    display: Option<&mut TestSpinnerDisplay>,  // NEW
) -> PyResult<PyRunReport> {

    for module in modules {
        let test_count = module.tests.len();

        // NEW: Notify display
        if let Some(d) = display.as_mut() {
            d.start_file(module.path.clone(), test_count);
        }

        for test in module.tests {
            let result = run_single_test(&test, ...)?;
            results.push(result);
        }

        // NEW: Finish file
        if let Some(d) = display.as_mut() {
            let passed = results.iter().filter(|r| r.status == "passed").count();
            let failed = results.iter().filter(|r| r.status == "failed").count();
            d.finish_file(&module.path, passed, failed, elapsed);
        }
    }

    Ok(PyRunReport { ... })
}
```

---

## Next Steps

### To Proceed with Investigation:

1. **Prototype File Spinners** (2-3 hours)
   - Add indicatif to Cargo.toml
   - Create minimal spinner in execution.rs
   - Test with real test suite
   - Take screenshots for comparison

2. **Benchmark Performance** (1 hour)
   - Measure overhead of live updates
   - Compare: no output vs Python output vs Rust spinners
   - Test with 100, 1000, 10000 tests

3. **User Feedback** (ongoing)
   - Create demo GIFs
   - Ask community preference: file-level, test-level, progress bar
   - Survey preferred symbols and colors

4. **Port Error Formatter** (if going Rust route) (1 week)
   - Study error_formatter.py logic
   - Research Rust alternatives (miette, color-eyre)
   - Prototype in Rust, compare output

5. **Design Event API** (if going hybrid) (3-4 hours)
   - Define event types
   - Design callback interface
   - Prototype in both Rust and Python

### Questions to Answer:

- **Should indicatif be optional?** (Feature flag for terminals without good Unicode support)
- **How to handle CI environments?** (Detect TTY, fall back to simple output)
- **What about Windows?** (indicatif supports Windows, but test it)
- **Accessibility concerns?** (Some users prefer simple text, provide `--no-spinners` flag)
- **How to handle very large test suites?** (1000+ files - need smart grouping)

---

## Conclusion

Adding indicatif to rustest has **strong potential** to significantly improve the user experience:

1. **Real-time feedback** is the #1 missing feature currently
2. **indicatif is mature and widely used** in the Rust ecosystem
3. **Multiple implementation paths** are available with varying risk/reward

**Recommended approach:** Start with **Hybrid Event Streaming** for quick wins, then evaluate full Rust migration based on user feedback and performance data.

The move to Rust output is **desirable but not urgent**. The hybrid approach can deliver 80% of the UX benefit with 20% of the effort, making it an excellent first step.
