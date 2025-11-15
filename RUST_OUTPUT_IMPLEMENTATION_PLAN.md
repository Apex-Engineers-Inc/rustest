# Updated Implementation Plan: All-Rust Real-Time Console Output

## Context

**Recent Changes Merged:**
- ✅ `console = "0.15"` already added to dependencies (PR #64)
- ✅ Error formatting enhancements in Rust (`src/execution.rs`)
  - Assertion value extraction now in Rust
  - Rich error context generation in Rust
- ✅ Styled terminal output already used (pytest-compat banner)

**Decision:** Go all-in on Rust for console handling with real-time feedback

---

## Architecture Decision: All-Rust Output

### Why This Makes Sense Now

1. **console crate already integrated** - No new dependencies needed (just move to main deps)
2. **Error formatting moving to Rust** - Momentum already in that direction
3. **indicatif builds on console** - Natural progression from console → indicatif
4. **Real-time requires Rust** - Can't stream from Python efficiently
5. **Cleaner architecture** - One language for execution + output

### Benefits of Going All-Rust

✅ **Real-time feedback** - Stream as tests execute
✅ **Better memory usage** - No buffering all results
✅ **Simpler data flow** - Rust → Terminal (no Python hop)
✅ **Parallel updates** - Thread-safe progress from rayon
✅ **Better terminal handling** - console crate auto-detects TTY, NO_COLOR, etc.
✅ **Future-ready** - Easier to add JSON output, JUnit XML, etc.

---

## Implementation Plan

### Phase 1: Streaming Infrastructure (Week 1)

**Goal:** Enable Rust to directly output progress without Python formatting

#### 1.1 Add indicatif to main dependencies

```toml
# Cargo.toml
[dependencies]
console = "0.15"      # Already present
indicatif = "0.17"    # Move from dev-dependencies
```

#### 1.2 Create output module

**New file:** `src/output/mod.rs`

```rust
//! Terminal output and progress display
//!
//! This module handles all terminal output for rustest, providing
//! real-time feedback during test execution.

mod renderer;
mod spinner_display;
mod progress_bar_display;
mod formatter;

pub use renderer::{OutputRenderer, OutputMode};
pub use spinner_display::SpinnerDisplay;
pub use progress_bar_display::ProgressBarDisplay;
pub use formatter::ErrorFormatter;

/// Configuration for output display
#[derive(Debug, Clone)]
pub struct OutputConfig {
    pub verbose: bool,
    pub ascii_mode: bool,
    pub use_colors: bool,
    pub mode: OutputMode,
}

impl OutputConfig {
    pub fn from_run_config(config: &crate::model::RunConfiguration) -> Self {
        Self {
            verbose: config.verbose,
            ascii_mode: config.ascii,
            use_colors: !config.no_color && console::Term::stderr().is_term(),
            mode: OutputMode::detect(config),
        }
    }
}
```

#### 1.3 Define output renderer trait

**New file:** `src/output/renderer.rs`

```rust
use crate::model::{TestCase, TestModule, PyTestResult};
use std::time::Duration;

#[derive(Debug, Clone, Copy)]
pub enum OutputMode {
    /// File-level spinners (default for < 50 files)
    FileSpinners,
    /// Hierarchical with test-level spinners (verbose mode)
    Hierarchical,
    /// Single progress bar with stats (> 50 files)
    ProgressBar,
    /// Quiet mode - minimal output
    Quiet,
}

impl OutputMode {
    pub fn detect(config: &crate::model::RunConfiguration) -> Self {
        // User can override with --output-mode flag (future)
        // For now, auto-detect based on verbose flag
        if config.verbose {
            Self::Hierarchical
        } else {
            Self::FileSpinners
        }
    }
}

/// Trait for rendering test execution progress
pub trait OutputRenderer {
    /// Called when discovery completes with total counts
    fn start_suite(&mut self, total_files: usize, total_tests: usize);

    /// Called when a file starts execution
    fn start_file(&mut self, module: &TestModule);

    /// Called when a test starts (only in verbose modes)
    fn start_test(&mut self, test: &TestCase);

    /// Called when a test completes
    fn test_completed(&mut self, result: &PyTestResult);

    /// Called when a file completes
    fn file_completed(&mut self, path: &str, duration: Duration, passed: usize, failed: usize, skipped: usize);

    /// Called when entire suite completes
    fn finish_suite(&mut self, total: usize, passed: usize, failed: usize, skipped: usize, duration: Duration);

    /// Print a message without disrupting progress display
    fn println(&self, message: &str);
}
```

#### 1.4 Implement file spinner renderer

**New file:** `src/output/spinner_display.rs`

```rust
use super::renderer::OutputRenderer;
use crate::model::{TestCase, TestModule, PyTestResult};
use console::style;
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use std::collections::HashMap;
use std::time::Duration;

pub struct SpinnerDisplay {
    multi: MultiProgress,
    spinners: HashMap<String, ProgressBar>,
    use_colors: bool,
    ascii_mode: bool,
    passed: usize,
    failed: usize,
    skipped: usize,
}

impl SpinnerDisplay {
    pub fn new(use_colors: bool, ascii_mode: bool) -> Self {
        Self {
            multi: MultiProgress::new(),
            spinners: HashMap::new(),
            use_colors,
            ascii_mode,
            passed: 0,
            failed: 0,
            skipped: 0,
        }
    }

    fn spinner_style(&self) -> ProgressStyle {
        if self.ascii_mode {
            ProgressStyle::with_template("{spinner} {msg} {pos}/{len}")
                .unwrap()
                .tick_chars("/-\\|")
        } else {
            ProgressStyle::with_template("{spinner:.cyan} {msg} {pos}/{len}")
                .unwrap()
                .tick_chars("⠁⠂⠄⡀⢀⠠⠐⠈ ")
        }
    }
}

impl OutputRenderer for SpinnerDisplay {
    fn start_suite(&mut self, _total_files: usize, _total_tests: usize) {
        // No-op for spinner mode
    }

    fn start_file(&mut self, module: &TestModule) {
        let pb = self.multi.add(ProgressBar::new(module.tests.len() as u64));
        pb.set_style(self.spinner_style());
        pb.set_message(module.path.clone());
        pb.enable_steady_tick(Duration::from_millis(100));
        self.spinners.insert(module.path.clone(), pb);
    }

    fn start_test(&mut self, _test: &TestCase) {
        // Not shown in file-level mode
    }

    fn test_completed(&mut self, result: &PyTestResult) {
        if let Some(pb) = self.spinners.get(&result.path) {
            pb.inc(1);
        }

        match result.status.as_str() {
            "passed" => self.passed += 1,
            "failed" => self.failed += 1,
            "skipped" => self.skipped += 1,
            _ => {}
        }
    }

    fn file_completed(&mut self, path: &str, duration: Duration, passed: usize, failed: usize, _skipped: usize) {
        if let Some(pb) = self.spinners.remove(path) {
            let (symbol, color_fn): (&str, fn(&str) -> console::StyledObject<&str>) = if failed > 0 {
                if self.ascii_mode {
                    ("FAIL", style)
                } else {
                    ("✗", style)
                }
            } else {
                if self.ascii_mode {
                    ("PASS", style)
                } else {
                    ("✓", style)
                }
            };

            let symbol_styled = if self.use_colors {
                if failed > 0 {
                    style(symbol).red()
                } else {
                    style(symbol).green()
                }
            } else {
                style(symbol)
            };

            pb.finish_with_message(format!(
                "{} {} - {} passed, {} failed ({:.2}s)",
                symbol_styled,
                path,
                passed,
                failed,
                duration.as_secs_f64()
            ));
        }
    }

    fn finish_suite(&mut self, total: usize, passed: usize, failed: usize, skipped: usize, duration: Duration) {
        // Print summary
        eprintln!();
        let summary = if failed > 0 {
            if self.use_colors {
                format!(
                    "{} {} tests: {} passed, {} failed, {} skipped in {:.2}s",
                    style("✗").red(),
                    total,
                    style(passed).green(),
                    style(failed).red(),
                    style(skipped).yellow(),
                    duration.as_secs_f64()
                )
            } else {
                format!(
                    "✗ {} tests: {} passed, {} failed, {} skipped in {:.2}s",
                    total, passed, failed, skipped, duration.as_secs_f64()
                )
            }
        } else {
            if self.use_colors {
                format!(
                    "{} {} tests: {} passed in {:.2}s",
                    style("✓").green(),
                    total,
                    style(passed).green(),
                    duration.as_secs_f64()
                )
            } else {
                format!(
                    "✓ {} tests: {} passed in {:.2}s",
                    total, passed, duration.as_secs_f64()
                )
            }
        };

        eprintln!("{}", summary);
    }

    fn println(&self, message: &str) {
        self.multi.println(message).unwrap();
    }
}
```

#### 1.5 Integrate into execution loop

**Modify:** `src/execution.rs`

```rust
use crate::output::{OutputRenderer, OutputConfig, SpinnerDisplay};

pub fn run_collected_tests(
    modules: Vec<TestModule>,
    config: &RunConfiguration,
) -> PyResult<PyRunReport> {
    // Create renderer based on config
    let output_config = OutputConfig::from_run_config(config);
    let mut renderer: Box<dyn OutputRenderer> = match output_config.mode {
        OutputMode::FileSpinners => Box::new(SpinnerDisplay::new(
            output_config.use_colors,
            output_config.ascii_mode
        )),
        // Other modes TODO
        _ => Box::new(SpinnerDisplay::new(
            output_config.use_colors,
            output_config.ascii_mode
        )),
    };

    let total_tests: usize = modules.iter().map(|m| m.tests.len()).sum();
    renderer.start_suite(modules.len(), total_tests);

    // Rest of execution loop...
    for module in modules {
        renderer.start_file(&module);

        for test in module.tests {
            let result = run_single_test(&test, ...)?;
            renderer.test_completed(&result);
            // ... handle fail-fast, etc.
        }

        renderer.file_completed(&module.path, elapsed, passed, failed, skipped);
    }

    renderer.finish_suite(total, passed, failed, skipped, total_duration);

    // Still return PyRunReport for now (for backwards compat)
    Ok(PyRunReport { ... })
}
```

---

### Phase 2: Error Formatting in Rust (Week 2)

**Goal:** Move error_formatter.py logic to Rust

#### 2.1 Port error formatter

**New file:** `src/output/formatter.rs`

```rust
use console::style;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

pub struct ErrorFormatter {
    use_colors: bool,
}

impl ErrorFormatter {
    pub fn new(use_colors: bool) -> Self {
        Self { use_colors }
    }

    pub fn format_failure(&self, test_name: &str, test_path: &str, message: &str) -> String {
        // Port Python logic:
        // 1. Parse traceback
        // 2. Extract file/line
        // 3. Read source context
        // 4. Format with colors
        // 5. Show expected/actual if assertion

        // This will be ~200-300 lines of Rust code
        // porting the Python error_formatter.py logic

        todo!("Port error formatter from Python")
    }

    fn parse_traceback(&self, message: &str) -> Option<ParsedTraceback> {
        todo!()
    }

    fn get_source_context(&self, file_path: &Path, line_number: usize, num_lines: usize) -> Option<Vec<(usize, String)>> {
        // Read file and extract context lines
        let file = File::open(file_path).ok()?;
        let reader = BufReader::new(file);
        let lines: Vec<String> = reader.lines().filter_map(Result::ok).collect();

        let start = line_number.saturating_sub(num_lines).saturating_sub(1);
        let end = (line_number + num_lines).min(lines.len());

        Some(lines[start..end]
            .iter()
            .enumerate()
            .map(|(i, line)| (start + i + 1, line.clone()))
            .collect())
    }
}

struct ParsedTraceback {
    error_type: String,
    error_message: String,
    file_path: String,
    line_number: usize,
    failing_code: String,
    // ...
}
```

**Strategy:**
- Use existing Rust error enrichment in `execution.rs` as foundation
- Port Python regex patterns to Rust `regex` crate
- Leverage console crate for styling
- Cache file reads for performance

#### 2.2 Display failures in real-time

**Modify:** `src/output/spinner_display.rs`

```rust
impl OutputRenderer for SpinnerDisplay {
    fn test_completed(&mut self, result: &PyTestResult) {
        // ... existing code ...

        // If failed, print error immediately
        if result.status == "failed" {
            let formatter = ErrorFormatter::new(self.use_colors);
            let formatted = formatter.format_failure(
                &result.name,
                &result.path,
                result.message.as_deref().unwrap_or("No error message")
            );
            self.println(&formatted);
        }
    }
}
```

---

### Phase 3: Remove Python Output Code (Week 3)

**Goal:** Clean up Python side, make Rust output the default

#### 3.1 Simplify Python CLI

**Modify:** `python/rustest/cli.py`

```python
def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Rust now handles all output
    # Just call run() and return exit code
    report = core.run(
        paths=args.paths,
        # ... other config ...
    )

    # Exit code based on results
    return 1 if report.failed > 0 else 0

# Remove _print_report, _print_default_report, _print_verbose_report
# Remove all color code definitions
```

#### 3.2 Remove error_formatter.py

**Delete:** `python/rustest/error_formatter.py` (599 lines removed!)

#### 3.3 Update PyRunReport

**Modify:** `src/model.rs`

```rust
#[pyclass]
pub struct PyRunReport {
    #[pyo3(get)]
    pub total: usize,
    #[pyo3(get)]
    pub passed: usize,
    #[pyo3(get)]
    pub failed: usize,
    #[pyo3(get)]
    pub skipped: usize,
    #[pyo3(get)]
    pub duration: f64,
    // Remove 'results' field - no longer needed since Rust prints directly
}
```

This reduces serialization overhead to nearly zero.

---

### Phase 4: Additional Display Modes (Week 4)

**Goal:** Implement hierarchical and progress bar modes

#### 4.1 Hierarchical display

**New file:** `src/output/hierarchical_display.rs`

```rust
// Similar to spinner_display.rs but:
// - Shows test-level spinners nested under files
// - Uses indentation for hierarchy
// - Perfect for verbose mode

pub struct HierarchicalDisplay {
    multi: MultiProgress,
    file_bars: HashMap<String, ProgressBar>,
    test_bars: HashMap<String, ProgressBar>,
    // ...
}

impl OutputRenderer for HierarchicalDisplay {
    fn start_test(&mut self, test: &TestCase) {
        // Create nested spinner for individual test
        let file_pb = &self.file_bars[&test.path];
        let test_pb = self.multi.insert_after(file_pb, ProgressBar::new_spinner());
        test_pb.set_message(format!("  {}", test.display_name));
        // ...
    }

    // ... implement other methods ...
}
```

#### 4.2 Progress bar display

**New file:** `src/output/progress_bar_display.rs`

```rust
// Single progress bar showing:
// - Overall completion percentage
// - Live stats (passed/failed/skipped)
// - Current test name

pub struct ProgressBarDisplay {
    pb: ProgressBar,
    // ...
}

impl OutputRenderer for ProgressBarDisplay {
    fn test_completed(&mut self, result: &PyTestResult) {
        self.pb.inc(1);
        self.pb.set_message(format!(
            "✓ {}  ✗ {}  ⊘ {}  |  {}",
            self.passed, self.failed, self.skipped, result.name
        ));
    }
}
```

---

### Phase 5: Additional Output Formats (Future)

#### 5.1 JSON output mode

```rust
pub struct JsonRenderer {
    events: Vec<TestEvent>,
}

impl OutputRenderer for JsonRenderer {
    fn finish_suite(&mut self, ...) {
        // Print JSON to stdout
        println!("{}", serde_json::to_string(&self.events).unwrap());
    }
}
```

Usage: `rustest --output=json tests/`

#### 5.2 JUnit XML output

```rust
pub struct JunitXmlRenderer {
    // Generate JUnit XML for CI integration
}
```

Usage: `rustest --junit-xml=results.xml tests/`

---

## Migration Strategy

### Compatibility During Transition

**Option A: Feature flag (safer)**
```toml
[dependencies]
indicatif = { version = "0.17", optional = true }

[features]
default = ["rust-output"]
rust-output = ["indicatif"]
```

```python
# cli.py
if hasattr(rust, 'run_with_output'):
    # New Rust output
    rust.run_with_output(...)
else:
    # Fall back to old Python output
    report = rust.run(...)
    _print_report(report)
```

**Option B: Direct replacement (cleaner)**
- Just implement in Rust and update Python
- Faster, simpler
- Users might notice output changes

**Recommendation:** Option B - users will love the real-time feedback

### Testing Strategy

1. **Unit tests:** Test each renderer separately
2. **Integration tests:** Compare output with Python version
3. **Snapshot tests:** Capture expected output formats
4. **Manual testing:** Run on large test suites

---

## Timeline

| Week | Deliverable | Status |
|------|-------------|--------|
| Week 1 | Streaming infrastructure + file spinners | Ready to start |
| Week 2 | Error formatting in Rust | After Week 1 |
| Week 3 | Remove Python output code | After Week 2 |
| Week 4 | Hierarchical + progress bar modes | Polish |
| Future | JSON/JUnit XML outputs | Nice to have |

**Total: ~3-4 weeks for complete migration**

---

## Success Metrics

✅ **Real-time feedback** - Users see progress immediately
✅ **Cleaner codebase** - Remove ~800 lines of Python output code
✅ **Better performance** - No PyO3 serialization overhead
✅ **Better UX** - Auto-detect terminal capabilities (colors, TTY)
✅ **Extensibility** - Easy to add new output formats

---

## Next Steps

1. ✅ Review this plan
2. ⏸️ Create feature branch: `feature/rust-output`
3. ⏸️ Implement Phase 1 (Week 1)
4. ⏸️ Demo with real test suite
5. ⏸️ Iterate based on feedback
6. ⏸️ Complete Phases 2-4
7. ⏸️ Update documentation
8. ⏸️ Release as next major version

---

## Questions to Answer

1. **Should we keep Python output as fallback?**
   - Probably not needed - Rust output will be better
   - Reduces maintenance burden

2. **Should indicatif be optional?**
   - No - it's small and brings huge value
   - console crate auto-handles environments without TTY

3. **What about Windows?**
   - indicatif supports Windows
   - console crate handles Windows terminals
   - Test on Windows to be sure

4. **How to handle very wide terminal output?**
   - console crate provides `Term::size()`
   - Truncate test names if needed
   - Smart wrapping

5. **Should we show stack traces immediately or at the end?**
   - **Immediately** for better developer experience
   - pytest-xdist does this, users expect it
   - Use `multi.println()` to not disrupt progress

---

## Comparison with Original Exploration

### What Changed

| Original Plan | Updated Plan |
|---------------|--------------|
| Hybrid event streaming | All-Rust output |
| Keep Python error formatter | Port to Rust |
| Python renders output | Rust renders output |
| Gradual migration | Direct replacement |

### Why the Change Makes Sense

1. **console crate already integrated** - Foundation is there
2. **Error formatting moving to Rust** - Natural next step
3. **Simpler architecture** - No Python<>Rust event streaming complexity
4. **Better performance** - No serialization at all
5. **Future-ready** - Easier to add features

The exploration was valuable to understand the design space. Now we have a clear, direct path forward.

---

## Dependencies

**Already have:**
- ✅ `console = "0.15"` (for styled output)
- ✅ `regex = "1.10"` (for traceback parsing)

**Need to add:**
- ⏸️ Move `indicatif = "0.17"` from dev-dependencies to dependencies

**That's it!** Very minimal dependency footprint.

---

## Code Size Estimate

| Component | Lines of Code |
|-----------|---------------|
| `src/output/mod.rs` | ~50 |
| `src/output/renderer.rs` | ~100 |
| `src/output/spinner_display.rs` | ~200 |
| `src/output/hierarchical_display.rs` | ~250 |
| `src/output/progress_bar_display.rs` | ~150 |
| `src/output/formatter.rs` | ~400 |
| Integration in `execution.rs` | ~100 |
| **Total Rust code added** | **~1,250 lines** |
| **Python code removed** | **~800 lines** |
| **Net change** | **+450 lines** |

**But:** Much cleaner architecture, real-time output, better UX!

---

## Let's Do This! 🚀

Ready to start implementation? I recommend:

1. Start with **Phase 1** - get file spinners working
2. Demo on a real test suite (your own tests)
3. Get feedback on the UX
4. Then proceed to error formatting

The POC examples are already working - this is just integrating them into the actual execution pipeline.
