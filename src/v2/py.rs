//! The v2 boundary: PyO3 entry points that expose the v2 core to Python.
//!
//! Two functions live here, and they differ in kind:
//!
//! * [`v2_resolve_config`] is a pure **debug surface**. It exists so the config subsystem
//!   can be diffed against **real pytest** from Python — see
//!   `python/tests/test_v2_config_oracle.py`, which builds `tmp_path` layouts, runs pytest
//!   in a subprocess, and compares its reported `rootdir`/`configfile` with what
//!   [`super::config::resolve_config`] produces for the same layout.
//! * [`v2_collect`] is the first **user-reachable** v2 surface, behind
//!   `rustest --v2-collect-only` (`python/rustest/cli.py` → `core.v2_collect_only`).
//!
//! Both return JSON strings rather than Python objects, for the same reason the manifest
//! itself is data: the boundary stays a single serialized value, so nothing about v2's
//! internal types leaks into the Python layer and the wire form is testable from both
//! sides. The JSON emitted by [`v2_resolve_config`] is a *contract with those tests*:
//! field names, ordering and the absolute-posix rendering of paths are pinned by
//! [`tests::json_is_the_frozen_field_list_in_order`]. [`v2_collect`]'s JSON is
//! [`super::manifest::CollectionManifest`], whose golden form is pinned in that module.
//!
//! **Exception kinds are load-bearing**, because the CLI turns them into pytest's exit
//! codes: a `ValueError` is a *usage* error (exit 4 — a bad path argument or an unusable
//! config file, pytest's `UsageError`), a `RuntimeError` is an orchestration failure
//! (exit 3). A file that merely fails to import is neither: it travels in the manifest's
//! `errors` list and becomes exit 2.

use std::path::{Path, PathBuf};

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use serde::Serialize;

use super::cache::LastFailedMode;
use super::collect::{collect, CollectError, CollectOptions, TierMode};
use super::config::{normpath, resolve_config, ResolvedConfig};
use super::execute::{run, RunError, RunOptions};
use super::static_collect::CacheMode;
use super::to_posix;

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

/// Serialize a [`ResolvedConfig`] to the JSON the debug surface returns.
fn resolved_to_json(config: &ResolvedConfig) -> String {
    serde_json::to_string(&ResolvedConfigJson::from(config))
        .expect("ResolvedConfigJson contains only strings and string lists")
}

/// Validate and normalize an `invocation_dir` argument.
///
/// `invocation_dir` stands in for `Config.invocation_params.dir`, so the three properties
/// pytest guarantees for that value are enforced here rather than assumed:
///
/// * **absolute** — `os.getcwd()` always is, and it is what makes the returned `rootdir`
///   absolute without a process-CWD dependency;
/// * **normalized** — `os.getcwd()` never contains `.` / `..` components, so the input is run
///   through [`super::config::normpath`]; without it a caller passing `C:/a/b/../b` gets that
///   spelling echoed back in `rootdir` (`b/../b/pytest.ini` really does open), and no
///   comparison against pytest's rootdir would ever match;
/// * **existing** — an absolute path that is not a directory would otherwise resolve
///   happily and hand back plausible-looking defaults for a layout that is not there.
fn validated_invocation_dir(invocation_dir: &str) -> PyResult<PathBuf> {
    let raw = Path::new(invocation_dir);
    if !raw.is_absolute() {
        return Err(PyValueError::new_err(format!(
            "invocation_dir must be an absolute path, got {invocation_dir:?}"
        )));
    }
    let dir = normpath(raw);
    if !dir.exists() {
        return Err(PyValueError::new_err(format!(
            "invocation_dir does not exist: {invocation_dir:?}"
        )));
    }
    if !dir.is_dir() {
        return Err(PyValueError::new_err(format!(
            "invocation_dir is not a directory: {invocation_dir:?}"
        )));
    }
    Ok(dir)
}

