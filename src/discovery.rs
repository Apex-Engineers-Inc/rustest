//! Test discovery pipeline.
//!
//! This module walks the file system, loads Python modules, and extracts both
//! fixtures and test functions.  The code heavily documents the involved steps
//! because the interaction with Python's reflection facilities can otherwise be
//! tricky to follow.

use std::collections::HashMap;
use std::ffi::CString;
use std::path::{Path, PathBuf};

use console::style;
use globset::{Glob, GlobSet, GlobSetBuilder};
use indexmap::IndexMap;
use pyo3::prelude::*;
use pyo3::prelude::{PyAnyMethods, PyDictMethods};
use pyo3::types::{PyAny, PyDict, PyList, PySequence};
use pyo3::Bound;
use walkdir::WalkDir;

use crate::cache;
use crate::mark_expr::MarkExpr;
use crate::model::{
    invalid_test_definition, Fixture, FixtureScope, LastFailedMode, Mark, ModuleIdGenerator,
    ParameterMap, RunConfiguration, TestCase, TestModule,
};
use crate::python_support::{setup_python_path, PyPaths};

/// Inject the pytest compatibility shim into sys.modules.
///
/// This allows existing pytest test files to work with rustest without any code changes.
/// When tests do `import pytest`, they'll get our compatibility shim which maps pytest's
/// API to rustest's implementations.
fn inject_pytest_compat_shim(py: Python<'_>) -> PyResult<()> {
    // Import our compatibility module
    let compat_module = py.import("rustest.compat.pytest")?;

    // Inject it as 'pytest' in sys.modules
    let sys = py.import("sys")?;
    let sys_modules: Bound<'_, PyDict> = sys.getattr("modules")?.cast_into()?;
    sys_modules.set_item("pytest", compat_module)?;

    // Print a banner to inform the user they're in compatibility mode
    let box_width = 62;
    let content_width = box_width - 2;

    // Helper to print a line with borders and padding
    let print_line = |text: &str| {
        let padding = if text.is_empty() {
            content_width
        } else {
            content_width - 1 - text.len() // 1 for leading space
        };
        eprintln!(
            "{}{}{}{}{}",
            style("║").yellow(),
            if text.is_empty() { "" } else { " " },
            text,
            " ".repeat(padding),
            style("║").yellow()
        );
    };

    // Print banner
    eprintln!();
    eprintln!(
        "{}{}{}",
        style("╔").yellow(),
        style("═".repeat(content_width)).yellow(),
        style("╗").yellow()
    );

    // Centered title
    let title = "RUSTEST PYTEST COMPATIBILITY MODE";
    let title_padding = (content_width - title.len()) / 2;
    eprintln!(
        "{}{}{}{}{}",
        style("║").yellow(),
        " ".repeat(title_padding),
        title,
        " ".repeat(content_width - title.len() - title_padding),
        style("║").yellow()
    );

    eprintln!(
        "{}{}{}",
        style("╠").yellow(),
        style("═".repeat(content_width)).yellow(),
        style("╣").yellow()
    );

    print_line("Running pytest tests with rustest.");
    print_line("");
    print_line("Supported: fixtures, parametrize, marks, approx");
    print_line("Built-ins: tmp_path, tmpdir, monkeypatch");
    print_line("Not yet: fixture params, some builtins");
    print_line("");
    print_line("For full features, use native rustest:");
    print_line("  from rustest import fixture, mark, ...");

    eprintln!(
        "{}{}{}",
        style("╚").yellow(),
        style("═".repeat(content_width)).yellow(),
        style("╝").yellow()
    );
    eprintln!();

    Ok(())
}

/// Check if a directory is a virtual environment by detecting marker files.
///
/// This mimics pytest's `_in_venv()` function, which checks for:
/// - `pyvenv.cfg`: Standard Python virtual environments (PEP 405)
/// - `conda-meta/history`: Conda environments
fn is_virtualenv(path: &Path) -> bool {
    path.join("pyvenv.cfg").is_file() || path.join("conda-meta").join("history").is_file()
}

/// Check if a directory basename matches a glob pattern.
///
/// This implements simplified fnmatch-style matching similar to pytest's fnmatch_ex.
/// If the pattern contains no path separator, it's matched against the basename only.
fn matches_pattern(basename: &str, pattern: &str) -> bool {
    // Common patterns that need exact or wildcard matching
    match pattern {
        // Match directories starting with dot (hidden directories)
        ".*" => basename.starts_with('.'),
        // Match directories ending with specific suffix
        "*.egg" => basename.ends_with(".egg"),
        // Exact matches
        _ => basename == pattern,
    }
}

