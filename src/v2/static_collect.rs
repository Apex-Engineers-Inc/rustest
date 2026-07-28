//! **Tier S** — collection without an interpreter.
//!
//! This module answers the same question `_v2_worker.py` answers — "what tests are in this
//! file?" — by *parsing* the file with ruff's Python parser instead of importing it.  When it
//! can answer, the orchestrator skips the worker round trip entirely; when it cannot, the file
//! goes to Tier D exactly as before.  Tier D is definitionally correct (it runs the same
//! Python pytest would), so it stays the oracle: everything here is an optimisation that must
//! be *invisible* in the output.
//!
//! # The asymmetry that decides every rule below
//!
//! A **false positive** (calling a static file dynamic) costs a worker round trip.  A **false
//! negative** (answering statically for a file whose real answer differs) is a silently wrong
//! manifest — tests that do not exist, or missing tests, with exit 0.  The two are not
//! comparable, so every rule is written to fail towards D.  Concretely, the detector is a
//! *whitelist*: a file is static only when every module-level statement, every decorator and
//! every parametrize argument is a shape this module recognises exactly.  Anything
//! unrecognised — including shapes that would obviously be fine — routes to D.
//!
//! # What "importing cannot change the answer" has to mean
//!
//! Three classes of hazard motivate rules that are otherwise surprising.
//!
//! **Importing can fail.**  `import numpy` in a test file is a *collection error* in the
//! manifest when numpy is missing (`_v2_worker.py::collect_file` turns any import-time
//! exception into an `errors` entry, and `_pytest/python.py::importtestmodule` does the
//! same).  A static answer would list tests for a file pytest reports as broken.  So the
//! import allowlist is exactly two names — `pytest` and `rustest` — because
//! `_v2_worker.py::install_pytest_shim` puts `pytest` (and `pytest_asyncio`) into
//! `sys.modules` before any test module is imported, and the worker *is* `rustest._v2_worker`,
//! so both are already-imported modules that cannot raise.  Every other import flags.
//!
//! **Importing runs code.**  Any module-level statement that is not a definition, an
//! allowlisted import or a literal binding can raise, mutate, or define tests conditionally.
//! `counter = itertools.count()` is a call; `if sys.version_info >= (3, 13): def test_x()` is
//! a conditional definition.  Both flag.
//!
//! **Fixtures can multiply ids.**  `_v2_worker.py::fixture_param_dimensions` expands one test
//! into one id per parametrized fixture in its closure — and the closure includes autouse
//! fixtures and every conftest in the chain, none of which the test file mentions.  So a file
//! whose conftest chain contains a parametrized fixture *cannot* be answered from the file
//! alone.  Rather than model conftest fixtures, this module requires the whole chain to be
//! statically safe **and** free of any parametrized-fixture call (`parametrized_fixture_call`,
//! the plan's rule expressed against the AST rather than against the file's text).
//!
//! # Byte-exactness is against v1's ids, not pytest's
//!
//! Param ids are **not** recomputed from pytest's `IdMaker`.  The worker consumes ids that
//! `python/rustest/decorators.py` computed at decoration time (`_build_cases` ->
//! `_resolve_case_id` -> `_generate_param_id`), and `_v2_worker.py::_parametrization` copies
//! them verbatim, with `_unique_parameterset_ids` applied on top.  Tier S therefore ports
//! *those* functions, divergences included — porting pytest's instead would produce ids that
//! match neither tier and break every `-k` a user has written.  [`generate_param_id`] and
//! [`unique_parameterset_ids`] name their sources line by line.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use rayon::prelude::*;
use ruff_python_ast::{Expr, Stmt};
use ruff_python_parser::parse_module;

use crate::v2::config::{
    matches_name_pattern, ResolvedConfig, DEFAULT_ASYNCIO_MODE, DEFAULT_ASYNCIO_TEST_LOOP_SCOPE,
};
use crate::v2::manifest::{CollectedTest, MarkSpec, Tier};
use crate::v2::manifest_cache::{digest_of_chain, DirCache, FreshByDir, ManifestCache};
use crate::v2::nodeid::build_nodeid;

// ---------------------------------------------------------------------------
// Dynamism
// ---------------------------------------------------------------------------

/// Why a file could not be answered statically.
///
/// Carried as a typed reason plus a human detail so the tier-attribution tests can assert
/// *which* rule fired rather than only that one did — a detector that flags everything for the
/// wrong reason passes a "did it flag?" test and fails the moment a rule is relaxed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Dynamic {
    pub reason: Reason,
    pub detail: String,
}

impl Dynamic {
    fn new(reason: Reason, detail: impl Into<String>) -> Self {
        Self {
            reason,
            detail: detail.into(),
        }
    }
}

impl std::fmt::Display for Dynamic {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:?}: {}", self.reason, self.detail)
    }
}

/// The closed set of dynamism triggers.
///
/// Exhaustive by construction: [`scan_module`] returns one of these or a test list, so a new
/// hazard has to be given a name here before it can be detected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reason {
    /// The file is not Python source this module parses (markdown, unreadable bytes).
    NotPythonSource,
    /// ruff's parser rejected the file.  Tier D reproduces pytest's `SyntaxError` entry;
    /// inventing one here would have to match its text byte for byte.
    ParseError,
    /// `from x import *` — the module namespace is whatever `x` happens to export.
    StarImport,
    /// An import of anything but `pytest` / `rustest`; it can raise, and a raising import is a
    /// collection error, not an empty file.
    ForeignImport,
    /// An imported name that would itself be collected under the configured patterns.
    ImportedTestName,
    /// `def __getattr__` at module level (PEP 562): attribute access is user code.
    ModuleGetattr,
    /// A PEP 263 encoding cookie naming anything but UTF-8, so this module and the
    /// interpreter would decode the same bytes into different text.
    EncodingCookie,
    /// A module-level statement that runs code: a call — `exec`/`eval`/`compile` included, since
    /// this arm is what makes them unreachable rather than special-cased — `if`/`for`/`while`/
    /// `try`/`with`, `raise`, `assert`, an augmented or attribute assignment.
    ModuleSideEffect,
    /// A `def`/`class` nested in a conditional, loop or `try` at module level.
    ConditionalDef,
    /// A class with any base other than `object` — inherited methods are not statically
    /// resolvable, and a base can be anything at all.
    ClassBases,
    /// A `unittest.TestCase` subclass: `TestLoader` semantics (name sorting, `runTest`
    /// fallback, `setUpClass` fixtures) stay in Tier D.
    UnittestCase,
    /// A decorator that is not one of the recognised mark/fixture/parametrize forms.
    UnknownDecorator,
    /// A `parametrize` whose argnames, values or ids are not literals.
    NonLiteralParametrize,
    /// A mark whose arguments are not literals, or a mark form with a factory this module
    /// does not model.
    NonLiteralMark,
    /// A `pytestmark` that is not a literal mark or list of them.
    NonLiteralPytestmark,
    /// A parametrized fixture in scope: it multiplies ids for tests that never mention it.
    ParametrizedFixture,
    /// A conftest in the chain that is unreadable, unparsable, or not statically safe.
    ConftestChain,
    /// `pytest_plugins` — plugins can register parametrized and autouse fixtures.
    PytestPlugins,
    /// A top-level name bound twice: `vars(module)` keeps the first *position* and the last
    /// *value*, which is not a shape worth modelling.
    DuplicateName,
    /// A dunder-shaped definition; `_pytest/python.py::IGNORED_ATTRIBUTES` decides these by
    /// exact name and a permissive `python_functions` can collect the ones not in it.
    DunderDefinition,
    /// `__test__` at module or class level — the collection veto pytest reads off the object.
    TestAttribute,
    /// A `yield` in a test body: pytest fails the whole module for a sync generator test, and
    /// an async generator test acquires a synthesised `xfail` mark.
    GeneratorTest,
    /// Two collection targets share a file stem, so the second import may be pytest's
    /// `import file mismatch` collection error.
    StemCollision,
}

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

/// Scan every walk target, in parallel, and say which ones Tier S can answer.
///
/// The returned vector is indexed by *target index*, so the orchestrator can keep walk order
/// without a second sort.  `Err` means "send this one to a worker".
///
/// Two whole-run rules live here rather than in [`scan_module`], because a single file cannot
/// see them:
///
/// * **stem collisions.**  Two `test_dup.py` files in different non-package directories import
///   under the same module name, and the second one is pytest's `import file mismatch`
///   collection error (`_v2_worker.py::_import_mismatch_message`).  The orchestrator routes
///   same-stem files to one worker precisely so that error reproduces; a static answer would
///   silently collect both.  Every file whose stem is shared goes to D.
/// * **the conftest chain**, which is per directory and shared by every file in it, so it is
///   analysed once and cached.
pub fn static_pass(
    targets: &[PathBuf],
    rootdir: &Path,
    config: &ResolvedConfig,
) -> Vec<Result<Vec<CollectedTest>, Dynamic>> {
    static_pass_cached(targets, rootdir, config, CacheMode::Off).outcomes
}

/// Whether a run may read and write the Tier S manifest cache.
///
/// [`CacheMode::Off`] is the control leg, in the same sense
/// [`crate::v2::collect::TierMode::DynamicOnly`] is: every cache test needs a way to produce
/// the answer the cache is supposed to reproduce, and every *user* needs a way to prove a
/// surprising result is not a stale entry.  It is also what the uncached [`static_pass`]
/// passes, which is what keeps the several dozen existing Tier S tests writing nothing to
/// disk.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum CacheMode {
    /// Read and write `<rootdir>/.rustest_cache/v2-manifest`.
    #[default]
    Auto,
    /// Parse every file, write nothing.
    Off,
}

impl CacheMode {
    /// Parse the wire spelling used by the `v2_collect` boundary and the
    /// `RUSTEST_V2_MANIFEST_CACHE` escape hatch.  An unknown value means the default, for the
    /// same reason [`crate::v2::collect::TierMode::from_wire`]'s does: a typo in a debug knob
    /// must not turn a user's run into a usage error.
    pub fn from_wire(value: &str) -> Self {
        match value {
            "off" | "no" | "0" => CacheMode::Off,
            _ => CacheMode::Auto,
        }
    }
}

/// What [`static_pass_cached`] produced, plus the cache it used.
pub struct StaticPass {
    /// Indexed by target, in walk order.  `Err` means "send this one to a worker".
    pub outcomes: Vec<Result<Vec<CollectedTest>, Dynamic>>,
    /// The cache, when one was enabled.  Its [`CacheStats`] are the instrument for the claim
    /// that a warm collection **parses nothing**: a miss is counted at exactly the point a
    /// file is handed to the parser, so `misses == 0` is that claim, checked rather than
    /// argued.
    pub cache: Option<ManifestCache>,
}

/// [`static_pass`], with the manifest cache.
///
/// The shape of a run:
///
/// 1. whole-run facts — the shadow set and the stem histogram — which no single file can see;
/// 2. per **directory**, once: the conftest chain (read once, analysed, and digested for the
///    cache key) and that directory's cache shard;
/// 3. per file, in parallel: the two whole-run refusals, then a cache lookup, then — only on
///    a miss — the parse;
/// 4. per directory, once: the shard write-back, which is skipped entirely when nothing
///    changed.
///
/// Steps 1 and 2 are **not** cached and run on every collection. That is deliberate: they are
/// the run's own view of the filesystem, and caching a view of the filesystem is how a cache
/// starts answering questions about a tree that no longer exists.
pub fn static_pass_cached(
    targets: &[PathBuf],
    rootdir: &Path,
    config: &ResolvedConfig,
    mode: CacheMode,
) -> StaticPass {
    let shadows = shadowing_names(targets, rootdir);
    let mut stems: HashMap<String, usize> = HashMap::new();
    for path in targets {
        let stem = path
            .file_stem()
            .map(|stem| stem.to_string_lossy().into_owned())
            .unwrap_or_default();
        *stems.entry(stem).or_insert(0) += 1;
    }

    let cache = match mode {
        CacheMode::Off => None,
        CacheMode::Auto => Some(ManifestCache::open(
            rootdir,
            config,
            &shadowable_names(&shadows),
        )),
    };

    // One entry per *directory*, because the chain is identical for every file in it and
    // analysing it is the expensive half (it reads and parses every conftest above the file).
    let mut dirs: HashMap<PathBuf, DirState> = HashMap::new();
    for path in targets {
        let Some(dir) = path.parent() else { continue };
        if dirs.contains_key(dir) {
            continue;
        }
        let chain = read_conftest_chain(dir, rootdir);
        let verdict = chain_is_static(&chain, &shadows, ChainRule::Collection);
        let shard = cache.as_ref().map(|cache| {
            cache.load_dir(
                &relative_posix(dir, rootdir),
                digest_of_chain(
                    chain
                        .iter()
                        .map(|source| (source.rel.as_str(), source.bytes.as_deref())),
                ),
            )
        });
        let _ = dirs.insert(dir.to_path_buf(), DirState { verdict, shard });
    }

    // Parallel because it is embarrassingly so — one file, one parse, no shared mutable state
    // — and because a fully-static tree spawns no processes at all, so this pass is the entire
    // cost of collection.  `map` on an indexed parallel iterator preserves order, so the
    // result stays indexed by target and walk order survives.
    let scanned: Vec<Scanned> = targets
        .par_iter()
        .map(|path| {
            let stem = path
                .file_stem()
                .map(|stem| stem.to_string_lossy().into_owned())
                .unwrap_or_default();
            if stems.get(&stem).copied().unwrap_or(0) > 1 {
                return (
                    Err(Dynamic::new(
                        Reason::StemCollision,
                        format!("another collection target shares the stem {stem:?}"),
                    )),
                    None,
                );
            }
            let state = path.parent().and_then(|dir| dirs.get(dir));
            if let Some(Err(err)) = state.map(|state| &state.verdict) {
                return (Err(err.clone()), None);
            }
            scan_target(path, rootdir, config, &shadows, cache.as_ref(), state)
        })
        .collect();

    let mut outcomes = Vec::with_capacity(scanned.len());
    let mut fresh_by_dir: FreshByDir = HashMap::new();
    for (outcome, fresh) in scanned {
        if let (Some(fresh), Ok(tests)) = (fresh, &outcome) {
            let _ = fresh_by_dir
                .entry(fresh.dir)
                .or_default()
                .insert(fresh.name, (fresh.key, tests.clone()));
        }
        outcomes.push(outcome);
    }

    if let Some(cache) = &cache {
        for (dir, fresh) in fresh_by_dir {
            if let Some(shard) = dirs.get(&dir).and_then(|state| state.shard.as_ref()) {
                let _ = cache.store_dir(shard, &dir, fresh);
            }
        }
    }

    StaticPass { outcomes, cache }
}

/// Which targets may have their assertions rewritten, and under which cache key.
///
/// Indexed by target, in walk order.  `Some(key)` is a 64-hex Tier S manifest cache key and
/// means "this file is statically analysable — rewrite it and cache the bytecode under this
/// key"; `None` means "leave it alone".  The key travels to the worker on `collect_file`
/// (`src/v2/protocol.rs`), which is the only consumer.
///
/// # Why this exists separately from [`static_pass_cached`]
///
/// A **run** collects through Tier D exclusively — `execute.rs` passes `TierMode::DynamicOnly`
/// so every file is imported by the worker that will execute it, which is what keeps the test
/// objects the ones enumeration saw.  Tier S therefore never *answers* on the run path.
///
/// It used to *also* be the gate: a file was rewritten only if [`scan_module`] accepted it.
/// **That coupling is gone** (Phase 3 Task 2), because it was answering a question rewriting
/// never asks.  Every rule in `scan_module` exists to decide whether a file's **node ids** are
/// predictable without importing it — a module-level call can add tests, a conditional `def`
/// can remove one, an unrecognised decorator can be a `parametrize` this module does not
/// model.  None of them changes what `assert a == b` inside a test body *means*, which is the
/// only thing the transform touches.  Measured on this repository: the coupling silenced the
/// failure messages of 27 of 55 files, 15 of them for `ModuleSideEffect` alone — a
/// `logging.basicConfig()` at module level costing that file pytest-grade assertion output.
///
/// # What is required, and why each one is
///
/// * **the file is readable as UTF-8 text** — [`read_source`].  The bytes are hashed into the
///   cache key and handed to nothing else; a file this module cannot decode is one whose key
///   it cannot compose.
/// * **every `conftest.py` in the chain is readable** — [`ChainRule::Rewrite`], for the same
///   reason one rung up: the key hashes the chain's bytes, so an unreadable conftest is a key
///   a later edit to that conftest could not invalidate.
/// * **the file parses** — ruff's parser, [`rewrite_is_parsable`].  A source the transform
///   cannot build an AST for is one it cannot rewrite, and a parse here is also the cheapest
///   possible check that the bytes are Python at all.
///
/// Two further refusals live in the worker, because they are properties of the *tree* rather
/// than of the file: a `PYTEST_DONT_REWRITE` module docstring and a `:=` inside an `assert`
/// (`python/rustest/_assertion_rewrite.py::_should_rewrite`).  Together the three-plus-two are
/// the whole eligibility model — parse-success, no walrus, no `PYTEST_DONT_REWRITE`.
///
/// The **stem-collision** veto is gone with the rest.  It is a real rule for collection (two
/// `test_dup.py` files import under one module name, and the second is pytest's `import file
/// mismatch` collection error) and it is inert here: registration is keyed by absolute path,
/// the bytecode artefact is named from a digest of that path, and the file that does get
/// imported is rewritten.  Registering a key for a file that is never imported costs nothing;
/// withholding one from the file that *is* costs its messages.
///
/// # Cost
///
/// One read and one ruff parse per file — measured at Task 2 as ~9 ms of reading and ~1 ms of
/// parsing for the 500-file benchmark suite, against a ~2.3 s run.  The read is unavoidable
/// anyway: the cache key hashes the file's bytes.  Dropping `scan_module` makes this *cheaper*
/// than it was: the parse is still one parse, and the structural walk over it is gone.
pub fn rewrite_plan(
    targets: &[PathBuf],
    rootdir: &Path,
    config: &ResolvedConfig,
) -> Vec<Option<String>> {
    let shadows = shadowing_names(targets, rootdir);

    // The cache handle is opened for its **key composition only** — `load_dir` below is
    // never called, so no shard is read and none is written.  Reusing it rather than calling
    // `cache_key` directly is deliberate: the run-global half of the key (build version,
    // config digest, shadow digest) is composed in exactly one place, so the bytecode cache
    // cannot drift from the manifest cache it is keyed against.
    let cache = ManifestCache::open(rootdir, config, &shadowable_names(&shadows));

    let mut chains: HashMap<PathBuf, (Result<(), Dynamic>, crate::v2::manifest_cache::Digest)> =
        HashMap::new();
    for path in targets {
        let Some(dir) = path.parent() else { continue };
        if chains.contains_key(dir) {
            continue;
        }
        let chain = read_conftest_chain(dir, rootdir);
        let verdict = chain_is_static(&chain, &shadows, ChainRule::Rewrite);
        let digest = digest_of_chain(
            chain
                .iter()
                .map(|source| (source.rel.as_str(), source.bytes.as_deref())),
        );
        let _ = chains.insert(dir.to_path_buf(), (verdict, digest));
    }

    targets
        .par_iter()
        .map(|path| {
            let (verdict, chain) = path.parent().and_then(|dir| chains.get(dir))?;
            verdict.as_ref().ok()?;
            let source = read_source(path).ok()?;
            let rel_path = relative_posix(path, rootdir);
            rewrite_is_parsable(&source, &rel_path).ok()?;
            Some(crate::v2::manifest_cache::hex_digest(&cache.key_for_chain(
                *chain,
                &rel_path,
                source.as_bytes(),
            )))
        })
        .collect()
}

/// The file's own half of the rewrite gate: **does it parse?**
///
/// Deliberately not [`scan_module`] — see [`rewrite_plan`] for the whole argument.  The parse
/// result is discarded because the transform re-parses with CPython's own `ast` anyway (from
/// the raw bytes, so a PEP 263 cookie is honoured by the interpreter that will run the code);
/// this is the cheap "is it Python this build understands" gate, and a refusal costs message
/// quality and never correctness.
fn rewrite_is_parsable(source: &str, rel_path: &str) -> Result<(), Dynamic> {
    parse_module(source)
        .map(|_| ())
        .map_err(|err| Dynamic::new(Reason::ParseError, format!("{rel_path}: {err}")))
}

/// What one directory contributes to every file in it.
struct DirState {
    verdict: Result<(), Dynamic>,
    shard: Option<DirCache>,
}

/// One target's outcome, plus the cache entry it produced when it was a miss that answered.
type Scanned = (Result<Vec<CollectedTest>, Dynamic>, Option<Fresh>);

/// A cache entry this run computed and should write back.
struct Fresh {
    dir: PathBuf,
    name: String,
    key: crate::v2::manifest_cache::Digest,
}

/// One file: read, look up, and parse only on a miss.
///
/// The read happens either way — the cache key hashes the file's bytes, so there is no
/// mtime-and-size shortcut here and none is wanted: a content hash cannot be fooled by a
/// checkout that restores timestamps, by a clock that moved, or by two edits inside one
/// filesystem timestamp tick, and those are exactly the situations where a stale manifest is
/// least likely to be suspected.
fn scan_target(
    path: &Path,
    rootdir: &Path,
    config: &ResolvedConfig,
    shadows: &HashSet<String>,
    cache: Option<&ManifestCache>,
    state: Option<&DirState>,
) -> Scanned {
    let source = match read_source(path) {
        Ok(source) => source,
        Err(err) => return (Err(err), None),
    };
    let rel_path = relative_posix(path, rootdir);

    let shard = cache.zip(state.and_then(|state| state.shard.as_ref()));
    let name = path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    let key = shard.map(|(cache, shard)| cache.key(shard, &rel_path, source.as_bytes()));

    if let (Some((cache, shard)), Some(key)) = (shard, key) {
        if let Some(tests) = cache.get(shard, &name, &key) {
            return (Ok(tests), None);
        }
    }
    if let Some(cache) = cache {
        cache.record_miss();
    }

    let outcome = scan_module(&source, &rel_path, config, shadows);
    // Only a Tier S **answer** is written.  A refusal is not cached: it costs one parse to
    // recompute and it implies a worker round trip that dwarfs it, so an entry for it would
    // buy nothing and would be one more thing that can go stale.
    let fresh = match (&outcome, key, path.parent()) {
        (Ok(_), Some(key), Some(dir)) if shard.is_some() => Some(Fresh {
            dir: dir.to_path_buf(),
            name,
            key,
        }),
        _ => None,
    };
    (outcome, fresh)
}

