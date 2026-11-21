//! The rustest engine core.
//!
//! The organising rule of the engine is that **collection output is data**: the
//! collector produces a serializable [`manifest::CollectionManifest`] rather than live
//! Python objects, and callables are resolved inside workers at execution time.  That
//! single rule is what makes the manifest cache, spawn-based process workers, and the
//! static (AST) collection tier possible.

pub mod cache;
pub mod collect;
pub mod config;
pub mod execute;
pub mod manifest;
pub mod manifest_cache;
pub mod nodeid;
pub mod protocol;
pub mod py;
pub mod selection;
pub mod static_collect;

use std::path::Path;

/// A path's file stem as an owned string, empty when it has none.
pub(crate) fn file_stem_lossy(path: &Path) -> String {
    path.file_stem()
        .map(|stem| stem.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// Render a path with posix separators — the path convention.
///
/// Every path the engine emits (manifest `rootdir`, node ids, protocol payloads, the debug
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