/// Check if a directory should be excluded from test discovery.
///
/// This implements pytest's norecursedirs behavior with the default patterns:
/// ["*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}"]
///
/// Additionally checks for virtual environments via marker files (pyvenv.cfg).
fn should_exclude_dir(entry: &walkdir::DirEntry) -> bool {
    if !entry.file_type().is_dir() {
        return false;
    }

    let path = entry.path();
    let basename = entry.file_name().to_string_lossy();

    // First check if this is a virtual environment (pytest's _in_venv check)
    if is_virtualenv(path) {
        return true;
    }

    // Apply pytest's default norecursedirs patterns
    const NORECURSE_PATTERNS: &[&str] = &[
        "*.egg",
        ".*",
        "_darcs",
        "build",
        "CVS",
        "dist",
        "node_modules",
        "venv",
        "{arch}",
    ];

    NORECURSE_PATTERNS
        .iter()
        .any(|pattern| matches_pattern(&basename, pattern))
}

/// Discover tests for the provided paths.
///
/// The return type is intentionally high level: the caller receives a list of
/// modules, each bundling the fixtures and tests that were defined in the
/// corresponding Python file.  This makes it straightforward for the execution
/// pipeline to run tests while still having quick access to fixtures.
pub fn discover_tests(
    py: Python<'_>,
    paths: &PyPaths,
    config: &RunConfiguration,
) -> PyResult<Vec<TestModule>> {
    let canonical_paths = paths.materialise()?;

    // Setup sys.path to enable imports like pytest does
    setup_python_path(py, &canonical_paths)?;

    // If pytest compatibility mode is enabled, inject the pytest shim
    if config.pytest_compat {
        inject_pytest_compat_shim(py)?;
    }

    let py_glob = build_file_glob()?;
    let md_glob = if config.enable_codeblocks {
        Some(build_markdown_glob()?)
    } else {
        None
    };
    let mut modules = Vec::new();
    let module_ids = ModuleIdGenerator::default();

    // First, discover all conftest.py files and their fixtures
    let mut conftest_fixtures: HashMap<PathBuf, IndexMap<String, Fixture>> = HashMap::new();
    for path in &canonical_paths {
        if path.is_dir() {
            discover_conftest_files(py, path, &mut conftest_fixtures, &module_ids)?;
        } else if path.is_file() {
            // Also check for conftest.py in the directory of a single file
            if let Some(parent) = path.parent() {
                discover_conftest_files(py, parent, &mut conftest_fixtures, &module_ids)?;
            }
        }
    }

    // Now discover test files, merging with conftest fixtures
    for path in canonical_paths {
        if path.is_dir() {
            for entry in WalkDir::new(&path)
                .into_iter()
                .filter_entry(|e| !should_exclude_dir(e))
                .filter_map(Result::ok)
            {
                let file = entry.into_path();
                if file.is_file() {
                    if py_glob.is_match(&file) {
                        if let Some(module) =
                            collect_from_file(py, &file, config, &module_ids, &conftest_fixtures)?
                        {
                            modules.push(module);
                        }
                    } else if let Some(ref md_glob_set) = md_glob {
                        if md_glob_set.is_match(&file) {
                            if let Some(module) =
                                collect_from_markdown(py, &file, config, &conftest_fixtures)?
                            {
                                modules.push(module);
                            }
                        }
                    }
                }
            }
        } else if path.is_file() {
            if py_glob.is_match(&path) {
                if let Some(module) =
                    collect_from_file(py, &path, config, &module_ids, &conftest_fixtures)?
                {
                    modules.push(module);
                }
            } else if let Some(ref md_glob_set) = md_glob {
                if md_glob_set.is_match(&path) {
                    if let Some(module) =
                        collect_from_markdown(py, &path, config, &conftest_fixtures)?
                    {
                        modules.push(module);
                    }
                }
            }
        }
    }

    // Apply last-failed filtering if configured
    if config.last_failed_mode != LastFailedMode::None {
        apply_last_failed_filter(&mut modules, config)?;
    }

    Ok(modules)
}

/// Discover all conftest.py files in a directory tree and load their fixtures.
fn discover_conftest_files(
    py: Python<'_>,
    root: &Path,
    conftest_map: &mut HashMap<PathBuf, IndexMap<String, Fixture>>,
    module_ids: &ModuleIdGenerator,
) -> PyResult<()> {
    for entry in WalkDir::new(root)
        .into_iter()
        .filter_entry(|e| !should_exclude_dir(e))
        .filter_map(Result::ok)
    {
        let path = entry.path();
        if path.is_file() && path.file_name() == Some("conftest.py".as_ref()) {
            let fixtures = load_conftest_fixtures(py, path, module_ids)?;
            if let Some(parent) = path.parent() {
                conftest_map.insert(parent.to_path_buf(), fixtures);
            }
        }
    }
    Ok(())
}