/// The names in `shadows` that can actually change an answer: the ones Tier S's stdlib
/// allowlist would otherwise have trusted.
///
/// `Scan::import_is_safe` consults `shadows` only after `STDLIB_ALLOWLIST.contains(root)`, so
/// every other name in the set is inert.  Narrowing it is not only an optimisation — the full
/// set contains every test file's own stem, so hashing it unfiltered would invalidate a whole
/// tree's cache every time anyone added a test file.
fn shadowable_names(shadows: &HashSet<String>) -> std::collections::BTreeSet<String> {
    shadows
        .iter()
        .filter(|name| STDLIB_ALLOWLIST.contains(&name.as_str()))
        .cloned()
        .collect()
}

/// Every module name a file in this run could shadow, so a stdlib import can be trusted.
///
/// `import json` in a test file is safe *because* it resolves to the standard library — and
/// that is not guaranteed: `_v2_worker.py::sys_path_root_for` puts a directory on `sys.path`
/// for every module it imports, so a `json.py` sitting beside a test file becomes `json` for
/// the whole worker, and importing it runs user code that can raise.
///
/// The set is complete for a run rather than heuristic.  The only directories that ever reach
/// `sys.path` are a target's own directory or its package root's parent
/// (`_v2_worker.py::sys_path_root_for`, called from `import_test_module` for test modules and
/// from `import_conftest` for conftests) — and every one of those is a target's parent or an
/// ancestor of one.  Enumerating exactly that set of directories therefore enumerates every
/// name that can win over the standard library.
///
/// Subdirectories count whether or not they hold an `__init__.py`: a namespace package shadows
/// just as effectively (PEP 420).
fn shadowing_names(targets: &[PathBuf], rootdir: &Path) -> HashSet<String> {
    let mut directories: HashSet<PathBuf> = HashSet::new();
    for path in targets {
        let mut current = path.parent();
        while let Some(dir) = current {
            // An ancestor already recorded means its own ancestors are too.
            if !directories.insert(dir.to_path_buf()) {
                break;
            }
            if dir == rootdir {
                break;
            }
            current = dir.parent();
        }
    }

    let mut names = HashSet::new();
    for dir in directories {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            // `file_type()` comes off the directory enumeration; `is_dir()` is a `stat` per
            // entry, and this loop visits every file in every directory a run touches.  See
            // `collect::is_dir` for why the two are the same answer.
            if crate::v2::collect::is_dir(&path, entry.file_type().ok()) {
                let _ = names.insert(entry.file_name().to_string_lossy().into_owned());
            } else if path.extension().is_some_and(|ext| ext == "py") {
                if let Some(stem) = path.file_stem() {
                    let _ = names.insert(stem.to_string_lossy().into_owned());
                }
            }
        }
    }
    names
}

/// Read and scan one file.
pub fn scan_path(
    path: &Path,
    rootdir: &Path,
    config: &ResolvedConfig,
    shadows: &HashSet<String>,
) -> Result<Vec<CollectedTest>, Dynamic> {
    let source = read_source(path)?;
    scan_module(&source, &relative_posix(path, rootdir), config, shadows)
}

/// The file's text, or the reason it is not this module's to answer.
///
/// Split out of [`scan_path`] because the cached pass needs the bytes *before* it decides
/// whether to parse them — the cache key hashes the source.
fn read_source(path: &Path) -> Result<String, Dynamic> {
    // The markdown tier is `_v2_worker.py::collect_markdown`: it evaluates fenced blocks and
    // has no static analogue.
    if !path.extension().is_some_and(|ext| ext == "py") {
        return Err(Dynamic::new(
            Reason::NotPythonSource,
            format!("{} is not a .py file", path.display()),
        ));
    }
    std::fs::read_to_string(path).map_err(|err| {
        Dynamic::new(
            Reason::NotPythonSource,
            format!("{} could not be read as UTF-8 text: {err}", path.display()),
        )
    })
}

/// Does this source declare a PEP 263 encoding that is **not** UTF-8?
///
/// This module reads every file as UTF-8.  When a file says otherwise, the interpreter decodes
/// the same bytes into *different text* — and the difference reaches the manifest, because
/// parametrize ids are copied out of string literals.  Probed: a file declaring
/// `# -*- coding: latin-1 -*-` whose `é` is the two bytes `C3 A9` is `é` to pytest and `Ã©`
/// to a UTF-8 reader, so `@parametrize("s", ["é"])` yields `test_x[é]` there and `test_x[Ã©]`
/// here — a one-byte-wrong nodeid, with nothing anywhere reporting a problem.
///
/// The obvious failure mode is not this one: a genuinely latin-1 file usually holds bytes that
/// are not valid UTF-8 at all, so `read_to_string` fails and the file is already refused.  The
/// dangerous case is the file whose bytes are valid UTF-8 *and* declare something else, which
/// decodes cleanly into the wrong string.
///
/// Only the first two lines are scanned, which is PEP 263's own rule (`tokenize.detect_encoding`
/// checks line 1, then line 2 when line 1 is blank or a comment).  The pattern is CPython's
/// `cookie_re` — `^[ \t\f]*#.*?coding[:=][ \t]*([-_.a-zA-Z0-9]+)`.  Anything that does not
/// normalise to `utf-8`/`utf8` refuses, `utf-8-sig` included: it strips a BOM that this module
/// would keep, and "conservative" means the answer has to be *identical*, not merely close.
fn declares_non_utf8_encoding(source: &str) -> Option<String> {
    for line in source.lines().take(2) {
        let trimmed = line.trim_start_matches([' ', '\t', '\u{c}']);
        if !trimmed.starts_with('#') {
            continue;
        }
        let Some(marker) = trimmed.find("coding") else {
            continue;
        };
        let rest = &trimmed[marker + "coding".len()..];
        let Some(rest) = rest.strip_prefix([':', '=']) else {
            continue;
        };
        let name: String = rest
            .trim_start_matches([' ', '\t'])
            .chars()
            .take_while(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
            .collect();
        if name.is_empty() {
            continue;
        }
        // `codecs.lookup` normalises case and treats `_` and `-` alike.
        let normalised = name.to_ascii_lowercase().replace('_', "-");
        if normalised != "utf-8" && normalised != "utf8" {
            return Some(name);
        }
    }
    None
}

/// `_v2_worker.py::_relative_posix` — the manifest's `path` contract.
fn relative_posix(path: &Path, rootdir: &Path) -> String {
    match path.strip_prefix(rootdir) {
        Ok(relative) => crate::v2::to_posix(relative),
        Err(_) => crate::v2::to_posix(path),
    }
}

/// Is every `conftest.py` that applies to `dir` safe to treat as "adds nothing an id depends
/// on"?
///
/// The chain is `_v2_worker.py::conftest_chain`'s: every `conftest.py` from `dir` up to and
/// including `rootdir`.  Each one must (a) define no parametrized fixture, and (b) pass the
/// same import-safety analysis a test file passes.
///
/// (a) is the rule the plan names: a parametrized fixture in *any* conftest in the chain
/// changes the nodeids of tests that never mention it (`fixture_param_dimensions` walks the
/// whole closure, and `build_closure` seeds the closure with `registry.autouse_names` before
/// the test's own arguments — so an autouse parametrized fixture contributes the **leftmost**
/// id component to every test in the directory).
///
/// It was originally `source.contains("params=")`, which is unfoolable and also unusably
/// coarse: on rustest's own tree it matched a *parameter default* in a compat shim
/// (`def _fixture(..., params=None, ...)` in `tests/conftest.py`) and took the whole 53-file
/// suite out of the static tier — and, later, out of assertion rewriting. It is now
/// [`parametrized_fixture_call`]; see that function for what it does and does not catch.
///
/// (b) is needed because a conftest that raises at import time is a *collection error* for
/// every test file below it: `_v2_worker.py::build_registry` imports the chain before the
/// module, inside `collect_file`'s `try`.
/// The first call in `body` that may create a **parametrized fixture**, described, or `None`.
///
/// Walks every statement and expression looking for a *call* whose callee's last name segment
/// is `fixture` — `fixture(...)`, `pytest.fixture(...)`, `rustest.fixture(...)`, or any other
/// attribute path ending in `.fixture` — and reports it when it carries `params=`, `ids=`, or
/// `**kwargs`.
///
/// # What it catches that matters
///
/// Every syntactic form that reaches `_v2_worker.py`'s registry with
/// `__rustest_fixture_params__` set: the decorator form `@fixture(params=[...])`, the
/// decorator-factory form assigned at module level, and `**kwargs` — where the keys are a
/// runtime value and `params` may be among them (the splat hole fixed in Task 1).
///
/// # What it deliberately no longer catches
///
/// `params=` as a **parameter default** (`def _fixture(*, params=None)`) or as a **keyword
/// forward to a non-fixture callee**. Neither can produce a parametrized fixture, and the
/// previous textual scan flagged both — which is how one compat shim in `tests/conftest.py`
/// removed 53 files from the static tier.
///
/// # The residual, stated rather than hidden
///
/// A conftest that builds a parametrized fixture **indirectly** — a helper that calls
/// `fixture(params=...)` and whose *return value* is bound at module level under a name this
/// walk cannot follow — is not caught by the callee check alone. It is caught anyway, because
/// the walk is over the whole module rather than only its top level: the `fixture(params=...)`
/// call still appears somewhere in the file, wherever it is nested. The cost of that choice is
/// the reverse false positive — a conftest that defines such a helper and *never uses it* is
/// flagged — and that is the direction this rule must err in, because a missed parametrized
/// fixture is a wrong manifest (the Critical class) while a spurious one costs only speed.
fn parametrized_fixture_call(body: &[ruff_python_ast::Stmt]) -> Option<String> {
    struct Finder {
        found: Option<String>,
    }

    impl ruff_python_ast::visitor::Visitor<'_> for Finder {
        fn visit_expr(&mut self, expr: &ruff_python_ast::Expr) {
            if self.found.is_some() {
                return;
            }
            if let ruff_python_ast::Expr::Call(call) = expr {
                if let Some(detail) = fixture_call_detail(call) {
                    self.found = Some(detail);
                    return;
                }
            }
            ruff_python_ast::visitor::walk_expr(self, expr);
        }
    }

    let mut finder = Finder { found: None };
    for stmt in body {
        ruff_python_ast::visitor::Visitor::visit_stmt(&mut finder, stmt);
        if finder.found.is_some() {
            break;
        }
    }
    finder.found
}

/// `Some(description)` when `call` is a `…fixture(...)` carrying `params=`, `ids=` or a splat.
fn fixture_call_detail(call: &ruff_python_ast::ExprCall) -> Option<String> {
    let name = callee_tail(&call.func)?;
    if name != "fixture" {
        return None;
    }
    for keyword in &call.arguments.keywords {
        match keyword.arg.as_ref().map(|arg| arg.as_str()) {
            // `fixture(**kwargs)`: the keys are a runtime value, so `params` may be among
            // them. Same hole `Scan::named_decorator` closes for the decorator form.
            None => {
                return Some("a fixture call carries `**kwargs`, which may be `params=`".into())
            }
            Some(key @ ("params" | "ids")) => {
                return Some(format!("a fixture call carries `{key}=`"))
            }
            Some(_) => {}
        }
    }
    None
}

/// The last name segment of a callee: `fixture` for `fixture`, `pytest.fixture`, `a.b.fixture`.
///
/// Deliberately **not** resolved against the file's import bindings, unlike
/// [`Scan::named_decorator`]. A conftest that shadows `fixture` with something of its own
/// would be a false positive here; resolving would risk the opposite, and this rule is the one
/// that must never miss.
fn callee_tail(func: &ruff_python_ast::Expr) -> Option<&str> {
    match func {
        ruff_python_ast::Expr::Name(name) => Some(name.id.as_str()),
        ruff_python_ast::Expr::Attribute(attribute) => Some(attribute.attr.as_str()),
        _ => None,
    }
}

pub fn conftest_chain_is_static(
    dir: &Path,
    rootdir: &Path,
    shadows: &HashSet<String>,
) -> Result<(), Dynamic> {
    chain_is_static(
        &read_conftest_chain(dir, rootdir),
        shadows,
        ChainRule::Collection,
    )
}

/// One `conftest.py` in a chain, read once.
///
/// The bytes are shared between the two consumers that need them — [`chain_is_static`], which
/// decodes and parses, and the manifest cache's chain digest, which hashes.  Reading twice
/// would be the obvious shape and would double the syscall count of the one part of the warm
/// path that is not already a single read per collected file.
pub struct ConftestSource {
    path: PathBuf,
    /// Rootdir-relative posix path — what the cache digest hashes, so the digest does not move
    /// when a checkout moves.
    rel: String,
    /// `None` when the file could not be read at all.
    bytes: Option<Vec<u8>>,
}

/// Read every conftest that applies to `dir`, outermost first.
pub fn read_conftest_chain(dir: &Path, rootdir: &Path) -> Vec<ConftestSource> {
    conftest_chain(dir, rootdir)
        .into_iter()
        .map(|path| {
            let rel = relative_posix(&path, rootdir);
            let bytes = std::fs::read(&path).ok();
            ConftestSource { path, rel, bytes }
        })
        .collect()
}

/// What a caller needs from a conftest chain.
///
/// The two callers ask different questions of the same files, and conflating them is what
/// kept assertion rewriting off 52 of rustest's own 53 test files.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChainRule {
    /// Tier S **collection**: every rule applies, the parametrized-fixture veto included.
    /// A parametrized fixture anywhere in the chain multiplies the ids of tests that never
    /// mention it, so a static answer for such a file would be a *wrong manifest*.
    Collection,
    /// Assertion **rewriting**: the chain must only be *readable*.
    ///
    /// Every other rule in this function exists to decide whether a file's **node ids** are
    /// predictable without importing anything, and rewriting does not depend on ids. It
    /// transforms a module's own `assert` statements; a conftest can make the id set
    /// unknowable — a parametrized fixture multiplies it, an unpredictable import can add to
    /// it — without changing what a single `assert a == b` in a test body means.
    ///
    /// Inheriting the collection rules here was over-conservative in the most expensive way,
    /// because every one of them is **transitive down the whole tree**: on rustest's own
    /// suite a single compat shim in the root `tests/conftest.py` — which forwards a
    /// `params=` keyword, and reaches `importlib` to rebuild a package path — silenced the
    /// failure messages of all 53 files beneath it. Neither property has anything to do with
    /// rewriting.
    ///
    /// **Readability is required**, and it is the one thing that is: the bytecode cache key
    /// hashes the chain's bytes (`ManifestCache::key_for_chain`), so a conftest that cannot
    /// be read is a key that cannot be composed — and therefore a cached artefact that a
    /// later conftest edit would not invalidate.
    ///
    /// The *file's* own gate followed at Phase 3 Task 2: `rewrite_plan` now requires only that
    /// the file parse, for the same reason spelled out there — the collection rules decide
    /// whether ids are predictable, and rewriting does not ask.
    Rewrite,
}

fn chain_is_static(
    chain: &[ConftestSource],
    shadows: &HashSet<String>,
    rule: ChainRule,
) -> Result<(), Dynamic> {
    for entry in chain {
        let conftest = &entry.path;
        let Some(source) = entry
            .bytes
            .as_deref()
            .and_then(|bytes| std::str::from_utf8(bytes).ok())
        else {
            return Err(Dynamic::new(
                Reason::ConftestChain,
                format!("{} could not be read", conftest.display()),
            ));
        };
        if rule == ChainRule::Rewrite {
            // Readable is the whole requirement; see `ChainRule::Rewrite`.  Everything below
            // decides whether *ids* are predictable, which rewriting does not ask.
            continue;
        }
        if let Some(encoding) = declares_non_utf8_encoding(source) {
            // Applied to conftests as well as to test files: the parse below and the
            // structural analysis both run over text this module decoded, and a file it is
            // decoding wrongly is not one it can make claims about.
            return Err(Dynamic::new(
                Reason::EncodingCookie,
                format!("{} declares `coding: {encoding}`", conftest.display()),
            ));
        }
        let parsed = parse_module(source).map_err(|err| {
            Dynamic::new(
                Reason::ConftestChain,
                format!("{} does not parse: {err}", conftest.display()),
            )
        })?;
        if let Some(detail) = parametrized_fixture_call(&parsed.syntax().body) {
            return Err(Dynamic::new(
                Reason::ParametrizedFixture,
                format!("{}: {detail}", conftest.display()),
            ));
        }
        // A conftest defines fixtures, not tests, so only the import-safety half applies —
        // but it applies in full, decorators included.
        let config = conftest_scan_config();
        let mut scan = Scan::new(&config, shadows);
        scan.module_is_import_safe(&parsed.syntax().body)
            .map_err(|err| {
                Dynamic::new(
                    Reason::ConftestChain,
                    format!("{}: {}", conftest.display(), err.detail),
                )
            })?;
    }
    Ok(())
}

/// `_v2_worker.py::conftest_chain`, outermost first (order is irrelevant here — every entry
/// must pass — but kept identical so the two are readably the same walk).
fn conftest_chain(dir: &Path, rootdir: &Path) -> Vec<PathBuf> {
    let mut chain = Vec::new();
    let mut current = Some(dir);
    while let Some(parent) = current {
        let conftest = parent.join("conftest.py");
        if conftest.is_file() {
            chain.push(conftest);
        }
        if parent == rootdir {
            break;
        }
        current = parent.parent();
    }
    chain.reverse();
    chain
}

/// Scan one module's source and return its tests, or the reason it is dynamic.
pub fn scan_module(
    source: &str,
    rel_path: &str,
    config: &ResolvedConfig,
    shadows: &HashSet<String>,
) -> Result<Vec<CollectedTest>, Dynamic> {
    if let Some(encoding) = declares_non_utf8_encoding(source) {
        return Err(Dynamic::new(
            Reason::EncodingCookie,
            format!("{rel_path} declares `coding: {encoding}`"),
        ));
    }
    let parsed = parse_module(source)
        .map_err(|err| Dynamic::new(Reason::ParseError, format!("{rel_path}: {err}")))?;
    let mut scan = Scan::new(config, shadows);
    scan.module(parsed.syntax().body.as_slice(), rel_path)
}

// ---------------------------------------------------------------------------
// Literals
// ---------------------------------------------------------------------------

/// A const-evaluated Python literal.
///
/// Deliberately narrow.  Floats are **absent**: `_generate_param_id` renders them with
/// Python's `str()`, whose shortest-round-trip spelling Rust's `{}` does not always match
/// (`str(1e16)` is `1e+16`), and an id that differs by one byte is a wrong nodeid.  Bytes,
/// complex, sets, enums and objects are absent for the same class of reason — v1 renders them
/// through `repr()` or the `param{index}` fallback, both of which need real Python.
#[derive(Debug, Clone, PartialEq)]
pub enum Lit {
    None,
    Bool(bool),
    /// Sign and magnitude, so `str()` of a negated literal is exact without an i128 detour.
    Int {
        negative: bool,
        magnitude: u64,
    },
    Str(String),
    /// A list or a tuple; `_generate_param_id` and `_build_cases` treat them identically.
    Seq(Vec<Lit>),
    Dict(Vec<(String, Lit)>),
}

impl Lit {
    /// The JSON `_v2_worker.py::_json_safe` would produce for this value, or `None` when this
    /// module cannot put the value on the wire **unchanged**.
    ///
    /// The `None` case is a negative integer below `i64::MIN`.  `serde_json::Number` holds
    /// `u64`/`i64`/`f64`, so such a value has no exact representation — and the previous
    /// `-(magnitude as i128 as i64)` silently *wrapped*, turning a mark argument into a
    /// positive number of a different magnitude.  Refusing the file is the right answer rather
    /// than the cautious one: Tier D's own answer here is a lossy `f64` (serde parses an
    /// out-of-range integer literal as a float), so emitting anything would be a **third**
    /// spelling of the same value, and the differential would be comparing two wrongs.
    fn to_json(&self) -> Option<serde_json::Value> {
        Some(match self {
            Lit::None => serde_json::Value::Null,
            Lit::Bool(value) => serde_json::Value::Bool(*value),
            Lit::Int {
                negative,
                magnitude,
            } => {
                if *negative {
                    // `-(2^63)` is representable even though `+(2^63)` is not, so that one
                    // magnitude is spelled out; every larger one has no `i64` at all.
                    let signed = if *magnitude == (i64::MAX as u64) + 1 {
                        i64::MIN
                    } else {
                        -i64::try_from(*magnitude).ok()?
                    };
                    serde_json::Value::from(signed)
                } else {
                    serde_json::Value::from(*magnitude)
                }
            }
            Lit::Str(value) => serde_json::Value::String(value.clone()),
            Lit::Seq(items) => serde_json::Value::Array(
                items.iter().map(Lit::to_json).collect::<Option<Vec<_>>>()?,
            ),
            Lit::Dict(items) => serde_json::Value::Object(
                items
                    .iter()
                    .map(|(key, value)| Some((key.clone(), value.to_json()?)))
                    .collect::<Option<serde_json::Map<_, _>>>()?,
            ),
        })
    }

    /// Python's `bool(value)` for the subset this enum admits — `None`, `False`, `0`, `""`
    /// and every empty container are falsy, everything else is truthy.
    fn is_truthy(&self) -> bool {
        match self {
            Lit::None => false,
            Lit::Bool(value) => *value,
            Lit::Int { magnitude, .. } => *magnitude != 0,
            Lit::Str(value) => !value.is_empty(),
            Lit::Seq(items) => !items.is_empty(),
            Lit::Dict(items) => !items.is_empty(),
        }
    }

    /// `str(value)` for the subset this enum admits.
    fn python_str(&self) -> Option<String> {
        match self {
            Lit::None => Some("None".to_string()),
            Lit::Bool(true) => Some("True".to_string()),
            Lit::Bool(false) => Some("False".to_string()),
            Lit::Int {
                negative,
                magnitude,
            } => Some(if *negative && *magnitude != 0 {
                // `str(-0)` is `"0"`: the literal is `0` with a unary minus applied, and
                // Python has no negative zero integer.
                format!("-{magnitude}")
            } else {
                magnitude.to_string()
            }),
            Lit::Str(value) => Some(value.clone()),
            _ => None,
        }
    }
}

