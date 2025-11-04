//! Helper utilities for bridging between Rust and the embedded Python runtime.
//!
//! The helpers in this module intentionally stay small and well commented so
//! that readers who are new to Rust can focus on the semantics instead of the
//! syntax.  They encapsulate the repetitive glue code that comes with
//! orchestrating Python objects from Rust.

use std::path::{Path, PathBuf};

use pyo3::prelude::*;

/// Simple wrapper holding the user supplied paths.
///
/// Paths are normalised lazily; discovery operates on the canonicalised
/// [`PathBuf`] values to keep IO fallible in a controlled place.
#[derive(Debug, Clone)]
pub struct PyPaths {
    raw: Vec<String>,
}

impl PyPaths {
    /// Construct from a list of raw string paths coming from Python.
    pub fn from_vec(raw: Vec<String>) -> Self {
        Self { raw }
    }

    /// Convert the raw strings into canonicalised [`PathBuf`] values.
    pub fn materialise(&self) -> PyResult<Vec<PathBuf>> {
        self.raw
            .iter()
            .map(|value| {
                let path = Path::new(value);
                if path.exists() {
                    Ok(path.canonicalize()?)
                } else {
                    Err(pyo3::exceptions::PyFileNotFoundError::new_err(format!(
                        "Path '{}' does not exist",
                        value
                    )))
                }
            })
            .collect()
    }
}