/// Load fixtures from a conftest.py file.
fn load_conftest_fixtures(
    py: Python<'_>,
    path: &Path,
    module_ids: &ModuleIdGenerator,
) -> PyResult<IndexMap<String, Fixture>> {
    let (module_name, package_name) = infer_module_names(path, module_ids.next());
    let module = load_python_module(py, path, &module_name, package_name.as_deref())?;
    let module_dict: Bound<'_, PyDict> = module.getattr("__dict__")?.cast_into()?;

    let inspect = py.import("inspect")?;
    let isfunction = inspect.getattr("isfunction")?;
    let mut fixtures = IndexMap::new();

    for (name_obj, value) in module_dict.iter() {
        let name: String = name_obj.extract()?;

        // Check if it's a function and a fixture
        if isfunction.call1((&value,))?.is_truthy()? && is_fixture(&value)? {
            let scope = extract_fixture_scope(&value)?;
            let is_generator = is_generator_function(py, &value)?;
            let autouse = extract_fixture_autouse(&value)?;
            fixtures.insert(
                name.clone(),
                Fixture::new(
                    name.clone(),
                    value.clone().unbind(),
                    extract_parameters(py, &value)?,
                    scope,
                    is_generator,
                    autouse,
                ),
            );
        }
    }

    Ok(fixtures)
}

/// Merge conftest fixtures for a test file with the file's own fixtures.
/// Conftest fixtures from parent directories are merged from farthest to nearest,
/// and the test file's own fixtures override any conftest fixtures with the same name.
fn merge_conftest_fixtures(
    py: Python<'_>,
    test_path: &Path,
    module_fixtures: IndexMap<String, Fixture>,
    conftest_map: &HashMap<PathBuf, IndexMap<String, Fixture>>,
) -> PyResult<IndexMap<String, Fixture>> {
    let mut merged = IndexMap::new();

    // Start with built-in fixtures so user-defined ones can override them.
    for (name, fixture) in load_builtin_fixtures(py)? {
        merged.insert(name, fixture);
    }

    // Collect all parent directories from farthest to nearest
    let mut parent_dirs = Vec::new();
    if let Some(mut parent) = test_path.parent() {
        loop {
            parent_dirs.push(parent.to_path_buf());
            if let Some(next_parent) = parent.parent() {
                parent = next_parent;
            } else {
                break;
            }
        }
    }
    parent_dirs.reverse(); // Process from farthest to nearest

    // Merge conftest fixtures from farthest to nearest
    for dir in parent_dirs {
        if let Some(fixtures) = conftest_map.get(&dir) {
            for (name, fixture) in fixtures {
                merged.insert(name.clone(), fixture.clone_with_py(py));
            }
        }
    }

    // Module's own fixtures override conftest fixtures
    for (name, fixture) in module_fixtures {
        merged.insert(name, fixture);
    }

    Ok(merged)
}

/// Load the built-in fixtures bundled with rustest.
fn load_builtin_fixtures(py: Python<'_>) -> PyResult<IndexMap<String, Fixture>> {
    let module = py.import("rustest.builtin_fixtures")?;
    let module_dict: Bound<'_, PyDict> = module.getattr("__dict__")?.cast_into()?;

    let inspect = py.import("inspect")?;
    let isfunction = inspect.getattr("isfunction")?;
    let mut fixtures = IndexMap::new();

    for (name_obj, value) in module_dict.iter() {
        let name: String = name_obj.extract()?;

        if isfunction.call1((&value,))?.is_truthy()? && is_fixture(&value)? {
            let scope = extract_fixture_scope(&value)?;
            let is_generator = is_generator_function(py, &value)?;
            let autouse = extract_fixture_autouse(&value)?;
            fixtures.insert(
                name.clone(),
                Fixture::new(
                    name,
                    value.clone().unbind(),
                    extract_parameters(py, &value)?,
                    scope,
                    is_generator,
                    autouse,
                ),
            );
        }
    }

    Ok(fixtures)
}

/// Build the default glob set matching `test_*.py` and `*_test.py` files.
fn build_file_glob() -> PyResult<GlobSet> {
    let mut builder = GlobSetBuilder::new();
    builder.add(
        Glob::new("**/test_*.py")
            .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))?,
    );
    builder.add(
        Glob::new("**/*_test.py")
            .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))?,
    );
    builder
        .build()
        .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))
}

/// Build the glob set matching markdown files (*.md).
fn build_markdown_glob() -> PyResult<GlobSet> {
    let mut builder = GlobSetBuilder::new();
    builder.add(
        Glob::new("**/*.md")
            .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))?,
    );
    builder
        .build()
        .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))
}