/// Port of `python/rustest/decorators.py::_generate_param_id`.
///
/// Port of `_pytest/python.py::IdMaker._idval_from_value` (l. 989-1007) and
/// `_idval_from_argname` (l. 1023-1027), matching `decorators.py::_generate_param_id`.
///
/// It used to be **v1's** generator, which invented a value-derived name for containers
/// (`empty`, `1-2`, `dict(1)`) and truncated long strings at 17 characters. Phase 4 Task 1
/// replaced both halves with pytest's: a container has no id of its own and falls back to
/// `<argname><index>`, and a string is ascii-escaped in full. 915 of click's ids and 95 of
/// jinja2's differed on nothing else, and the two engines have to agree, so Tier S moves with
/// Tier D or it may not answer at all.
///
/// `None` means "no static id" -- the caller refuses the file to the worker rather than
/// guessing, which is what keeps a value kind this does not model from silently getting a
/// different id from the one the worker would compute.
fn generate_param_id(value: &Lit, index: usize, argname: &str) -> Option<String> {
    match value {
        Lit::None | Lit::Bool(_) | Lit::Int { .. } => value.python_str(),
        Lit::Str(text) => Some(ascii_escaped(text)),
        // `_idval_from_value` answers `None` for a container, and `_idval` then falls back to
        // `str(argname) + str(idx)`.
        Lit::Seq(_) | Lit::Dict(_) => Some(format!("{argname}{index}")),
    }
}

/// Port of `_pytest/compat.py::ascii_escaped` (l. 195-215) for the `str` half.
///
/// `str.encode("unicode_escape")` in Rust terms: every code point outside printable ASCII
/// becomes its `\xNN`/`\uNNNN`/`\UNNNNNNNN` escape, and the three whitespace characters get
/// their short forms. Backslash itself doubles, which `unicode_escape` also does.
fn ascii_escaped(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '\t' => out.push_str("\\t"),
            '\r' => out.push_str("\\r"),
            '\n' => out.push_str("\\n"),
            '\\' => out.push_str("\\\\"),
            c if (' '..='~').contains(&c) => out.push(c),
            c if (c as u32) < 0x100 => out.push_str(&format!("\\x{:02x}", c as u32)),
            c if (c as u32) < 0x10000 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push_str(&format!("\\U{:08x}", c as u32)),
        }
    }
    out
}

/// Port of `_v2_worker.py::_unique_parameterset_ids`, itself a port of
/// `_pytest/python.py::IdMaker.make_unique_parameterset_ids`.
///
/// Duplicate ids get a numeric suffix (`1` -> `1_0`, `1_1`; `a` -> `a0`, `a1`), the underscore
/// appearing only when the id already ends in a digit.  The `while new_id in set(resolved)`
/// probe is ported with it: without it a suffixed id can collide with an id already present.
fn unique_parameterset_ids(ids: Vec<String>) -> Vec<String> {
    let distinct: HashSet<&String> = ids.iter().collect();
    if distinct.len() == ids.len() {
        return ids;
    }

    let mut counts: HashMap<&String, usize> = HashMap::new();
    for id in &ids {
        *counts.entry(id).or_insert(0) += 1;
    }
    let mut suffixes: HashMap<String, usize> = HashMap::new();
    let mut resolved = ids.clone();
    for (index, value) in ids.iter().enumerate() {
        if counts.get(value).copied().unwrap_or(0) <= 1 {
            continue;
        }
        let separator = if value.chars().last().is_some_and(|c| c.is_ascii_digit()) {
            "_"
        } else {
            ""
        };
        let counter = suffixes.entry(value.clone()).or_insert(0);
        let mut candidate = format!("{value}{separator}{counter}");
        while resolved.iter().any(|existing| existing == &candidate) {
            *counter += 1;
            candidate = format!("{value}{separator}{counter}");
        }
        resolved[index] = candidate;
        *suffixes.get_mut(value).unwrap() += 1;
    }
    resolved
}

/// One parametrization case: the id component and the argnames it binds.
type Case = (String, Vec<String>);

/// `python/rustest/decorators.py::_cross_product_cases` — **stacked decorators on one
/// function**: ids join with `-` outer first, and the outer dimension varies slowest.
fn cross_product_cases(outer: &[Case], inner: &[Case]) -> Vec<Case> {
    cross_cases(outer, inner, false)
}

/// `_v2_worker.py::_cross_product_cases` — a **class's** cases crossed with a *method's*.
///
/// Same nesting (the class dimension varies slowest) but the id joins **method component
/// first**, because pytest appends each `parametrize` call's component to
/// `CallSpec2._idlist` in call order and a method's own decorator is applied before the
/// enclosing class's mark is unpacked. Measured on pytest 8.4.2: `test_m[10-1]` for a class
/// carrying `[1, 2]` and a method carrying `[10, 20]`.
fn cross_class_and_method_cases(outer: &[Case], inner: &[Case]) -> Vec<Case> {
    cross_cases(outer, inner, true)
}

fn cross_cases(outer: &[Case], inner: &[Case], method_outermost: bool) -> Vec<Case> {
    let mut combined = Vec::with_capacity(outer.len() * inner.len());
    // `method_outermost` flips **both** halves together, and they have to move together: the
    // id order and the iteration order are the same fact seen twice. pytest emits
    // `[10-1], [10-2], [20-1], [20-2]` for a class carrying `[1, 2]` and a method carrying
    // `[10, 20]` -- method component first in the id, and the method dimension varying
    // slowest in the run.
    let pairs: Vec<(&Case, &Case)> = if method_outermost {
        inner
            .iter()
            .flat_map(|i| outer.iter().map(move |o| (o, i)))
            .collect()
    } else {
        outer
            .iter()
            .flat_map(|o| inner.iter().map(move |i| (o, i)))
            .collect()
    };
    for ((outer_id, outer_names), (inner_id, inner_names)) in pairs {
        let mut names = outer_names.clone();
        for name in inner_names {
            if !names.contains(name) {
                names.push(name.clone());
            }
        }
        let id = if method_outermost {
            format!("{inner_id}-{outer_id}")
        } else {
            format!("{outer_id}-{inner_id}")
        };
        combined.push((id, names));
    }
    combined
}

// ---------------------------------------------------------------------------
// Decorator vocabulary
// ---------------------------------------------------------------------------

/// The decorator forms Tier S recognises.  Anything else is [`Reason::UnknownDecorator`].
#[derive(Debug, Clone, PartialEq)]
enum Decor {
    /// `@fixture` / `@pytest.fixture(...)` — makes the name *not* a test.
    Fixture,
    /// `@staticmethod`, which changes whether the first parameter is a fixture request.
    StaticMethod,
    /// `@classmethod`.
    ClassMethod,
    /// A mark, already reduced to its wire form.
    Mark(MarkSpec),
    /// A `parametrize`, already expanded into cases.
    Parametrize(Vec<Case>),
}

/// Marks whose decorator surface is a *factory* with its own signature
/// (`decorators.py::MarkGenerator`), rather than the generic
/// `BareOrFactoryMark(name, MarkDecorator(name, args, kwargs))`.
///
/// `asyncio` is the one this module refuses outright: it rewrites class members
/// (`MarkGenerator.asyncio` walks `inspect.getmembers` and re-decorates every coroutine
/// method), which is not a transformation an AST can predict.
const FACTORY_MARKS: [&str; 4] = ["skipif", "xfail", "usefixtures", "asyncio"];

// ---------------------------------------------------------------------------
// The scanner
// ---------------------------------------------------------------------------

/// Names bound by allowlisted imports, so a decorator's base can be resolved.
#[derive(Default, Debug)]
struct Bindings {
    /// Local names bound to the `pytest` or `rustest` *module* object.
    modules: HashSet<String>,
    /// Local name -> the rustest/pytest symbol it refers to (`fixture`, `parametrize`, `mark`).
    symbols: HashMap<String, String>,
}

struct Scan<'a> {
    config: &'a ResolvedConfig,
    /// Module names this run's own files would shadow — see [`shadowing_names`].
    shadows: &'a HashSet<String>,
    bindings: Bindings,
    /// Top-level names already bound, for the duplicate-binding rule.
    bound: HashSet<String>,
}

impl<'a> Scan<'a> {
    fn new(config: &'a ResolvedConfig, shadows: &'a HashSet<String>) -> Self {
        Self {
            config,
            shadows,
            bindings: Bindings::default(),
            bound: HashSet::new(),
        }
    }

    // -- module ----------------------------------------------------------

    /// The whole of a test module: import-safety plus test extraction, in one pass so the
    /// bindings a decorator needs are already recorded when the decorator is read.
    fn module(&mut self, body: &[Stmt], rel_path: &str) -> Result<Vec<CollectedTest>, Dynamic> {
        let mut module_marks: Vec<MarkSpec> = Vec::new();
        let mut tests = Vec::new();

        // Two passes: `pytestmark` applies to every test in the file whatever line it is on
        // (pytest reads it off the module object *after* the module has fully executed —
        // `_pytest/python.py` l. 284), so it has to be known before the first test is built.
        for statement in body {
            self.statement_is_safe(statement)?;
            if let Stmt::Assign(assign) = statement {
                if let Some(name) = single_name_target(assign) {
                    if name == "pytestmark" {
                        module_marks = self.pytestmark_specs(&assign.value)?;
                    }
                }
            }
        }

        for statement in body {
            match statement {
                Stmt::FunctionDef(func) => {
                    if let Some(test) =
                        self.function_test(func, rel_path, &[], None, &module_marks, &[])?
                    {
                        tests.extend(test);
                    }
                }
                Stmt::ClassDef(class) => {
                    tests.extend(self.class_tests(class, rel_path, &[], &module_marks)?);
                }
                _ => {}
            }
        }
        Ok(tests)
    }

    /// The import-safety half on its own, for conftests (which define no tests).
    fn module_is_import_safe(&mut self, body: &[Stmt]) -> Result<(), Dynamic> {
        for statement in body {
            self.statement_is_safe(statement)?;
        }
        Ok(())
    }

    // -- statements ------------------------------------------------------

    /// Is this module-level statement one that cannot change what a later import sees?
    ///
    /// The allowlist is the whole detector for "importing runs code".  Every arm that is
    /// *not* here — `If`, `For`, `While`, `Try`, `With`, `Match`, `Raise`, `Assert`, `Delete`,
    /// `Global`, `AugAssign`, `AnnAssign`, a bare call — flags, which is what makes
    /// [`Reason::ConditionalDef`] and [`Reason::ModuleSideEffect`] complete rather than a list
    /// of shapes somebody thought of.
    fn statement_is_safe(&mut self, statement: &Stmt) -> Result<(), Dynamic> {
        match statement {
            Stmt::Pass(_) => Ok(()),
            // A bare string is a docstring or a stray literal; either way it binds nothing
            // and runs nothing.  Every other bare expression is a call.
            Stmt::Expr(expr) => match expr.value.as_ref() {
                Expr::StringLiteral(_) | Expr::EllipsisLiteral(_) => Ok(()),
                other => Err(Dynamic::new(
                    Reason::ModuleSideEffect,
                    format!("module-level expression statement: {}", describe(other)),
                )),
            },
            Stmt::Import(import) => {
                for alias in &import.names {
                    let module = alias.name.as_str();
                    let root = module.split('.').next().unwrap_or(module);
                    if !self.import_is_safe(root) {
                        return Err(Dynamic::new(
                            Reason::ForeignImport,
                            format!("import {module}"),
                        ));
                    }
                    let bound = alias
                        .asname
                        .as_ref()
                        .map(|name| name.as_str().to_string())
                        .unwrap_or_else(|| root.to_string());
                    self.bind_import(&bound)?;
                    // Only a *plain* `import pytest` binds a usable module name; `import
                    // rustest.decorators` binds `rustest`, which is still the module.
                    self.bindings.modules.insert(bound);
                }
                Ok(())
            }
            Stmt::ImportFrom(import) => {
                if import.level > 0 {
                    return Err(Dynamic::new(
                        Reason::ForeignImport,
                        "relative import".to_string(),
                    ));
                }
                let module = import
                    .module
                    .as_ref()
                    .map(|name| name.as_str())
                    .unwrap_or_default();
                let root = module.split('.').next().unwrap_or(module);
                if !self.import_is_safe(root) {
                    return Err(Dynamic::new(
                        Reason::ForeignImport,
                        format!("from {module} import ..."),
                    ));
                }
                for alias in &import.names {
                    let name = alias.name.as_str();
                    if name == "*" {
                        return Err(Dynamic::new(
                            Reason::StarImport,
                            format!("from {module} import *"),
                        ));
                    }
                    let bound = alias
                        .asname
                        .as_ref()
                        .map(|alias| alias.as_str().to_string())
                        .unwrap_or_else(|| name.to_string());
                    self.bind_import(&bound)?;
                    match name {
                        "fixture" | "parametrize" | "mark" => {
                            self.bindings.symbols.insert(bound, name.to_string());
                        }
                        _ => {}
                    }
                }
                Ok(())
            }
            Stmt::Assign(assign) => self.assignment_is_safe(assign),
            Stmt::FunctionDef(func) => {
                let name = func.name.as_str();
                self.bind(name)?;
                if name == "__getattr__" {
                    return Err(Dynamic::new(
                        Reason::ModuleGetattr,
                        "module-level __getattr__ (PEP 562)".to_string(),
                    ));
                }
                self.reject_dunder(name)?;
                // Decorators are *evaluated* at import time, so an unrecognised one can do
                // anything; recognising it here is what makes it safe.
                let _ = self.decorators(&func.decorator_list)?;
                Ok(())
            }
            Stmt::ClassDef(class) => {
                self.bind(class.name.as_str())?;
                self.reject_dunder(class.name.as_str())?;
                let _ = self.decorators(&class.decorator_list)?;
                self.class_body_is_safe(class)
            }
            other => Err(Dynamic::new(
                statement_reason(other),
                format!("module-level {}", describe_stmt(other)),
            )),
        }
    }

    /// A module- or class-level assignment: single plain-name target, literal value, and a
    /// name that neither the collector nor the fixture machinery reads.
    fn assignment_is_safe(&mut self, assign: &ruff_python_ast::StmtAssign) -> Result<(), Dynamic> {
        let Some(name) = single_name_target(assign) else {
            return Err(Dynamic::new(
                Reason::ModuleSideEffect,
                "assignment to something other than a single plain name".to_string(),
            ));
        };
        self.bind(&name)?;
        match name.as_str() {
            // `pytestmark` is read as marks, not as a value; validated by the caller.
            "pytestmark" => {
                let _ = self.pytestmark_specs(&assign.value)?;
                return Ok(());
            }
            "__test__" => {
                return Err(Dynamic::new(
                    Reason::TestAttribute,
                    "__test__ vetoes collection".to_string(),
                ))
            }
            "pytest_plugins" => {
                return Err(Dynamic::new(
                    Reason::PytestPlugins,
                    "pytest_plugins registers arbitrary fixtures".to_string(),
                ))
            }
            _ => {}
        }
        if matches_name_pattern(&name, &self.config.python_functions)
            || matches_name_pattern(&name, &self.config.python_classes)
        {
            // The name is in the collector's sights, so *what* it is bound to decides whether
            // a test exists.  A literal is not callable and not a class, so pytest skips it —
            // but proving that for every literal shape is not worth the risk here.
            return Err(Dynamic::new(
                Reason::ModuleSideEffect,
                format!("{name} is bound to a value but matches a collection pattern"),
            ));
        }
        match literal(&assign.value) {
            Some(_) => Ok(()),
            None => Err(Dynamic::new(
                Reason::ModuleSideEffect,
                format!("{name} = {}", describe(&assign.value)),
            )),
        }
    }

    /// A class body executes at import time, so it gets the same treatment as a module body —
    /// minus imports, which are legal but vanishingly rare in a class body and not worth a
    /// second binding scope.
    fn class_body_is_safe(&mut self, class: &ruff_python_ast::StmtClassDef) -> Result<(), Dynamic> {
        for statement in &class.body {
            match statement {
                Stmt::Pass(_) => {}
                Stmt::Expr(expr) => match expr.value.as_ref() {
                    Expr::StringLiteral(_) | Expr::EllipsisLiteral(_) => {}
                    other => {
                        return Err(Dynamic::new(
                            Reason::ModuleSideEffect,
                            format!("class-body expression statement: {}", describe(other)),
                        ))
                    }
                },
                Stmt::Assign(assign) => {
                    let Some(name) = single_name_target(assign) else {
                        return Err(Dynamic::new(
                            Reason::ModuleSideEffect,
                            "class-body assignment to something other than a plain name"
                                .to_string(),
                        ));
                    };
                    if name == "__test__" {
                        return Err(Dynamic::new(
                            Reason::TestAttribute,
                            "class-level __test__ vetoes collection".to_string(),
                        ));
                    }
                    if name != "pytestmark" && literal(&assign.value).is_none() {
                        return Err(Dynamic::new(
                            Reason::ModuleSideEffect,
                            format!("class-body {name} = {}", describe(&assign.value)),
                        ));
                    }
                    if name == "pytestmark" {
                        let _ = self.pytestmark_specs(&assign.value)?;
                    }
                }
                Stmt::FunctionDef(func) => {
                    let _ = self.decorators(&func.decorator_list)?;
                }
                Stmt::ClassDef(nested) => {
                    let _ = self.decorators(&nested.decorator_list)?;
                    self.class_body_is_safe(nested)?;
                }
                other => {
                    return Err(Dynamic::new(
                        statement_reason(other),
                        format!("class-body {}", describe_stmt(other)),
                    ))
                }
            }
        }
        Ok(())
    }

    fn bind(&mut self, name: &str) -> Result<(), Dynamic> {
        if !self.bound.insert(name.to_string()) {
            return Err(Dynamic::new(
                Reason::DuplicateName,
                format!("{name} is bound more than once at module level"),
            ));
        }
        Ok(())
    }

    /// An imported name that the configured patterns would collect is a test under Tier D —
    /// `_v2_worker.py::collect_module` iterates `vars(module)`, and `collect_imported_tests`
    /// defaults to `True` — while Tier S, which only sees `def`s, would miss it entirely.
    fn bind_import(&mut self, name: &str) -> Result<(), Dynamic> {
        self.bind(name)?;
        if matches_name_pattern(name, &self.config.python_functions)
            || matches_name_pattern(name, &self.config.python_classes)
        {
            return Err(Dynamic::new(
                Reason::ImportedTestName,
                format!("imported name {name} matches a collection pattern"),
            ));
        }
        Ok(())
    }

    /// May this module root be imported without the answer changing?
    ///
    /// Two tiers, and the difference between them is the shadowing check.  A pre-imported root
    /// ([`PREIMPORTED_ROOTS`]) never touches the filesystem, so nothing can shadow it.  A
    /// standard-library root ([`STDLIB_ALLOWLIST`]) does go through the import system, and
    /// `sys.path` carries this run's own directories — so a `queue.py` beside a test file makes
    /// `import queue` user code, and the name is refused.
    fn import_is_safe(&self, root: &str) -> bool {
        if PREIMPORTED_ROOTS.contains(&root) {
            return true;
        }
        STDLIB_ALLOWLIST.contains(&root) && !self.shadows.contains(root)
    }

    fn reject_dunder(&self, name: &str) -> Result<(), Dynamic> {
        if name.starts_with("__") && name.ends_with("__") {
            return Err(Dynamic::new(
                Reason::DunderDefinition,
                format!("{name} is decided by IGNORED_ATTRIBUTES, by exact name"),
            ));
        }
        Ok(())
    }

    // -- decorators ------------------------------------------------------

    /// Every decorator on a definition, in **source order**.
    ///
    /// Callers that care about order reverse it: Python applies the bottom decorator first, so
    /// `decorators.py::MarkDecorator.__call__` appends to `__rustest_marks__` bottom-up and
    /// `parametrize` crosses `existing x new` in the same direction.
    fn decorators(&self, decorators: &[ruff_python_ast::Decorator]) -> Result<Vec<Decor>, Dynamic> {
        decorators
            .iter()
            .map(|decorator| self.decorator(&decorator.expression))
            .collect()
    }

    fn decorator(&self, expr: &Expr) -> Result<Decor, Dynamic> {
        match expr {
            Expr::Name(name) => match name.id.as_str() {
                "staticmethod" if !self.bound.contains("staticmethod") => Ok(Decor::StaticMethod),
                "classmethod" if !self.bound.contains("classmethod") => Ok(Decor::ClassMethod),
                other => match self.bindings.symbols.get(other).map(String::as_str) {
                    Some("fixture") => Ok(Decor::Fixture),
                    _ => Err(Dynamic::new(Reason::UnknownDecorator, format!("@{other}"))),
                },
            },
            Expr::Attribute(_) => match self.dotted(expr) {
                Some(path) => self.named_decorator(&path, None),
                None => Err(Dynamic::new(
                    Reason::UnknownDecorator,
                    format!("@{}", describe(expr)),
                )),
            },
            Expr::Call(call) => {
                let path = match call.func.as_ref() {
                    Expr::Name(name) => vec![name.id.to_string()],
                    Expr::Attribute(_) => self.dotted(call.func.as_ref()).ok_or_else(|| {
                        Dynamic::new(Reason::UnknownDecorator, format!("@{}", describe(expr)))
                    })?,
                    other => {
                        return Err(Dynamic::new(
                            Reason::UnknownDecorator,
                            format!("@{}", describe(other)),
                        ))
                    }
                };
                self.named_decorator(&path, Some(&call.arguments))
            }
            other => Err(Dynamic::new(
                Reason::UnknownDecorator,
                format!("@{}", describe(other)),
            )),
        }
    }

