//! The rustest **v2** core.
//!
//! v2 is being built alongside the v1 runner (see
//! `docs/superpowers/specs/2026-07-25-rustest-v2-architecture-design.md`).  Until the
//! conformance corpus reports parity, both trees live in this crate: everything under
//! `src/v2/` is new code, and nothing outside it may be changed by v2 work.
//!
//! The organising rule of the v2 spine is that **collection output is data**: the
//! collector produces a serializable [`manifest::CollectionManifest`] rather than live
//! Python objects, and callables are resolved inside workers at execution time.  That
//! single rule is what makes the manifest cache, spawn-based process workers, and the
//! static (AST) collection tier possible.

pub mod cache;
pub mod collect;
pub mod config;
pub mod execute;
pub mod manifest;
pub mod nodeid;
pub mod protocol;
pub mod py;
pub mod selection;
pub mod static_collect;

use std::path::Path;

/// Render a path with posix separators — the v2 path convention.
///
/// Every path v2 emits (manifest `rootdir`, node ids, protocol payloads, the debug
/// surface's JSON) goes through here, so the wire form is platform-independent.
///
/// Only Windows separators are rewritten: on unix a backslash is a legal filename byte
/// and rewriting it would corrupt the path.
#[cfg(windows)]
pub(crate) fn to_posix(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

#[cfg(not(windows))]
pub(crate) fn to_posix(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

/// The interpreter the **real** worker runs under in Rust tests.
///
/// `RUSTEST_TEST_PYTHON` wins when set (CI, or a dev pointing at another interpreter).
/// Otherwise the repo's own `.venv` is used, because that is the one environment
/// guaranteed to have `rustest` importable — `python` on `PATH` usually is not, and a bare
/// fallback would fail with a confusing `No module named rustest`.  `python` remains the
/// last resort so the suite still runs from an activated venv.
#[cfg(test)]
pub(crate) fn test_python() -> String {
    if let Ok(python) = std::env::var("RUSTEST_TEST_PYTHON") {
        return python;
    }
    let venv = Path::new(env!("CARGO_MANIFEST_DIR")).join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });
    if venv.is_file() {
        return venv.to_string_lossy().into_owned();
    }
    "python".to_string()
}