/// Load a module from `path` and extract fixtures and tests.
fn collect_from_file(
    py: Python<'_>,
    path: &Path,
    config: &RunConfiguration,
    module_ids: &ModuleIdGenerator,
    conftest_map: &HashMap<PathBuf, IndexMap<String, Fixture>>,
) -> PyResult<Option<TestModule>> {
    let (module_name, package_name) = infer_module_names(path, module_ids.next());
    let module = load_python_module(py, path, &module_name, package_name.as_deref())?;
    let module_dict: Bound<'_, PyDict> = module.getattr("__dict__")?.cast_into()?;

    let (module_fixtures, mut tests) = inspect_module(py, path, &module_dict)?;

    // Merge conftest fixtures with the module's own fixtures
    let fixtures = merge_conftest_fixtures(py, path, module_fixtures, conftest_map)?;

    if let Some(pattern) = &config.pattern {
        tests.retain(|case| test_matches_pattern(case, pattern));
    }

    // Apply mark filtering if specified
    if let Some(mark_expr_str) = &config.mark_expr {
        let mark_expr = MarkExpr::parse(mark_expr_str)
            .map_err(|e| invalid_test_definition(format!("Invalid mark expression: {}", e)))?;
        tests.retain(|case| mark_expr.matches(&case.marks));
    }

    if tests.is_empty() {
        return Ok(None);
    }

    Ok(Some(TestModule::new(path.to_path_buf(), fixtures, tests)))
}

/// Parse markdown file and extract Python code blocks as tests.
fn collect_from_markdown(
    py: Python<'_>,
    path: &Path,
    config: &RunConfiguration,
    conftest_map: &HashMap<PathBuf, IndexMap<String, Fixture>>,
) -> PyResult<Option<TestModule>> {
    // Read the markdown file
    let content = std::fs::read_to_string(path).map_err(|e| {
        invalid_test_definition(format!("Failed to read {}: {}", path.display(), e))
    })?;

    // Parse Python code blocks
    let mut tests = Vec::new();
    let code_blocks = extract_python_code_blocks(&content);

    for (index, code) in code_blocks.into_iter().enumerate() {
        // Create a test name based on the block index
        let test_name = format!("codeblock_{}", index);
        let display_name = format!("{}[{}]", path.display(), test_name);

        // Create a Python callable that executes the code block
        let callable = create_codeblock_callable(py, &code)?;

        // Create codeblock mark
        let codeblock_mark = Mark::new(
            "codeblock".to_string(),
            PyList::empty(py).unbind(),
            PyDict::new(py).unbind(),
        );

        tests.push(TestCase {
            name: test_name.clone(),
            display_name,
            path: path.to_path_buf(),
            callable,
            parameters: Vec::new(),
            parameter_values: ParameterMap::new(),
            skip_reason: None,
            marks: vec![codeblock_mark],
            class_name: None,
        });
    }

    // Apply pattern filtering if specified
    if let Some(pattern) = &config.pattern {
        tests.retain(|case| test_matches_pattern(case, pattern));
    }

    // Apply mark filtering if specified
    if let Some(mark_expr_str) = &config.mark_expr {
        let mark_expr = MarkExpr::parse(mark_expr_str)
            .map_err(|e| invalid_test_definition(format!("Invalid mark expression: {}", e)))?;
        tests.retain(|case| mark_expr.matches(&case.marks));
    }

    if tests.is_empty() {
        return Ok(None);
    }

    // Merge conftest fixtures for the markdown file
    let fixtures = merge_conftest_fixtures(py, path, IndexMap::new(), conftest_map)?;

    Ok(Some(TestModule::new(path.to_path_buf(), fixtures, tests)))
}

/// Extract Python code blocks from markdown content.
/// Returns a vector of Python code strings.
fn extract_python_code_blocks(content: &str) -> Vec<String> {
    let mut code_blocks = Vec::new();
    let mut in_code_block = false;
    let mut current_block = String::new();
    let mut block_language = String::new();

    for line in content.lines() {
        let trimmed = line.trim();

        if let Some(stripped) = trimmed.strip_prefix("```") {
            if in_code_block {
                // End of code block
                if block_language == "python" {
                    code_blocks.push(current_block.clone());
                }
                current_block.clear();
                block_language.clear();
                in_code_block = false;
            } else {
                // Start of code block
                in_code_block = true;
                // Extract the language identifier
                block_language = stripped.trim().to_lowercase();
            }
        } else if in_code_block {
            // Add line to current block
            if !current_block.is_empty() {
                current_block.push('\n');
            }
            current_block.push_str(line);
        }
    }

    code_blocks
}

/// Create a Python callable that executes a code block.
fn create_codeblock_callable(py: Python<'_>, code: &str) -> PyResult<Py<PyAny>> {
    // Create a wrapper function that executes the code block
    let wrapper_code = format!(
        r#"
def run_codeblock():
{}
"#,
        // Indent the code block by 4 spaces
        code.lines()
            .map(|line| format!("    {}", line))
            .collect::<Vec<_>>()
            .join("\n")
    );

    let namespace = PyDict::new(py);
    let code_cstr = CString::new(wrapper_code).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("Invalid code string: {}", e))
    })?;

    py.run(&code_cstr, Some(&namespace), Some(&namespace))?;
    let run_codeblock = namespace
        .get_item("run_codeblock")?
        .ok_or_else(|| invalid_test_definition("Failed to create codeblock callable"))?;

    Ok(run_codeblock.unbind())
}

