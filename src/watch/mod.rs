//! Watch mode implementation for hot-reloading tests.
//!
//! This module provides file watching capabilities that track dependencies
//! between test files and source files, re-running only affected tests when
//! changes are detected.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::mpsc::channel;
use std::time::Duration;

use notify_debouncer_full::{new_debouncer, notify::*, DebounceEventResult};
use pyo3::prelude::*;

use crate::discovery::discover_tests;
use crate::execution::run_collected_tests;
use crate::model::{PyRunReport, RunConfiguration};
use crate::python_support::PyPaths;

/// Track dependencies between test files and source files.
#[derive(Default)]
struct DependencyTracker {
    /// Map of source file -> set of test files that import it
    dependencies: HashMap<PathBuf, HashSet<PathBuf>>,
    /// Map of test file -> set of source files it imports
    test_imports: HashMap<PathBuf, HashSet<PathBuf>>,
}

impl DependencyTracker {
    fn new() -> Self {
        Self::default()
    }

    /// Check if a file is a test file based on naming convention.
    fn is_test_file(path: &Path) -> bool {
        if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
            name.starts_with("test_") || name.ends_with("_test.py")
        } else {
            false
        }
    }

    /// Update dependencies for a test file by analyzing its imports.
    fn update_dependencies(&mut self, py: Python<'_>, test_file: &Path) -> PyResult<()> {
        let test_file = test_file
            .canonicalize()
            .unwrap_or_else(|_| test_file.to_path_buf());

        // Remove old dependencies for this test file
        if let Some(old_imports) = self.test_imports.remove(&test_file) {
            for source_file in old_imports {
                if let Some(deps) = self.dependencies.get_mut(&source_file) {
                    deps.remove(&test_file);
                }
            }
        }

        // Analyze imports in the test file
        let imports = self.analyze_imports(py, &test_file)?;
        self.test_imports.insert(test_file.clone(), imports.clone());

        // Update reverse mapping
        for source_file in imports {
            self.dependencies
                .entry(source_file)
                .or_default()
                .insert(test_file.clone());
        }

        Ok(())
    }

    /// Analyze a Python file to extract its imports.
    fn analyze_imports(&self, py: Python<'_>, file_path: &Path) -> PyResult<HashSet<PathBuf>> {
        let mut imports = HashSet::new();

        // Read the file
        let content = match std::fs::read_to_string(file_path) {
            Ok(c) => c,
            Err(_) => return Ok(imports),
        };

        // Use Python's ast module to parse imports
        let ast = py.import("ast")?;
        let code = ast.call_method1("parse", (&content, file_path.to_string_lossy().as_ref()))?;

        // Walk the AST
        for node in ast.call_method1("walk", (code,))?.try_iter()? {
            let node = node?;
            let node_type = node.get_type().name()?;

            match node_type.to_string().as_str() {
                "Import" => {
                    // Handle: import module
                    if let Ok(names) = node.getattr("names") {
                        for alias in names.try_iter()? {
                            if let Ok(name) = alias?.getattr("name") {
                                let module_name: String = name.extract()?;
                                if let Some(path) =
                                    self.resolve_import(py, &module_name, file_path)?
                                {
                                    imports.insert(path);
                                }
                            }
                        }
                    }
                }
                "ImportFrom" => {
                    // Handle: from module import name
                    if let Ok(module) = node.getattr("module") {
                        if !module.is_none() {
                            let module_name: String = module.extract()?;
                            if let Some(path) = self.resolve_import(py, &module_name, file_path)? {
                                imports.insert(path);
                            }
                        }
                    }
                }
                _ => {}
            }
        }

        Ok(imports)
    }

    /// Resolve an import statement to a file path.
    fn resolve_import(
        &self,
        py: Python<'_>,
        module_name: &str,
        from_file: &Path,
    ) -> PyResult<Option<PathBuf>> {
        // Handle relative imports
        if module_name.starts_with('.') {
            return self.resolve_relative_import(module_name, from_file);
        }

        // Try to resolve absolute import using Python's import system
        let sys = py.import("sys")?;
        let path_list = sys.getattr("path")?;

        let parts: Vec<&str> = module_name.split('.').collect();

        for path_obj in path_list.try_iter()? {
            let path_str: String = match path_obj?.extract() {
                Ok(s) => s,
                Err(_) => continue,
            };

            let base = PathBuf::from(path_str);
            let mut target = base.clone();

            for part in &parts {
                target.push(part);
            }

            // Try as a module file
            let module_file = target.with_extension("py");
            if module_file.exists() {
                return Ok(Some(
                    module_file
                        .canonicalize()
                        .unwrap_or_else(|_| module_file.clone()),
                ));
            }

            // Try as a package
            let package_init = target.join("__init__.py");
            if package_init.exists() {
                return Ok(Some(
                    package_init
                        .canonicalize()
                        .unwrap_or_else(|_| package_init.clone()),
                ));
            }
        }

        Ok(None)
    }

    /// Resolve a relative import (starting with dots).
    fn resolve_relative_import(
        &self,
        module_name: &str,
        from_file: &Path,
    ) -> PyResult<Option<PathBuf>> {
        let mut base_dir = from_file
            .parent()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>("Invalid file path"))?
            .to_path_buf();

        // Count dots to determine how many levels to go up
        let dots = module_name.chars().take_while(|&c| c == '.').count();
        for _ in 1..dots {
            base_dir = base_dir
                .parent()
                .ok_or_else(|| {
                    PyErr::new::<pyo3::exceptions::PyValueError, _>("Too many relative levels")
                })?
                .to_path_buf();
        }

        let module_part = module_name.trim_start_matches('.');
        if module_part.is_empty() {
            // Just dots, refers to the package
            let package_init = base_dir.join("__init__.py");
            if package_init.exists() {
                return Ok(Some(
                    package_init
                        .canonicalize()
                        .unwrap_or_else(|_| package_init.clone()),
                ));
            }
            return Ok(None);
        }

        let parts: Vec<&str> = module_part.split('.').collect();
        let mut target = base_dir.clone();
        for part in &parts {
            target.push(part);
        }

        // Try as a module file
        let module_file = target.with_extension("py");
        if module_file.exists() {
            return Ok(Some(
                module_file
                    .canonicalize()
                    .unwrap_or_else(|_| module_file.clone()),
            ));
        }

        // Try as a package
        let package_init = target.join("__init__.py");
        if package_init.exists() {
            return Ok(Some(
                package_init
                    .canonicalize()
                    .unwrap_or_else(|_| package_init.clone()),
            ));
        }

        Ok(None)
    }

    /// Get test files affected by a change to the given file.
    fn get_affected_tests(&self, changed_file: &Path) -> HashSet<PathBuf> {
        let changed_file = changed_file
            .canonicalize()
            .unwrap_or_else(|_| changed_file.to_path_buf());

        // If the changed file is a test file, return it
        if Self::is_test_file(&changed_file) {
            let mut result = HashSet::new();
            result.insert(changed_file);
            return result;
        }

        // Otherwise, return all test files that depend on it
        self.dependencies
            .get(&changed_file)
            .cloned()
            .unwrap_or_default()
    }
}

