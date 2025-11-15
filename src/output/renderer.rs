//! Output renderer trait and mode selection

use crate::model::{PyTestResult, RunConfiguration, TestCase, TestModule};
use std::time::Duration;

/// Output display mode
#[derive(Debug, Clone, Copy)]
pub enum OutputMode {
    /// File-level spinners (default for < 50 files)
    FileSpinners,
    /// Hierarchical with test-level spinners (verbose mode)
    Hierarchical,
    /// Single progress bar with stats (> 50 files)
    #[allow(dead_code)]
    ProgressBar,
    /// Quiet mode - minimal output
    #[allow(dead_code)]
    Quiet,
}

impl OutputMode {
    /// Auto-detect the best output mode based on configuration
    pub fn detect(config: &RunConfiguration) -> Self {
        // For now, use file spinners for non-verbose, hierarchical for verbose
        // Future: could detect file count and use progress bar for very large suites
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

    /// Called when a test starts (only used in verbose modes)
    #[allow(dead_code)]
    fn start_test(&mut self, test: &TestCase);

    /// Called when a test completes
    fn test_completed(&mut self, result: &PyTestResult);

    /// Called when a file completes
    fn file_completed(
        &mut self,
        path: &str,
        duration: Duration,
        passed: usize,
        failed: usize,
        skipped: usize,
    );

    /// Called when entire suite completes
    fn finish_suite(
        &mut self,
        total: usize,
        passed: usize,
        failed: usize,
        skipped: usize,
        duration: Duration,
    );

    /// Print a message without disrupting progress display
    #[allow(dead_code)]
    fn println(&self, message: &str);
}
