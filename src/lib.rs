//! Top-level crate entry point for the `rustest` Python extension.
//!
//! The whole engine lives under [`engine`]: config resolution, the file walk, the static (AST)
//! collection tier, the manifest cache, and the spawn-based worker pool that collects and
//! then executes.  This file is only the PyO3 boundary — four functions, each documented on
//! the Rust side in `src/engine/py.rs` and typed on the Python side in
//! `python/rustest/rust.pyi`.

#![allow(clippy::useless_conversion)]

pub mod engine;

use pyo3::prelude::*;
use pyo3::wrap_pyfunction;

/// Entry point for the Python extension module.
///
/// `gil_used = false`: on a free-threaded build, importing a module that does not declare
/// this turns the GIL back on for the whole process. The module holds no Python objects
/// between calls and no state behind the GIL (its statics are a `OnceLock` and an atomic).
#[pymodule(gil_used = false)]
fn rust(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    // `resolve_config` is internal (exercised by the pytest-oracle differential tests);
    // `collect` backs `rustest --collect-only` and `run` backs a flagless `rustest`.
    m.add_function(wrap_pyfunction!(engine::py::py_resolve_config, m)?)?;
    m.add_function(wrap_pyfunction!(engine::py::py_collect, m)?)?;
    m.add_function(wrap_pyfunction!(engine::py::py_run, m)?)?;
    // Internal too: the Tier S import allowlist, exported so a Python test can prove every
    // name on it really is importable standard library on the interpreter in use.
    m.add_function(wrap_pyfunction!(engine::py::py_static_stdlib_allowlist, m)?)?;

    Ok(())
}