/// Run tests in watch mode, re-running affected tests on file changes.
#[pyfunction]
pub fn watch(
    py: Python<'_>,
    paths: Vec<String>,
    pattern: Option<String>,
    workers: Option<usize>,
    capture_output: bool,
) -> PyResult<PyRunReport> {
    let config = RunConfiguration::new(pattern.clone(), workers, capture_output);
    let input_paths = PyPaths::from_vec(paths.clone());

    // Run initial test suite
    println!("Running initial test suite...\n");
    let collected = discover_tests(py, &input_paths, &config)?;
    let mut last_report = run_collected_tests(py, &collected, &config)?;

    // Build dependency tracker
    let mut tracker = DependencyTracker::new();

    // Initialize dependencies for all test files
    for module in &collected {
        if let Err(e) = tracker.update_dependencies(py, &module.path) {
            eprintln!(
                "Warning: Failed to analyze dependencies for {}: {}",
                module.path.display(),
                e
            );
        }
    }

    // Set up file watcher
    let (tx, rx) = channel();
    let mut debouncer = new_debouncer(
        Duration::from_millis(500),
        None,
        move |result: DebounceEventResult| match result {
            Ok(events) => {
                for event in events {
                    for path in &event.paths {
                        if path.extension().and_then(|s| s.to_str()) == Some("py") {
                            let _ = tx.send(path.clone());
                        }
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
    .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{}", e)))?;

    // Watch all specified paths
    for path in &paths {
        let path_obj = PathBuf::from(path);
        let watch_path = if path_obj.is_file() {
            path_obj.parent().unwrap_or(&path_obj)
        } else {
            &path_obj
        };

        debouncer
            .watcher()
            .watch(watch_path, RecursiveMode::Recursive)
            .map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                    "Failed to watch path {}: {}",
                    watch_path.display(),
                    e
                ))
            })?;
    }

    println!("\nWatch mode enabled.");
    println!("Watching for changes in {} path(s)...", paths.len());
    println!("Press Ctrl+C to exit.\n");

    // Process file changes
    let mut pending_changes = HashSet::new();
    let mut last_run = std::time::Instant::now();

    loop {
        // Allow Python to handle signals (like Ctrl+C)
        py.check_signals()?;

        // Check for file changes (non-blocking with timeout)
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(changed_file) => {
                pending_changes.insert(changed_file);
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                // No new changes, check if we should run tests
                if !pending_changes.is_empty()
                    && last_run.elapsed() >= Duration::from_millis(500)
                {
                    // Run affected tests
                    let mut affected_tests = HashSet::new();

                    for changed_file in &pending_changes {
                        // Update dependencies if it's a test file
                        if DependencyTracker::is_test_file(changed_file) {
                            if let Err(e) = tracker.update_dependencies(py, changed_file) {
                                eprintln!(
                                    "Warning: Failed to update dependencies for {}: {}",
                                    changed_file.display(),
                                    e
                                );
                            }
                        }

                        let tests = tracker.get_affected_tests(changed_file);
                        affected_tests.extend(tests);
                    }

                    pending_changes.clear();

                    if !affected_tests.is_empty() {
                        println!("\n{}", "=".repeat(70));
                        println!(
                            "File changes detected. Re-running {} affected test file(s)...",
                            affected_tests.len()
                        );
                        for test_file in &affected_tests {
                            if let Ok(rel_path) = test_file.strip_prefix(std::env::current_dir()?)
                            {
                                println!("  {}", rel_path.display());
                            } else {
                                println!("  {}", test_file.display());
                            }
                        }
                        println!("{}\n", "=".repeat(70));

                        // Run the affected tests
                        let test_paths: Vec<String> = affected_tests
                            .iter()
                            .map(|p| p.to_string_lossy().into_owned())
                            .collect();
                        let affected_input = PyPaths::from_vec(test_paths);
                        let affected_collected =
                            discover_tests(py, &affected_input, &config)?;
                        last_report = run_collected_tests(py, &affected_collected, &config)?;

                        println!("\nWatching for changes...");
                    }

                    last_run = std::time::Instant::now();
                }
            }
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                break;
            }
        }
    }

    Ok(last_report)
}
