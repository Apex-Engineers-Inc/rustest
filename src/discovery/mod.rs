//! Test discovery pipeline.
//!
//! This module walks the file system, loads Python modules, and extracts both
//! fixtures and test functions.  The code heavily documents the involved steps
//! because the interaction with Python's reflection facilities can otherwise be
//! tricky to follow.

use std::collections::HashMap;
use std::ffi::CString;
use std::path::{Path, PathBuf};

use globset::{Glob, GlobSet, GlobSetBuilder};
use indexmap::IndexMap;
use pyo3::prelude::*;
use pyo3::prelude::{PyAnyMethods, PyDictMethods};
use pyo3::types::{PyAny, PyDict, PySequence};
use pyo3::Bound;
use walkdir::WalkDir;

use crate::model::{
    invalid_test_definition, Fixture, FixtureScope, ModuleIdGenerator, ParameterMap,
    RunConfiguration, TestCase, TestModule,
};
use crate::python_support::PyPaths;

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
    let glob = build_file_glob()?;
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
            for entry in WalkDir::new(&path).into_iter().filter_map(Result::ok) {
                let file = entry.into_path();
                if file.is_file() && glob.is_match(&file) {
                    if let Some(module) =
                        collect_from_file(py, &file, config, &module_ids, &conftest_fixtures)?
                    {
                        modules.push(module);
                    }
                }
            }
        } else if path.is_file() && glob.is_match(&path) {
            if let Some(module) =
                collect_from_file(py, &path, config, &module_ids, &conftest_fixtures)?
            {
                modules.push(module);
            }
        }
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
    for entry in WalkDir::new(root).into_iter().filter_map(Result::ok) {
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
            fixtures.insert(
                name.clone(),
                Fixture::new(
                    name.clone(),
                    value.clone().unbind(),
                    extract_parameters(py, &value)?,
                    scope,
                    is_generator,
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
) -> IndexMap<String, Fixture> {
    let mut merged = IndexMap::new();

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

    merged
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
    let fixtures = merge_conftest_fixtures(py, path, module_fixtures, conftest_map);

    if let Some(pattern) = &config.pattern {
        tests.retain(|case| test_matches_pattern(case, pattern));
    }

    if tests.is_empty() {
        return Ok(None);
    }

    Ok(Some(TestModule::new(path.to_path_buf(), fixtures, tests)))
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
                fixtures.insert(
                    name.clone(),
                    Fixture::new(
                        name.clone(),
                        value.clone().unbind(),
                        extract_parameters(py, &value)?,
                        scope,
                        is_generator,
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
        // Check if it's a class (unittest.TestCase support)
        else if isclass.call1((&value,))?.is_truthy()? && is_test_case_class(py, &value)? {
            let class_tests = discover_class_tests(py, path, &name, &value)?;
            tests.extend(class_tests);
        }
    }

    Ok((fixtures, tests))
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

/// Discover test methods in a TestCase class.
fn discover_class_tests(
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
            let display_name = format!("{}.{}", class_name, name);

            // Create a callable that properly instantiates and runs the test
            let test_callable = create_test_method_runner(py, cls, &name)?;

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

/// Check if an object is callable.
fn is_callable(obj: &Bound<'_, PyAny>) -> PyResult<bool> {
    let builtins = obj.py().import("builtins")?;
    let callable_fn = builtins.getattr("callable")?;
    callable_fn.call1((obj,))?.is_truthy()
}

/// Create a callable that instantiates a TestCase and runs a specific test method.
/// This follows unittest's pattern of instantiating with the method name.
fn create_test_method_runner(
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
fn collect_marks(value: &Bound<'_, PyAny>) -> PyResult<Vec<String>> {
    let Ok(attr) = value.getattr("__rustest_marks__") else {
        return Ok(Vec::new());
    };
    let sequence: Bound<'_, PySequence> = attr.cast_into()?;
    let mut marks = Vec::new();
    for element in sequence.try_iter()? {
        let element = element?;
        let mark_dict: Bound<'_, PyDict> = element.cast_into()?;
        let name = mark_dict
            .get_item("name")?
            .ok_or_else(|| invalid_test_definition("Missing name in mark metadata"))?;
        let name: String = name.extract()?;
        marks.push(name);
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
