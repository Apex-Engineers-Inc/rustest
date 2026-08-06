//! Top-level crate entry point for the `rustest` Python extension.
//!
//! The whole engine lives under [`v2`]: config resolution, the file walk, the static (AST)
//! collection tier, the manifest cache, and the spawn-based worker pool that collects and
//! then executes.  This file is only the PyO3 boundary — three functions and an allowlist
//! accessor, each documented on the Rust side in `src/v2/py.rs` and typed on the Python side
//! in `python/rustest/rust.pyi`.
//!
//! **The v1 engine was deleted in Phase 4 Task 2.**  It was a second, independent
//! implementation — `discovery.rs`, `execution.rs`, `model.rs`, `output/`, and their
//! supporting modules, ~7 500 lines — reached through a `--v1` flag after the Phase 1c flip
//! made v2 the default.  It is gone rather than deprecated because a frozen second engine is
//! not free: it kept `pyo3` classes, an event-callback protocol and six external crates alive
//! in the build, it owned half of `cargo test` (whose dead tests were #133), and every
//! compat fix had to be reasoned about twice.  Its conformance ledger — 24 waivers, each one
//! a v1 bug with a fixed-in-v2 citation — is archived in git history, under the
//! `docs/superpowers/history/` tree removed before 1.0.0.

#![allow(clippy::useless_conversion)]

pub mod v2;

use pyo3::prelude::*;
use pyo3::wrap_pyfunction;

/// Entry point for the Python extension module.
#[pymodule]
fn rust(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    // `v2_resolve_config` is internal (exercised by the pytest-oracle differential tests);
    // `v2_collect` backs `rustest --v2-collect-only` and `v2_run` backs a flagless `rustest`.
    m.add_function(wrap_pyfunction!(v2::py::v2_resolve_config, m)?)?;
    m.add_function(wrap_pyfunction!(v2::py::v2_collect, m)?)?;
    m.add_function(wrap_pyfunction!(v2::py::v2_run, m)?)?;
    // Internal too: the Tier S import allowlist, exported so a Python test can prove every
    // name on it really is importable standard library on the interpreter in use.
    m.add_function(wrap_pyfunction!(v2::py::v2_static_stdlib_allowlist, m)?)?;

    Ok(())
}
