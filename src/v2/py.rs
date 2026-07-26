//! The v2 debug surface: PyO3 entry points that expose v2 internals to Python.
//!
//! Nothing here is part of the user-facing API. These functions exist so the v2 subsystems
//! can be diffed against **real pytest** from Python — see
//! `python/tests/test_v2_config_oracle.py`, which builds `tmp_path` layouts, runs pytest in
//! a subprocess, and compares its reported `rootdir`/`configfile` with what
//! [`super::config::resolve_config`] produces for the same layout.
//!
//! The JSON emitted by [`v2_resolve_config`] is a *contract with those tests*: field names,
//! ordering and the absolute-posix rendering of paths are pinned by
//! [`tests::json_is_the_frozen_field_list_in_order`].

use std::path::{Path, PathBuf};

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde::Serialize;

use super::config::{resolve_config, ResolvedConfig};

/// Wire form of [`ResolvedConfig`].
///
/// Paths become absolute posix strings (`config_file` is `null` when pytest would report no
/// config file at all); every other field is the ini value verbatim. Field order here is the
/// field order in the JSON, since `serde_json` preserves struct declaration order.
#[derive(Serialize)]
struct ResolvedConfigJson<'a> {
    rootdir: String,
    config_file: Option<String>,
    testpaths: &'a [String],
    python_files: &'a [String],
    python_classes: &'a [String],
    python_functions: &'a [String],
    norecursedirs: &'a [String],
    addopts: &'a [String],
    markers: &'a [String],
}

impl<'a> From<&'a ResolvedConfig> for ResolvedConfigJson<'a> {
    fn from(config: &'a ResolvedConfig) -> Self {
        Self {
            rootdir: to_posix(&config.rootdir),
            config_file: config.config_file.as_deref().map(to_posix),
            testpaths: &config.testpaths,
            python_files: &config.python_files,
            python_classes: &config.python_classes,
            python_functions: &config.python_functions,
            norecursedirs: &config.norecursedirs,
            addopts: &config.addopts,
            markers: &config.markers,
        }
    }
}