/// Determine whether a test case should be kept for the provided pattern.
fn test_matches_pattern(test_case: &TestCase, pattern: &str) -> bool {
    let pattern_lower = pattern.to_ascii_lowercase();
    test_case
        .display_name
        .to_ascii_lowercase()
        .contains(&pattern_lower)
        || test_case
            .path
            .display()
            .to_string()
            .to_ascii_lowercase()
            .contains(&pattern_lower)
}

/// Inspect the module dictionary and extract fixtures/tests.
fn inspect_module(
    py: Python<'_>,
    path: &Path,
    module_dict: &Bound<'_, PyDict>,
) -> PyResult<(IndexMap<String, Fixture>, Vec<TestCase>)> {
    let inspect = py.import("inspect")?;
    let isfunction = inspect.getattr("isfunction")?;
    let isclass = inspect.getattr("isclass")?;
    let mut fixtures = IndexMap::new();
    let mut tests = Vec::new();

    for (name_obj, value) in module_dict.iter() {
        let name: String = name_obj.extract()?;

        // Check if it's a function
        if isfunction.call1((&value,))?.is_truthy()? {
            if is_fixture(&value)? {
                let scope = extract_fixture_scope(&value)?;
                let is_generator = is_generator_function(py, &value)?;
                let autouse = extract_fixture_autouse(&value)?;
                fixtures.insert(
                    name.clone(),
                    Fixture::new(
                        name.clone(),
                        value.clone().unbind(),
                        extract_parameters(py, &value)?,
                        scope,
                        is_generator,
                        autouse,
                    ),
                );
                continue;
            }

            if !name.starts_with("test") {
                continue;
            }

            let parameters = extract_parameters(py, &value)?;
            let skip_reason = string_attribute(&value, "__rustest_skip__")?;
            let param_cases = collect_parametrization(py, &value)?;
            let marks = collect_marks(&value)?;

            if param_cases.is_empty() {
                tests.push(TestCase {
                    name: name.clone(),
                    display_name: name.clone(),
                    path: path.to_path_buf(),
                    callable: value.clone().unbind(),
                    parameters: parameters.clone(),
                    parameter_values: ParameterMap::new(),
                    skip_reason: skip_reason.clone(),
                    marks: marks.clone(),
                    class_name: None,
                });
            } else {
                for (case_id, values) in param_cases {
                    let display_name = format!("{}[{}]", name, case_id);
                    tests.push(TestCase {
                        name: name.clone(),
                        display_name,
                        path: path.to_path_buf(),
                        callable: value.clone().unbind(),
                        parameters: parameters.clone(),
                        parameter_values: values,
                        skip_reason: skip_reason.clone(),
                        marks: marks.clone(),
                        class_name: None,
                    });
                }
            }
        }
        // Check if it's a class (both unittest.TestCase and plain test classes)
        else if isclass.call1((&value,))?.is_truthy()? {
            if is_test_case_class(py, &value)? {
                // unittest.TestCase support
                let class_tests = discover_unittest_class_tests(py, path, &name, &value)?;
                tests.extend(class_tests);
            } else if is_plain_test_class(&name) {
                // Plain pytest-style test class support
                // Extract both test methods and fixture methods from the class
                let (class_fixtures, class_tests) =
                    discover_plain_class_tests_and_fixtures(py, path, &name, &value)?;
                // Merge class fixtures into module fixtures
                for (fixture_name, fixture) in class_fixtures {
                    fixtures.insert(fixture_name, fixture);
                }
                tests.extend(class_tests);
            }
        }
    }

    Ok((fixtures, tests))
}

/// Check if a class name follows the pytest-style test class naming convention.
/// A plain test class starts with "Test" (capital T).
fn is_plain_test_class(name: &str) -> bool {
    name.starts_with("Test")
}

/// Check if a class is a unittest.TestCase subclass.
fn is_test_case_class(py: Python<'_>, cls: &Bound<'_, PyAny>) -> PyResult<bool> {
    let unittest = py.import("unittest")?;
    let test_case = unittest.getattr("TestCase")?;

    // Use issubclass to check inheritance
    let builtins = py.import("builtins")?;
    let issubclass_fn = builtins.getattr("issubclass")?;

    match issubclass_fn.call1((cls, &test_case)) {
        Ok(result) => Ok(result.is_truthy()?),
        Err(_) => Ok(false),
    }
}

/// Discover test methods in a unittest.TestCase class.
fn discover_unittest_class_tests(
    py: Python<'_>,
    path: &Path,
    class_name: &str,
    cls: &Bound<'_, PyAny>,
) -> PyResult<Vec<TestCase>> {
    let mut tests = Vec::new();
    let inspect = py.import("inspect")?;

    // Get all members of the class
    let members = inspect.call_method1("getmembers", (cls,))?;

    for member in members.try_iter()? {
        let member = member?;

        // Each member is a tuple (name, value)
        let name: String = member.get_item(0)?.extract()?;
        let method = member.get_item(1)?;

        // Check if it's a method and starts with "test"
        if name.starts_with("test") && is_callable(&method)? {
            let display_name = format!("{}::{}", class_name, name);

            // Create a callable that properly instantiates and runs the test
            let test_callable = create_unittest_method_runner(py, cls, &name)?;

            tests.push(TestCase {
                name: name.clone(),
                display_name,
                path: path.to_path_buf(),
                callable: test_callable,
                parameters: Vec::new(),
                parameter_values: ParameterMap::new(),
                skip_reason: None,
                marks: Vec::new(),
                class_name: Some(class_name.to_string()),
            });
        }
    }

    Ok(tests)
}