/// Resolve pytest's rootdir + ini values for `invocation_dir` and CLI `args`, as JSON.
///
/// `args` are the raw CLI arguments (option-looking entries and `::` nodeid suffixes are
/// handled exactly as `_pytest/config/findpaths.py::get_dirs_from_args` handles them).
///
/// Raises `ValueError` for an `invocation_dir` that is relative, missing or not a directory,
/// and for every [`super::config::ConfigError`] (pytest raises `UsageError` for the latter).
#[pyfunction]
pub fn v2_resolve_config(invocation_dir: &str, args: Vec<String>) -> PyResult<String> {
    let dir = validated_invocation_dir(invocation_dir)?;
    let args: Vec<PathBuf> = args.into_iter().map(PathBuf::from).collect();
    let config =
        resolve_config(&dir, &args).map_err(|err| PyValueError::new_err(err.to_string()))?;
    Ok(resolved_to_json(&config))
}

/// The standard-library roots Tier S treats as import-safe
/// ([`super::static_collect::stdlib_allowlist`]).
///
/// A **debug surface**, exactly like [`v2_resolve_config`]: it exists so the list can be
/// checked against a real interpreter instead of trusted. Tier S's whole import rule rests on
/// "these names cannot raise", and the only way to know that on 3.12, 3.13 and 3.14 is to
/// import them there — which is what
/// `python/tests/test_v2_static_tier.py::test_the_stdlib_allowlist_is_importable_and_actually_stdlib`
/// does with this list on every interpreter CI runs. Duplicating the names on the Python side
/// would let the two copies drift, and a drifted copy would certify the wrong list.
#[pyfunction]
pub fn v2_static_stdlib_allowlist() -> Vec<String> {
    super::static_collect::stdlib_allowlist()
        .iter()
        .map(|name| (*name).to_string())
        .collect()
}

/// Turn a [`CollectError`] into the Python exception whose *kind* carries the exit code.
///
/// The split is pytest's, not ours: a `UsageError` — a path argument that does not exist,
/// or a config file pytest itself would refuse — exits 4, while anything that goes wrong
/// inside the machinery is an internal error (exit 3). Collapsing both into one exception
/// type would make the CLI report a broken worker pool as the user's typo.
///
/// The match is **exhaustive on purpose** — no `_` arm. A new [`CollectError`] variant must
/// be classified as usage-or-internal here, at compile time; a wildcard would silently
/// default it to exit 3 and the miscategorisation would only ever show up as a confusing
/// exit code in the field.
fn collect_error_to_py(err: CollectError) -> PyErr {
    let message = err.to_string();
    match err {
        // A malformed `-k`/`-m` expression joins the usage errors: pytest's
        // `_parse_expression` raises `UsageError` and `wrap_session` maps that to exit 4.
        CollectError::Config(_)
        | CollectError::ArgNotFound(_)
        | CollectError::NoCollectors(_)
        | CollectError::Selection(_) => PyValueError::new_err(message),
        // Reachable only from the **collection** phase.  A `pytest.exit()` in a *test body*
        // is intercepted by `execute::worker_life`, which keeps the results already produced
        // and exits 2; one at *import* time gets here instead, where there are no results to
        // keep and the interim fix has no channel to say "the user ended the session" —
        // so it is loud at exit 3 where pytest exits 2.  Residual divergence, recorded in the
        // `marks/pytest-exit` waiver; closing it needs the session-stop signal on the wire.
        CollectError::SessionExit { .. }
        | CollectError::Spawn { .. }
        | CollectError::Io { .. }
        | CollectError::Handshake { .. }
        | CollectError::Protocol { .. }
        | CollectError::ExecuteProtocol { .. }
        | CollectError::WorkerDied { .. }
        | CollectError::ExecuteWorkerDied { .. }
        | CollectError::Shutdown { .. }
        | CollectError::WorkerPanicked { .. }
        | CollectError::MissingResponse { .. }
        | CollectError::MissingResult { .. } => PyRuntimeError::new_err(message),
    }
}

