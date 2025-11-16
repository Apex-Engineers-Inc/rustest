//! File-level spinner display
//!
//! Shows a spinner next to each test file as it runs, updating to a
//! status symbol when complete.

use super::formatter::ErrorFormatter;
use super::renderer::OutputRenderer;
use crate::model::{PyTestResult, TestCase, TestModule};
use console::style;
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use std::collections::HashMap;
use std::time::Duration;

/// Spinner display showing file-level progress
pub struct SpinnerDisplay {
    multi: MultiProgress,
    spinners: HashMap<String, ProgressBar>,
    formatter: ErrorFormatter,
    use_colors: bool,
    ascii_mode: bool,
    passed: usize,
    failed: usize,
    skipped: usize,
}

impl SpinnerDisplay {
    /// Create a new spinner display
    pub fn new(use_colors: bool, ascii_mode: bool) -> Self {
        Self {
            multi: MultiProgress::new(),
            spinners: HashMap::new(),
            formatter: ErrorFormatter::new(use_colors),
            use_colors,
            ascii_mode,
            passed: 0,
            failed: 0,
            skipped: 0,
        }
    }

    /// Get the progress bar style for spinners
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

    /// Format a symbol based on status
    fn format_symbol(&self, failed: usize) -> String {
        if self.ascii_mode {
            if failed > 0 {
                if self.use_colors {
                    format!("{}", style("FAIL").red())
                } else {
                    "FAIL".to_string()
                }
            } else if self.use_colors {
                format!("{}", style("PASS").green())
            } else {
                "PASS".to_string()
            }
        } else if failed > 0 {
            if self.use_colors {
                format!("{}", style("✗").red())
            } else {
                "✗".to_string()
            }
        } else if self.use_colors {
            format!("{}", style("✓").green())
        } else {
            "✓".to_string()
        }
    }
}

impl OutputRenderer for SpinnerDisplay {
    fn start_suite(&mut self, _total_files: usize, _total_tests: usize) {
        // No-op for spinner mode - we don't show overall progress
    }

    fn start_file(&mut self, module: &TestModule) {
        let pb = self.multi.add(ProgressBar::new(module.tests.len() as u64));
        pb.set_style(self.spinner_style());
        let path_str = module.path.to_string_lossy().to_string();
        pb.set_message(path_str.clone());
        pb.enable_steady_tick(Duration::from_millis(100));
        self.spinners.insert(path_str, pb);
    }

    fn start_test(&mut self, _test: &TestCase) {
        // Not shown in file-level mode
    }

    fn test_completed(&mut self, result: &PyTestResult) {
        // Increment the spinner for this file
        if let Some(pb) = self.spinners.get(&result.path) {
            pb.inc(1);
        }

        // Update overall counters and show failures immediately
        match result.status.as_str() {
            "passed" => self.passed += 1,
            "failed" => {
                self.failed += 1;

                // Format and display the error immediately
                if let Some(ref message) = result.message {
                    let formatted =
                        self.formatter
                            .format_failure(&result.name, &result.path, message);
                    let _ = self.multi.println(&formatted);
                }
            }
            "skipped" => self.skipped += 1,
            _ => {}
        }
    }

    fn file_completed(
        &mut self,
        path: &str,
        duration: Duration,
        passed: usize,
        failed: usize,
        _skipped: usize,
    ) {
        if let Some(pb) = self.spinners.remove(path) {
            let symbol = self.format_symbol(failed);

            pb.finish_with_message(format!(
                "{} {} - {} passed, {} failed ({:.2}s)",
                symbol,
                path,
                passed,
                failed,
                duration.as_secs_f64()
            ));
        }
    }

    fn finish_suite(
        &mut self,
        total: usize,
        passed: usize,
        failed: usize,
        skipped: usize,
        duration: Duration,
    ) {
        // Print summary line
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
                    total,
                    passed,
                    failed,
                    skipped,
                    duration.as_secs_f64()
                )
            }
        } else if self.use_colors {
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
                total,
                passed,
                duration.as_secs_f64()
            )
        };

        eprintln!("{}", summary);
    }

    fn println(&self, message: &str) {
        let _ = self.multi.println(message);
    }
}
