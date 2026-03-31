//! File-level spinner display
//!
//! Shows a spinner next to each test file as it runs, updating to a
//! status symbol when complete.

use super::formatter::ErrorFormatter;
use super::renderer::OutputRenderer;
use crate::model::{to_relative_path, CollectionError, PyTestResult, TestCase, TestModule};
use console::style;
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use std::collections::HashMap;
use std::time::Duration;

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
    /// Collect failures to display at the end
    deferred_failures: Vec<(String, String, String)>, // (name, path, message)
    /// Collect collection errors to display at the end
    collection_errors: Vec<(String, String)>, // (path, message)
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
            deferred_failures: Vec::new(),
            collection_errors: Vec::new(),
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

    /// Format a symbol based on status
    fn format_symbol(&self, failed: usize) -> String {
        if failed > 0 {
            let text = if self.ascii_mode { "FAIL" } else { "✗" };
            self.styled(text, |s| s.red())
        } else {
            let text = if self.ascii_mode { "PASS" } else { "✓" };
            self.styled(text, |s| s.green())
        }
    }
}

impl OutputRenderer for SpinnerDisplay {
    fn collection_error(&mut self, error: &CollectionError) {
        // Store collection errors to display at the end (like pytest does)
        self.collection_errors
            .push((error.path.clone(), error.message.clone()));
    }

    fn start_suite(&mut self, _total_files: usize, _total_tests: usize) {
        // No-op for spinner mode - we don't show overall progress
    }

    fn start_file(&mut self, module: &TestModule) {
        let pb = self.multi.add(ProgressBar::new(module.tests.len() as u64));
        pb.set_style(self.spinner_style());
        let path_str = to_relative_path(&module.path);
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

        // Update overall counters
        match result.status.as_str() {
            "passed" => self.passed += 1,
            "failed" => {
                self.failed += 1;

                // Defer error output to the end
                if let Some(ref message) = result.message {
                    self.deferred_failures.push((
                        result.name.clone(),
                        result.path.clone(),
                        message.clone(),
                    ));
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
            let total = passed + failed;
            let time_str = format_duration(duration, self.use_colors);

            // Build the status parts conditionally
            let mut status_parts = Vec::new();
            if passed > 0 {
                status_parts.push(self.styled(&format!("{} passing", passed), |s| s.green()));
            }
            if failed > 0 {
                status_parts.push(self.styled(&format!("{} failed", failed), |s| s.red()));
            }

            let status_str = if status_parts.is_empty() {
                "0 tests".to_string()
            } else {
                status_parts.join(", ")
            };

            pb.finish_with_message(format!(
                "{} {} - {}/{} {} {}",
                symbol, path, total, total, status_str, time_str
            ));
        }
    }

    fn finish_suite(
        &mut self,
        total: usize,
        passed: usize,
        failed: usize,
        skipped: usize,
        errors: usize,
        duration: Duration,
    ) {
        // Print collection errors first (like pytest does with "ERRORS" section)
        if !self.collection_errors.is_empty() {
            eprintln!();
            eprintln!("{}", self.styled("ERRORS", |s| s.red().bold()));

            for (path, message) in &self.collection_errors {
                // Print header like pytest
                eprintln!(
                    "{}",
                    style(format!("ERROR collecting {}", path)).red().bold()
                );
                eprintln!("{}", style("─".repeat(70)).dim());
                // Print the error message (may contain traceback)
                for line in message.lines() {
                    eprintln!("{}", line);
                }
                eprintln!();
            }
        }

        // Print deferred failures at the end
        if !self.deferred_failures.is_empty() {
            eprintln!();
            eprintln!("{}", self.styled("FAILURES", |s| s.red().bold()));

            for (name, path, message) in &self.deferred_failures {
                let formatted = self.formatter.format_failure(name, path, message);
                eprintln!("{}", formatted);
            }
        }

        // Print summary line
        eprintln!();

        let time_str = format_duration(duration, self.use_colors);

        // Build summary with conditional parts
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

        let status_str = if parts.is_empty() {
            "0 tests".to_string()
        } else {
            parts.join(", ")
        };

        // Symbol is red if there are failures OR errors
        let symbol = if failed > 0 || errors > 0 {
            self.styled("✗", |s| s.red())
        } else {
            self.styled("✓", |s| s.green())
        };

        eprintln!("{} {}/{} {} {}", symbol, total, total, status_str, time_str);
    }

    fn println(&self, message: &str) {
        self.multi.suspend(|| {
            eprintln!("{}", message);
        });
    }
}
