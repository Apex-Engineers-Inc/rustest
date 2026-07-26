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

pub mod config;
pub mod manifest;
pub mod nodeid;
pub mod protocol;
pub mod py;