/// Turn a [`RunError`] into the Python exception whose *kind* carries the exit code.
///
/// A malformed `-k`/`-m` expression is pytest's `UsageError` — `_pytest/mark/__init__.py::
/// _parse_expression` raises it and `wrap_session` maps it to `ExitCode.USAGE_ERROR` (4) —
/// so it joins the other usage errors as a `ValueError`. Everything else keeps collection's
/// classification.
fn run_error_to_py(err: RunError) -> PyErr {
    match err {
        RunError::Collect(err) => collect_error_to_py(err),
        RunError::Selection(err) => PyValueError::new_err(err.to_string()),
    }
}

/// Collect `args` (or `testpaths`, or `invocation_dir`) and return the manifest as JSON.
///
/// This is the whole of v2 collection seen from Python. The return value is a
/// [`super::manifest::CollectionManifest`] encoded with `serde_json`; the caller reads
/// `tests[*].id` for node ids in manifest (== walk) order and `errors[*]` for files that
/// could not be imported.
///
/// `python_executable` is the interpreter the collection workers run under. It is resolved
/// on the Python side (`sys.executable`) and passed through, so this crate never guesses an
/// interpreter — a guess would silently collect against the wrong environment. `workers` is
/// the pool size; [`collect`] clamps it to `[1, files]`, so `0` is harmless and an
/// over-large value never spawns idle interpreters.
///
/// The GIL is released for the duration: collection blocks on subprocess pipes for as long
/// as the slowest file takes to import, and no Python object is touched while it does.
///
/// Raises `ValueError` for a bad `invocation_dir`, a missing path argument or an unusable
/// config file (pytest's `UsageError` shape), and `RuntimeError` for an orchestration
/// failure. An unimportable *test file* raises nothing: it is data in `errors`.
///
/// Selection (`keyword`/`mark_expr`) is applied **inside** [`collect`], not here. pytest
/// applies it in `pytest_collection_modifyitems`, i.e. inside collection, and v2 needs it
/// there for a second reason: `-k` is evaluated against Tier S's (and the cache's) answers
/// *before* the worker pool is sized, so a fully static tree whose every test is deselected
/// starts no interpreter at all. What has not changed is the rule that keeps `errors`
/// complete — a Tier D file is collected and reported however aggressively the expression
/// deselects, because `-k` must never hide a file that failed to import.
///
/// `collect_tier` is the **differential's control**, not a user feature: `"d"` forbids the
/// static tier and sends every file to a worker, so a caller can collect the same tree twice
/// and diff the two manifests against each other and against pytest. Anything else means the
/// default (static where possible). The CLI reads it from `RUSTEST_V2_COLLECT_TIER` and does
/// not advertise it; see [`super::collect::TierMode`].
///
/// `cache_mode` is its twin for the manifest cache: `"off"` parses every file and writes
/// nothing, which is how a user (or a test) asks "is this answer stale?". Read from
/// `RUSTEST_V2_MANIFEST_CACHE`; see [`CacheMode`].
#[pyfunction]
#[pyo3(signature = (invocation_dir, args, python_executable, workers, keyword=None, mark_expr=None, codeblocks=true, collect_tier="auto", cache_mode="auto"))]
#[allow(clippy::too_many_arguments)]
pub fn v2_collect(
    py: Python<'_>,
    invocation_dir: &str,
    args: Vec<String>,
    python_executable: &str,
    workers: usize,
    keyword: Option<String>,
    mark_expr: Option<String>,
    codeblocks: bool,
    collect_tier: &str,
    cache_mode: &str,
) -> PyResult<String> {
    let dir = validated_invocation_dir(invocation_dir)?;
    let args: Vec<PathBuf> = args.into_iter().map(PathBuf::from).collect();
    let options = CollectOptions {
        codeblocks,
        tier: TierMode::from_wire(collect_tier),
        cache: CacheMode::from_wire(cache_mode),
        keyword,
        mark: mark_expr,
    };
    let manifest = py
        .detach(|| collect(&dir, &args, python_executable, workers, &options))
        .map_err(collect_error_to_py)?;

    Ok(serde_json::to_string(&manifest)
        .expect("CollectionManifest is plain data and always serializes"))
}