    /// Resolve a dotted decorator name against the recorded import bindings.
    ///
    /// The bindings matter: `@fixture` is rustest's fixture only if `fixture` was imported
    /// from `rustest`/`pytest`, and a module that defines its own `fixture` would otherwise
    /// have it silently misread.  A locally-defined name never reaches here as a recognised
    /// form, because [`Scan::bind`] and [`Scan::bind_import`] share one namespace and a
    /// duplicate binding flags the file.
    fn named_decorator(
        &self,
        path: &[String],
        arguments: Option<&ruff_python_ast::Arguments>,
    ) -> Result<Decor, Dynamic> {
        let rendered = path.join(".");
        let unknown = || Dynamic::new(Reason::UnknownDecorator, format!("@{rendered}"));

        // `pytest.mark.X` / `rustest.mark.X`
        let tail: Vec<&str> = path.iter().map(String::as_str).collect();
        let resolved: Vec<&str> = match tail.as_slice() {
            [head, rest @ ..] if self.bindings.modules.contains(*head) => rest.to_vec(),
            [head, rest @ ..] => match self.bindings.symbols.get(*head).map(String::as_str) {
                Some(symbol) => {
                    let mut out = vec![symbol];
                    out.extend_from_slice(rest);
                    out
                }
                None => return Err(unknown()),
            },
            [] => return Err(unknown()),
        };

        match resolved.as_slice() {
            ["fixture"] => {
                if let Some(arguments) = arguments {
                    for keyword in &arguments.keywords {
                        // `**kwargs` on a fixture: the keys are a runtime value, so `params`
                        // may be among them.  Reading the `arg` as the literal string `"**"`
                        // and comparing *that* to `params` was the bug — a splat sailed
                        // through as a plain fixture and `@fixture(**{"params": [1, 2]})`
                        // doubled every id in the directory with nothing to show for it.
                        let Some(key) = keyword.arg.as_ref().map(|arg| arg.as_str()) else {
                            return Err(Dynamic::new(
                                Reason::ParametrizedFixture,
                                format!("@{rendered}(**kwargs) may carry params="),
                            ));
                        };
                        if key == "params" || key == "ids" {
                            return Err(Dynamic::new(
                                Reason::ParametrizedFixture,
                                format!("@{rendered}({key}=...)"),
                            ));
                        }
                    }
                }
                Ok(Decor::Fixture)
            }
            ["mark", "parametrize"] | ["parametrize"] => match arguments {
                Some(arguments) => Ok(Decor::Parametrize(self.parametrize_cases(arguments)?)),
                // `@pytest.mark.parametrize` uncalled raises TypeError at import
                // (`decorators.py::_create_parametrize_mark`), i.e. a collection error.
                None => Err(Dynamic::new(
                    Reason::NonLiteralParametrize,
                    "@parametrize used without arguments".to_string(),
                )),
            },
            ["mark", name] => Ok(Decor::Mark(self.mark_spec(name, arguments)?)),
            _ => Err(unknown()),
        }
    }

    /// The `MarkSpec` `decorators.py` would store for `@mark.<name>(...)`.
    ///
    /// The generic case is `MarkDecorator(name, args, kwargs)` verbatim.  The four names in
    /// [`FACTORY_MARKS`] go through their own factory, which rewrites the arguments:
    ///
    /// * `skipif(condition, reason=None)` -> `MarkDecorator("skipif", (condition,), {"reason":
    ///   reason})`, and `MarkDecorator._normalize_args` then **evaluates a string condition**
    ///   against the test's module globals — genuinely dynamic, so only a `True`/`False`
    ///   literal is admitted;
    /// * `xfail(condition=None, *, reason, raises, run, strict)` ->
    ///   `MarkDecorator("xfail", (condition,) or (), {reason, raises, run, strict})`, i.e. the
    ///   four keywords are **always present** with their defaults, which is why a naive
    ///   "copy the kwargs the user wrote" would produce the wrong wire form;
    /// * `usefixtures(*names)` -> `MarkDecorator("usefixtures", names, {})`;
    /// * `asyncio` re-decorates class members and is refused.
    ///
    /// The **bare** form of all of them is `BareOrFactoryMark._bare`, i.e.
    /// `MarkDecorator(name, (), {})` — empty args *and* empty kwargs, which is what makes a
    /// bare `skipif` an unconditional skip.
    fn mark_spec(
        &self,
        name: &str,
        arguments: Option<&ruff_python_ast::Arguments>,
    ) -> Result<MarkSpec, Dynamic> {
        // `asyncio` is refused in **both** forms, and the bare one is why this check is here
        // rather than beside the other factories below.  `MarkGenerator.asyncio` is a plain
        // method, not a `BareOrFactoryMark`, so `@mark.asyncio` calls it with the decorated
        // object — and when that object is a *class* it walks `inspect.getmembers` and
        // re-decorates every coroutine method, which is not a transformation an AST predicts.
        // The bare-on-a-function case happens to produce `MarkDecorator("asyncio", (), {})`
        // like every other bare mark; admitting it would mean the rule held for one of the two
        // shapes and nothing would say which.
        if name == "asyncio" {
            return Err(Dynamic::new(
                Reason::NonLiteralMark,
                "@mark.asyncio rewrites the object it decorates".to_string(),
            ));
        }

        let Some(arguments) = arguments else {
            // Bare: `BareOrFactoryMark._bare` is `MarkDecorator(name, (), {})` for every mark,
            // factory or not — empty args *and* empty kwargs.
            return Ok(MarkSpec {
                name: name.to_string(),
                args: Vec::new(),
                kwargs: serde_json::Map::new(),
            });
        };

        let mut args = Vec::new();
        for arg in &arguments.args {
            let Some(value) = literal(arg) else {
                return Err(Dynamic::new(
                    Reason::NonLiteralMark,
                    format!("@mark.{name}({})", describe(arg)),
                ));
            };
            args.push(value);
        }
        let mut kwargs: Vec<(String, Lit)> = Vec::new();
        for keyword in &arguments.keywords {
            let Some(key) = keyword.arg.as_ref() else {
                return Err(Dynamic::new(
                    Reason::NonLiteralMark,
                    format!("@mark.{name}(**kwargs)"),
                ));
            };
            let Some(value) = literal(&keyword.value) else {
                return Err(Dynamic::new(
                    Reason::NonLiteralMark,
                    format!("@mark.{name}({}=...)", key.as_str()),
                ));
            };
            kwargs.push((key.as_str().to_string(), value));
        }

        if !FACTORY_MARKS.contains(&name) {
            return spec(name, args, kwargs);
        }

        match name {
            "skipif" => {
                let condition = match args.as_slice() {
                    [Lit::Bool(value)] => Lit::Bool(*value),
                    _ => {
                        return Err(Dynamic::new(
                            Reason::NonLiteralMark,
                            "@mark.skipif needs a bool literal condition (a string one is \
                             eval'd against the module's globals at decoration time)"
                                .to_string(),
                        ))
                    }
                };
                // `_skipif(condition, reason=None, *, _kw_reason=None)`: positional `reason`
                // is legal too, but only the keyword form is modelled here.
                let mut reason = Lit::None;
                for (key, value) in &kwargs {
                    if key != "reason" {
                        return Err(Dynamic::new(
                            Reason::NonLiteralMark,
                            format!("@mark.skipif({key}=...)"),
                        ));
                    }
                    reason = value.clone();
                }
                spec(
                    "skipif",
                    vec![condition],
                    vec![("reason".to_string(), reason)],
                )
            }
            "xfail" => {
                let condition = match args.as_slice() {
                    [] => None,
                    [Lit::Bool(value)] => Some(Lit::Bool(*value)),
                    _ => {
                        return Err(Dynamic::new(
                            Reason::NonLiteralMark,
                            "@mark.xfail needs a bool literal condition".to_string(),
                        ))
                    }
                };
                let mut reason = Lit::None;
                let mut run = Lit::Bool(true);
                let mut strict = Lit::Bool(false);
                for (key, value) in &kwargs {
                    match key.as_str() {
                        "reason" => reason = value.clone(),
                        "run" => run = value.clone(),
                        "strict" => strict = value.clone(),
                        // `raises=ValueError` is a *class*, never a literal; it must reach the
                        // execute half as an object, so a static answer cannot produce it.
                        other => {
                            return Err(Dynamic::new(
                                Reason::NonLiteralMark,
                                format!("@mark.xfail({other}=...)"),
                            ))
                        }
                    }
                }
                spec(
                    "xfail",
                    condition.into_iter().collect(),
                    vec![
                        ("reason".to_string(), reason),
                        ("raises".to_string(), Lit::None),
                        ("run".to_string(), run),
                        ("strict".to_string(), strict),
                    ],
                )
            }
            "usefixtures" => {
                if !kwargs.is_empty() || !args.iter().all(|arg| matches!(arg, Lit::Str(_))) {
                    return Err(Dynamic::new(
                        Reason::NonLiteralMark,
                        "@mark.usefixtures takes positional string names only".to_string(),
                    ));
                }
                spec("usefixtures", args, Vec::new())
            }
            _ => Err(Dynamic::new(
                Reason::NonLiteralMark,
                format!("@mark.{name} has a factory Tier S does not model"),
            )),
        }
    }

    /// `pytestmark = <mark>` or `pytestmark = [<mark>, ...]`.
    ///
    /// `_v2_worker.py::_spec_from_pytestmark` reads `.name`/`.args`/`.kwargs` off whatever the
    /// expression evaluated to, which for an uncalled `pytest.mark.slow` is a
    /// `BareOrFactoryMark` with empty args and kwargs and for a called one is the
    /// `MarkDecorator` the factory returned — i.e. exactly the same two shapes
    /// [`Scan::mark_spec`] models.  `_normalize_args` is **not** applied on this path (it runs
    /// inside `MarkDecorator.__call__`, and nothing calls the decorator here), which changes
    /// nothing for the subset admitted: it only rewrites *string* skipif conditions, and those
    /// are refused.
    fn pytestmark_specs(&self, value: &Expr) -> Result<Vec<MarkSpec>, Dynamic> {
        // A **list** is unpacked and nothing else is: `get_unpacked_marks` tests
        // `isinstance(item, list)` (`_pytest/mark/structures.py` l. 427/433), so a *tuple*
        // is appended whole and then dies in `normalize_mark_list` with
        // `TypeError: got (...) instead of Mark`.  Measured: `pytestmark = (m1, m2)` is a
        // collection error under pytest 8.4.2 and under the v2 worker; unpacking it here
        // made Tier S the only surface that accepted it.
        let entries: Vec<&Expr> = match value {
            Expr::List(list) => list.elts.iter().collect(),
            single => vec![single],
        };
        entries
            .into_iter()
            .map(|entry| match self.decorator(entry) {
                Ok(Decor::Mark(spec)) => Ok(spec),
                Ok(_) | Err(_) => Err(Dynamic::new(
                    Reason::NonLiteralPytestmark,
                    format!("pytestmark entry {}", describe(entry)),
                )),
            })
            .collect()
    }

    /// Expand one `@parametrize(...)` into cases.
    ///
    /// Port of `decorators.py::parametrize` -> `_normalize_arg_names` + `_build_cases`.  The
    /// **values** are the only place const-eval happens, and it is total: every element must
    /// be a [`Lit`], because `_generate_param_id` on anything else needs a live object.
    fn parametrize_cases(
        &self,
        arguments: &ruff_python_ast::Arguments,
    ) -> Result<Vec<Case>, Dynamic> {
        let flag = |detail: &str| Dynamic::new(Reason::NonLiteralParametrize, detail.to_string());

        let mut positional = arguments.args.iter();
        let Some(argnames_expr) = positional.next() else {
            return Err(flag("@parametrize with no argument names"));
        };
        let names = match literal(argnames_expr) {
            Some(Lit::Str(raw)) => {
                let parts: Vec<String> = raw
                    .split(',')
                    .map(|part| part.trim().to_string())
                    .filter(|part| !part.is_empty())
                    .collect();
                if parts.is_empty() {
                    return Err(flag("@parametrize with an empty argument-name string"));
                }
                parts
            }
            Some(Lit::Seq(items)) => {
                let mut parts = Vec::new();
                for item in items {
                    match item {
                        Lit::Str(name) => parts.push(name),
                        _ => return Err(flag("@parametrize argument names must be strings")),
                    }
                }
                parts
            }
            _ => return Err(flag("@parametrize argument names are not a literal")),
        };

        let mut values_expr = positional.next();
        let mut ids_expr: Option<&Expr> = None;
        for keyword in &arguments.keywords {
            let Some(key) = keyword.arg.as_ref() else {
                return Err(flag("@parametrize(**kwargs)"));
            };
            match key.as_str() {
                "values" | "argvalues" => values_expr = Some(&keyword.value),
                "ids" => ids_expr = Some(&keyword.value),
                // `indirect` turns a parametrized name into a fixture reference, which
                // changes what `_fixture_names` reports and what the closure resolves.
                other => return Err(flag(&format!("@parametrize({other}=...)"))),
            }
        }
        if positional.next().is_some() {
            return Err(flag("@parametrize with unexpected positional arguments"));
        }
        let Some(values_expr) = values_expr else {
            return Err(flag("@parametrize with no values"));
        };

        let Some(Lit::Seq(values)) = literal(values_expr) else {
            return Err(flag("@parametrize values are not a literal sequence"));
        };
        // `_build_cases` on an empty sequence stores `()`, which `_parametrization` reads back
        // as "not parametrized" — and which the *next* stacked decorator then overwrites
        // instead of crossing, because `if existing_cases:` is falsy.  Not modelled.
        if values.is_empty() {
            return Err(flag("@parametrize with an empty value list"));
        }

        let ids = match ids_expr {
            None => None,
            Some(expr) => match literal(expr) {
                Some(Lit::Seq(items)) => {
                    let mut out = Vec::new();
                    for item in items {
                        match item {
                            Lit::Str(id) => out.push(id),
                            _ => return Err(flag("@parametrize(ids=[...]) must be strings")),
                        }
                    }
                    if out.len() != values.len() {
                        // `_build_cases` raises ValueError at decoration time -> import error.
                        return Err(flag("@parametrize(ids=...) length mismatch"));
                    }
                    Some(out)
                }
                // A callable `ids=` runs user code per value.
                _ => return Err(flag("@parametrize(ids=...) is not a literal list")),
            },
        };

        let mut cases = Vec::with_capacity(values.len());
        for (index, value) in values.iter().enumerate() {
            // `_build_cases`: a tuple/list whose length matches the names is unpacked; a
            // single name takes the whole value; anything else is a ValueError at decoration.
            let bound: Vec<String> = match value {
                Lit::Seq(items) if items.len() == names.len() => names.clone(),
                _ if names.len() == 1 => names.clone(),
                _ => return Err(flag("@parametrize value does not match argument names")),
            };
            // Dicts as value sets (`{"a": 1, "b": 2}` with several names) are a v1 shape whose
            // id generation reads the dict, not the unpacked values; not modelled.
            if matches!(value, Lit::Dict(_)) && names.len() > 1 {
                return Err(flag("@parametrize mapping value sets are not modelled"));
            }
            let id =
                match &ids {
                    Some(ids) => ids[index].clone(),
                    // One component per **argname**, joined with `-`, which is
                    // `IdMaker._resolve_ids` l. 945-948. A single-name parametrize has one
                    // component and reads exactly as before for scalars.
                    None => {
                        let mut parts = Vec::with_capacity(bound.len());
                        match value {
                            Lit::Seq(items) if items.len() == names.len() && names.len() > 1 => {
                                for (item, name) in items.iter().zip(names.iter()) {
                                    parts.push(generate_param_id(item, index, name).ok_or_else(
                                        || flag("@parametrize value has no static id"),
                                    )?);
                                }
                            }
                            _ => parts.push(
                                generate_param_id(value, index, &names[0])
                                    .ok_or_else(|| flag("@parametrize value has no static id"))?,
                            ),
                        }
                        parts.join("-")
                    }
                };
            cases.push((id, bound));
        }
        Ok(cases)
    }

    // -- tests -----------------------------------------------------------

    /// One `def`, if it is a test.  Returns `None` when the name is not collected at all.
    ///
    /// `outer_cases` is the enclosing class's parametrization, handed down exactly as
    /// `_v2_worker.py::_collect_class` hands it down (a class-level `@parametrize` writes onto
    /// the *class* object, where a method cannot see it).
    #[allow(clippy::too_many_arguments)]
    fn function_test(
        &self,
        func: &ruff_python_ast::StmtFunctionDef,
        rel_path: &str,
        parts: &[String],
        owner: Option<&ruff_python_ast::StmtClassDef>,
        outer_marks: &[MarkSpec],
        outer_cases: &[Case],
    ) -> Result<Option<Vec<CollectedTest>>, Dynamic> {
        let name = func.name.as_str();
        if !matches_name_pattern(name, &self.config.python_functions) {
            return Ok(None);
        }
        let decorators = self.decorators(&func.decorator_list)?;
        // `_is_test_function` refuses a fixture even when the name matches
        // (`PyCollector.istestfunction`'s `getfixturemarker(obj) is None`).
        if decorators.contains(&Decor::Fixture) {
            return Ok(None);
        }
        if body_yields(&func.body) {
            return Err(Dynamic::new(
                Reason::GeneratorTest,
                format!("{name} is a generator function"),
            ));
        }

        // Bottom decorator first: Python applies them inside-out, and both
        // `MarkDecorator.__call__` (append) and `parametrize` (cross `existing x new`) record
        // that order.
        let mut own_marks = Vec::new();
        let mut own_cases: Option<Vec<Case>> = None;
        for decor in decorators.iter().rev() {
            match decor {
                Decor::Mark(spec) => own_marks.push(spec.clone()),
                Decor::Parametrize(cases) => {
                    own_cases = Some(match own_cases {
                        None => cases.clone(),
                        Some(existing) => cross_product_cases(&existing, cases),
                    });
                }
                Decor::Fixture | Decor::StaticMethod | Decor::ClassMethod => {}
            }
        }

        let mut marks = own_marks;
        marks.extend_from_slice(outer_marks);

        // `_collect_function`: the class's cases are the outer dimension; the method's own are
        // crossed inside them.
        let cases: Option<Vec<Case>> = match (outer_cases.is_empty(), own_cases) {
            (true, own) => own,
            (false, None) => Some(outer_cases.to_vec()),
            (false, Some(own)) => Some(cross_class_and_method_cases(outer_cases, &own)),
        };

        let is_static_method = decorators.contains(&Decor::StaticMethod);
        let requested = requested_argnames(func, owner.is_some(), is_static_method);

        let mut full_parts: Vec<String> = parts.to_vec();
        full_parts.push(name.to_string());
        let part_refs: Vec<&str> = full_parts.iter().map(String::as_str).collect();

        let entry = |param_id: Option<&str>, bound: &[String]| CollectedTest {
            id: build_nodeid(rel_path, &part_refs, param_id),
            path: rel_path.to_string(),
            qualname: full_parts.join("."),
            class_name: if full_parts.len() > 1 {
                Some(full_parts[..full_parts.len() - 1].join("."))
            } else {
                None
            },
            param_id: param_id.map(str::to_string),
            marks: marks.clone(),
            // `_fixture_names` is the requested names minus the ones parametrize supplies.
            fixtures: requested
                .iter()
                .filter(|name| !bound.contains(name))
                .cloned()
                .collect(),
            tier: Tier::Static,
        };

        Ok(Some(match cases {
            None => vec![entry(None, &[])],
            Some(cases) => {
                let ids = unique_parameterset_ids(
                    cases.iter().map(|(id, _)| id.clone()).collect::<Vec<_>>(),
                );
                cases
                    .iter()
                    .zip(ids)
                    .map(|((_, bound), id)| entry(Some(&id), bound))
                    .collect()
            }
        }))
    }

    /// One `class`, if it is a test class.  Port of `_v2_worker.py::_collect_class`.
    fn class_tests(
        &mut self,
        class: &ruff_python_ast::StmtClassDef,
        rel_path: &str,
        parts: &[String],
        outer_marks: &[MarkSpec],
    ) -> Result<Vec<CollectedTest>, Dynamic> {
        let name = class.name.as_str();
        // The base check comes **before** the name filter, because a `unittest.TestCase`
        // subclass is collected whatever its name is (`_pytest/unittest.py::
        // pytest_pycollect_makeitem` runs before `python.py`'s trylast hook), so a
        // `class Legacy(unittest.TestCase)` that fails `python_classes` is still a test class
        // in Tier D and must not be silently dropped here.
        self.class_bases_are_object(class)?;
        if !matches_name_pattern(name, &self.config.python_classes) {
            return Ok(Vec::new());
        }

        let decorators = self.decorators(&class.decorator_list)?;
        // `_hasinit`/`_hasnew`: pytest warns and collects **nothing** from such a class, and
        // returns without failing the run.  With no bases, the body is the whole story.
        //
        // The oracle is `getattr(cls, "__init__") != object.__init__`, which is about the
        // *name being bound*, not about how.  `__init__ = 5` binds it just as surely as a
        // `def` does — pytest and Tier D both refuse such a class, and matching only
        // `FunctionDef` here made Tier S collect it.
        let has_constructor = class.body.iter().any(|statement| {
            matches!(
                class_body_binding(statement),
                Some(("__init__" | "__new__", true))
            )
        });
        if has_constructor {
            return Ok(Vec::new());
        }

        let mut own_marks = Vec::new();
        let mut own_cases: Option<Vec<Case>> = None;
        for decor in decorators.iter().rev() {
            match decor {
                Decor::Mark(spec) => own_marks.push(spec.clone()),
                Decor::Parametrize(cases) => {
                    own_cases = Some(match own_cases {
                        None => cases.clone(),
                        Some(existing) => cross_product_cases(&existing, cases),
                    });
                }
                Decor::Fixture | Decor::StaticMethod | Decor::ClassMethod => {}
            }
        }
        // `_mark_specs(cls, consider_mro=True)` walks `reversed(cls.__mro__)`; with `object`
        // as the only base there is exactly one `__dict__` carrying marks — this class's — so
        // a class-body `pytestmark` precedes the decorator marks, as `_mark_specs` orders its
        // two sources.
        let mut class_marks = Vec::new();
        for statement in &class.body {
            if let Stmt::Assign(assign) = statement {
                if single_name_target(assign).as_deref() == Some("pytestmark") {
                    class_marks.extend(self.pytestmark_specs(&assign.value)?);
                }
            }
        }
        class_marks.extend(own_marks);
        class_marks.extend_from_slice(outer_marks);

        let mut child_parts = parts.to_vec();
        child_parts.push(name.to_string());
        let cases = own_cases.unwrap_or_default();

        let mut tests = Vec::new();
        for statement in &class.body {
            match statement {
                Stmt::FunctionDef(func) => {
                    if let Some(found) = self.function_test(
                        func,
                        rel_path,
                        &child_parts,
                        Some(class),
                        &class_marks,
                        &cases,
                    )? {
                        tests.extend(found);
                    }
                }
                Stmt::ClassDef(nested) => {
                    // A nested class inherits the enclosing class's cases through
                    // `_make_items`' `outer_cases`, which this port does not thread further —
                    // so a parametrized class holding a nested class flags rather than
                    // silently dropping a dimension.
                    if !cases.is_empty() {
                        return Err(Dynamic::new(
                            Reason::NonLiteralParametrize,
                            "a parametrized class containing a nested class is not modelled"
                                .to_string(),
                        ));
                    }
                    tests.extend(self.class_tests(nested, rel_path, &child_parts, &class_marks)?);
                }
                _ => {}
            }
        }
        Ok(tests)
    }