/// Discover test methods and fixture methods in a plain pytest-style test class.
/// Returns both fixtures defined in the class and the test cases.
fn discover_plain_class_tests_and_fixtures(
    py: Python<'_>,
    path: &Path,
    class_name: &str,
    cls: &Bound<'_, PyAny>,
) -> PyResult<(IndexMap<String, Fixture>, Vec<TestCase>)> {
    let mut fixtures = IndexMap::new();
    let mut tests = Vec::new();
    let inspect = py.import("inspect")?;

    // Get all members of the class
    let members = inspect.call_method1("getmembers", (cls,))?;

    for member in members.try_iter()? {
        let member = member?;

        // Each member is a tuple (name, value)
        let name: String = member.get_item(0)?.extract()?;
        let method = member.get_item(1)?;

        // Skip special methods (like __init__, __str__, etc.)
        if name.starts_with("__") {
            continue;
        }

        // Check if it's a fixture method
        if is_callable(&method)? && is_fixture(&method)? {
            // Extract fixture metadata
            let scope = extract_fixture_scope(&method)?;
            let is_generator = is_generator_function(py, &method)?;
            let autouse = extract_fixture_autouse(&method)?;

            // Extract parameters (excluding 'self')
            let all_params = extract_parameters(py, &method)?;
            let parameters: Vec<String> = all_params.into_iter().filter(|p| p != "self").collect();

            // Create a wrapper that instantiates the class and calls the fixture method
            let fixture_callable = create_plain_class_method_runner(py, cls, &name)?;

            fixtures.insert(
                name.clone(),
                Fixture::new(
                    name.clone(),
                    fixture_callable,
                    parameters,
                    scope,
                    is_generator,
                    autouse,
                ),
            );
            continue;
        }

        // Check if it's a test method
        if name.starts_with("test") && is_callable(&method)? {
            let display_name = format!("{}::{}", class_name, name);

            // Extract parameters (excluding 'self')
            let all_params = extract_parameters(py, &method)?;
            let parameters: Vec<String> = all_params.into_iter().filter(|p| p != "self").collect();

            // Extract metadata
            let skip_reason = string_attribute(&method, "__rustest_skip__")?;
            let marks = collect_marks(&method)?;
            let param_cases = collect_parametrization(py, &method)?;

            // Create a callable that instantiates the class and calls the method with fixtures
            let test_callable = create_plain_class_method_runner(py, cls, &name)?;

            if param_cases.is_empty() {
                tests.push(TestCase {
                    name: name.clone(),
                    display_name,
                    path: path.to_path_buf(),
                    callable: test_callable,
                    parameters,
                    parameter_values: ParameterMap::new(),
                    skip_reason,
                    marks,
                    class_name: Some(class_name.to_string()),
                });
            } else {
                // Handle parametrized test methods
                for (case_id, values) in param_cases {
                    let param_display_name = format!("{}::{}[{}]", class_name, name, case_id);
                    tests.push(TestCase {
                        name: name.clone(),
                        display_name: param_display_name,
                        path: path.to_path_buf(),
                        callable: test_callable.clone_ref(py),
                        parameters: parameters.clone(),
                        parameter_values: values,
                        skip_reason: skip_reason.clone(),
                        marks: marks.clone(),
                        class_name: Some(class_name.to_string()),
                    });
                }
            }
        }
    }

    Ok((fixtures, tests))
}

/// Check if an object is callable.
fn is_callable(obj: &Bound<'_, PyAny>) -> PyResult<bool> {
    let builtins = obj.py().import("builtins")?;
    let callable_fn = builtins.getattr("callable")?;
    callable_fn.call1((obj,))?.is_truthy()
}

/// Create a callable that instantiates a unittest.TestCase and runs a specific test method.
/// This follows unittest's pattern of instantiating with the method name.
fn create_unittest_method_runner(
    py: Python<'_>,
    cls: &Bound<'_, PyAny>,
    method_name: &str,
) -> PyResult<Py<PyAny>> {
    // Create a wrapper function that instantiates the test class and runs the method
    // This will properly invoke setUp, the test method, and tearDown
    let code = format!(
        r#"
def run_test():
    test_instance = test_class('{}')
    test_instance()
"#,
        method_name
    );

    let namespace = PyDict::new(py);
    namespace.set_item("test_class", cls)?;

    let code_cstr = CString::new(code).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("Invalid code string: {}", e))
    })?;
    // Use the same dict for both globals and locals to ensure proper variable resolution
    py.run(&code_cstr, Some(&namespace), Some(&namespace))?;
    let run_test = namespace.get_item("run_test")?.unwrap();

    Ok(run_test.unbind())
}