/// Run `args` with the v2 engine and return the [`super::execute::RunReport`] as JSON.
///
/// This is the whole of `rustest --v2` seen from Python: config resolution, the file walk,
/// a worker pool that collects and then *stays alive* to execute, `-k`/`-m` selection
/// between the two, and pytest's exit code in the report's `exit_code` field.
///
/// The GIL is released for the duration — the run blocks on subprocess pipes for as long as
/// the slowest test takes, and no Python object is touched while it does.
///
/// Raises `ValueError` for a usage error (a bad `invocation_dir`, a missing path argument,
/// an unusable config file, or a malformed `-k`/`-m` expression) and `RuntimeError` for an
/// orchestration failure. A test file that fails to import raises nothing: it is data in
/// `collection_errors`, and the report's `exit_code` is 2.
/// The wire spelling of `--lf`/`--ff` on this boundary: `"none"`, `"only"`, `"first"`.
///
/// A string rather than two booleans because the two flags are mutually exclusive and a
/// `(lf, ff)` pair has a fourth state that means nothing.  The spellings are v1's
/// (`core.run`'s `last_failed_mode`), so the two engines' CLIs describe the same option the
/// same way even though the caches are separate.
fn parse_last_failed(mode: &str) -> PyResult<LastFailedMode> {
    match mode {
        "none" => Ok(LastFailedMode::None),
        "only" => Ok(LastFailedMode::Only),
        "first" => Ok(LastFailedMode::First),
        other => Err(PyValueError::new_err(format!(
            "last_failed_mode must be one of 'none', 'only', 'first', got {other:?}"
        ))),
    }
}