    /// A test class may inherit from nothing at all, or from `object`.
    ///
    /// Any other base flags, and the reason is not caution for its own sake: pytest collects
    /// **inherited** methods (`_pytest/python.py::PyCollector.collect` walks the whole MRO, and
    /// `_v2_worker.py::_mro_ordered_members` reproduces the base-first ordering), so a base
    /// class defined in another file — or produced by a metaclass, or a `unittest.TestCase` —
    /// contributes tests, marks and fixtures that are not in this file's AST at all.
    fn class_bases_are_object(&self, class: &ruff_python_ast::StmtClassDef) -> Result<(), Dynamic> {
        let Some(arguments) = class.arguments.as_ref() else {
            return Ok(());
        };
        if !arguments.keywords.is_empty() {
            // `metaclass=` / `**kwargs` — `__init_subclass__` and metaclass `__new__` both run
            // arbitrary code at class creation.
            return Err(Dynamic::new(
                Reason::ClassBases,
                format!("class {} has class keywords", class.name.as_str()),
            ));
        }
        for base in &arguments.args {
            let is_object = matches!(base, Expr::Name(name) if name.id.as_str() == "object")
                && !self.bound.contains("object");
            if !is_object {
                let unittest_case = self
                    .dotted(base)
                    .is_some_and(|path| path.last().is_some_and(|tail| tail == "TestCase"));
                return Err(Dynamic::new(
                    if unittest_case {
                        Reason::UnittestCase
                    } else {
                        Reason::ClassBases
                    },
                    format!("class {}({})", class.name.as_str(), describe(base)),
                ));
            }
        }
        Ok(())
    }

    /// `a.b.c` as `["a", "b", "c"]`, or `None` if the chain is not all plain names.
    fn dotted(&self, expr: &Expr) -> Option<Vec<String>> {
        match expr {
            Expr::Name(name) => Some(vec![name.id.to_string()]),
            Expr::Attribute(attribute) => {
                let mut path = self.dotted(attribute.value.as_ref())?;
                path.push(attribute.attr.as_str().to_string());
                Some(path)
            }
            _ => None,
        }
    }
}

/// Build a [`MarkSpec`] from const-evaluated arguments, applying the wire's omission rules
/// (`_v2_worker.py::MarkSpec.to_wire`: empty `args`/`kwargs` are dropped).
/// Build a [`MarkSpec`] from const-evaluated arguments, or refuse the file.
///
/// `Err` means a value this module cannot put on the wire unchanged — see [`Lit::to_json`].
/// It is a refusal rather than a best effort because a mark argument is *data the execute half
/// reads*, and a silently different one changes no nodeid at all.
fn spec(name: &str, args: Vec<Lit>, kwargs: Vec<(String, Lit)>) -> Result<MarkSpec, Dynamic> {
    let unrepresentable = || {
        Dynamic::new(
            Reason::NonLiteralMark,
            format!("@mark.{name} carries an integer with no exact JSON form"),
        )
    };
    Ok(MarkSpec {
        name: name.to_string(),
        args: args
            .iter()
            .map(Lit::to_json)
            .collect::<Option<Vec<_>>>()
            .ok_or_else(unrepresentable)?,
        kwargs: kwargs
            .into_iter()
            .map(|(key, value)| Some((key, value.to_json()?)))
            .collect::<Option<serde_json::Map<_, _>>>()
            .ok_or_else(unrepresentable)?,
    })
}

/// Port of `_v2_worker.py::_requested_argnames` for the subset Tier S admits.
///
/// Only `POSITIONAL_OR_KEYWORD` and `KEYWORD_ONLY` parameters without a default count
/// (`_pytest/compat.py::getfuncargnames`), and the bound-method first argument is dropped
/// unless the attribute is a `staticmethod` — a rule pytest states with `inspect.getattr_static`
/// and **not** by looking for a parameter named `self`.  The `POSITIONAL_ONLY` guard is ported
/// with it: a signature with any positional-only parameter keeps its first name.
///
/// `mock.patch`'s `__wrapped__` stripping has no analogue here, because a `@patch` decorator is
/// not in the recognised set and flags the file first.
fn requested_argnames(
    func: &ruff_python_ast::StmtFunctionDef,
    is_method: bool,
    is_static_method: bool,
) -> Vec<String> {
    let parameters = func.parameters.as_ref();
    let mut names: Vec<String> = parameters
        .args
        .iter()
        .chain(parameters.kwonlyargs.iter())
        .filter(|parameter| parameter.default.is_none())
        .map(|parameter| parameter.parameter.name.as_str().to_string())
        .collect();
    let has_positional_only = !parameters.posonlyargs.is_empty();
    if !names.is_empty() && !has_positional_only && is_method && !is_static_method {
        names.remove(0);
    }
    names
}

/// Does this body contain a `yield` that belongs to *this* function?
///
/// Nested `def`s and lambdas own their own yields, so the walk stops at them — the same rule
/// `inspect.isgeneratorfunction` follows by reading the compiled code object's flags.
fn body_yields(body: &[Stmt]) -> bool {
    body.iter().any(statement_yields)
}

fn statement_yields(statement: &Stmt) -> bool {
    match statement {
        Stmt::FunctionDef(_) | Stmt::ClassDef(_) => false,
        Stmt::Expr(expr) => expr_yields(&expr.value),
        Stmt::Return(ret) => ret.value.as_ref().is_some_and(|value| expr_yields(value)),
        Stmt::Assign(assign) => expr_yields(&assign.value),
        Stmt::AugAssign(assign) => expr_yields(&assign.value),
        Stmt::AnnAssign(assign) => assign
            .value
            .as_ref()
            .is_some_and(|value| expr_yields(value)),
        Stmt::For(node) => {
            expr_yields(&node.iter) || body_yields(&node.body) || body_yields(&node.orelse)
        }
        Stmt::While(node) => {
            expr_yields(&node.test) || body_yields(&node.body) || body_yields(&node.orelse)
        }
        Stmt::If(node) => {
            expr_yields(&node.test)
                || body_yields(&node.body)
                || node
                    .elif_else_clauses
                    .iter()
                    .any(|clause| body_yields(&clause.body))
        }
        Stmt::With(node) => {
            body_yields(&node.body)
                || node
                    .items
                    .iter()
                    .any(|item| expr_yields(&item.context_expr))
        }
        Stmt::Try(node) => {
            body_yields(&node.body)
                || body_yields(&node.orelse)
                || body_yields(&node.finalbody)
                || node.handlers.iter().any(|handler| match handler {
                    ruff_python_ast::ExceptHandler::ExceptHandler(handler) => {
                        body_yields(&handler.body)
                    }
                })
        }
        _ => false,
    }
}

fn expr_yields(expr: &Expr) -> bool {
    match expr {
        Expr::Yield(_) | Expr::YieldFrom(_) => true,
        Expr::Lambda(_) => false,
        Expr::Await(node) => expr_yields(&node.value),
        Expr::BoolOp(node) => node.values.iter().any(expr_yields),
        Expr::BinOp(node) => expr_yields(&node.left) || expr_yields(&node.right),
        Expr::UnaryOp(node) => expr_yields(&node.operand),
        Expr::Named(node) => expr_yields(&node.value),
        Expr::If(node) => {
            expr_yields(&node.test) || expr_yields(&node.body) || expr_yields(&node.orelse)
        }
        Expr::Call(node) => {
            expr_yields(&node.func)
                || node.arguments.args.iter().any(expr_yields)
                || node
                    .arguments
                    .keywords
                    .iter()
                    .any(|keyword| expr_yields(&keyword.value))
        }
        Expr::Tuple(node) => node.elts.iter().any(expr_yields),
        Expr::List(node) => node.elts.iter().any(expr_yields),
        Expr::Set(node) => node.elts.iter().any(expr_yields),
        Expr::Starred(node) => expr_yields(&node.value),
        Expr::Attribute(node) => expr_yields(&node.value),
        Expr::Subscript(node) => expr_yields(&node.value) || expr_yields(&node.slice),
        Expr::Compare(node) => expr_yields(&node.left) || node.comparators.iter().any(expr_yields),
        _ => false,
    }
}

/// The name a class-body statement binds and whether the bound value is **truthy**, if it
/// binds exactly one plain name to something this module can evaluate.
///
/// Both halves matter, and the second is the one that is easy to get wrong.
/// `_v2_worker.py::_hasinit` is
///
/// ```text
/// init = getattr(obj, "__init__", None)
/// return bool(init) and init != object.__init__
/// ```
///
/// — so a *falsy* binding does not refuse the class at all. **Probed** (pytest 8.4.2,
/// `class TestBox: __init__ = <v>` plus one test method):
///
/// | `<v>` | collected |
/// |---|---|
/// | `None`, `0`, `""`, `[]` | **1** — falsy, so `hasinit` is False |
/// | `5`, `"x"`, `[1]`, `lambda self: None` | **0** |
///
/// A `def` is always truthy. A bare annotation (`__init__: int`, no value) binds *nothing*
/// and is correctly absent here — pytest collects that class — and it flags for an unrelated
/// reason anyway, since `class_body_is_safe` refuses annotated assignments outright.
fn class_body_binding(statement: &Stmt) -> Option<(&str, bool)> {
    match statement {
        Stmt::FunctionDef(func) => Some((func.name.as_str(), true)),
        Stmt::ClassDef(class) => Some((class.name.as_str(), true)),
        Stmt::Assign(assign) => match assign.targets.as_slice() {
            // A non-literal value cannot be reached: `class_body_is_safe` runs over the same
            // body first and refuses the file.  `None` rather than a guess, so a future
            // widening of that allowlist cannot silently decide truthiness here.
            [Expr::Name(name)] => Some((name.id.as_str(), literal(&assign.value)?.is_truthy())),
            _ => None,
        },
        Stmt::AnnAssign(assign) => match (assign.value.as_ref(), assign.target.as_ref()) {
            (Some(value), Expr::Name(name)) => {
                Some((name.id.as_str(), literal(value)?.is_truthy()))
            }
            _ => None,
        },
        _ => None,
    }
}

/// The single plain-name target of an assignment, if that is what it has.
fn single_name_target(assign: &ruff_python_ast::StmtAssign) -> Option<String> {
    match assign.targets.as_slice() {
        [Expr::Name(name)] => Some(name.id.to_string()),
        _ => None,
    }
}

/// Const-eval one expression, or `None` if it is not a literal this module admits.
///
/// This is the "const-eval: literals, tuples, lists; anything else flags" rule.  Note what is
/// *not* folded: `1 + 1 == 2` is a `Compare` over a `BinOp` and returns `None`, so
/// `@pytest.mark.skipif(1 + 1 == 2, ...)` routes its file to D.  Folding it would mean
/// reimplementing Python's numeric tower to be sure the fold is the value Python computes.
fn literal(expr: &Expr) -> Option<Lit> {
    match expr {
        Expr::NoneLiteral(_) => Some(Lit::None),
        Expr::BooleanLiteral(value) => Some(Lit::Bool(value.value)),
        Expr::NumberLiteral(number) => match &number.value {
            ruff_python_ast::Number::Int(value) => Some(Lit::Int {
                negative: false,
                // `Int::as_u64` is `None` for a value that did not fit a `u64` — ruff keeps the
                // raw token for those, not the decimal value, so `str()` is not recoverable.
                magnitude: value.as_u64()?,
            }),
            // Floats and complex: see `Lit`.
            _ => None,
        },
        Expr::StringLiteral(string) => {
            // An implicitly concatenated string is still one value, and `to_str` joins it.
            Some(Lit::Str(string.value.to_str().to_string()))
        }
        Expr::UnaryOp(unary) => {
            let inner = literal(&unary.operand)?;
            match (unary.op, inner) {
                (
                    ruff_python_ast::UnaryOp::USub,
                    Lit::Int {
                        negative: false,
                        magnitude,
                    },
                ) => Some(Lit::Int {
                    negative: true,
                    magnitude,
                }),
                (
                    ruff_python_ast::UnaryOp::UAdd,
                    Lit::Int {
                        negative,
                        magnitude,
                    },
                ) => Some(Lit::Int {
                    negative,
                    magnitude,
                }),
                _ => None,
            }
        }
        Expr::Tuple(tuple) => tuple
            .elts
            .iter()
            .map(literal)
            .collect::<Option<_>>()
            .map(Lit::Seq),
        Expr::List(list) => list
            .elts
            .iter()
            .map(literal)
            .collect::<Option<_>>()
            .map(Lit::Seq),
        Expr::Dict(dict) => {
            let mut items = Vec::with_capacity(dict.items.len());
            for item in &dict.items {
                let key = match item.key.as_ref() {
                    Some(Expr::StringLiteral(key)) => key.value.to_str().to_string(),
                    // A non-string key would need `str(key)` on the wire and `**{...}`
                    // unpacking has no key at all.
                    _ => return None,
                };
                items.push((key, literal(&item.value)?));
            }
            Some(Lit::Dict(items))
        }
        _ => None,
    }
}

/// Roots that are in `sys.modules` **before** any test module is imported, and therefore
/// cannot reach the filesystem at all.
///
/// `_v2_worker.py::install_pytest_shim` assigns `sys.modules["pytest"]` and
/// `sys.modules["pytest_asyncio"]` in `main()`, before the protocol loop starts; `rustest` is
/// the package the worker itself lives in. An `import` of any of them is a dictionary lookup,
/// so no shadowing check applies — a local `pytest.py` is never consulted.
const PREIMPORTED_ROOTS: [&str; 3] = ["pytest", "rustest", "pytest_asyncio"];

/// Standard-library roots Tier S accepts as import-safe.
///
/// **Why an allowlist at all, and why this one.** The module docs give the rule: an import
/// that can raise is a *collection error*, and Tier S must never answer for a file pytest
/// reports as broken. `import numpy` can raise `ModuleNotFoundError`; `import json` cannot,
/// because the standard library is part of the interpreter that is already running.
///
/// **Why it is hand-written rather than derived.** `ruff_python_stdlib::sys::
/// is_known_standard_library` is the obvious source and is the wrong one: at the pinned
/// revision its table is a *historical union* — it answers `true` for `distutils`, `telnetlib`
/// and `imp`, all removed from the versions this project supports — so trusting it would
/// allowlist imports that really do raise. A union is safe for ruff's purposes (classifying
/// imports for lint ordering) and unsafe for this one.
///
/// **What is deliberately absent.** Every module whose import depends on an optional C
/// extension the interpreter may have been built without — `ssl`, `sqlite3`, `lzma`, `bz2`,
/// `zlib`, `gzip`, `ctypes`, `curses`, `tkinter`, `readline`, `dbm`, `zoneinfo`,
/// `multiprocessing`. Those raise `ImportError` on a stripped or minimal build, which is
/// precisely the failure this list exists to exclude.
///
/// The list is checked, not asserted: `python/tests/test_v2_static_tier.py::
/// test_the_stdlib_allowlist_is_importable_and_actually_stdlib` reads it back through
/// [`crate::v2::py::v2_static_stdlib_allowlist`] and, on every interpreter CI runs, imports
/// each entry and confirms its file sits inside `sysconfig`'s stdlib directory. A name that
/// stops being stdlib fails there rather than becoming a silent false negative.
const STDLIB_ALLOWLIST: [&str; 92] = [
    // A future statement is a compiler directive; the module behind it ships with the
    // interpreter and binds only a `_Feature` object, which no naming pattern collects.
    "__future__",
    "abc",
    "argparse",
    "array",
    "ast",
    "asyncio",
    "base64",
    "binascii",
    "bisect",
    "calendar",
    "cmath",
    "codecs",
    "collections",
    "configparser",
    "contextlib",
    "contextvars",
    "copy",
    "csv",
    "dataclasses",
    "datetime",
    "decimal",
    "difflib",
    "dis",
    "email",
    "enum",
    "errno",
    "filecmp",
    "fileinput",
    "fnmatch",
    "fractions",
    "functools",
    "gc",
    "getpass",
    "glob",
    "hashlib",
    "heapq",
    "hmac",
    "html",
    "http",
    "importlib",
    "inspect",
    "io",
    "ipaddress",
    "itertools",
    "json",
    "keyword",
    "linecache",
    "locale",
    "logging",
    "math",
    "mimetypes",
    "numbers",
    "operator",
    "os",
    "pathlib",
    "pickle",
    "pkgutil",
    "platform",
    "posixpath",
    "pprint",
    "queue",
    "random",
    "re",
    "reprlib",
    "secrets",
    "shlex",
    "shutil",
    "signal",
    "stat",
    "statistics",
    "string",
    "struct",
    "subprocess",
    "sys",
    "sysconfig",
    "tempfile",
    "textwrap",
    "threading",
    "time",
    "timeit",
    "token",
    "tokenize",
    "tomllib",
    "traceback",
    "types",
    "typing",
    "unittest",
    "urllib",
    "uuid",
    "warnings",
    "weakref",
    "xml",
];

/// The allowlist, for the Python oracle test that keeps it honest.
pub fn stdlib_allowlist() -> &'static [&'static str] {
    &STDLIB_ALLOWLIST
}

/// Which reason a refused statement kind reports.  Definitions nested in control flow are
/// [`Reason::ConditionalDef`]; everything else is a side effect.
fn statement_reason(statement: &Stmt) -> Reason {
    match statement {
        Stmt::If(node) => {
            if body_defines(&node.body)
                || node
                    .elif_else_clauses
                    .iter()
                    .any(|clause| body_defines(&clause.body))
            {
                Reason::ConditionalDef
            } else {
                Reason::ModuleSideEffect
            }
        }
        Stmt::Try(node) => {
            if body_defines(&node.body) || body_defines(&node.orelse) {
                Reason::ConditionalDef
            } else {
                Reason::ModuleSideEffect
            }
        }
        Stmt::For(node) => {
            if body_defines(&node.body) {
                Reason::ConditionalDef
            } else {
                Reason::ModuleSideEffect
            }
        }
        Stmt::While(node) => {
            if body_defines(&node.body) {
                Reason::ConditionalDef
            } else {
                Reason::ModuleSideEffect
            }
        }
        _ => Reason::ModuleSideEffect,
    }
}

fn body_defines(body: &[Stmt]) -> bool {
    body.iter()
        .any(|statement| matches!(statement, Stmt::FunctionDef(_) | Stmt::ClassDef(_)))
}

/// A short, stable description of an expression for the `detail` string.
///
/// A call is rendered `name(...)`, so an `exec(...)` or `eval(...)` at module level names
/// itself in the refusal.  There is deliberately no separate reason for those two: the
/// statement allowlist refuses *every* module-level call, and a rule that singles out two of
/// them would suggest the others were considered safe.
fn describe(expr: &Expr) -> String {
    match expr {
        Expr::Name(name) => name.id.to_string(),
        Expr::Attribute(attribute) => format!("{}.{}", describe(&attribute.value), attribute.attr),
        Expr::Call(call) => format!("{}(...)", describe(&call.func)),
        Expr::StringLiteral(_) => "<str>".to_string(),
        Expr::NumberLiteral(_) => "<number>".to_string(),
        Expr::List(_) => "[...]".to_string(),
        Expr::Tuple(_) => "(...)".to_string(),
        Expr::Dict(_) => "{...}".to_string(),
        Expr::Lambda(_) => "<lambda>".to_string(),
        Expr::Compare(_) => "<comparison>".to_string(),
        Expr::BinOp(_) => "<binop>".to_string(),
        _ => "<expression>".to_string(),
    }
}

fn describe_stmt(statement: &Stmt) -> &'static str {
    match statement {
        Stmt::If(_) => "if",
        Stmt::For(_) => "for",
        Stmt::While(_) => "while",
        Stmt::With(_) => "with",
        Stmt::Try(_) => "try",
        Stmt::Match(_) => "match",
        Stmt::Raise(_) => "raise",
        Stmt::Assert(_) => "assert",
        Stmt::Delete(_) => "del",
        Stmt::Global(_) => "global",
        Stmt::Nonlocal(_) => "nonlocal",
        Stmt::AugAssign(_) => "augmented assignment",
        Stmt::AnnAssign(_) => "annotated assignment",
        Stmt::Return(_) => "return",
        Stmt::TypeAlias(_) => "type alias",
        _ => "statement",
    }
}

/// The naming patterns a conftest is analysed under.
///
/// A conftest defines fixtures, not tests, so the patterns are never consulted for collection
/// — but [`Scan::assignment_is_safe`] and [`Scan::bind_import`] read them, and pytest's
/// defaults are the right choice there: they are what decides whether a *test file's* names
/// are collectible, and a conftest is not a test file whatever the project configured.
fn conftest_scan_config() -> ResolvedConfig {
    ResolvedConfig {
        rootdir: PathBuf::new(),
        config_file: None,
        testpaths: Vec::new(),
        python_files: owned(crate::v2::config::DEFAULT_PYTHON_FILES),
        python_classes: owned(crate::v2::config::DEFAULT_PYTHON_CLASSES),
        python_functions: owned(crate::v2::config::DEFAULT_PYTHON_FUNCTIONS),
        norecursedirs: Vec::new(),
        addopts: Vec::new(),
        pythonpath: Vec::new(),
        markers: Vec::new(),
        asyncio_mode: DEFAULT_ASYNCIO_MODE.to_string(),
        asyncio_default_fixture_loop_scope: None,
        asyncio_default_test_loop_scope: DEFAULT_ASYNCIO_TEST_LOOP_SCOPE.to_string(),
    }
}