/// Create a callable that instantiates a plain test class and runs a specific test method.
/// This wrapper will receive fixtures as arguments and pass them to the method.
fn create_plain_class_method_runner(
    py: Python<'_>,
    cls: &Bound<'_, PyAny>,
    method_name: &str,
) -> PyResult<Py<PyAny>> {
    // Create a wrapper function that:
    // 1. Instantiates the test class (without arguments)
    // 2. Gets the test method
    // 3. Calls the method with provided fixtures (as *args)
    let code = format!(
        r#"
def run_test(*args, **kwargs):
    test_instance = test_class()
    test_method = getattr(test_instance, '{}')
    return test_method(*args, **kwargs)
"#,
        method_name
    );

    let namespace = PyDict::new(py);
    namespace.set_item("test_class", cls)?;

    let code_cstr = CString::new(code).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("Invalid code string: {}", e))
    })?;
    // Use the same dict for both globals and locals to ensure proper variable resolution
    py.run(&code_cstr, Some(&namespace), Some(&namespace))?;
    let run_test = namespace.get_item("run_test")?.unwrap();

    Ok(run_test.unbind())
}

/// Determine whether a Python object has been marked as a fixture.
fn is_fixture(value: &Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(match value.getattr("__rustest_fixture__") {
        Ok(flag) => flag.is_truthy()?,
        Err(_) => false,
    })
}

/// Check if a function is a generator function (contains yield).
fn is_generator_function(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<bool> {
    let inspect = py.import("inspect")?;
    let is_gen = inspect.call_method1("isgeneratorfunction", (value,))?;
    is_gen.is_truthy()
}

/// Extract the scope of a fixture, defaulting to "function" if not specified.
fn extract_fixture_scope(value: &Bound<'_, PyAny>) -> PyResult<FixtureScope> {
    match string_attribute(value, "__rustest_fixture_scope__")? {
        Some(scope_str) => FixtureScope::from_str(&scope_str).map_err(invalid_test_definition),
        None => Ok(FixtureScope::default()),
    }
}

/// Extract the autouse flag of a fixture, defaulting to false if not specified.
fn extract_fixture_autouse(value: &Bound<'_, PyAny>) -> PyResult<bool> {
    match value.getattr("__rustest_fixture_autouse__") {
        Ok(flag) => flag.is_truthy(),
        Err(_) => Ok(false),
    }
}

/// Extract a string attribute from the object, if present.
fn string_attribute(value: &Bound<'_, PyAny>, attr: &str) -> PyResult<Option<String>> {
    match value.getattr(attr) {
        Ok(obj) => {
            if obj.is_none() {
                Ok(None)
            } else {
                Ok(Some(obj.extract()?))
            }
        }
        Err(_) => Ok(None),
    }
}

/// Extract the parameter names from a Python callable.
fn extract_parameters(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<Vec<String>> {
    let inspect = py.import("inspect")?;
    let signature = inspect.call_method1("signature", (value,))?;
    let params = signature.getattr("parameters")?;
    let mut names = Vec::new();
    for key in params.call_method0("keys")?.try_iter()? {
        let key = key?;
        names.push(key.extract()?);
    }
    Ok(names)
}

/// Collect parameterisation information attached to a test function.
fn collect_parametrization(
    _py: Python<'_>,
    value: &Bound<'_, PyAny>,
) -> PyResult<Vec<(String, ParameterMap)>> {
    let mut parametrized = Vec::new();
    let Ok(attr) = value.getattr("__rustest_parametrization__") else {
        return Ok(parametrized);
    };
    let sequence: Bound<'_, PySequence> = attr.cast_into()?;
    for element in sequence.try_iter()? {
        let element = element?;
        let case: Bound<'_, PyDict> = element.cast_into()?;
        let case_id = case
            .get_item("id")?
            .ok_or_else(|| invalid_test_definition("Missing id in parametrization metadata"))?;
        let case_id: String = case_id.extract()?;
        let values = case
            .get_item("values")?
            .ok_or_else(|| invalid_test_definition("Missing values in parametrization metadata"))?;
        let values: Bound<'_, PyDict> = values.cast_into()?;
        let mut parameters = ParameterMap::new();
        for (key, value) in values.iter() {
            let key: String = key.extract()?;
            parameters.insert(key, value.unbind());
        }
        parametrized.push((case_id, parameters));
    }
    Ok(parametrized)
}

/// Collect mark information attached to a test function.
fn collect_marks(value: &Bound<'_, PyAny>) -> PyResult<Vec<Mark>> {
    let Ok(attr) = value.getattr("__rustest_marks__") else {
        return Ok(Vec::new());
    };
    let sequence: Bound<'_, PySequence> = attr.cast_into()?;
    let mut marks = Vec::new();
    for element in sequence.try_iter()? {
        let element = element?;
        let mark_dict: Bound<'_, PyDict> = element.cast_into()?;

        // Extract name
        let name = mark_dict
            .get_item("name")?
            .ok_or_else(|| invalid_test_definition("Missing name in mark metadata"))?;
        let name: String = name.extract()?;

        // Extract args (default to empty list if not present)
        // Convert tuple to list if necessary, since Python decorators store args as tuples
        let args_raw = mark_dict
            .get_item("args")?
            .unwrap_or_else(|| PyList::empty(value.py()).into_any());
        let args: Py<PyList> = if args_raw.is_instance_of::<pyo3::types::PyTuple>() {
            let tuple: Bound<'_, pyo3::types::PyTuple> = args_raw.cast_into()?;
            PyList::new(value.py(), tuple.iter())?.unbind()
        } else {
            args_raw.extract()?
        };

        // Extract kwargs (default to empty dict if not present)
        let kwargs = mark_dict
            .get_item("kwargs")?
            .unwrap_or_else(|| PyDict::new(value.py()).into_any());
        let kwargs: Py<PyDict> = kwargs.extract()?;

        marks.push(Mark::new(name, args, kwargs));
    }
    Ok(marks)
}

/// Load the Python module from disk.
fn load_python_module<'py>(
    py: Python<'py>,
    path: &Path,
    module_name: &str,
    package: Option<&str>,
) -> PyResult<Bound<'py, PyAny>> {
    let importlib = py.import("importlib.util")?;
    let path_str = path.to_string_lossy();
    let spec =
        importlib.call_method1("spec_from_file_location", (module_name, path_str.as_ref()))?;
    let loader = spec.getattr("loader")?;
    if loader.is_none() {
        return Err(invalid_test_definition(format!(
            "Unable to load module for {}",
            path.display()
        )));
    }
    let module = importlib.call_method1("module_from_spec", (&spec,))?;
    if let Some(package_name) = package {
        module.setattr("__package__", package_name)?;
    }
    let sys = py.import("sys")?;
    let modules: Bound<'_, PyDict> = sys.getattr("modules")?.cast_into()?;
    modules.set_item(module_name, &module)?;
    loader.call_method1("exec_module", (&module,))?;
    Ok(module)
}

