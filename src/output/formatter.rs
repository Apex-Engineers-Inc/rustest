//! Error formatting for test failures
//!
//! Formats test errors in a user-friendly way with colors, code context,
//! and extracted assertion values.

use console::style;
use std::fs::File;
use std::io::{BufRead, BufReader};

/// Formats test failures for display
pub struct ErrorFormatter {
    use_colors: bool,
}

impl ErrorFormatter {
    /// Create a new error formatter
    pub fn new(use_colors: bool) -> Self {
        Self { use_colors }
    }

    /// Format a test failure message
    pub fn format_failure(&self, test_name: &str, test_path: &str, message: &str) -> String {
        let mut output = String::new();

        // Header with test name and path
        output.push('\n');
        if self.use_colors {
            output.push_str(&format!(
                "{} {}\n",
                style(test_name).bold(),
                style(format!("({})", test_path)).dim()
            ));
            output.push_str(&format!("{}\n", style("─".repeat(70)).red()));
        } else {
            output.push_str(&format!("{} ({})\n", test_name, test_path));
            output.push_str(&format!("{}\n", "─".repeat(70)));
        }

        // Parse the error message
        let parsed = self.parse_traceback(message);

        // Show error type and message
        if let Some((error_type, error_msg)) = &parsed.error {
            let header = if let Some(msg) = error_msg {
                format!("{}: {}", error_type, msg)
            } else {
                error_type.clone()
            };

            if self.use_colors {
                output.push_str(&format!("{} {}\n", style("✗").red(), style(&header).bold()));
            } else {
                output.push_str(&format!("✗ {}\n", header));
            }
            output.push('\n');
        }

        // Show code context if available
        if let Some((file_path, line_num, failing_line)) = &parsed.location {
            if let Some(context) = self.get_code_context(file_path, *line_num, 3) {
                output.push_str(&self.format_code_context(&context, *line_num, failing_line));
                output.push('\n');
            }
        }

        // Show expected/received values if available
        if let Some((expected, actual)) = &parsed.assertion_values {
            output.push_str(&self.format_assertion_values(expected, actual));
            output.push('\n');
        }

        // If we didn't get structured data, just show the raw message
        if parsed.error.is_none() {
            output.push_str(message);
            output.push('\n');
        }

        output
    }

    /// Parse a Python traceback to extract key information
    fn parse_traceback(&self, message: &str) -> ParsedError {
        let mut error_type = None;
        let mut error_msg = None;
        let mut location = None;
        let mut assertion_values = None;

        // Look for the last line which typically has the error type and message
        let lines: Vec<&str> = message.lines().collect();

        // Find error type (last non-empty line that doesn't start with whitespace)
        for line in lines.iter().rev() {
            if !line.trim().is_empty() && !line.starts_with(' ') {
                if let Some(colon_pos) = line.find(':') {
                    error_type = Some(line[..colon_pos].trim().to_string());
                    error_msg = Some(line[colon_pos + 1..].trim().to_string());
                } else {
                    error_type = Some(line.trim().to_string());
                }
                break;
            }
        }

        // Extract file location and failing line
        // Look for lines like "  File "/path/file.py", line 123"
        for i in 0..lines.len() {
            let line = lines[i];
            if line.contains("File \"") && line.contains(", line ") {
                if let Some(file_start) = line.find("File \"") {
                    if let Some(file_end) = line[file_start + 6..].find('"') {
                        let file_path = &line[file_start + 6..file_start + 6 + file_end];

                        if let Some(line_start) = line.find(", line ") {
                            let line_num_str = &line[line_start + 7..];
                            if let Some(line_end) = line_num_str.find(|c: char| !c.is_numeric()) {
                                if let Ok(line_num) = line_num_str[..line_end].parse::<usize>() {
                                    // The next line typically has the failing code
                                    if i + 1 < lines.len() {
                                        let failing_line = lines[i + 1].trim().to_string();
                                        location =
                                            Some((file_path.to_string(), line_num, failing_line));
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // Extract assertion values if present (from Rust's enrichment)
        if let Some(marker_pos) = message.find("__RUSTEST_ASSERTION_VALUES__") {
            let values_section = &message[marker_pos..];
            let mut expected = None;
            let mut received = None;

            for line in values_section.lines() {
                if let Some(stripped) = line.strip_prefix("Expected: ") {
                    expected = Some(stripped.to_string());
                } else if let Some(stripped) = line.strip_prefix("Received: ") {
                    received = Some(stripped.to_string());
                }
            }

            if let (Some(exp), Some(rec)) = (expected, received) {
                assertion_values = Some((exp, rec));
            }
        }

        ParsedError {
            error: error_type.map(|t| (t, error_msg)),
            location,
            assertion_values,
        }
    }

    /// Get code context around a specific line
    fn get_code_context(
        &self,
        file_path: &str,
        line_num: usize,
        context_lines: usize,
    ) -> Option<Vec<(usize, String)>> {
        let file = File::open(file_path).ok()?;
        let reader = BufReader::new(file);
        let all_lines: Vec<String> = reader.lines().map_while(Result::ok).collect();

        if line_num == 0 || line_num > all_lines.len() {
            return None;
        }

        let start = line_num.saturating_sub(context_lines + 1);
        let end = (line_num + context_lines).min(all_lines.len());

        Some(
            all_lines[start..end]
                .iter()
                .enumerate()
                .map(|(i, line)| (start + i + 1, line.clone()))
                .collect(),
        )
    }

    /// Format code context with line numbers and highlighting
    fn format_code_context(
        &self,
        context: &[(usize, String)],
        failing_line_num: usize,
        _failing_code: &str,
    ) -> String {
        let mut output = String::new();

        for (line_num, line) in context {
            if *line_num == failing_line_num {
                // Highlight the failing line
                if self.use_colors {
                    output.push_str(&format!(
                        "  {} {}\n",
                        style("→").red().bold(),
                        style(line).bold()
                    ));
                } else {
                    output.push_str(&format!("  → {}\n", line));
                }
            } else {
                // Show context lines dimmed
                if self.use_colors {
                    output.push_str(&format!("    {}\n", style(line).dim()));
                } else {
                    output.push_str(&format!("    {}\n", line));
                }
            }
        }

        output
    }

    /// Format expected vs actual values for assertions
    fn format_assertion_values(&self, expected: &str, actual: &str) -> String {
        if self.use_colors {
            format!(
                "  {}: {}\n  {}: {}",
                style("Expected").cyan(),
                style(expected).green(),
                style("Received").cyan(),
                style(actual).red()
            )
        } else {
            format!("  Expected: {}\n  Received: {}", expected, actual)
        }
    }
}

/// Parsed error information
struct ParsedError {
    /// Error type and optional message
    error: Option<(String, Option<String>)>,
    /// File path, line number, and failing code line
    location: Option<(String, usize, String)>,
    /// Expected and actual values for assertions
    assertion_values: Option<(String, String)>,
}