fn owned(items: &[&str]) -> Vec<String> {
    items.iter().map(|item| (*item).to_string()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config() -> ResolvedConfig {
        conftest_scan_config()
    }

    /// No file in this run shadows a standard-library name.  The shadowing rule has its own
    /// tests below; every other test here is about the module body, not about sys.path.
    fn no_shadows() -> HashSet<String> {
        HashSet::new()
    }

    /// Ids only — what pytest prints and what `-k` selects on.
    fn ids(source: &str) -> Vec<String> {
        scan_module(source, "test_a.py", &config(), &no_shadows())
            .expect("expected a static answer")
            .into_iter()
            .map(|test| test.id)
            .collect()
    }

    fn refusal(source: &str) -> Reason {
        scan_module(source, "test_a.py", &config(), &no_shadows())
            .expect_err("expected a dynamism flag")
            .reason
    }

    // -- extraction ------------------------------------------------------

    #[test]
    fn collects_module_level_test_functions_in_source_order() {
        assert_eq!(
            ids("def test_b():\n    pass\n\n\ndef test_a():\n    pass\n"),
            ["test_a.py::test_b", "test_a.py::test_a"]
        );
    }

    /// `_v2_worker.py::_is_test_function` is a **prefix** test first, so `testfoo` collects
    /// under the default `python_functions = ["test"]` (corpus `collection/naming-testfoo`).
    #[test]
    fn the_naming_rule_is_prefix_first() {
        assert_eq!(
            ids("def testfoo():\n    pass\n\n\ndef _test_hidden():\n    pass\n"),
            ["test_a.py::testfoo"]
        );
    }

    #[test]
    fn async_defs_are_tests() {
        assert_eq!(
            ids("async def test_async():\n    pass\n"),
            ["test_a.py::test_async"]
        );
    }

    /// A nested `def` is invisible to `vars(module)` (corpus `collection/nested-function`).
    #[test]
    fn a_nested_function_is_not_collected() {
        assert_eq!(
            ids("def test_outer():\n    def test_inner():\n        pass\n"),
            ["test_a.py::test_outer"]
        );
    }

    #[test]
    fn collects_test_classes_and_their_methods() {
        assert_eq!(
            ids("class TestBox:\n    def test_method(self):\n        pass\n"),
            ["test_a.py::TestBox::test_method"]
        );
    }

    /// `class Helper` fails `python_classes`; pytest never looks inside it.
    #[test]
    fn a_non_matching_class_is_skipped_whole() {
        assert_eq!(
            ids("class Helper:\n    def test_ignored(self):\n        pass\n"),
            Vec::<String>::new()
        );
    }

    /// `_hasinit` — pytest warns and collects nothing, **without** failing the run.
    #[test]
    fn a_class_with_a_constructor_collects_nothing() {
        assert_eq!(
            ids("class TestWithInit:\n    def __init__(self):\n        pass\n\n    def test_x(self):\n        pass\n"),
            Vec::<String>::new()
        );
        assert_eq!(
            ids("class TestWithNew:\n    def __new__(cls):\n        pass\n\n    def test_x(self):\n        pass\n"),
            Vec::<String>::new()
        );
    }

    #[test]
    fn an_explicit_object_base_is_still_static() {
        assert_eq!(
            ids("class TestBox(object):\n    def test_method(self):\n        pass\n"),
            ["test_a.py::TestBox::test_method"]
        );
    }

    #[test]
    fn nested_test_classes_nest_the_nodeid() {
        assert_eq!(
            ids("class TestOuter:\n    class TestInner:\n        def test_deep(self):\n            pass\n"),
            ["test_a.py::TestOuter::TestInner::test_deep"]
        );
    }

    /// `_fixture_names`: the bound-method first argument is dropped, and a parameter with a
    /// default is not a fixture request.
    #[test]
    fn fixtures_are_the_signature_minus_self_and_defaults() {
        let tests = scan_module(
            "def test_top(tmp_path, flag=1, *, capsys):\n    pass\n\n\nclass TestBox:\n    def test_m(self, monkeypatch):\n        pass\n",
            "test_a.py",
            &config(),
            &no_shadows(),
        )
        .unwrap();
        assert_eq!(tests[0].fixtures, ["tmp_path", "capsys"]);
        assert_eq!(tests[1].fixtures, ["monkeypatch"]);
    }

    /// pytest decides "drop the first argument" with `inspect.getattr_static(cls, name)`, not
    /// by looking for a parameter named `self` — so a `@staticmethod` keeps its first
    /// parameter and a method written `def test_m(this)` loses `this`.
    #[test]
    fn a_staticmethod_keeps_its_first_parameter() {
        let tests = scan_module(
            "class TestBox:\n    @staticmethod\n    def test_s(tmp_path):\n        pass\n\n    def test_m(this, tmp_path):\n        pass\n",
            "test_a.py",
            &config(),
            &no_shadows(),
        )
        .unwrap();
        assert_eq!(tests[0].fixtures, ["tmp_path"]);
        assert_eq!(tests[1].fixtures, ["tmp_path"]);
    }

    #[test]
    fn qualname_and_class_name_follow_the_class_chain() {
        let tests = scan_module(
            "class TestOuter:\n    class TestInner:\n        def test_deep(self):\n            pass\n",
            "test_a.py",
            &config(),
            &no_shadows(),
        )
        .unwrap();
        assert_eq!(tests[0].qualname, "TestOuter.TestInner.test_deep");
        assert_eq!(tests[0].class_name.as_deref(), Some("TestOuter.TestInner"));
    }

    #[test]
    fn a_fixture_named_like_a_test_is_not_a_test() {
        assert_eq!(
            ids("import pytest\n\n\n@pytest.fixture\ndef test_looking_fixture():\n    return 1\n"),
            Vec::<String>::new()
        );
    }

    // -- parametrize -----------------------------------------------------

    #[test]
    fn literal_parametrize_expands_with_v1_ids() {
        assert_eq!(
            ids("import pytest\n\n\n@pytest.mark.parametrize(\"value\", [1, 2, 3])\ndef test_value(value):\n    pass\n"),
            [
                "test_a.py::test_value[1]",
                "test_a.py::test_value[2]",
                "test_a.py::test_value[3]"
            ]
        );
    }

    #[test]
    fn explicit_ids_win() {
        assert_eq!(
            ids("import pytest\n\n\n@pytest.mark.parametrize(\"v\", [1, 2], ids=[\"one\", \"two\"])\ndef test_named(v):\n    pass\n"),
            ["test_a.py::test_named[one]", "test_a.py::test_named[two]"]
        );
    }

    /// Corpus `parametrize/stacking`: the **bottom** decorator is applied first, so it is the
    /// outer dimension and its id component comes first.
    #[test]
    fn stacked_parametrize_crosses_bottom_decorator_first() {
        assert_eq!(
            ids("import pytest\n\n\n@pytest.mark.parametrize(\"a\", [1, 2])\n@pytest.mark.parametrize(\"b\", [\"x\", \"y\"])\ndef test_grid(a, b):\n    pass\n"),
            [
                "test_a.py::test_grid[x-1]",
                "test_a.py::test_grid[x-2]",
                "test_a.py::test_grid[y-1]",
                "test_a.py::test_grid[y-2]"
            ]
        );
    }

    #[test]
    fn a_tuple_value_set_binds_every_name_and_joins_the_id() {
        assert_eq!(
            ids("import pytest\n\n\n@pytest.mark.parametrize(\"a,b\", [(1, \"x\"), (2, \"y\")])\ndef test_pair(a, b):\n    pass\n"),
            ["test_a.py::test_pair[1-x]", "test_a.py::test_pair[2-y]"]
        );
    }

    /// The parametrized names are **not** reported as fixtures — they come from the
    /// decorator, not from a fixture.
    #[test]
    fn parametrized_names_are_not_fixtures() {
        let tests = scan_module(
            "import pytest\n\n\n@pytest.mark.parametrize(\"v\", [1])\ndef test_x(v, tmp_path):\n    pass\n",
            "test_a.py",
            &config(),
            &no_shadows(),
        )
        .unwrap();
        assert_eq!(tests[0].fixtures, ["tmp_path"]);
    }

    /// `_unique_parameterset_ids`: the underscore appears only when the id ends in a digit.
    #[test]
    fn duplicate_ids_get_pytests_suffixes() {
        assert_eq!(
            ids("import pytest\n\n\n@pytest.mark.parametrize(\"v\", [1, 1])\ndef test_x(v):\n    pass\n"),
            ["test_a.py::test_x[1_0]", "test_a.py::test_x[1_1]"]
        );
        assert_eq!(
            ids("import pytest\n\n\n@pytest.mark.parametrize(\"v\", [\"a\", \"a\"])\ndef test_x(v):\n    pass\n"),
            ["test_a.py::test_x[a0]", "test_a.py::test_x[a1]"]
        );
    }

    /// The pathological id shapes `src/v2/nodeid.rs` pins: an empty id keeps its brackets, and
    /// an id may contain `]`, `[` or `::`.
    #[test]
    fn pathological_ids_survive_verbatim() {
        assert_eq!(
            ids("import pytest\n\n\n@pytest.mark.parametrize(\"s\", [\"\", \"a\"])\ndef test_x(s):\n    pass\n"),
            ["test_a.py::test_x[]", "test_a.py::test_x[a]"]
        );
        assert_eq!(
            ids("import pytest\n\n\n@pytest.mark.parametrize(\"s\", [1], ids=[\"p]q\"])\ndef test_x(s):\n    pass\n"),
            ["test_a.py::test_x[p]q]"]
        );
        assert_eq!(
            ids("import pytest\n\n\n@pytest.mark.parametrize(\"s\", [1], ids=[\"trail[\"])\ndef test_y(s):\n    pass\n"),
            ["test_a.py::test_y[trail[]"]
        );
        assert_eq!(
            ids("import pytest\n\n\n@pytest.mark.parametrize(\"s\", [1], ids=[\"a::b\"])\ndef test_z(s):\n    pass\n"),
            ["test_a.py::test_z[a::b]"]
        );
    }

    /// `decorators.py::_generate_param_id`'s own branches, which are **v1's** and not
    /// `IdMaker`'s: `None`/bools spell out, a long string truncates at 17 + `...`, an empty
    /// container is `empty`, and a container over three items gains `-...(n)`.
    #[test]
    fn id_generation_branches_match_pytests_idmaker() {
        assert_eq!(
            ids("import pytest


@pytest.mark.parametrize(\"v\", [None, True, False, -3])
def test_x(v):
    pass
"),
            [
                "test_a.py::test_x[None]",
                "test_a.py::test_x[True]",
                "test_a.py::test_x[False]",
                "test_a.py::test_x[-3]"
            ]
        );
        // Kept whole: pytest ascii-escapes and does not truncate. v1 cut at 17 and appended
        // `...`, which is what 95 of jinja2's ids differed by.
        assert_eq!(
            ids("import pytest


@pytest.mark.parametrize(\"v\", [\"abcdefghijklmnopqrstuvwxyz\"])
def test_x(v):
    pass
"),
            ["test_a.py::test_x[abcdefghijklmnopqrstuvwxyz]"]
        );
        // A container has no id of its own -- `<argname><index>`, not `empty`/`1-2-3-...(4)`.
        assert_eq!(
            ids("import pytest


@pytest.mark.parametrize(\"v\", [[], [1, 2, 3, 4]])
def test_x(v):
    pass
"),
            ["test_a.py::test_x[v0]", "test_a.py::test_x[v1]"]
        );
    }

    #[test]
    fn a_class_level_parametrize_is_the_outer_dimension() {
        // pytest's spelling and pytest's order, since Phase 4 Task 1's review: the
        // **method** component leads the id and the method dimension varies slowest.
        // Measured on pytest 8.4.2 with `[1, 2]` on the class and `[10, 20]` on the method.
        assert_eq!(
            ids("import pytest


@pytest.mark.parametrize(\"x\", [1, 2])
class TestBox:
    @pytest.mark.parametrize(\"y\", [10, 20])
    def test_m(self, x, y):
        pass
"),
            [
                "test_a.py::TestBox::test_m[10-1]",
                "test_a.py::TestBox::test_m[10-2]",
                "test_a.py::TestBox::test_m[20-1]",
                "test_a.py::TestBox::test_m[20-2]"
            ]
        );
    }

    // -- marks -----------------------------------------------------------

    #[test]
    fn a_bare_custom_mark_is_name_only() {
        let tests = scan_module(
            "import pytest\n\n\n@pytest.mark.smoke\ndef test_x():\n    pass\n",
            "test_a.py",
            &config(),
            &no_shadows(),
        )
        .unwrap();
        assert_eq!(tests[0].marks.len(), 1);
        assert_eq!(tests[0].marks[0].name, "smoke");
        assert!(tests[0].marks[0].args.is_empty());
        assert!(tests[0].marks[0].kwargs.is_empty());
    }

    /// Decorators are applied bottom-up, so `__rustest_marks__` records the bottom one first.
    #[test]
    fn mark_order_is_bottom_decorator_first_then_class_then_module() {
        let tests = scan_module(
            "import pytest\n\npytestmark = pytest.mark.mod\n\n\n@pytest.mark.outer\n@pytest.mark.inner\nclass TestBox:\n    @pytest.mark.top\n    @pytest.mark.bottom\n    def test_m(self):\n        pass\n",
            "test_a.py",
            &config(),
            &no_shadows(),
        )
        .unwrap();
        let names: Vec<&str> = tests[0]
            .marks
            .iter()
            .map(|mark| mark.name.as_str())
            .collect();
        assert_eq!(names, ["bottom", "top", "inner", "outer", "mod"]);
    }

    /// `_xfail` always fills all four keywords, whatever the user wrote — the wire form is the
    /// factory's output, not the call's arguments.
    #[test]
    fn xfail_carries_the_factorys_default_keywords() {
        let tests = scan_module(
            "import pytest\n\n\n@pytest.mark.xfail(reason=\"known broken\")\ndef test_x():\n    pass\n",
            "test_a.py",
            &config(),
            &no_shadows(),
        )
        .unwrap();
        let mark = &tests[0].marks[0];
        assert_eq!(mark.name, "xfail");
        assert!(mark.args.is_empty());
        assert_eq!(mark.kwargs["reason"], serde_json::json!("known broken"));
        assert_eq!(mark.kwargs["raises"], serde_json::Value::Null);
        assert_eq!(mark.kwargs["run"], serde_json::json!(true));
        assert_eq!(mark.kwargs["strict"], serde_json::json!(false));
    }

    /// The bare form is `MarkDecorator(name, (), {})` for **every** mark, factory or not —
    /// empty args is what makes a bare `skipif` an unconditional skip.
    #[test]
    fn a_bare_factory_mark_has_no_args_and_no_kwargs() {
        for source in [
            "import pytest\n\n\n@pytest.mark.xfail\ndef test_x():\n    pass\n",
            "import pytest\n\n\n@pytest.mark.skipif\ndef test_x():\n    pass\n",
        ] {
            let tests = scan_module(source, "test_a.py", &config(), &no_shadows()).unwrap();
            assert!(tests[0].marks[0].args.is_empty(), "{source}");
            assert!(tests[0].marks[0].kwargs.is_empty(), "{source}");
        }
    }

    #[test]
    fn module_pytestmark_applies_to_every_test() {
        let tests = scan_module(
            "import pytest\n\npytestmark = [pytest.mark.slow, pytest.mark.smoke]\n\n\ndef test_x():\n    pass\n",
            "test_a.py",
            &config(),
            &no_shadows(),
        )
        .unwrap();
        let names: Vec<&str> = tests[0]
            .marks
            .iter()
            .map(|mark| mark.name.as_str())
            .collect();
        assert_eq!(names, ["slow", "smoke"]);
    }

    /// pytest unpacks a `list` and *only* a list: a tuple is appended whole and then fails
    /// `normalize_mark_list` (`_pytest/mark/structures.py` l. 427/450-453) with
    /// `TypeError: got (...) instead of Mark`.  Tier S used to unpack tuples, which made it
    /// the one surface that accepted a shape pytest and the v2 worker both refuse.
    #[test]
    fn a_tuple_pytestmark_is_refused_the_way_pytest_refuses_it() {
        assert_eq!(
            refusal("import pytest\n\npytestmark = (pytest.mark.slow, pytest.mark.smoke)\n\n\ndef test_x():\n    pass\n"),
            Reason::NonLiteralPytestmark
        );
    }

    // -- dynamism --------------------------------------------------------

    #[test]
    fn star_imports_flag() {
        assert_eq!(refusal("from pytest import *\n"), Reason::StarImport);
    }

    /// The one rule that keeps a *missing dependency* from becoming a silently-shorter
    /// manifest: an import that can raise is a collection error, and only Tier D writes those.
    #[test]
    fn a_foreign_import_flags() {
        assert_eq!(refusal("import numpy\n"), Reason::ForeignImport);
        assert_eq!(
            refusal("from helpers import thing\n"),
            Reason::ForeignImport
        );
        assert_eq!(refusal("from . import sibling\n"), Reason::ForeignImport);
        // Optional-C-extension stdlib modules are deliberately off the allowlist: they raise
        // `ImportError` on a build without the extension.
        assert_eq!(refusal("import sqlite3\n"), Reason::ForeignImport);
        assert_eq!(refusal("import ssl\n"), Reason::ForeignImport);
        // ...and so are the names ruff's historical-union table would have admitted.
        assert_eq!(refusal("import distutils\n"), Reason::ForeignImport);
        assert_eq!(refusal("import telnetlib\n"), Reason::ForeignImport);
    }

    /// A standard-library import is a *fact about the interpreter already running*, so it
    /// cannot raise and cannot change what the file collects.  This is the rule that decides
    /// whether Tier S ever fires on real code: without it every test file that says
    /// `import os` goes to a worker.
    #[test]
    fn an_allowlisted_stdlib_import_is_static() {
        assert_eq!(
            ids("import os\nimport sys\nfrom pathlib import Path\nimport xml.etree.ElementTree\n\n\ndef test_x():\n    pass\n"),
            ["test_a.py::test_x"]
        );
    }

    /// ...unless this run's own tree could *be* that module.  `_v2_worker.py::
    /// sys_path_root_for` puts a test file's directory on `sys.path`, so a `queue.py` beside
    /// it makes `import queue` user code — which can raise, and which no AST here has seen.
    #[test]
    fn a_shadowed_stdlib_import_flags() {
        let shadows: HashSet<String> = ["queue".to_string()].into_iter().collect();
        let source = "import queue\n\n\ndef test_x():\n    pass\n";

        assert!(scan_module(source, "test_a.py", &config(), &no_shadows()).is_ok());
        assert_eq!(
            scan_module(source, "test_a.py", &config(), &shadows)
                .unwrap_err()
                .reason,
            Reason::ForeignImport
        );
    }

    /// `pytest` and `rustest` are exempt from the shadowing rule, and the exemption is a fact
    /// about the worker rather than optimism: `install_pytest_shim` assigns
    /// `sys.modules["pytest"]` before the protocol loop starts, so the import never reaches
    /// the filesystem for a local `pytest.py` to win.
    #[test]
    fn a_preimported_root_is_not_subject_to_shadowing() {
        let shadows: HashSet<String> = ["pytest".to_string(), "rustest".to_string()]
            .into_iter()
            .collect();

        assert!(scan_module(
            "import pytest\n\n\ndef test_x():\n    pass\n",
            "test_a.py",
            &config(),
            &shadows
        )
        .is_ok());
    }

    /// The shadow set is derived from the directories that can reach `sys.path`, which is
    /// every target's parent and its ancestors up to rootdir — so a `types.py` two directories
    /// above a test file still counts.
    #[test]
    fn shadowing_names_covers_every_sys_path_reachable_directory() {
        let tmp = tempfile::TempDir::new().unwrap();
        let deep = tmp.path().join("a").join("b");
        std::fs::create_dir_all(&deep).unwrap();
        std::fs::write(tmp.path().join("types.py"), "").unwrap();
        std::fs::write(deep.join("queue.py"), "").unwrap();
        std::fs::write(deep.join("test_x.py"), "def test_x():\n    pass\n").unwrap();

        let names = shadowing_names(&[deep.join("test_x.py")], tmp.path());

        assert!(names.contains("types"), "{names:?}");
        assert!(names.contains("queue"), "{names:?}");
        // Directories shadow too — a namespace package needs no `__init__.py` (PEP 420).
        assert!(names.contains("a"), "{names:?}");
    }

    #[test]
    fn module_getattr_flags() {
        assert_eq!(
            refusal("def __getattr__(name):\n    return None\n"),
            Reason::ModuleGetattr
        );
    }

    #[test]
    fn a_module_level_call_flags() {
        assert_eq!(
            refusal("exec(\"def test_x(): pass\")\n"),
            Reason::ModuleSideEffect
        );
        assert_eq!(refusal("print(1)\n"), Reason::ModuleSideEffect);
        assert_eq!(
            refusal("import pytest\n\ncounter = pytest.something()\n"),
            Reason::ModuleSideEffect
        );
    }

    #[test]
    fn a_conditional_definition_flags() {
        assert_eq!(
            refusal("if True:\n    def test_x():\n        pass\n"),
            Reason::ConditionalDef
        );
        assert_eq!(
            refusal("for _ in range(2):\n    def test_x():\n        pass\n"),
            Reason::ConditionalDef
        );
        assert_eq!(
            refusal("try:\n    def test_x():\n        pass\nexcept Exception:\n    pass\n"),
            Reason::ConditionalDef
        );
    }

    /// Inherited methods are not resolvable from one file's AST, so any base but `object`
    /// flags — and a `unittest.TestCase` base says so by name, because `TestLoader` semantics
    /// are a tier of their own.
    #[test]
    fn a_class_base_other_than_object_flags() {
        assert_eq!(
            refusal("class TestBox(Base):\n    def test_m(self):\n        pass\n"),
            Reason::ClassBases
        );
        assert_eq!(
            refusal("import unittest\n\n\nclass TestLegacy(unittest.TestCase):\n    def test_m(self):\n        pass\n"),
            Reason::UnittestCase
        );
        assert_eq!(
            refusal("class TestMeta(metaclass=type):\n    def test_m(self):\n        pass\n"),
            Reason::ClassBases
        );
    }

    /// A `TestCase` subclass is collected **whatever its name is**, so the base check has to
    /// run before the `python_classes` filter or `class Legacy(TestCase)` would be dropped in
    /// silence rather than routed to D.
    #[test]
    fn a_non_matching_unittest_class_still_flags() {
        assert_eq!(
            refusal("class Legacy(TestCase):\n    def test_m(self):\n        pass\n"),
            Reason::UnittestCase
        );
    }

    #[test]
    fn an_unknown_decorator_flags() {
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.something\ndef test_x():\n    pass\n"),
            Reason::UnknownDecorator
        );
        assert_eq!(
            refusal("@functools.wraps\ndef test_x():\n    pass\n"),
            Reason::UnknownDecorator
        );
    }

    /// Corpus `marks/skip-and-skipif` writes `skipif(1 + 1 == 2, ...)`, which is exactly the
    /// const-eval boundary: a `Compare` over a `BinOp` is not a literal.
    #[test]
    fn a_non_literal_mark_argument_flags() {
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.mark.skipif(1 + 1 == 2, reason=\"x\")\ndef test_x():\n    pass\n"),
            Reason::NonLiteralMark
        );
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.mark.xfail(raises=ValueError)\ndef test_x():\n    pass\n"),
            Reason::NonLiteralMark
        );
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.mark.asyncio\nasync def test_x():\n    pass\n"),
            Reason::NonLiteralMark
        );
    }

    #[test]
    fn a_non_literal_parametrize_flags() {
        assert_eq!(
            refusal("import pytest\n\nCASES = [1, 2]\n\n\n@pytest.mark.parametrize(\"v\", CASES)\ndef test_x(v):\n    pass\n"),
            Reason::NonLiteralParametrize
        );
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.mark.parametrize(\"v\", [1], ids=str)\ndef test_x(v):\n    pass\n"),
            Reason::NonLiteralParametrize
        );
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.mark.parametrize(\"v\", [pytest.param(1, id=\"a\")])\ndef test_x(v):\n    pass\n"),
            Reason::NonLiteralParametrize
        );
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.mark.parametrize(\"v\", [\"x\"], indirect=True)\ndef test_x(v):\n    pass\n"),
            Reason::NonLiteralParametrize
        );
        // Floats: `str(1e16)` is `1e+16` in Python, and an id that differs by one byte is a
        // wrong nodeid.
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.mark.parametrize(\"v\", [1.5])\ndef test_x(v):\n    pass\n"),
            Reason::NonLiteralParametrize
        );
    }

    /// A parametrized fixture multiplies the ids of every test in its closure, including tests
    /// that never mention it.
    #[test]
    fn a_parametrized_fixture_in_the_file_flags() {
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.fixture(params=[1, 2])\ndef number(request):\n    return request.param\n\n\ndef test_n(number):\n    pass\n"),
            Reason::ParametrizedFixture
        );
    }

    #[test]
    fn a_plain_fixture_in_the_file_is_fine() {
        assert_eq!(
            ids("import pytest\n\n\n@pytest.fixture\ndef value():\n    return 1\n\n\n@pytest.fixture(scope=\"module\")\ndef shared():\n    return 2\n\n\ndef test_x(value, shared):\n    pass\n"),
            ["test_a.py::test_x"]
        );
    }

    #[test]
    fn a_generator_test_flags() {
        assert_eq!(
            refusal("def test_x():\n    yield 1\n"),
            Reason::GeneratorTest
        );
        assert_eq!(
            refusal("async def test_x():\n    yield 1\n"),
            Reason::GeneratorTest
        );
    }

    #[test]
    fn a_yield_inside_a_nested_def_belongs_to_that_def() {
        assert_eq!(
            ids("def test_x():\n    def inner():\n        yield 1\n\n    return None\n"),
            ["test_a.py::test_x"]
        );
    }

    #[test]
    fn a_test_attribute_veto_flags() {
        assert_eq!(refusal("__test__ = False\n"), Reason::TestAttribute);
        assert_eq!(
            refusal(
                "class TestBox:\n    __test__ = False\n\n    def test_m(self):\n        pass\n"
            ),
            Reason::TestAttribute
        );
    }

    #[test]
    fn pytest_plugins_flags() {
        assert_eq!(
            refusal("pytest_plugins = [\"myplugin\"]\n"),
            Reason::PytestPlugins
        );
    }

    /// `vars(module)` keeps the *first* insertion position and the *last* value, which is not
    /// a shape worth modelling for the sake of a pathological file.
    #[test]
    fn a_duplicate_top_level_name_flags() {
        assert_eq!(
            refusal("def test_x():\n    pass\n\n\ndef test_x():\n    pass\n"),
            Reason::DuplicateName
        );
    }

    /// `IGNORED_ATTRIBUTES` is an exact-name set, so a dunder that is *not* in it is
    /// collectible under a permissive `python_functions`.
    #[test]
    fn a_dunder_definition_flags() {
        assert_eq!(
            refusal("def __test_helper__():\n    pass\n"),
            Reason::DunderDefinition
        );
    }

    /// `collect_imported_tests` defaults to `True`, so an imported name that matches the
    /// patterns is a test in Tier D — and invisible to an AST that only sees `def`s.
    #[test]
    fn an_imported_name_matching_the_patterns_flags() {
        assert_eq!(
            refusal("from rustest import test_helper\n"),
            Reason::ImportedTestName
        );
    }

    #[test]
    fn a_syntax_error_flags_rather_than_inventing_pytests_message() {
        assert_eq!(refusal("def test_x(:\n    pass\n"), Reason::ParseError);
    }

    /// Renaming an import does not launder it.
    #[test]
    fn an_aliased_foreign_import_still_flags() {
        assert_eq!(refusal("import numpy as np\n"), Reason::ForeignImport);
    }

    #[test]
    fn the_allowlisted_imports_are_accepted_in_every_spelling() {
        for source in [
            "import pytest\n",
            "import rustest\n",
            "import pytest_asyncio\n",
            "from pytest import fixture\n",
            "from rustest import fixture, mark, parametrize\n",
            "import rustest.decorators\n",
            "import os\n",
            "from typing import Any\n",
            "import os.path as osp\n",
        ] {
            assert!(
                scan_module(source, "test_a.py", &config(), &no_shadows()).is_ok(),
                "{source} should be static"
            );
        }
    }

    /// A locally-shadowed `fixture` is not rustest's, and the duplicate-binding rule is what
    /// catches it before the decorator resolver can be fooled.
    #[test]
    fn a_locally_shadowed_decorator_name_flags() {
        assert_eq!(
            refusal("from rustest import fixture\n\n\ndef fixture():\n    pass\n"),
            Reason::DuplicateName
        );
    }

    // -- unit ports ------------------------------------------------------

    #[test]
    fn unique_parameterset_ids_matches_the_python_port() {
        let unique = |raw: &[&str]| {
            unique_parameterset_ids(raw.iter().map(|id| (*id).to_string()).collect::<Vec<_>>())
        };
        assert_eq!(unique(&["a", "b"]), ["a", "b"]);
        assert_eq!(unique(&["1", "1"]), ["1_0", "1_1"]);
        assert_eq!(unique(&["a", "a", "a"]), ["a0", "a1", "a2"]);
        // The `while new_id in set(resolved)` probe: `a0` already exists, so the first
        // suffixed `a` has to skip past it.
        assert_eq!(unique(&["a", "a", "a0"]), ["a1", "a2", "a0"]);
    }

    #[test]
    fn generate_param_id_matches_pytests_idmaker() {
        // pytest's `IdMaker._idval_from_value`, not v1's generator: a container has no id of
        // its own and falls back to `<argname><index>`, and a long string is kept whole.
        let id = |lit: Lit| generate_param_id(&lit, 7, "x").unwrap();
        assert_eq!(id(Lit::None), "None");
        assert_eq!(id(Lit::Bool(true)), "True");
        assert_eq!(
            id(Lit::Int {
                negative: true,
                magnitude: 0
            }),
            "0"
        );
        assert_eq!(id(Lit::Str("short".to_string())), "short");
        assert_eq!(id(Lit::Str("x".repeat(30))), "x".repeat(30));
        assert_eq!(id(Lit::Str("tab\there".to_string())), "tab\\there");
        assert_eq!(id(Lit::Str("uni\u{6e2c}".to_string())), "uni\\u6e2c");
        assert_eq!(id(Lit::Seq(Vec::new())), "x7");
        assert_eq!(id(Lit::Seq(vec![Lit::None])), "x7");
        assert_eq!(id(Lit::Dict(Vec::new())), "x7");
        assert_eq!(id(Lit::Dict(vec![("a".to_string(), Lit::None)])), "x7");
    }

    // -- conftest chain --------------------------------------------------

    #[test]
    fn a_parametrized_conftest_fixture_flags_the_whole_directory() {
        let tmp = tempfile::TempDir::new().unwrap();
        std::fs::write(
            tmp.path().join("conftest.py"),
            "import pytest\n\n\n@pytest.fixture(params=[1, 2])\ndef number(request):\n    return request.param\n",
        )
        .unwrap();

        let err = conftest_chain_is_static(tmp.path(), tmp.path(), &no_shadows()).unwrap_err();
        assert_eq!(err.reason, Reason::ParametrizedFixture);
    }

    /// A conftest that raises at import time is a *collection error for every file below it*,
    /// so import safety is checked, not just `params=`.
    #[test]
    fn an_unsafe_conftest_flags_the_whole_directory() {
        let tmp = tempfile::TempDir::new().unwrap();
        std::fs::write(tmp.path().join("conftest.py"), "import django\n").unwrap();

        let err = conftest_chain_is_static(tmp.path(), tmp.path(), &no_shadows()).unwrap_err();
        assert_eq!(err.reason, Reason::ConftestChain);
    }

    #[test]
    fn a_plain_conftest_is_accepted() {
        let tmp = tempfile::TempDir::new().unwrap();
        std::fs::write(
            tmp.path().join("conftest.py"),
            "import pytest\n\n\n@pytest.fixture\ndef shared_value():\n    return 42\n",
        )
        .unwrap();

        assert!(conftest_chain_is_static(tmp.path(), tmp.path(), &no_shadows()).is_ok());
    }

    /// The chain stops at rootdir (`_v2_worker.py::conftest_chain`'s confcutdir rule), so a
    /// conftest *above* rootdir neither helps nor flags.
    #[test]
    fn the_chain_stops_at_rootdir() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path().join("root");
        let sub = root.join("sub");
        std::fs::create_dir_all(&sub).unwrap();
        std::fs::write(tmp.path().join("conftest.py"), "import django\n").unwrap();

        assert!(conftest_chain_is_static(&sub, &root, &no_shadows()).is_ok());
    }

    // -- the whole-run rules ---------------------------------------------

    #[test]
    fn a_shared_stem_routes_every_copy_to_tier_d() {
        let tmp = tempfile::TempDir::new().unwrap();
        let a = tmp.path().join("a");
        let b = tmp.path().join("b");
        std::fs::create_dir_all(&a).unwrap();
        std::fs::create_dir_all(&b).unwrap();
        std::fs::write(a.join("test_dup.py"), "def test_one():\n    pass\n").unwrap();
        std::fs::write(b.join("test_dup.py"), "def test_two():\n    pass\n").unwrap();
        std::fs::write(
            tmp.path().join("test_solo.py"),
            "def test_three():\n    pass\n",
        )
        .unwrap();

        let targets = vec![
            a.join("test_dup.py"),
            b.join("test_dup.py"),
            tmp.path().join("test_solo.py"),
        ];
        let outcomes = static_pass(&targets, tmp.path(), &config());

        assert_eq!(
            outcomes[0].as_ref().unwrap_err().reason,
            Reason::StemCollision
        );
        assert_eq!(
            outcomes[1].as_ref().unwrap_err().reason,
            Reason::StemCollision
        );
        assert!(outcomes[2].is_ok());
    }

    // -- the manifest cache ----------------------------------------------
    //
    // The cache is the one place in Tier S where a bug is *silent*: a stale entry is a
    // manifest for a tree that no longer exists, delivered with exit 0.  So each of these
    // asserts the whole loop -- cold pass, change something, warm pass -- rather than a key.
    // The key-level mutation table lives in `manifest_cache.rs`.

    /// Run the cached pass over `targets` and return the outcomes plus the cache.
    fn cached_pass(
        targets: &[PathBuf],
        rootdir: &Path,
        config: &ResolvedConfig,
    ) -> (Vec<Result<Vec<CollectedTest>, Dynamic>>, ManifestCache) {
        let pass = static_pass_cached(targets, rootdir, config, CacheMode::Auto);
        let cache = pass.cache.expect("CacheMode::Auto opens a cache");
        (pass.outcomes, cache)
    }

    fn ids_of(outcome: &Result<Vec<CollectedTest>, Dynamic>) -> Vec<String> {
        outcome
            .as_ref()
            .expect("a static answer")
            .iter()
            .map(|test| test.id.clone())
            .collect()
    }

    /// The property the whole task exists for: a second pass over an unchanged tree answers
    /// identically and **hands nothing to the parser**.  `misses` is incremented at exactly
    /// the point a file is passed to `scan_module`, so `misses == 0` is that claim measured
    /// rather than argued.
    #[test]
    fn a_warm_pass_answers_from_the_cache_without_parsing_anything() {
        let tmp = tempfile::TempDir::new().unwrap();
        let mut targets = Vec::new();
        for index in 0..5 {
            let path = tmp.path().join(format!("test_{index}.py"));
            std::fs::write(&path, format!("def test_case_{index}():\n    pass\n")).unwrap();
            targets.push(path);
        }

        let (cold, cache) = cached_pass(&targets, tmp.path(), &config());
        assert_eq!(cache.stats().hits(), 0);
        assert_eq!(cache.stats().misses(), 5);
        assert_eq!(cache.stats().writes(), 1, "one shard for one directory");

        let (warm, cache) = cached_pass(&targets, tmp.path(), &config());
        assert_eq!(cache.stats().misses(), 0, "the warm pass parsed something");
        assert_eq!(cache.stats().hits(), 5);
        assert_eq!(
            cache.stats().writes(),
            0,
            "an unchanged shard was rewritten"
        );

        let cold_ids: Vec<_> = cold.iter().map(ids_of).collect();
        let warm_ids: Vec<_> = warm.iter().map(ids_of).collect();
        assert_eq!(cold_ids, warm_ids);
        // A served entry is a Tier S entry, which is what the differential's attribution reads.
        assert!(warm
            .iter()
            .flat_map(|outcome| outcome.as_ref().unwrap())
            .all(|test| test.tier == Tier::Static));
    }

    /// ...and the cached answer is the answer, not merely the same *count*: a whole-entry
    /// comparison against the uncached pass, which is the tier's own oracle here.
    #[test]
    fn a_cached_answer_equals_the_uncached_one_entry_for_entry() {
        let tmp = tempfile::TempDir::new().unwrap();
        let path = tmp.path().join("test_a.py");
        std::fs::write(
            &path,
            "import pytest\n\n\n@pytest.mark.slow\n@pytest.mark.parametrize(\"x\", [1, \"a\"])\nclass TestBox:\n    def test_m(self, x, tmp_path):\n        pass\n",
        )
        .unwrap();
        let targets = vec![path];

        let uncached = static_pass(&targets, tmp.path(), &config());
        let _ = cached_pass(&targets, tmp.path(), &config());
        let (warm, cache) = cached_pass(&targets, tmp.path(), &config());
        assert_eq!(cache.stats().hits(), 1);
        assert_eq!(warm[0].as_ref().unwrap(), uncached[0].as_ref().unwrap());
    }

    /// Editing a file invalidates that file and **only** that file.
    #[test]
    fn editing_a_file_invalidates_its_entry_and_no_others() {
        let tmp = tempfile::TempDir::new().unwrap();
        let a = tmp.path().join("test_a.py");
        let b = tmp.path().join("test_b.py");
        std::fs::write(&a, "def test_one():\n    pass\n").unwrap();
        std::fs::write(&b, "def test_two():\n    pass\n").unwrap();
        let targets = vec![a.clone(), b];
        let _ = cached_pass(&targets, tmp.path(), &config());

        std::fs::write(
            &a,
            "def test_one():\n    pass\n\n\ndef test_extra():\n    pass\n",
        )
        .unwrap();
        let (warm, cache) = cached_pass(&targets, tmp.path(), &config());
        assert_eq!(cache.stats().misses(), 1);
        assert_eq!(cache.stats().hits(), 1);
        assert_eq!(
            ids_of(&warm[0]),
            ["test_a.py::test_one", "test_a.py::test_extra"]
        );
    }

    /// A config change invalidates everything, because the config decides what a test *is*.
    #[test]
    fn a_config_change_invalidates_every_entry() {
        let tmp = tempfile::TempDir::new().unwrap();
        let path = tmp.path().join("test_a.py");
        std::fs::write(
            &path,
            "def test_one():\n    pass\n\n\ndef check_two():\n    pass\n",
        )
        .unwrap();
        let targets = vec![path];

        let (cold, _) = cached_pass(&targets, tmp.path(), &config());
        assert_eq!(ids_of(&cold[0]), ["test_a.py::test_one"]);

        let renamed = ResolvedConfig {
            python_functions: owned(&["check"]),
            ..config()
        };
        let (warm, cache) = cached_pass(&targets, tmp.path(), &renamed);
        assert_eq!(cache.stats().hits(), 0, "a config change was ignored");
        assert_eq!(ids_of(&warm[0]), ["test_a.py::check_two"]);
    }

    /// **The flagship stale-cache scenario, and the one the plan's three named components
    /// would have missed.**  A file whose `import queue` was safe is cached as static.  A
    /// `queue.py` then appears beside it: the file's bytes have not changed, the config has
    /// not changed, no conftest has changed -- and the correct answer has flipped, because
    /// `sys.path` now resolves `queue` to user code that can raise.  Without the shadow
    /// component in the key this is a static answer for a file pytest may report as broken.
    #[test]
    fn a_new_local_module_shadowing_the_stdlib_invalidates_a_cached_file() {
        let tmp = tempfile::TempDir::new().unwrap();
        let path = tmp.path().join("test_a.py");
        std::fs::write(&path, "import queue\n\n\ndef test_one():\n    pass\n").unwrap();
        let targets = vec![path];

        let (cold, _) = cached_pass(&targets, tmp.path(), &config());
        assert_eq!(ids_of(&cold[0]), ["test_a.py::test_one"]);

        std::fs::write(tmp.path().join("queue.py"), "raise RuntimeError\n").unwrap();
        let (warm, cache) = cached_pass(&targets, tmp.path(), &config());
        assert_eq!(cache.stats().hits(), 0, "a stale entry was served");
        assert_eq!(warm[0].as_ref().unwrap_err().reason, Reason::ForeignImport);
    }

    /// A conftest edit invalidates the whole directory.  Belt **and** braces: the chain
    /// analysis re-runs on every pass and routes the directory to Tier D on its own, and the
    /// chain digest independently moves every key in the directory -- so relaxing either rule
    /// later cannot quietly start serving entries written under the other.
    #[test]
    fn a_conftest_change_invalidates_the_directory() {
        let tmp = tempfile::TempDir::new().unwrap();
        let path = tmp.path().join("test_a.py");
        std::fs::write(&path, "def test_one():\n    pass\n").unwrap();
        std::fs::write(tmp.path().join("conftest.py"), "").unwrap();
        let targets = vec![path];

        let (cold, _) = cached_pass(&targets, tmp.path(), &config());
        assert!(cold[0].is_ok());

        // A harmless-looking edit that the chain analysis still admits: the key must move
        // anyway, which is the half this test is really about.
        std::fs::write(tmp.path().join("conftest.py"), "# a comment\n").unwrap();
        let (warm, cache) = cached_pass(&targets, tmp.path(), &config());
        assert_eq!(
            cache.stats().hits(),
            0,
            "a conftest edit did not move the key"
        );
        assert!(warm[0].is_ok());

        // ...and the edit that changes the answer routes the file to Tier D.
        std::fs::write(
            tmp.path().join("conftest.py"),
            "import pytest\n\n\n@pytest.fixture(params=[1, 2], autouse=True)\ndef p(request):\n    return request.param\n",
        )
        .unwrap();
        let (after, _) = cached_pass(&targets, tmp.path(), &config());
        assert_eq!(
            after[0].as_ref().unwrap_err().reason,
            Reason::ParametrizedFixture
        );
    }

    /// A **refusal** is never written.  Recomputing one costs a parse; the worker round trip
    /// it implies costs a thousand times that, so an entry would buy nothing and would be one
    /// more thing that can go stale.
    #[test]
    fn a_dynamism_refusal_is_not_cached() {
        let tmp = tempfile::TempDir::new().unwrap();
        let static_path = tmp.path().join("test_static.py");
        let dynamic_path = tmp.path().join("test_dynamic.py");
        std::fs::write(&static_path, "def test_one():\n    pass\n").unwrap();
        std::fs::write(
            &dynamic_path,
            "import numpy\n\n\ndef test_two():\n    pass\n",
        )
        .unwrap();
        let targets = vec![static_path, dynamic_path];

        let (cold, _) = cached_pass(&targets, tmp.path(), &config());
        assert!(cold[0].is_ok() && cold[1].is_err());

        let (_, cache) = cached_pass(&targets, tmp.path(), &config());
        assert_eq!(cache.stats().hits(), 1, "only the static file is cached");
        assert_eq!(cache.stats().misses(), 1, "the refusal is recomputed");
    }

    /// `CacheMode::Off` reads nothing and writes nothing -- the control leg, and what every
    /// other Tier S test in this module runs under.
    #[test]
    fn the_cache_can_be_turned_off_entirely() {
        let tmp = tempfile::TempDir::new().unwrap();
        let path = tmp.path().join("test_a.py");
        std::fs::write(&path, "def test_one():\n    pass\n").unwrap();
        let targets = vec![path];

        let pass = static_pass_cached(&targets, tmp.path(), &config(), CacheMode::Off);
        assert!(pass.cache.is_none());
        assert!(!tmp.path().join(".rustest_cache").exists());
        assert_eq!(ids_of(&pass.outcomes[0]), ["test_a.py::test_one"]);
    }

    #[test]
    fn the_cache_mode_wire_spelling_defaults_to_on() {
        assert_eq!(CacheMode::from_wire("off"), CacheMode::Off);
        assert_eq!(CacheMode::from_wire("auto"), CacheMode::Auto);
        // A typo in a debug knob is not a usage error.
        assert_eq!(CacheMode::from_wire("offf"), CacheMode::Auto);
        assert_eq!(CacheMode::default(), CacheMode::Auto);
    }

    /// Only the allowlisted names reach the shadow digest, so adding a test file does not
    /// invalidate the tree.  Without this filter every `test_*.py` stem would be in the set
    /// and the cache would be cold on every new test.
    #[test]
    fn adding_a_test_file_does_not_invalidate_the_other_entries() {
        let tmp = tempfile::TempDir::new().unwrap();
        let a = tmp.path().join("test_a.py");
        std::fs::write(&a, "def test_one():\n    pass\n").unwrap();
        let _ = cached_pass(std::slice::from_ref(&a), tmp.path(), &config());

        let b = tmp.path().join("test_b.py");
        std::fs::write(&b, "def test_two():\n    pass\n").unwrap();
        let (_, cache) = cached_pass(&[a, b], tmp.path(), &config());
        assert_eq!(cache.stats().hits(), 1, "the untouched file went cold");
        assert_eq!(cache.stats().misses(), 1);
    }

    #[test]
    fn markdown_targets_are_never_static() {
        let tmp = tempfile::TempDir::new().unwrap();
        let path = tmp.path().join("README.md");
        std::fs::write(&path, "```python\nassert True\n```\n").unwrap();

        let err = scan_path(&path, tmp.path(), &config(), &no_shadows()).unwrap_err();
        assert_eq!(err.reason, Reason::NotPythonSource);
    }

    // -- review round: the four detector gaps -----------------------------

    /// C1.  `**kwargs` on a fixture may *be* `params=`, and the keys are a runtime value.
    ///
    /// The bug this pins was silent by construction: the splat keyword has no `arg`, the code
    /// read that absence as the literal name `"**"`, compared it to `params`, found no match
    /// and returned a plain fixture. `@fixture(**{"params": [1, 2]})` then doubled every id in
    /// the directory while Tier S reported the single unparametrized one.
    #[test]
    fn a_splat_on_a_fixture_flags() {
        // A splat over a name is refused by the decorator rule, not by the binding: a literal
        // dict is a legal module-level assignment, so nothing else would have caught it.
        assert_eq!(
            refusal("import pytest\n\nOPTS = {\"params\": [1, 2]}\n\n\n@pytest.fixture(**OPTS)\ndef n(request):\n    return request.param\n\n\ndef test_n(n):\n    pass\n"),
            Reason::ParametrizedFixture
        );
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.fixture(**{\"params\": [1, 2]})\ndef n(request):\n    return request.param\n\n\ndef test_n(n):\n    pass\n"),
            Reason::ParametrizedFixture
        );
        // ...and a splat carrying something harmless is refused just the same, because the
        // keys are not knowable: over-refusal costs a worker round trip, under-refusal costs
        // the manifest.
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.fixture(**{\"scope\": \"module\"})\ndef n():\n    return 1\n\n\ndef test_n(n):\n    pass\n"),
            Reason::ParametrizedFixture
        );
    }

    /// C1, the directory-wide half: the same splat in a **conftest** must flag every file
    /// below it, which is the shape that actually loses ids (the test file mentions nothing).
    #[test]
    fn a_splat_on_a_conftest_fixture_flags_the_whole_directory() {
        let tmp = tempfile::TempDir::new().unwrap();
        std::fs::write(
            tmp.path().join("conftest.py"),
            "import pytest\n\n\n@pytest.fixture(**{\"autouse\": True})\ndef always():\n    return 1\n",
        )
        .unwrap();

        // `parametrized_fixture_call` runs ahead of the structural pass and names the hazard
        // directly, so the reason is `ParametrizedFixture` where the structural pass used to
        // produce the generic `ConftestChain` for the same file. Both refuse; this one tells
        // a reader *which* rule fired, which is the point of having a named reason at all.
        // The detail pins the splat rule specifically, rather than letting some other
        // conftest failure stand in for it.
        let err = conftest_chain_is_static(tmp.path(), tmp.path(), &no_shadows()).unwrap_err();
        assert_eq!(err.reason, Reason::ParametrizedFixture);
        assert!(err.detail.contains("**kwargs"), "{}", err.detail);
    }

    /// C2.  A PEP 263 cookie naming anything but UTF-8 means this module and the interpreter
    /// read the same bytes as different text — and parametrize ids are copied out of string
    /// literals, so the difference lands in a nodeid.
    #[test]
    fn a_non_utf8_encoding_cookie_flags() {
        for source in [
            "# -*- coding: latin-1 -*-\ndef test_x():\n    pass\n",
            "#!/usr/bin/env python\n# coding: cp1252\ndef test_x():\n    pass\n",
            "# vim: set fileencoding=iso-8859-15 :\ndef test_x():\n    pass\n",
            // `utf-8-sig` strips a BOM this module would keep.
            "# coding=utf-8-sig\ndef test_x():\n    pass\n",
        ] {
            assert_eq!(
                scan_module(source, "test_a.py", &config(), &no_shadows())
                    .expect_err(source)
                    .reason,
                Reason::EncodingCookie,
                "{source}"
            );
        }
    }

    /// ...and a UTF-8 cookie, in any of its spellings, is not a refusal — otherwise the rule
    /// would quietly disable the tier for the very many files that carry one.
    #[test]
    fn a_utf8_encoding_cookie_is_accepted() {
        for source in [
            "# -*- coding: utf-8 -*-\ndef test_x():\n    pass\n",
            "# coding: UTF8\ndef test_x():\n    pass\n",
            "#!/usr/bin/env python\n# -*- coding: utf_8 -*-\ndef test_x():\n    pass\n",
            // Line 3 is too late to be a cookie, so it is not one.
            "def test_x():\n    pass\n\n\n# coding: latin-1\n",
            // A `coding:` inside a *string* is not a comment and never was a cookie.
            "MESSAGE = \"coding: latin-1\"\n\n\ndef test_x():\n    pass\n",
        ] {
            assert!(
                scan_module(source, "test_a.py", &config(), &no_shadows()).is_ok(),
                "{source}"
            );
        }
    }

    /// I1.  `_hasinit` is `getattr(cls, "__init__") != object.__init__` — a question about the
    /// *name being bound*, not about how. Matching only `def` let Tier S collect a class both
    /// pytest and Tier D refuse.
    #[test]
    fn a_truthy_class_body_binding_of_dunder_init_collects_nothing() {
        for source in [
            "class TestBox:\n    __init__ = 5\n\n    def test_m(self):\n        pass\n",
            "class TestBox:\n    __new__ = 5\n\n    def test_m(self):\n        pass\n",
            "class TestBox:\n    __init__ = \"x\"\n\n    def test_m(self):\n        pass\n",
            "class TestBox:\n    __init__ = [1]\n\n    def test_m(self):\n        pass\n",
        ] {
            assert_eq!(
                scan_module(source, "test_a.py", &config(), &no_shadows()).unwrap(),
                Vec::new(),
                "{source}"
            );
        }
    }

    /// The other half of `bool(init) and init != object.__init__`, and the half a naive
    /// "does the body bind `__init__`?" rule gets wrong: a **falsy** binding leaves the class
    /// collectable.  Probed against pytest 8.4.2 — `None`, `0`, `""` and `[]` all collect.
    #[test]
    fn a_falsy_class_body_binding_of_dunder_init_still_collects() {
        for source in [
            "class TestBox:\n    __init__ = None\n\n    def test_m(self):\n        pass\n",
            "class TestBox:\n    __init__ = 0\n\n    def test_m(self):\n        pass\n",
            "class TestBox:\n    __init__ = \"\"\n\n    def test_m(self):\n        pass\n",
            "class TestBox:\n    __init__ = []\n\n    def test_m(self):\n        pass\n",
        ] {
            assert_eq!(
                scan_module(source, "test_a.py", &config(), &no_shadows())
                    .unwrap()
                    .into_iter()
                    .map(|test| test.id)
                    .collect::<Vec<_>>(),
                ["test_a.py::TestBox::test_m"],
                "{source}"
            );
        }
    }

    /// A bare annotation binds nothing, so the class *is* collected — but an annotated
    /// assignment in a class body flags for its own reason, so neither shape can reach a
    /// wrong answer.
    #[test]
    fn an_annotated_constructor_binding_never_produces_a_wrong_answer() {
        assert_eq!(
            refusal(
                "class TestBox:\n    __init__: int = 5\n\n    def test_m(self):\n        pass\n"
            ),
            Reason::ModuleSideEffect
        );
    }

    /// I2.  A negative integer below `i64::MIN` has no exact `serde_json` form. The old
    /// `as i64` cast wrapped it into a *positive* number of a different magnitude — a mark
    /// argument silently changed, and no nodeid moved to give it away.
    #[test]
    fn an_unrepresentable_integer_mark_argument_flags() {
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.mark.limit(-9223372036854775809)\ndef test_x():\n    pass\n"),
            Reason::NonLiteralMark
        );
    }

    /// The boundaries either side of it are exact, including `-(2^63)`, which is
    /// representable although `+(2^63)` is not.
    #[test]
    fn integer_mark_arguments_are_exact_to_the_i64_boundary() {
        let payload = |source: &str| {
            scan_module(source, "test_a.py", &config(), &no_shadows()).unwrap()[0].marks[0]
                .args
                .clone()
        };
        assert_eq!(
            payload("import pytest\n\n\n@pytest.mark.limit(-9223372036854775808)\ndef test_x():\n    pass\n"),
            vec![serde_json::json!(i64::MIN)]
        );
        assert_eq!(
            payload("import pytest\n\n\n@pytest.mark.limit(9223372036854775807)\ndef test_x():\n    pass\n"),
            vec![serde_json::json!(i64::MAX)]
        );
        // A positive magnitude above `i64::MAX` is still exact — `u64` covers it.
        assert_eq!(
            payload("import pytest\n\n\n@pytest.mark.limit(18446744073709551615)\ndef test_x():\n    pass\n"),
            vec![serde_json::json!(u64::MAX)]
        );
    }

    /// The refusal reaches nested payloads too, not just top-level arguments.
    #[test]
    fn an_unrepresentable_integer_inside_a_container_flags() {
        assert_eq!(
            refusal("import pytest\n\n\n@pytest.mark.limit(values=[1, -9223372036854775809])\ndef test_x():\n    pass\n"),
            Reason::NonLiteralMark
        );
    }

    // -- the assertion-rewrite plan ---------------------------------------

    /// Diagnostic: how much of *this repository's own* suite is rewrite-eligible, and why the
    /// rest is not.
    ///
    /// `#[ignore]`d because it reads the working tree rather than a fixture, so it is a
    /// measurement rather than an assertion — but it lives here, in the suite, because the
    /// number it produces is the one the reach question is actually about and it should be
    /// re-runnable rather than re-derived by hand.
    ///
    /// `cargo test --lib rewrite_reach -- --ignored --nocapture`
    #[test]
    #[ignore = "diagnostic: prints rewrite reach over this repository's own suite"]
    fn rewrite_reach_over_this_repository() {
        let rootdir = Path::new(env!("CARGO_MANIFEST_DIR"));
        let mut targets: Vec<PathBuf> = Vec::new();
        for dir in ["tests", "examples/tests"] {
            gather_test_files(&rootdir.join(dir), &mut targets);
        }
        targets.sort();
        let config = ResolvedConfig {
            rootdir: rootdir.to_path_buf(),
            ..conftest_scan_config()
        };
        let plan = rewrite_plan(&targets, rootdir, &config);
        let shadows = shadowing_names(&targets, rootdir);

        let mut reasons: std::collections::BTreeMap<String, usize> =
            std::collections::BTreeMap::new();
        for (path, key) in targets.iter().zip(&plan) {
            if key.is_some() {
                continue;
            }
            let rel = relative_posix(path, rootdir);
            let reason = read_source(path)
                .and_then(|source| rewrite_is_parsable(&source, &rel))
                .err()
                .map(|dynamic| format!("{:?}", dynamic.reason))
                .unwrap_or_else(|| "unreadable conftest chain".to_string());
            *reasons.entry(reason).or_default() += 1;
        }

        // The Tier S tally the reach used to be pinned to, printed alongside so the two
        // numbers can be read against each other: they are now expected to *disagree*, and
        // that disagreement is the whole content of the decoupling.
        let mut collection_reasons: std::collections::BTreeMap<String, usize> =
            std::collections::BTreeMap::new();
        let mut collectible = 0usize;
        for path in &targets {
            let rel = relative_posix(path, rootdir);
            match read_source(path).and_then(|source| scan_module(&source, &rel, &config, &shadows))
            {
                Ok(_) => collectible += 1,
                Err(dynamic) => {
                    *collection_reasons
                        .entry(format!("{:?}", dynamic.reason))
                        .or_default() += 1
                }
            }
        }

        let eligible = plan.iter().filter(|key| key.is_some()).count();
        println!("rewrite reach: {eligible}/{} files", targets.len());
        for (reason, count) in reasons {
            println!("  refused {count:3} x {reason}");
        }
        println!(
            "tier-S scan (no longer the rewrite gate): {collectible}/{} files",
            targets.len()
        );
        for (reason, count) in collection_reasons {
            println!("  refused {count:3} x {reason}");
        }
    }

    fn gather_test_files(dir: &Path, out: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                gather_test_files(&path, out);
            } else if path.extension().is_some_and(|ext| ext == "py")
                && path
                    .file_name()
                    .is_some_and(|name| name.to_string_lossy().starts_with("test_"))
            {
                out.push(path);
            }
        }
    }

    /// The plan's shape in one test: a parsable file gets a key, an unparsable one gets
    /// `None`, and the vector stays indexed by target.
    ///
    /// The refusal used is a **syntax error**, because after the Phase 3 Task 2 decoupling
    /// that is the only file-level refusal left. The companion test below pins the other half
    /// — that a Tier D file is now rewritten — which is the change itself.
    #[test]
    fn the_rewrite_plan_keys_parsable_files_and_skips_unparsable_ones() {
        let tmp = tempfile::TempDir::new().unwrap();
        let good_path = tmp.path().join("test_good.py");
        let broken_path = tmp.path().join("test_broken.py");
        std::fs::write(&good_path, "def test_one():\n    assert 1 == 1\n").unwrap();
        std::fs::write(&broken_path, "def test_two(:\n    assert 1 ==\n").unwrap();
        let targets = vec![good_path, broken_path];

        let plan = rewrite_plan(&targets, tmp.path(), &config());
        assert_eq!(plan.len(), targets.len(), "the plan is indexed by target");
        let key = plan[0].as_deref().expect("a parsable file is rewritable");
        assert_eq!(key.len(), 64, "the key is a 64-hex digest: {key}");
        assert!(key.chars().all(|c| c.is_ascii_hexdigit()));
        assert!(
            plan[1].is_none(),
            "a file the parser rejects must not be rewritten"
        );
    }

    /// **The decoupling, asserted as a disagreement.** Every shape below takes the file out of
    /// Tier S — and none of them changes what an `assert` in a test body means, so every one
    /// of them is now rewritten.
    ///
    /// Written as a table rather than as one case per reason because the *set* is the claim:
    /// these are the four refusals that accounted for 27 of this repository's 55 test files
    /// before the change (`rewrite_reach_over_this_repository`), and a future rule that
    /// re-coupled them would have to delete a row here to pass.
    #[test]
    fn files_tier_s_refuses_are_still_rewritten() {
        let cases: [(&str, &str); 5] = [
            ("test_star.py", "from os.path import *\n\n\ndef test_a():\n    assert 1 == 1\n"),
            (
                "test_side_effect.py",
                "import logging\n\nlogging.basicConfig()\n\n\ndef test_a():\n    assert 1 == 1\n",
            ),
            (
                "test_conditional.py",
                "import sys\n\nif sys.version_info >= (3, 12):\n\n    def test_a():\n        assert 1 == 1\n",
            ),
            (
                "test_decorator.py",
                "import functools\n\n\n@functools.cache\ndef helper():\n    return 1\n\n\ndef test_a():\n    assert 1 == 1\n",
            ),
            (
                "test_unittest.py",
                "import unittest\n\n\nclass TestThing(unittest.TestCase):\n    def test_a(self):\n        assert 1 == 1\n",
            ),
        ];
        let tmp = tempfile::TempDir::new().unwrap();
        let mut targets = Vec::new();
        for (name, source) in cases {
            let path = tmp.path().join(name);
            std::fs::write(&path, source).unwrap();
            targets.push(path);
        }

        let plan = rewrite_plan(&targets, tmp.path(), &config());
        for (path, key) in targets.iter().zip(&plan) {
            let rel = relative_posix(path, tmp.path());
            let source = read_source(path).unwrap();
            assert!(
                scan_module(&source, &rel, &config(), &no_shadows()).is_err(),
                "{rel} is supposed to be a Tier S refusal; the case has stopped testing anything"
            );
            assert!(key.is_some(), "{rel} must still be rewritten");
        }
    }

    /// The key is **the manifest cache key**, not a second digest that happens to be
    /// unique — which is the whole justification for keeping Task 2's cache alive.
    ///
    /// Asserted by recomputing it through the manifest cache's own composer, so a component
    /// added to `KeyComponent` moves both keys or neither. A test that merely checked "the
    /// key changes when the file changes" would pass for two unrelated digests.
    #[test]
    fn the_rewrite_key_is_the_manifest_cache_key() {
        let tmp = tempfile::TempDir::new().unwrap();
        let path = tmp.path().join("test_a.py");
        let source = "def test_one():\n    assert 1 == 1\n";
        std::fs::write(&path, source).unwrap();
        let targets = vec![path];

        let plan = rewrite_plan(&targets, tmp.path(), &config());
        let planned = plan[0].as_deref().expect("a static file is rewritable");

        let cache = ManifestCache::open(tmp.path(), &config(), &Default::default());
        let expected = crate::v2::manifest_cache::hex_digest(&cache.key_for_chain(
            crate::v2::manifest_cache::empty_chain_digest(),
            "test_a.py",
            source.as_bytes(),
        ));
        assert_eq!(planned, expected);
    }

    /// Editing a file moves its key, so the cached bytecode for the previous source can
    /// never be executed for the new one — the invalidation the whole key composition
    /// exists for, checked through the surface the rewrite actually uses.
    #[test]
    fn editing_a_file_moves_its_rewrite_key() {
        let tmp = tempfile::TempDir::new().unwrap();
        let path = tmp.path().join("test_a.py");
        std::fs::write(&path, "def test_one():\n    assert 1 == 1\n").unwrap();
        let targets = vec![path.clone()];

        let before = rewrite_plan(&targets, tmp.path(), &config())[0].clone();
        std::fs::write(&path, "def test_one():\n    assert 1 == 2\n").unwrap();
        let after = rewrite_plan(&targets, tmp.path(), &config())[0].clone();

        assert!(before.is_some() && after.is_some());
        assert_ne!(before, after, "an edited file kept its rewrite key");
    }

    /// Two files with the same **stem** are refused, matching `static_pass`'s own rule.
    ///
    /// Same-stem files **do** get rewrite keys, and the keys differ.
    ///
    /// This inverts what the test asserted before Phase 3 Task 2, and the inversion is the
    /// record of the argument: the stem-collision rule is a *collection* rule (two `test_dup.py`
    /// files import under one module name, so the second is pytest's `import file mismatch`
    /// collection error), and rewriting is keyed by absolute path all the way down —
    /// `_assertion_rewrite.register` normcases the absolute path, and the artefact is named
    /// from a digest of it. The file that does get imported is the one whose registration is
    /// consulted; the other one's key is simply never looked up. Withholding both keys cost
    /// the imported file its messages to protect nothing.
    #[test]
    fn same_stem_files_each_get_their_own_rewrite_key() {
        let tmp = tempfile::TempDir::new().unwrap();
        std::fs::create_dir_all(tmp.path().join("one")).unwrap();
        std::fs::create_dir_all(tmp.path().join("two")).unwrap();
        let first = tmp.path().join("one").join("test_dup.py");
        let second = tmp.path().join("two").join("test_dup.py");
        for path in [&first, &second] {
            std::fs::write(path, "def test_one():\n    assert 1 == 1\n").unwrap();
        }

        let plan = rewrite_plan(&[first, second], tmp.path(), &config());
        assert!(plan[0].is_some() && plan[1].is_some());
        assert_ne!(
            plan[0], plan[1],
            "the key carries the rootdir-relative path, so identical sources in two \
             directories must not share a cached artefact"
        );
    }

    /// A directory whose conftest chain is not statically safe contributes no keys — the
    /// same veto `static_pass_cached` applies, reached the same way.
    #[test]
    fn a_parametrized_conftest_fixture_does_not_withhold_the_rewrite_key() {
        let tmp = tempfile::TempDir::new().unwrap();
        std::fs::write(
            tmp.path().join("conftest.py"),
            "import rustest\n\n\n@rustest.fixture(params=[1, 2])\ndef anything(request):\n    return request.param\n",
        )
        .unwrap();
        let path = tmp.path().join("test_a.py");
        std::fs::write(&path, "def test_one():\n    assert 1 == 1\n").unwrap();

        // The **collection** tier refuses this directory, and must: a parametrized fixture
        // multiplies the ids of every test in scope.
        assert!(conftest_chain_is_static(tmp.path(), tmp.path(), &no_shadows()).is_err());

        // Rewriting does not care. It transforms this file's own `assert` statements, and how
        // many times the file's tests are instantiated changes none of them. Asserting that
        // the two tiers *disagree* here is the whole point of `ChainRule`: the first version
        // of this test asserted they agreed, which is what kept assertion rewriting off 52 of
        // rustest's own 53 test files.
        assert!(rewrite_plan(&[path], tmp.path(), &config())[0].is_some());
    }

    /// The one chain property rewriting *does* require: every conftest must be readable.
    ///
    /// The bytecode cache key hashes the chain's bytes, so a conftest that cannot be read is
    /// a key that cannot be composed — and therefore a cached artefact that a later edit to
    /// that conftest would not invalidate. Undecodable bytes reach the same arm as an
    /// unreadable file, and are the portable way to produce one.
    #[test]
    fn an_unreadable_conftest_withholds_the_rewrite_key() {
        let tmp = tempfile::TempDir::new().unwrap();
        std::fs::write(tmp.path().join("conftest.py"), [0xff, 0xfe, 0x00, 0x80]).unwrap();
        let path = tmp.path().join("test_a.py");
        std::fs::write(&path, "def test_one():\n    assert 1 == 1\n").unwrap();

        assert_eq!(rewrite_plan(&[path], tmp.path(), &config()), vec![None]);
    }

    /// A `--v2-collect-only` run must not pay for a plan it cannot use. Asserted at the
    /// option that gates it, because the cost is a read and a parse **per file** and the
    /// collect-only path is the one whose whole point is latency.
    #[test]
    fn collect_only_does_not_compute_a_rewrite_plan() {
        assert!(
            !crate::v2::collect::CollectOptions::new().assert_rewrite,
            "the default must be off; `collect::plan` turns it on for runs"
        );
    }
}