/// Compute a stable module and package name for the test file.
fn infer_module_names(path: &Path, fallback_id: usize) -> (String, Option<String>) {
    let stem = path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("rustest_module");

    let mut components = vec![stem.to_string()];
    let mut parent = path.parent();

    while let Some(dir) = parent {
        let init_file = dir.join("__init__.py");
        if init_file.exists() {
            if let Some(name) = dir.file_name().and_then(|value| value.to_str()) {
                components.push(name.to_string());
            }
            parent = dir.parent();
        } else {
            break;
        }
    }

    if components.len() == 1 {
        // Fall back to a generated name when no package structure exists.
        return (format!("rustest_module_{}", fallback_id), None);
    }

    components.reverse();
    let package_components = components[..components.len() - 1].to_vec();
    let module_name = components.join(".");
    let package_name = if package_components.is_empty() {
        None
    } else {
        Some(package_components.join("."))
    };

    (module_name, package_name)
}

/// Apply last-failed filtering to the collected test modules.
/// This modifies the modules in place, filtering or reordering tests based on the last failed cache.
fn apply_last_failed_filter(
    modules: &mut Vec<TestModule>,
    config: &RunConfiguration,
) -> PyResult<()> {
    // Read the last failed test IDs from cache
    let failed_ids = cache::read_last_failed()?;

    // If the cache is empty and we're in OnlyFailed mode, return empty modules
    if failed_ids.is_empty() && config.last_failed_mode == LastFailedMode::OnlyFailed {
        modules.clear();
        return Ok(());
    }

    // Process each module
    for module in modules.iter_mut() {
        let mut failed_tests = Vec::new();
        let mut other_tests = Vec::new();

        // Separate tests into failed and non-failed
        for test in module.tests.drain(..) {
            let test_id = test.unique_id();
            if failed_ids.contains(&test_id) {
                failed_tests.push(test);
            } else {
                other_tests.push(test);
            }
        }

        // Apply the filtering/ordering based on mode
        match config.last_failed_mode {
            LastFailedMode::None => {
                // This should not happen as we check this before calling this function
                module.tests = failed_tests;
                module.tests.extend(other_tests);
            }
            LastFailedMode::OnlyFailed => {
                // Only include failed tests
                module.tests = failed_tests;
            }
            LastFailedMode::FailedFirst => {
                // Include failed tests first, then other tests
                module.tests = failed_tests;
                module.tests.extend(other_tests);
            }
        }
    }

    // Remove modules that have no tests (only relevant in OnlyFailed mode)
    modules.retain(|m| !m.tests.is_empty());

    Ok(())
}
