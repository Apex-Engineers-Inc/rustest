//! Test discovery pipeline.
//!
//! This module walks the file system, loads Python modules, and extracts both
//! fixtures and test functions.  The code heavily documents the involved steps
//! because the interaction with Python's reflection facilities can otherwise be
//! tricky to follow.

use std::path::Path;

use globset::{Glob, GlobSet, GlobSetBuilder};
use indexmap::IndexMap;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PySequence};
use walkdir::WalkDir;

use crate::model::{
    invalid_test_definition, Fixture, ModuleIdGenerator, ParameterMap, RunConfiguration, TestCase,
    TestModule,
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

    for path in canonical_paths {
        if path.is_dir() {
            for entry in WalkDir::new(&path).into_iter().filter_map(Result::ok) {
                let file = entry.into_path();
                if file.is_file() && glob.is_match(&file) {
                    if let Some(module) = collect_from_file(py, &file, config, &module_ids)? {
                        modules.push(module);
                    }
                }
            }
        } else if path.is_file() {
            if glob.is_match(&path) {
                if let Some(module) = collect_from_file(py, &path, config, &module_ids)? {
                    modules.push(module);
                }
            }
        }
    }

    Ok(modules)
}

/// Build the default glob set matching `test_*.py` and `*_test.py` files.
fn build_file_glob() -> PyResult<GlobSet> {
    let mut builder = GlobSetBuilder::new();
    builder.add(
        Glob::new("test_*.py")
            .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))?,
    );
    builder.add(
        Glob::new("*_test.py")
            .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))?,
    );
    Ok(builder
        .build()
        .map_err(|err| PyErr::new::<pyo3::exceptions::PyValueError, _>(err.to_string()))?)
}

/// Load a module from `path` and extract fixtures and tests.
fn collect_from_file(
    py: Python<'_>,
    path: &Path,
    config: &RunConfiguration,
    module_ids: &ModuleIdGenerator,
) -> PyResult<Option<TestModule>> {
    let (module_name, package_name) = infer_module_names(path, module_ids.next());
    let module = load_python_module(py, path, &module_name, package_name.as_deref())?;
    let module_dict: &PyDict = module.getattr("__dict__")?.downcast()?;

    let (fixtures, mut tests) = inspect_module(py, path, module_dict)?;

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
    module_dict: &PyDict,
) -> PyResult<(IndexMap<String, Fixture>, Vec<TestCase>)> {
    let inspect = py.import("inspect")?;
    let isfunction = inspect.getattr("isfunction")?;
    let mut fixtures = IndexMap::new();
    let mut tests = Vec::new();

    for (name_obj, value) in module_dict.iter() {
        if !is_true(py, &isfunction.call1((value,))?)? {
            continue;
        }

        let name: String = name_obj.extract()?;
        if is_fixture(py, value)? {
            fixtures.insert(
                name.clone(),
                Fixture::new(
                    name.clone(),
                    value.into_py(py),
                    extract_parameters(py, value)?,
                ),
            );
            continue;
        }

        if !name.starts_with("test") {
            continue;
        }

        let parameters = extract_parameters(py, value)?;
        let skip_reason = string_attribute(py, value, "__rustest_skip__")?;
        let param_cases = collect_parametrization(py, value)?;

        if param_cases.is_empty() {
            tests.push(TestCase {
                name: name.clone(),
                display_name: name.clone(),
                path: path.to_path_buf(),
                callable: value.into_py(py),
                parameters: parameters.clone(),
                parameter_values: ParameterMap::new(),
                skip_reason: skip_reason.clone(),
            });
        } else {
            for (case_id, values) in param_cases {
                let display_name = format!("{}[{}]", name, case_id);
                tests.push(TestCase {
                    name: name.clone(),
                    display_name,
                    path: path.to_path_buf(),
                    callable: value.into_py(py),
                    parameters: parameters.clone(),
                    parameter_values: values,
                    skip_reason: skip_reason.clone(),
                });
            }
        }
    }

    Ok((fixtures, tests))
}

/// Determine whether a Python object has been marked as a fixture.
fn is_fixture(py: Python<'_>, value: &PyAny) -> PyResult<bool> {
    Ok(match value.getattr("__rustest_fixture__") {
        Ok(flag) => flag.is_true()?,
        Err(_) => false,
    })
}

/// Extract a string attribute from the object, if present.
fn string_attribute(py: Python<'_>, value: &PyAny, attr: &str) -> PyResult<Option<String>> {
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
fn extract_parameters(py: Python<'_>, value: &PyAny) -> PyResult<Vec<String>> {
    let inspect = py.import("inspect")?;
    let signature = inspect.call_method1("signature", (value,))?;
    let params = signature.getattr("parameters")?;
    let mut names = Vec::new();
    for key in params.call_method0("keys")?.iter()? {
        let key: &PyAny = key?;
        names.push(key.extract()?);
    }
    Ok(names)
}

/// Collect parameterisation information attached to a test function.
fn collect_parametrization(py: Python<'_>, value: &PyAny) -> PyResult<Vec<(String, ParameterMap)>> {
    let mut parametrized = Vec::new();
    let Ok(attr) = value.getattr("__rustest_parametrization__") else {
        return Ok(parametrized);
    };
    let sequence: &PySequence = attr.downcast()?;
    for element in sequence.iter()? {
        let element = element?;
        let case: &PyDict = element.downcast()?;
        let case_id: String = case
            .get_item("id")
            .ok_or_else(|| invalid_test_definition("Missing id in parametrization metadata"))?
            .extract()?;
        let values: &PyDict = case
            .get_item("values")
            .ok_or_else(|| invalid_test_definition("Missing values in parametrization metadata"))?
            .downcast()?;
        let mut parameters = ParameterMap::new();
        for (key, value) in values.iter() {
            let key: String = key.extract()?;
            parameters.insert(key, value.to_object(py));
        }
        parametrized.push((case_id, parameters));
    }
    Ok(parametrized)
}

/// Load the Python module from disk.
fn load_python_module(
    py: Python<'_>,
    path: &Path,
    module_name: &str,
    package: Option<&str>,
) -> PyResult<Bound<'_, PyAny>> {
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
    let modules: &PyDict = sys.getattr("modules")?.downcast()?;
    modules.set_item(module_name, &module)?;
    loader.call_method1("exec_module", (&module,))?;
    Ok(module)
}

/// Compute a stable module and package name for the test file.
fn infer_module_names(path: &Path, fallback_id: usize) -> (String, Option<String>) {
    let stem = path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or_else(|| "rustest_module");

    let mut components = vec![stem.to_string()];
    let mut package_components = Vec::new();
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
    package_components = components[..components.len() - 1].to_vec();
    let module_name = components.join(".");
    let package_name = if package_components.is_empty() {
        None
    } else {
        Some(package_components.join("."))
    };

    (module_name, package_name)
}

/// Evaluate whether the provided Python object is truthy.
fn is_true(py: Python<'_>, value: &PyAny) -> PyResult<bool> {
    Ok(value.is_true()?)
}
