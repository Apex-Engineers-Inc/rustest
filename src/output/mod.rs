//! Terminal output and progress display
//!
//! This module handles all terminal output for rustest, providing
//! real-time feedback during test execution.

mod formatter;
mod renderer;
mod spinner_display;

pub use renderer::{OutputMode, OutputRenderer};
pub use spinner_display::SpinnerDisplay;

use crate::model::RunConfiguration;

/// Configuration for output display
#[derive(Debug, Clone)]
pub struct OutputConfig {
    #[allow(dead_code)]
    pub verbose: bool,
    pub ascii_mode: bool,
    pub use_colors: bool,
    #[allow(dead_code)]
    pub mode: OutputMode,
}

impl OutputConfig {
    pub fn from_run_config(config: &RunConfiguration) -> Self {
        // Check if we're in a terminal and colors aren't disabled
        let is_terminal = console::Term::stderr().is_term();
        let use_colors = !config.no_color && is_terminal;

        Self {
            verbose: config.verbose,
            ascii_mode: config.ascii,
            use_colors,
            mode: OutputMode::detect(config),
        }
    }
}