/// Render a path with posix separators, matching the manifest's path convention.
///
/// Only Windows separators are rewritten: on unix a backslash is a legal filename byte and
/// rewriting it would corrupt the path.
#[cfg(windows)]
fn to_posix(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

#[cfg(not(windows))]
fn to_posix(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

/// Serialize a [`ResolvedConfig`] to the JSON the debug surface returns.
fn resolved_to_json(config: &ResolvedConfig) -> String {
    serde_json::to_string(&ResolvedConfigJson::from(config))
        .expect("ResolvedConfigJson contains only strings and string lists")
}

/// Resolve pytest's rootdir + ini values for `invocation_dir` and CLI `args`, as JSON.
///
/// `invocation_dir` stands in for `Config.invocation_params.dir` and **must be absolute** —
/// that is what pytest always passes, and it is what makes the returned `rootdir` absolute.
/// `args` are the raw CLI arguments (option-looking entries and `::` nodeid suffixes are
/// handled exactly as `_pytest/config/findpaths.py::get_dirs_from_args` handles them).
///
/// Raises `ValueError` for a relative `invocation_dir` and for every
/// [`super::config::ConfigError`] (pytest raises `UsageError` for the latter).
#[pyfunction]
pub fn v2_resolve_config(invocation_dir: &str, args: Vec<String>) -> PyResult<String> {
    let dir = Path::new(invocation_dir);
    if !dir.is_absolute() {
        return Err(PyValueError::new_err(format!(
            "invocation_dir must be an absolute path, got {invocation_dir:?}"
        )));
    }
    let args: Vec<PathBuf> = args.into_iter().map(PathBuf::from).collect();
    let config =
        resolve_config(dir, &args).map_err(|err| PyValueError::new_err(err.to_string()))?;
    Ok(resolved_to_json(&config))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::v2::config::{
        DEFAULT_NORECURSEDIRS, DEFAULT_PYTHON_CLASSES, DEFAULT_PYTHON_FILES,
        DEFAULT_PYTHON_FUNCTIONS,
    };

    /// An absolute path with a native prefix, so the posix rewrite is actually exercised.
    #[cfg(windows)]
    const ROOT: &str = r"C:\repo";
    #[cfg(not(windows))]
    const ROOT: &str = "/repo";

    #[cfg(windows)]
    const ROOT_POSIX: &str = "C:/repo";
    #[cfg(not(windows))]
    const ROOT_POSIX: &str = "/repo";

    fn owned(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| (*s).to_string()).collect()
    }

    fn sample() -> ResolvedConfig {
        ResolvedConfig {
            rootdir: PathBuf::from(ROOT),
            config_file: Some(Path::new(ROOT).join("pytest.ini")),
            testpaths: owned(&["tests"]),
            python_files: owned(DEFAULT_PYTHON_FILES),
            python_classes: owned(DEFAULT_PYTHON_CLASSES),
            python_functions: owned(DEFAULT_PYTHON_FUNCTIONS),
            norecursedirs: owned(DEFAULT_NORECURSEDIRS),
            addopts: owned(&["-ra"]),
            markers: owned(&["slow: marks tests as slow"]),
        }
    }

    #[test]
    fn json_is_the_frozen_field_list_in_order() {
        // The field list `python/tests/test_v2_config_oracle.py` reads by name, in the
        // order the Task 5 brief froze it.
        let expected = format!(
            concat!(
                r#"{{"rootdir":"{root}","config_file":"{root}/pytest.ini","#,
                r#""testpaths":["tests"],"#,
                r#""python_files":["test_*.py","*_test.py"],"#,
                r#""python_classes":["Test"],"#,
                r#""python_functions":["test"],"#,
                r#""norecursedirs":["*.egg",".*","_darcs","build","CVS","dist","node_modules","venv","{{arch}}"],"#,
                r#""addopts":["-ra"],"#,
                r#""markers":["slow: marks tests as slow"]}}"#
            ),
            root = ROOT_POSIX
        );
        assert_eq!(resolved_to_json(&sample()), expected);
    }

    #[test]
    fn paths_are_rendered_posix_and_absolute() {
        let json = resolved_to_json(&sample());
        let value: serde_json::Value = serde_json::from_str(&json).unwrap();
        let rootdir = value["rootdir"].as_str().unwrap();
        assert_eq!(rootdir, ROOT_POSIX);
        assert!(
            !rootdir.contains('\\'),
            "rootdir kept a native separator: {rootdir}"
        );
        assert!(
            Path::new(rootdir).is_absolute(),
            "rootdir is not absolute: {rootdir}"
        );
        assert_eq!(
            value["config_file"].as_str().unwrap(),
            format!("{ROOT_POSIX}/pytest.ini")
        );
    }

    #[test]
    fn missing_config_file_serializes_as_null() {
        let mut config = sample();
        config.config_file = None;
        let value: serde_json::Value = serde_json::from_str(&resolved_to_json(&config)).unwrap();
        assert!(value["config_file"].is_null());
    }

    #[test]
    fn empty_ini_lists_serialize_as_empty_arrays() {
        // The oracle test asserts `resolved["addopts"] == []`, so empty must stay an array
        // rather than being omitted.
        let mut config = sample();
        config.testpaths.clear();
        config.addopts.clear();
        config.markers.clear();
        let value: serde_json::Value = serde_json::from_str(&resolved_to_json(&config)).unwrap();
        for field in ["testpaths", "addopts", "markers"] {
            assert_eq!(
                value[field].as_array().map(Vec::len),
                Some(0),
                "field {field}"
            );
        }
    }

    #[test]
    fn relative_invocation_dir_is_rejected() {
        let err = v2_resolve_config("relative/dir", Vec::new()).unwrap_err();
        Python::attach(|py| {
            assert!(err.is_instance_of::<PyValueError>(py));
            assert!(
                err.value(py).to_string().contains("absolute"),
                "unexpected message: {err}"
            );
        });
    }

    #[test]
    fn resolves_a_real_layout_end_to_end() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        std::fs::write(
            root.join("pytest.ini"),
            "[pytest]\npython_classes = Check\n",
        )
        .unwrap();
        std::fs::create_dir_all(root.join("tests")).unwrap();

        let json = v2_resolve_config(&root.join("tests").to_string_lossy(), Vec::new()).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(value["rootdir"].as_str().unwrap(), to_posix(root));
        assert_eq!(
            value["config_file"].as_str().unwrap(),
            to_posix(&root.join("pytest.ini"))
        );
        assert_eq!(value["python_classes"][0].as_str().unwrap(), "Check");
    }

    #[test]
    fn config_error_becomes_a_value_error() {
        // `[pytest]` in setup.cfg is a UsageError in pytest; here it must surface as a
        // Python exception rather than a panic.
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        std::fs::write(root.join("setup.cfg"), "[pytest]\npython_classes = Nope\n").unwrap();

        let err = v2_resolve_config(&root.to_string_lossy(), Vec::new()).unwrap_err();
        Python::attach(|py| {
            assert!(err.is_instance_of::<PyValueError>(py));
            assert!(
                err.value(py).to_string().contains("[tool:pytest]"),
                "unexpected message: {err}"
            );
        });
    }
}
