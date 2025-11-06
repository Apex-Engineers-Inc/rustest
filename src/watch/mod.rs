//! Watch mode for hot-reloading tests.
//!
//! This module implements file watching with intelligent dependency tracking,
//! ensuring that only affected tests are re-run when files change.

mod dependency_tracker;

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::mpsc::channel;
use std::time::Duration;

use notify_debouncer_full::{new_debouncer, notify::*, DebounceEventResult};
use pyo3::prelude::*;

use crate::discovery::discover_tests;
use crate::execution::run_collected_tests;
use crate::model::{PyRunReport, RunConfiguration};
use crate::python_support::PyPaths;

use dependency_tracker::DependencyTracker;

/// Run tests in watch mode, re-running affected tests when files change.
pub fn watch_mode(
    py: Python<'_>,
    paths: Vec<String>,
    pattern: Option<String>,
    workers: Option<usize>,
    capture_output: bool,
) -> PyResult<PyRunReport> {
    let config = RunConfiguration::new(pattern.clone(), workers, capture_output);
    let input_paths = PyPaths::from_vec(paths.clone());

    // Run initial test suite
    println!("\x1b[1mRunning initial test suite...\x1b[0m\n");
    let collected = discover_tests(py, &input_paths, &config)?;
    let mut report = run_collected_tests(py, &collected, &config)?;

    // Initialize dependency tracker
    let mut tracker = DependencyTracker::new();

    // Build initial dependency graph from collected test modules
    for module in &collected {
        tracker.analyze_file(&module.path);
    }

    // Set up file watcher
    let (tx, rx) = channel();
    let mut debouncer = new_debouncer(
        Duration::from_millis(500),
        None,
        move |result: DebounceEventResult| match result {
            Ok(events) => {
                for event in events {
                    if let Err(e) = tx.send(event) {
                        eprintln!("Error sending event: {}", e);
                    }
                }
            }
            Err(errors) => {
                for error in errors {
                    eprintln!("Watch error: {:?}", error);
                }
            }
        },
    )
    .map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to create watcher: {}", e))
    })?;

    // Watch all paths
    for path in &paths {
        let path_obj = Path::new(path);
        let watch_path = if path_obj.is_dir() {
            path_obj.to_path_buf()
        } else if path_obj.is_file() {
            path_obj.parent().unwrap_or(Path::new(".")).to_path_buf()
        } else {
            continue;
        };

        debouncer
            .watcher()
            .watch(&watch_path, RecursiveMode::Recursive)
            .map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Failed to watch {}: {}",
                    watch_path.display(),
                    e
                ))
            })?;
    }

    println!("\n\x1b[32mWatch mode enabled.\x1b[0m");
    println!(
        "\x1b[2mWatching for changes in {} path(s)...\x1b[0m",
        paths.len()
    );
    println!("\x1b[2mPress Ctrl+C to exit.\x1b[0m\n");

    // Process file change events
    loop {
        match rx.recv() {
            Ok(event) => {
                let affected = process_file_event(py, &event, &mut tracker, &config)?;

                if !affected.is_empty() {
                    // Print what's being re-run
                    println!("\n\x1b[36m{}\x1b[0m", "=".repeat(70));
                    println!(
                        "\x1b[1mFile changes detected. Re-running {} affected test file(s)...\x1b[0m",
                        affected.len()
                    );
                    for test_file in &affected {
                        if let Ok(rel_path) = test_file.strip_prefix(std::env::current_dir()?) {
                            println!("  \x1b[2m{}\x1b[0m", rel_path.display());
                        } else {
                            println!("  \x1b[2m{}\x1b[0m", test_file.display());
                        }
                    }
                    println!("\x1b[36m{}\x1b[0m\n", "=".repeat(70));

                    // Convert affected paths to strings
                    let affected_paths: Vec<String> = affected
                        .iter()
                        .map(|p| p.to_string_lossy().into_owned())
                        .collect();

                    // Run affected tests
                    let affected_input = PyPaths::from_vec(affected_paths);
                    let collected = discover_tests(py, &affected_input, &config)?;
                    report = run_collected_tests(py, &collected, &config)?;

                    println!("\n\x1b[2mWatching for changes...\x1b[0m");
                }
            }
            Err(e) => {
                eprintln!("Watch error: {}", e);
                break;
            }
        }
    }

    Ok(report)
}

/// Process a file system event and return the set of affected test files.
fn process_file_event(
    _py: Python<'_>,
    event: &notify_debouncer_full::DebouncedEvent,
    tracker: &mut DependencyTracker,
    _config: &RunConfiguration,
) -> PyResult<Vec<PathBuf>> {
    let mut affected = HashSet::new();

    for path in &event.paths {
        // Only process Python files
        if path.extension().and_then(|s| s.to_str()) != Some("py") {
            continue;
        }

        // Skip __pycache__ and hidden files
        if path
            .components()
            .any(|c| c.as_os_str().to_string_lossy().starts_with('.'))
            || path.to_string_lossy().contains("__pycache__")
        {
            continue;
        }

        // Update dependency tracking if it's a test file
        if is_test_file(path) {
            tracker.analyze_file(path);
        }

        // Get affected test files
        let tests = tracker.get_affected_tests(path);
        affected.extend(tests);
    }

    Ok(affected.into_iter().collect())
}

/// Check if a file is a test file based on naming convention.
fn is_test_file(path: &Path) -> bool {
    if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
        name.starts_with("test_") || name.ends_with("_test.py")
    } else {
        false
    }
}