#[pyfunction]
#[pyo3(signature = (
    invocation_dir,
    args,
    python_executable,
    workers,
    keyword=None,
    mark_expr=None,
    fail_fast=false,
    last_failed_mode="none",
    no_capture=false,
    codeblocks=true,
))]
#[allow(clippy::too_many_arguments)]
pub fn v2_run(
    py: Python<'_>,
    invocation_dir: &str,
    args: Vec<String>,
    python_executable: &str,
    workers: usize,
    keyword: Option<String>,
    mark_expr: Option<String>,
    fail_fast: bool,
    last_failed_mode: &str,
    no_capture: bool,
    codeblocks: bool,
) -> PyResult<String> {
    let dir = validated_invocation_dir(invocation_dir)?;
    let args: Vec<PathBuf> = args.into_iter().map(PathBuf::from).collect();
    let options = RunOptions {
        fail_fast,
        last_failed: parse_last_failed(last_failed_mode)?,
        no_capture,
        codeblocks,
    };
    let report = py
        .detach(|| {
            run(
                &dir,
                &args,
                python_executable,
                workers,
                keyword.as_deref(),
                mark_expr.as_deref(),
                options,
            )
        })
        .map_err(run_error_to_py)?;
    Ok(serde_json::to_string(&report).expect("RunReport is plain data and always serializes"))
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
    fn invocation_dir_is_normalized_before_resolution() {
        // `os.getcwd()` never has `.`/`..` components, so pytest's rootdir never does either.
        // Without normalizing at the boundary the unnormalized spelling survives into rootdir,
        // because `<dir>/tests/../tests/pytest.ini` really does open on both platforms.
        let tmp = tempfile::TempDir::new().unwrap();
        let tests = tmp.path().join("tests");
        std::fs::create_dir_all(&tests).unwrap();
        std::fs::write(tests.join("pytest.ini"), "[pytest]\n").unwrap();

        let detour = tests.join("..").join("tests");
        let json = v2_resolve_config(&detour.to_string_lossy(), Vec::new()).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(value["rootdir"].as_str().unwrap(), to_posix(&tests));
        assert_eq!(
            value["config_file"].as_str().unwrap(),
            to_posix(&tests.join("pytest.ini"))
        );
    }

    #[test]
    fn nonexistent_invocation_dir_is_rejected() {
        // Otherwise a typo'd path resolves happily and hands back plausible defaults.
        let tmp = tempfile::TempDir::new().unwrap();
        let missing = tmp.path().join("no-such-dir");

        let err = v2_resolve_config(&missing.to_string_lossy(), Vec::new()).unwrap_err();
        Python::attach(|py| {
            assert!(err.is_instance_of::<PyValueError>(py));
            assert!(
                err.value(py).to_string().contains("does not exist"),
                "unexpected message: {err}"
            );
        });
    }

    #[test]
    fn file_invocation_dir_is_rejected() {
        let tmp = tempfile::TempDir::new().unwrap();
        let file = tmp.path().join("pytest.ini");
        std::fs::write(&file, "[pytest]\n").unwrap();

        let err = v2_resolve_config(&file.to_string_lossy(), Vec::new()).unwrap_err();
        Python::attach(|py| {
            assert!(err.is_instance_of::<PyValueError>(py));
            assert!(
                err.value(py).to_string().contains("not a directory"),
                "unexpected message: {err}"
            );
        });
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

    // ----------------------------------------------------------------------
    // v2_collect
    // ----------------------------------------------------------------------
    //
    // The orchestrator's own behaviour (walk order, routing, pool lifecycle) is covered
    // exhaustively in `super::collect`; what is tested here is the *boundary* — argument
    // validation, the exception kind each failure surfaces as (the CLI turns kind into
    // exit code), and that the manifest survives the round trip to JSON intact.

    /// Write a file, creating parents.
    fn write_file(path: &Path, content: &str) {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(path, content).unwrap();
    }

    /// A tree with its own `pytest.ini`, so rootdir is the temp dir rather than whatever
    /// happens to live above the system temp directory.
    fn tree(files: &[(&str, &str)]) -> tempfile::TempDir {
        let tmp = tempfile::TempDir::new().unwrap();
        write_file(&tmp.path().join("pytest.ini"), "[pytest]\n");
        for (rel, content) in files {
            write_file(&tmp.path().join(rel), content);
        }
        tmp
    }

    fn collect_json(dir: &Path, args: Vec<String>) -> serde_json::Value {
        let json = Python::attach(|py| {
            v2_collect(
                py,
                &dir.to_string_lossy(),
                args,
                &crate::v2::test_python(),
                2,
                None,
                None,
                true,
                "auto",
                "off",
            )
        })
        .unwrap();
        serde_json::from_str(&json).unwrap()
    }

    /// End to end through the real worker: the JSON the CLI parses is the manifest, and
    /// node ids arrive in walk order.
    #[test]
    fn collect_returns_the_manifest_as_json() {
        let tmp = tree(&[
            ("test_a.py", "def test_one():\n    pass\n"),
            ("sub/test_b.py", "def test_two():\n    pass\n"),
        ]);

        let value = collect_json(tmp.path(), Vec::new());

        assert_eq!(value["schema_version"].as_u64().unwrap(), 2);
        assert_eq!(value["rootdir"].as_str().unwrap(), to_posix(tmp.path()));
        let ids: Vec<&str> = value["tests"]
            .as_array()
            .unwrap()
            .iter()
            .map(|test| test["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids, vec!["sub/test_b.py::test_two", "test_a.py::test_one"]);
    }

    /// A file that cannot be imported is **data**, not an exception: it must reach Python
    /// as an `errors` entry so the CLI can print it and exit 2, rather than aborting the
    /// whole run.
    #[test]
    fn an_unimportable_file_is_an_error_entry_not_an_exception() {
        let tmp = tree(&[
            ("test_bad.py", "def test_x(:\n    pass\n"),
            ("test_ok.py", "def test_ok():\n    pass\n"),
        ]);

        let value = collect_json(tmp.path(), Vec::new());

        let ids: Vec<&str> = value["tests"]
            .as_array()
            .unwrap()
            .iter()
            .map(|test| test["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids, vec!["test_ok.py::test_ok"]);
        let errors = value["errors"].as_array().unwrap();
        assert_eq!(errors.len(), 1, "{errors:?}");
        assert_eq!(errors[0]["path"].as_str().unwrap(), "test_bad.py");
        assert!(
            errors[0]["message"]
                .as_str()
                .unwrap()
                .contains("SyntaxError"),
            "unexpected message: {:?}",
            errors[0]["message"]
        );
    }

    /// Nothing to collect is a *successful* empty manifest — the CLI, not this function,
    /// turns that into exit 5.
    #[test]
    fn an_empty_tree_collects_successfully() {
        let tmp = tree(&[]);

        let value = collect_json(tmp.path(), Vec::new());

        assert!(value["tests"].as_array().unwrap().is_empty());
        assert!(value.get("errors").is_none(), "{value}");
    }

    /// A missing path argument is pytest's `UsageError`, and the CLI maps `ValueError` to
    /// pytest's exit 4 — so the *kind* here is the contract, not just the message.
    #[test]
    fn a_missing_path_argument_is_a_value_error() {
        let tmp = tree(&[("test_a.py", "def test_one():\n    pass\n")]);

        let err = Python::attach(|py| {
            v2_collect(
                py,
                &tmp.path().to_string_lossy(),
                vec!["nope".to_string()],
                &crate::v2::test_python(),
                1,
                None,
                None,
                true,
                "auto",
                "off",
            )
        })
        .unwrap_err();

        Python::attach(|py| {
            assert!(err.is_instance_of::<PyValueError>(py));
            assert!(
                err.value(py)
                    .to_string()
                    .contains("file or directory not found: nope"),
                "unexpected message: {err}"
            );
        });
    }

    /// An interpreter that cannot be started is an orchestration failure, not a usage
    /// error: `RuntimeError`, which the CLI maps to pytest's internal-error exit 3.
    ///
    /// Pinned at `collect_tier = "d"` **because the question needs a worker to exist**.  Under
    /// the default tier this tree is answered statically and no interpreter is ever spawned —
    /// which is the feature, and which would turn this into a test that passes by never
    /// reaching the code it is about.
    #[test]
    fn an_unspawnable_interpreter_is_a_runtime_error() {
        let tmp = tree(&[("test_a.py", "def test_one():\n    pass\n")]);

        let err = Python::attach(|py| {
            v2_collect(
                py,
                &tmp.path().to_string_lossy(),
                Vec::new(),
                "definitely-not-an-interpreter",
                1,
                None,
                None,
                true,
                "d",
                "off",
            )
        })
        .unwrap_err();

        Python::attach(|py| {
            assert!(
                err.is_instance_of::<PyRuntimeError>(py),
                "expected RuntimeError, got {err}"
            );
            assert!(
                err.value(py).to_string().contains("could not spawn"),
                "unexpected message: {err}"
            );
        });
    }

    /// The `invocation_dir` guards are shared with [`v2_resolve_config`]; this pins that
    /// `v2_collect` really goes through them rather than trusting its caller.
    #[test]
    fn collect_rejects_a_relative_invocation_dir() {
        let err = Python::attach(|py| {
            v2_collect(
                py,
                "relative/dir",
                Vec::new(),
                &crate::v2::test_python(),
                1,
                None,
                None,
                true,
                "auto",
                "off",
            )
        })
        .unwrap_err();

        Python::attach(|py| {
            assert!(err.is_instance_of::<PyValueError>(py));
            assert!(
                err.value(py).to_string().contains("absolute"),
                "unexpected message: {err}"
            );
        });
    }

    /// `workers = 0` would be a zero-sized pool; [`collect`] clamps it, and this pins that
    /// the boundary does not add a guard of its own that would reject it instead.
    #[test]
    fn zero_workers_is_clamped_rather_than_rejected() {
        let tmp = tree(&[("test_a.py", "def test_one():\n    pass\n")]);

        let json = Python::attach(|py| {
            v2_collect(
                py,
                &tmp.path().to_string_lossy(),
                Vec::new(),
                &crate::v2::test_python(),
                0,
                None,
                None,
                true,
                "auto",
                "off",
            )
        })
        .unwrap();

        let value: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(
            value["tests"][0]["id"].as_str().unwrap(),
            "test_a.py::test_one"
        );
    }
}
