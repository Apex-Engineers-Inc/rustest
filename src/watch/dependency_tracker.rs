//! Dependency tracking for Python imports.
//!
//! This module analyzes Python files to extract their imports and tracks which
//! test files depend on which source files. This enables intelligent re-running
//! of only affected tests when a source file changes.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

/// Tracks dependencies between test files and source files.
pub struct DependencyTracker {
    /// Map of source file -> set of test files that import it
    dependencies: HashMap<PathBuf, HashSet<PathBuf>>,
    /// Map of test file -> set of source files it imports
    test_imports: HashMap<PathBuf, HashSet<PathBuf>>,
}

impl DependencyTracker {
    /// Create a new dependency tracker.
    pub fn new() -> Self {
        Self {
            dependencies: HashMap::new(),
            test_imports: HashMap::new(),
        }
    }

    /// Analyze a Python file and update dependency tracking.
    pub fn analyze_file(&mut self, file_path: &Path) {
        let file_path = match file_path.canonicalize() {
            Ok(p) => p,
            Err(_) => return,
        };

        // Remove old dependencies for this file if it's a test file
        if is_test_file(&file_path) {
            if let Some(old_imports) = self.test_imports.remove(&file_path) {
                for source_file in old_imports {
                    if let Some(deps) = self.dependencies.get_mut(&source_file) {
                        deps.remove(&file_path);
                    }
                }
            }

            // Analyze and add new dependencies
            let imports = self.extract_imports(&file_path);
            for import in &imports {
                self.dependencies
                    .entry(import.clone())
                    .or_default()
                    .insert(file_path.clone());
            }
            self.test_imports.insert(file_path, imports);
        }
    }

    /// Get the set of test files affected by a change to the given file.
    pub fn get_affected_tests(&self, changed_file: &Path) -> Vec<PathBuf> {
        let changed_file = match changed_file.canonicalize() {
            Ok(p) => p,
            Err(_) => return Vec::new(),
        };

        // If the changed file is a test file, return it
        if is_test_file(&changed_file) {
            return vec![changed_file];
        }

        // Otherwise, return all test files that depend on it
        self.dependencies
            .get(&changed_file)
            .map(|set| set.iter().cloned().collect())
            .unwrap_or_default()
    }

    /// Extract imports from a Python file using simple regex-based parsing.
    ///
    /// This is a simplified implementation that handles common cases:
    /// - `import module`
    /// - `from module import ...`
    /// - `from . import ...` (relative imports)
    ///
    /// For production, you might want to use a proper Python parser, but this
    /// approach is fast and handles the majority of cases.
    fn extract_imports(&self, file_path: &Path) -> HashSet<PathBuf> {
        let mut imports = HashSet::new();

        let content = match fs::read_to_string(file_path) {
            Ok(c) => c,
            Err(_) => return imports,
        };

        let file_dir = file_path.parent().unwrap_or(Path::new("."));

        for line in content.lines() {
            let line = line.trim();

            // Skip comments and empty lines
            if line.is_empty() || line.starts_with('#') {
                continue;
            }

            // Handle "import module" or "import module as alias"
            if line.starts_with("import ") {
                if let Some(module_part) = line.strip_prefix("import ") {
                    // Split by comma for multiple imports
                    for module in module_part.split(',') {
                        let module = module.trim().split_whitespace().next().unwrap_or("");
                        if let Some(path) = self.resolve_import(module, file_dir, false) {
                            imports.insert(path);
                        }
                    }
                }
            }
            // Handle "from module import ..." or "from . import ..."
            else if line.starts_with("from ") {
                if let Some(rest) = line.strip_prefix("from ") {
                    if let Some(import_pos) = rest.find(" import ") {
                        let module = rest[..import_pos].trim();
                        let is_relative = module.starts_with('.');

                        if let Some(path) = self.resolve_import(module, file_dir, is_relative) {
                            imports.insert(path);
                        }
                    }
                }
            }
        }

        imports
    }

    /// Resolve an import statement to an actual file path.
    fn resolve_import(
        &self,
        module_name: &str,
        from_dir: &Path,
        is_relative: bool,
    ) -> Option<PathBuf> {
        if is_relative {
            // Handle relative imports like "from . import foo" or "from ..pkg import bar"
            let level = module_name.chars().take_while(|&c| c == '.').count();
            let module_part = &module_name[level..];

            let mut base_dir = from_dir.to_path_buf();
            for _ in 1..level {
                base_dir = base_dir.parent()?.to_path_buf();
            }

            if module_part.is_empty() {
                // "from . import foo" - the import is from the current package
                return self.try_resolve_path(&base_dir);
            } else {
                // "from .module import foo"
                let parts: Vec<&str> = module_part.split('.').collect();
                let target = parts.iter().fold(base_dir, |path, part| path.join(part));
                return self.try_resolve_path(&target);
            }
        }

        // Handle absolute imports
        let parts: Vec<&str> = module_name.split('.').collect();

        // Try to resolve relative to the current file's directory
        let local_target = parts
            .iter()
            .fold(from_dir.to_path_buf(), |path, part| path.join(part));
        if let Some(path) = self.try_resolve_path(&local_target) {
            return Some(path);
        }

        // Try to resolve from parent directories (walking up to find the package root)
        let mut current_dir = from_dir;
        while let Some(parent) = current_dir.parent() {
            let target = parts.iter().fold(parent.to_path_buf(), |path, part| {
                path.join(part)
            });
            if let Some(path) = self.try_resolve_path(&target) {
                return Some(path);
            }

            // Stop if we've reached a non-package directory (no __init__.py)
            if !parent.join("__init__.py").exists() {
                break;
            }
            current_dir = parent;
        }

        None
    }

    /// Try to resolve a path as either a Python file or a package.
    fn try_resolve_path(&self, path: &Path) -> Option<PathBuf> {
        // Try as a .py file
        let py_file = path.with_extension("py");
        if py_file.exists() {
            return py_file.canonicalize().ok();
        }

        // Try as a package (__init__.py)
        let init_file = path.join("__init__.py");
        if init_file.exists() {
            return init_file.canonicalize().ok();
        }

        None
    }
}

/// Check if a file is a test file based on naming convention.
fn is_test_file(path: &Path) -> bool {
    if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
        name.starts_with("test_") || name.ends_with("_test.py")
    } else {
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_test_file() {
        assert!(is_test_file(Path::new("test_example.py")));
        assert!(is_test_file(Path::new("example_test.py")));
        assert!(!is_test_file(Path::new("example.py")));
        assert!(!is_test_file(Path::new("conftest.py")));
    }
}
