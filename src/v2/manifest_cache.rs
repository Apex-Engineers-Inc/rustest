//! The **Tier S manifest cache**: Tier S's answers, remembered on disk.
//!
//! [`crate::v2::static_collect`] can answer "what tests are in this file?" without importing
//! it.  This module remembers those answers, keyed on every input they depend on, so a second
//! run over an unchanged tree skips the parse entirely.  Combined with the orchestrator's
//! "a fully static tree spawns no worker" property, a warm `--collect-only` over a fully
//! static tree does **no parsing and starts no process**.
//!
//! # What it is worth, measured — and it is not what was expected
//!
//! The premise of a manifest cache is that collection is parse-bound.  For Tier S it is not,
//! and the numbers are worth stating here rather than in a report nobody reads (Phase 2
//! Task 2, release build, 500 files / 5 000 tests, medians of 11):
//!
//! | | cache off | cache on |
//! | --- | --- | --- |
//! | cold | 27.9 ms | 37.7 ms |
//! | warm | — | 31.2 ms |
//!
//! A warm hit is **3 ms slower** than simply parsing the files again, and on a
//! parametrize-heavy tree (200 files / 20 600 tests) the gap widens to 12 ms.  The reason is
//! not a slow cache: it is that ruff parses 500 test files and this crate extracts their tests
//! in about two milliseconds *total*, while the answer serialises to 544 KB of JSON that costs
//! more to read back than the source cost to parse.  Nothing about the encoding changes that
//! conclusion — the artifact is larger than the input and the transform is nearly free.
//!
//! It is kept, and kept on by default, for three reasons and not because it is fast:
//!
//! 1. the difference is under 1 % of the wall time of the command it belongs to — a
//!    `--collect-only` at 5 000 tests spends ~460 ms, of which ~420 ms is CPython starting up
//!    and importing this package;
//! 2. the *key* is the durable artifact.  Phase 2 Task 3 caches rewritten assertion bytecode
//!    against this key, and there the asymmetry is real: compiling Python source is orders of
//!    magnitude dearer than reading a `.pyc`;
//! 3. its behaviour — hit ⇒ no parse, no worker, and every invalidation rule — is specified,
//!    tested and mutation-checked, so if the payoff arrives the correctness argument is
//!    already made.
//!
//! If Task 3's bytecode cache does not land, the honest move is to delete this module rather
//! than to keep a store that costs more than it saves.
//!
//! # Tier S results only — and that is a correctness rule, not a scoping decision
//!
//! Only [`crate::v2::manifest::Tier::Static`] entries are ever written here.  A **Tier D**
//! result is what a Python worker reported after *importing* the module, and an import reads
//! the interpreter, the installed packages, the environment, every conftest in the chain and
//! whatever those conftests did when they ran.  None of that is in this key, and no practical
//! key could hold it: two runs of the same bytes under the same config legitimately produce
//! different Tier D manifests (a package was installed, `sys.version_info` moved, a
//! conftest read a file).  Caching one would be a stale answer with exit 0.
//!
//! The rule is enforced structurally rather than by review: this module is only ever reached
//! from [`crate::v2::static_collect::static_pass_cached`], which handles nothing but Tier S,
//! and `collect.rs` hands worker outcomes straight to `assemble` without passing this way.
//! `a_dynamic_only_run_writes_nothing` in `collect.rs` is the assertion.
//!
//! # Why a *stale* cache is the dangerous failure and a *missing* one is not
//!
//! A missing entry costs a parse — about four microseconds a file.  A **stale** entry is a
//! manifest describing tests that no longer exist, or missing ones that do, delivered with
//! exit 0 and nothing anywhere reporting a problem.  So every input a Tier S answer depends
//! on is in the key, the key composition is destructured exhaustively (a new
//! [`ResolvedConfig`] field is a compile error until it is hashed), and every component
//! carries a mutation test proving that dropping it makes two states that must differ
//! collide.  See [`KeyComponent`].
//!
//! # Layout
//!
//! ```text
//! <rootdir>/.rustest_cache/v2-manifest/<blake3(rel dir)>.json
//! ```
//!
//! One **shard per directory**, not per file.  Per-file entries would mean one file open per
//! collected file on the warm path — 500 opens for the 5 000-test benchmark suite, which on
//! Windows costs more than the parses it saves.  One shard per directory makes the warm read
//! count the number of *directories* a run touches, matches the granularity the conftest
//! chain already works at, and keeps a run over a subdirectory from reading (or rewriting)
//! the rest of the tree's entries.
//!
//! `.rustest_cache` is the directory v1 and the v2 last-failed cache already use, so it is
//! already in `.gitignore` and already the one directory a user deletes to reset everything.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use serde::{Deserialize, Serialize};

use crate::v2::config::ResolvedConfig;
use crate::v2::manifest::{CollectedTest, Tier};

/// Shared with v1 and with the last-failed cache: one directory to gitignore, one to delete.
const CACHE_DIR: &str = ".rustest_cache";

/// The manifest cache's own sub-directory, named for the artifact it holds.  A *directory*
/// per artifact rather than a file per artifact, for the same reason `v2/lastfailed` is a
/// directory: a build that does not understand this format must not be able to read it.
const MANIFEST_CACHE_DIR: &str = "v2-manifest";

/// On-disk shape version.  A shard whose `schema` is not this is discarded on sight, which is
/// what lets the shape change without a migration.
const STORE_SCHEMA_VERSION: u32 = 1;

/// Domain separator, so a digest computed here can never be confused with one computed
/// anywhere else that happens to hash the same bytes.
const DOMAIN: &[u8] = b"rustest/v2-manifest-cache\x00";

/// The cache **epoch** — bump this whenever Tier S's extraction rules change.
///
/// The crate version alone is not enough and the gap is not theoretical: a developer rebuilds
/// `static_collect.rs` a hundred times without `CARGO_PKG_VERSION` moving, and every one of
/// those builds would happily read a store written by the *previous* rules.  The three-way
/// differential is the backstop — its Tier D leg never touches this cache, so a stale Tier S
/// answer fails the comparison — but the backstop only runs when someone runs it, and this
/// constant costs one line.
const TIER_S_EPOCH: u32 = 1;

/// A 32-byte blake3 digest.
pub type Digest = [u8; 32];

/// The inputs a cached Tier S answer depends on.
///
/// Every field is a **key component** ([`KeyComponent`]) with a mutation test to its name.
/// Three of them arrive pre-digested because they are shared: [`Self::config`] and
/// [`Self::shadows`] are computed once per run and [`Self::chain`] once per directory, so a
/// per-file key costs one short hash rather than a re-serialisation of the whole config.
#[derive(Debug, Clone, Copy)]
pub struct KeyInputs<'a> {
    /// The build identity — see [`build_version`].
    pub version: &'a str,
    /// [`digest_of_config`] over the whole [`ResolvedConfig`].
    pub config: Digest,
    /// [`digest_of_shadows`] over the module names this run's own files can shadow.
    pub shadows: Digest,
    /// [`digest_of_chain`] over the conftest chain that applies to this file's directory.
    pub chain: Digest,
    /// The file's rootdir-relative posix path — what every node id in the answer embeds.
    pub rel_path: &'a str,
    /// The file's bytes.
    pub content: &'a [u8],
}

/// One component of a cache key, and the question "what would a stale entry look like if this
/// were left out?".
///
/// The variants are the mutation table.  [`Self::ALL`] is what [`cache_key`] iterates, so a
/// component cannot be added without appearing in the key, and
/// `the_component_list_is_exhaustive` fails if a variant is added without being listed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyComponent {
    /// The rustest build.  Dropped: a store written by rules that no longer exist is served
    /// to the rules that replaced them.
    Version,
    /// The whole resolved config.  Dropped: `python_functions = check_*` reports the tests
    /// the *previous* setting collected.
    Config,
    /// The stdlib names this run's own files shadow.  Dropped: a `queue.py` appearing beside
    /// a cached file leaves the file answered statically when its `import queue` has become
    /// user code — the file must go to Tier D and does not.
    Shadows,
    /// The conftest chain's bytes.  Dropped: a conftest change that this module's chain
    /// analysis does not currently notice can never be noticed later either.
    ConftestChain,
    /// The file's rootdir-relative path.  Dropped: an answer is served for a path it was not
    /// computed for — every node id in it starts with that path.
    Path,
    /// The file's bytes.  Dropped: editing a test file changes nothing.
    Content,
}

impl KeyComponent {
    /// Every component, in the order [`cache_key`] hashes them.
    pub const ALL: [KeyComponent; 6] = [
        KeyComponent::Version,
        KeyComponent::Config,
        KeyComponent::Shadows,
        KeyComponent::ConftestChain,
        KeyComponent::Path,
        KeyComponent::Content,
    ];
}

/// The build this binary is, as a cache-key component.
///
/// `CARGO_PKG_VERSION` plus [`TIER_S_EPOCH`]; see that constant for why the version alone is
/// not enough.
pub fn build_version() -> String {
    format!("{}+{}", env!("CARGO_PKG_VERSION"), TIER_S_EPOCH)
}

/// The key for one file.
///
/// Every component is written **labelled and length-prefixed**, so no two different component
/// sets can produce the same byte stream: without the length prefix a `rel_path` of `"ab"`
/// followed by content `"c"` would hash identically to `"a"` followed by `"bc"`.
pub fn cache_key(inputs: &KeyInputs<'_>) -> Digest {
    cache_key_omitting(inputs, None)
}

/// [`cache_key`] with one component left out — the mutation harness's only entry point.
///
/// `#[cfg(test)]` on purpose: this exists so a test can *prove* a component is load-bearing
/// by showing that two states which must not collide do collide without it.  Nothing in
/// production may call it.
#[cfg(test)]
pub fn cache_key_without(inputs: &KeyInputs<'_>, omit: KeyComponent) -> Digest {
    cache_key_omitting(inputs, Some(omit))
}

fn cache_key_omitting(inputs: &KeyInputs<'_>, omit: Option<KeyComponent>) -> Digest {
    let mut hasher = blake3::Hasher::new();
    let _ = hasher.update(DOMAIN);
    let _ = hasher.update(&STORE_SCHEMA_VERSION.to_le_bytes());
    for component in KeyComponent::ALL {
        if omit == Some(component) {
            continue;
        }
        let bytes: &[u8] = match component {
            KeyComponent::Version => inputs.version.as_bytes(),
            KeyComponent::Config => &inputs.config,
            KeyComponent::Shadows => &inputs.shadows,
            KeyComponent::ConftestChain => &inputs.chain,
            KeyComponent::Path => inputs.rel_path.as_bytes(),
            KeyComponent::Content => inputs.content,
        };
        // The label is part of the stream as well as the bytes: two components that happen to
        // carry the same value still hash differently, so swapping two of them is a different
        // key rather than the same one.
        let _ = hasher.update(format!("{component:?}").as_bytes());
        let _ = hasher.update(&[0]);
        let _ = hasher.update(&(bytes.len() as u64).to_le_bytes());
        let _ = hasher.update(bytes);
    }
    *hasher.finalize().as_bytes()
}

/// Digest the **whole** resolved config.
///
/// Destructured exhaustively rather than `#[derive(Hash)]`d: adding a field to
/// [`ResolvedConfig`] is then a compile error here until it is hashed, which is the only form
/// of "any config field invalidates the cache" a reviewer does not have to take on trust.
/// `#[derive(Hash)]` would have silently ignored a new field, and the resulting stale entry
/// is invisible by construction.
pub fn digest_of_config(config: &ResolvedConfig) -> Digest {
    let ResolvedConfig {
        rootdir,
        config_file,
        testpaths,
        python_files,
        python_classes,
        python_functions,
        norecursedirs,
        addopts,
        markers,
        asyncio_mode,
        asyncio_default_fixture_loop_scope,
        asyncio_default_test_loop_scope,
    } = config;

    let mut hasher = blake3::Hasher::new();
    let _ = hasher.update(DOMAIN);
    let mut field = |label: &str, values: &[String]| {
        let _ = hasher.update(label.as_bytes());
        let _ = hasher.update(&(values.len() as u64).to_le_bytes());
        for value in values {
            let _ = hasher.update(&(value.len() as u64).to_le_bytes());
            let _ = hasher.update(value.as_bytes());
        }
    };
    field("rootdir", &[rootdir.to_string_lossy().into_owned()]);
    field(
        "config_file",
        &[config_file
            .as_ref()
            .map(|path| path.to_string_lossy().into_owned())
            .unwrap_or_default()],
    );
    field("testpaths", testpaths);
    field("python_files", python_files);
    field("python_classes", python_classes);
    field("python_functions", python_functions);
    field("norecursedirs", norecursedirs);
    field("addopts", addopts);
    field("markers", markers);
    // `asyncio_mode` is a **collection** input, not only an execution one: in `auto` mode an
    // `async def` + `yield` test acquires a synthesised `xfail(run=False)` mark
    // (`_v2_worker.py::_async_generator_xfail`) that it does not get in `strict` mode, and the
    // mark travels in the cached manifest entry.  A run that flips the mode and reuses a
    // manifest built under the other one would serve the wrong mark set.
    field("asyncio_mode", std::slice::from_ref(asyncio_mode));
    // Hashed as **zero or one** values rather than as a string, so `None` (fall back to the
    // fixture's own scope) and `Some("")` do not collide: the length prefix separates them.
    field(
        "asyncio_default_fixture_loop_scope",
        asyncio_default_fixture_loop_scope
            .as_ref()
            .map_or(&[][..], std::slice::from_ref),
    );
    field(
        "asyncio_default_test_loop_scope",
        std::slice::from_ref(asyncio_default_test_loop_scope),
    );
    *hasher.finalize().as_bytes()
}

/// Digest the module names this run's own files could shadow.
///
/// The caller passes the set already intersected with Tier S's stdlib allowlist, because that
/// intersection is the only part that can change an answer
/// (`static_collect::Scan::import_is_safe` consults `shadows` only for a name already in the
/// allowlist).  Narrowing it is not just an optimisation: the *unfiltered* set contains every
/// test file's own stem, so an unfiltered digest would invalidate the whole tree's cache every
/// time a test file was added.
pub fn digest_of_shadows(names: &BTreeSet<String>) -> Digest {
    let mut hasher = blake3::Hasher::new();
    let _ = hasher.update(DOMAIN);
    let _ = hasher.update(b"shadows");
    for name in names {
        let _ = hasher.update(&(name.len() as u64).to_le_bytes());
        let _ = hasher.update(name.as_bytes());
    }
    *hasher.finalize().as_bytes()
}

/// Digest a directory's conftest chain: each conftest's rootdir-relative path and its bytes,
/// in chain order.
///
/// A conftest that appears, disappears or changes moves this digest, so it moves every key in
/// the directory.  Today that is **defence in depth** rather than the only line: Tier S also
/// re-analyses the chain on every run and routes the whole directory to Tier D when it is not
/// statically safe, so a conftest that grows a `params=` is caught with or without this
/// component.  It is in the key anyway because the chain analysis is a *rule set*, and the
/// day a rule is relaxed the cache must not still be serving answers written under the strict
/// one.
/// An unreadable conftest is hashed as *absent bytes*, distinctly from an empty file: the two
/// are different situations (the first routes the whole directory to Tier D), and a key that
/// could not tell them apart would let one be served the other's answer.
pub fn digest_of_chain<'a>(chain: impl IntoIterator<Item = (&'a str, Option<&'a [u8]>)>) -> Digest {
    let mut hasher = blake3::Hasher::new();
    let _ = hasher.update(DOMAIN);
    let _ = hasher.update(b"chain");
    let mut count: u64 = 0;
    for (path, bytes) in chain {
        count += 1;
        let _ = hasher.update(&(path.len() as u64).to_le_bytes());
        let _ = hasher.update(path.as_bytes());
        match bytes {
            Some(bytes) => {
                let _ = hasher.update(&[1]);
                let _ = hasher.update(&(bytes.len() as u64).to_le_bytes());
                let _ = hasher.update(bytes);
            }
            None => {
                let _ = hasher.update(&[0]);
            }
        }
    }
    let _ = hasher.update(&count.to_le_bytes());
    *hasher.finalize().as_bytes()
}

/// The digest of an empty chain — the common case (a directory with no conftest above it).
pub fn empty_chain_digest() -> Digest {
    digest_of_chain(std::iter::empty())
}

/// Lower-case hex, without a `format!` per byte.
///
/// The naive `push_str(&format!("{byte:02x}"))` allocates once per *nibble pair* -- 32
/// allocations for one key, and a 500-file warm collection computes one key per file.  It was
/// measurable (see the task report's breakdown), which is why this is spelled out.
pub fn hex_digest(digest: &Digest) -> String {
    hex(digest)
}

fn hex(digest: &Digest) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(digest.len() * 2);
    for byte in digest {
        out.push(HEX[usize::from(byte >> 4)] as char);
        out.push(HEX[usize::from(byte & 0x0f)] as char);
    }
    out
}

/// The inverse, for reading a stored key back into bytes.
///
/// Comparing `Digest`s rather than hex strings is what keeps the lookup off the formatter:
/// a shard is decoded once, at load, and every per-file comparison after that is 32 bytes
/// against 32 bytes.  A malformed key decodes to `None` and its entry is dropped, which is the
/// same "damaged shard is a miss" rule the document-level parse follows.
fn unhex(text: &str) -> Option<Digest> {
    let bytes = text.as_bytes();
    if bytes.len() != 64 {
        return None;
    }
    let mut digest = [0u8; 32];
    for (index, pair) in bytes.chunks_exact(2).enumerate() {
        let hi = (pair[0] as char).to_digit(16)?;
        let lo = (pair[1] as char).to_digit(16)?;
        digest[index] = (hi * 16 + lo) as u8;
    }
    Some(digest)
}

// ---------------------------------------------------------------------------
// The store
// ---------------------------------------------------------------------------

/// One directory's cached answers.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
struct Shard {
    schema: u32,
    /// The rootdir-relative posix directory, for a human reading the file.  Never trusted:
    /// the shard is *addressed* by the digest of this path, and every entry re-checks its own
    /// key, so a wrong value here cannot produce a wrong answer.
    dir: String,
    /// File name (not path — the shard is one directory) to entry.
    entries: BTreeMap<String, ShardEntry>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
struct ShardEntry {
    /// Hex [`cache_key`].  An entry whose key does not match the one computed now is a miss,
    /// which is what makes every invalidation rule a property of the key rather than of the
    /// lookup.
    key: String,
    tests: Vec<CollectedTest>,
}

/// Hit/miss counters, so a test can assert "the warm run parsed nothing" rather than infer it.
#[derive(Debug, Default)]
pub struct CacheStats {
    hits: AtomicUsize,
    /// Files that reached the parser: a miss is exactly a parse, which is what makes this the
    /// instrument for "a warm collection does no parsing".
    misses: AtomicUsize,
    /// Shard files rewritten.
    writes: AtomicUsize,
}

impl CacheStats {
    pub fn hits(&self) -> usize {
        self.hits.load(Ordering::Relaxed)
    }
    pub fn misses(&self) -> usize {
        self.misses.load(Ordering::Relaxed)
    }
    pub fn writes(&self) -> usize {
        self.writes.load(Ordering::Relaxed)
    }
}

/// A run's handle on the manifest cache.
///
/// Opened once, before the parallel scan; holds the run-global half of every key and the
/// shards loaded so far.
#[derive(Debug)]
pub struct ManifestCache {
    dir: PathBuf,
    version: String,
    config: Digest,
    shadows: Digest,
    stats: CacheStats,
}

/// One directory's loaded shard plus the per-directory half of the key.
#[derive(Debug)]
pub struct DirCache {
    shard_path: PathBuf,
    dir_rel: String,
    chain: Digest,
    /// Decoded at load time: the wire form is hex, the lookup form is bytes.
    loaded: BTreeMap<String, LoadedEntry>,
}

/// A shard entry with its key decoded.
#[derive(Debug, Clone, PartialEq)]
struct LoadedEntry {
    key: Digest,
    tests: Vec<CollectedTest>,
}

impl ManifestCache {
    /// Open the cache for a run.  Never fails: an unwritable or unreadable cache directory
    /// simply produces misses.
    pub fn open(rootdir: &Path, config: &ResolvedConfig, shadows: &BTreeSet<String>) -> Self {
        Self::open_with_version(rootdir, config, shadows, build_version())
    }

    /// [`Self::open`] with the build identity supplied.
    ///
    /// The seam exists for one test — `a_version_change_invalidates_every_entry` — which has
    /// to observe what a *different build* of rustest does to a store this one wrote, and
    /// cannot do that by rebuilding itself.
    pub fn open_with_version(
        rootdir: &Path,
        config: &ResolvedConfig,
        shadows: &BTreeSet<String>,
        version: String,
    ) -> Self {
        Self {
            dir: rootdir.join(CACHE_DIR).join(MANIFEST_CACHE_DIR),
            version,
            config: digest_of_config(config),
            shadows: digest_of_shadows(shadows),
            stats: CacheStats::default(),
        }
    }

    pub fn stats(&self) -> &CacheStats {
        &self.stats
    }

    /// Load the shard for `dir` (rootdir-relative posix `dir_rel`), with that directory's
    /// conftest-chain digest.
    ///
    /// A missing, unreadable, truncated, mis-schema'd or otherwise unparsable shard loads as
    /// **empty**, never as an error: a cache is an optimisation, and a corrupt one is a cache
    /// to rebuild rather than a reason to refuse to collect.  Same position pytest takes in
    /// `_pytest/cacheprovider.py::Cache.get`, and the same one `v2::cache` takes for
    /// `lastfailed`.
    pub fn load_dir(&self, dir_rel: &str, chain: Digest) -> DirCache {
        let shard_path = self
            .dir
            .join(format!("{}.json", hex(&digest_of_dir(dir_rel))));
        let loaded = std::fs::read_to_string(&shard_path)
            .ok()
            .and_then(|raw| serde_json::from_str::<Shard>(&raw).ok())
            .filter(|shard| shard.schema == STORE_SCHEMA_VERSION)
            .map(|shard| {
                shard
                    .entries
                    .into_iter()
                    .filter_map(|(name, entry)| {
                        Some((
                            name,
                            LoadedEntry {
                                key: unhex(&entry.key)?,
                                tests: entry.tests,
                            },
                        ))
                    })
                    .collect()
            })
            .unwrap_or_default();
        DirCache {
            shard_path,
            dir_rel: dir_rel.to_string(),
            chain,
            loaded,
        }
    }

    /// The key for one file in `dir_cache`.
    pub fn key(&self, dir_cache: &DirCache, rel_path: &str, content: &[u8]) -> Digest {
        self.key_for_chain(dir_cache.chain, rel_path, content)
    }

    /// [`Self::key`] with the conftest-chain digest passed directly instead of read off a
    /// loaded shard.
    ///
    /// The seam exists for [`crate::v2::static_collect::rewrite_plan`], which needs a key for
    /// every target but must not touch the store: it runs on the **run** path, where the
    /// manifest cache is off by construction, and loading a shard there would both cost a
    /// file open per directory and blur the "the run path reads and writes nothing" rule the
    /// Task 2 tests assert.
    ///
    /// Both entry points compose the key through the same [`cache_key`] call with the same
    /// run-global components, so the bytecode cache and the manifest cache cannot drift: a
    /// component added to [`KeyComponent`] moves both, or neither.
    pub fn key_for_chain(&self, chain: Digest, rel_path: &str, content: &[u8]) -> Digest {
        cache_key(&KeyInputs {
            version: &self.version,
            config: self.config,
            shadows: self.shadows,
            chain,
            rel_path,
            content,
        })
    }

    /// The cached answer for `file_name` in `dir_cache`, if its key matches `key`.
    ///
    /// Every returned test has its tier forced to [`Tier::Static`].  The entries this module
    /// writes always carry `"tier":"s"`, but a *hand-edited or truncated-and-still-valid*
    /// document could omit it — and an omitted tier decodes as `Dynamic` (the worker's
    /// omission rule).  A "static" answer labelled Tier D would corrupt exactly the
    /// attribution surface the three-way differential reads, so the label is asserted here
    /// rather than trusted from disk.
    pub fn get(
        &self,
        dir_cache: &DirCache,
        file_name: &str,
        key: &Digest,
    ) -> Option<Vec<CollectedTest>> {
        let entry = dir_cache.loaded.get(file_name)?;
        if entry.key != *key {
            return None;
        }
        // Only hits are counted here.  A miss is counted by [`Self::record_miss`], at the one
        // point a file is actually handed to the parser -- counting it in both places would
        // double-count a key mismatch and quietly break the "the warm run parsed nothing"
        // instrument that reads this number.
        self.stats.hits.fetch_add(1, Ordering::Relaxed);
        let mut tests = entry.tests.clone();
        for test in &mut tests {
            test.tier = Tier::Static;
        }
        Some(tests)
    }

    /// Record that a file was **not** served from the cache and is about to be parsed.
    pub fn record_miss(&self) {
        self.stats.misses.fetch_add(1, Ordering::Relaxed);
    }

    /// Write `fresh` (file name → key, tests) into `dir_cache`'s shard.
    ///
    /// Merged over what was loaded, then pruned of entries whose file no longer exists, so the
    /// shard stays bounded by the directory's current contents rather than by its history.
    /// A shard whose merged content equals what was loaded is **not rewritten** — the common
    /// warm run touches no file at all, and neither reads nor stats one on this path.
    ///
    /// The pruning is deliberately **lazy**: a run in which every file was a hit returns
    /// before the merge, so an entry for a file deleted since the last write survives until
    /// something else in that directory changes.  A dead entry is inert — it is only ever
    /// looked up by a file name the walk produced, and the walk cannot produce a name for a
    /// file that is gone — and paying a `stat` per cached file on every warm collection to
    /// tidy it sooner is exactly the trade this cache exists to avoid.
    ///
    /// Best-effort: every failure is swallowed, because a read-only checkout must still be
    /// able to collect.  Returns whether a write happened, which is what the tests assert on.
    pub fn store_dir(
        &self,
        dir_cache: &DirCache,
        dir_abs: &Path,
        fresh: BTreeMap<String, (Digest, Vec<CollectedTest>)>,
    ) -> bool {
        // Nothing new to record means nothing to write, and the check is up here rather than
        // after the merge because the merge is not free: it clones every cached test in the
        // directory and then deep-compares the result.  The warm run — every file a hit, no
        // fresh entry — is the common case and must not pay for either.
        if fresh.is_empty() {
            return false;
        }

        let mut entries = dir_cache.loaded.clone();
        let mut refreshed: BTreeSet<String> = BTreeSet::new();
        for (name, (key, tests)) in fresh {
            let _ = refreshed.insert(name.clone());
            let _ = entries.insert(name, LoadedEntry { key, tests });
        }
        // Only *carried-over* entries are stat'd.  A file this run just cached was read
        // moments ago by the pass that produced it, so asking the filesystem again would be
        // one syscall per collected file for an answer already known — the same cost that
        // dominated the walk before [`crate::v2::collect::is_dir`] existed.
        entries.retain(|name, _| refreshed.contains(name) || dir_abs.join(name).is_file());
        if entries == dir_cache.loaded {
            return false;
        }

        let shard = Shard {
            schema: STORE_SCHEMA_VERSION,
            dir: dir_cache.dir_rel.clone(),
            entries: entries
                .into_iter()
                .map(|(name, entry)| {
                    (
                        name,
                        ShardEntry {
                            key: hex(&entry.key),
                            tests: entry.tests,
                        },
                    )
                })
                .collect(),
        };
        let Ok(encoded) = serde_json::to_string(&shard) else {
            return false;
        };
        if write_atomically(&dir_cache.shard_path, encoded.as_bytes()) {
            self.stats.writes.fetch_add(1, Ordering::Relaxed);
            return true;
        }
        false
    }
}

/// The shard file's name: the digest of the rootdir-relative directory path.
///
/// Hashed rather than sanitised because a directory path holds separators, and on Windows also
/// a drive letter and a colon — none of which are legal in a file name.  The path is kept
/// inside the document for a human; the digest is what addresses it.
fn digest_of_dir(dir_rel: &str) -> Digest {
    let mut hasher = blake3::Hasher::new();
    let _ = hasher.update(DOMAIN);
    let _ = hasher.update(b"dir");
    let _ = hasher.update(dir_rel.as_bytes());
    *hasher.finalize().as_bytes()
}

/// Write `bytes` to `path` so a concurrent reader sees either the old file or the new one,
/// never a half-written one.
///
/// Write to a uniquely named temporary in the **same directory** — a rename is only atomic
/// within a filesystem — then rename over the destination.
///
/// **Windows.** `std::fs::rename` is `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING`, which
/// replaces the destination atomically as far as readers are concerned, but *fails* with
/// `ACCESS_DENIED` when another process holds the destination open without
/// `FILE_SHARE_DELETE` — the classic case being an antivirus scanner mid-scan.  Rust's own
/// `File::open` does share delete, so two rustest runs never block each other; a third-party
/// scanner can, and when it does the write is simply skipped and the entry recomputed next
/// time.  That is the whole handling: a cache write that loses a race must never be an error
/// a user sees.
///
/// The temporary is removed on a failed rename so a blocked write does not leave litter.
fn write_atomically(path: &Path, bytes: &[u8]) -> bool {
    let Some(parent) = path.parent() else {
        return false;
    };
    if std::fs::create_dir_all(parent).is_err() {
        return false;
    }
    // Unique per process *and* per call: two threads of one run may write different shards at
    // the same moment, and two runs share nothing but the directory.
    static COUNTER: AtomicUsize = AtomicUsize::new(0);
    let tmp = parent.join(format!(
        ".tmp-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    if std::fs::write(&tmp, bytes).is_err() {
        let _ = std::fs::remove_file(&tmp);
        return false;
    }
    if std::fs::rename(&tmp, path).is_err() {
        let _ = std::fs::remove_file(&tmp);
        return false;
    }
    true
}

/// Fresh entries to write, grouped by absolute directory — [`ManifestCache::store_dir`]'s input.
pub type FreshByDir = HashMap<PathBuf, BTreeMap<String, (Digest, Vec<CollectedTest>)>>;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::v2::config::{
        DEFAULT_ASYNCIO_MODE, DEFAULT_ASYNCIO_TEST_LOOP_SCOPE, DEFAULT_PYTHON_CLASSES,
        DEFAULT_PYTHON_FILES, DEFAULT_PYTHON_FUNCTIONS,
    };
    use tempfile::TempDir;

    fn owned(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| (*item).to_string()).collect()
    }

    fn config_at(rootdir: &Path) -> ResolvedConfig {
        ResolvedConfig {
            rootdir: rootdir.to_path_buf(),
            config_file: None,
            testpaths: Vec::new(),
            python_files: owned(DEFAULT_PYTHON_FILES),
            python_classes: owned(DEFAULT_PYTHON_CLASSES),
            python_functions: owned(DEFAULT_PYTHON_FUNCTIONS),
            norecursedirs: Vec::new(),
            addopts: Vec::new(),
            markers: Vec::new(),
            asyncio_mode: DEFAULT_ASYNCIO_MODE.to_string(),
            asyncio_default_fixture_loop_scope: None,
            asyncio_default_test_loop_scope: DEFAULT_ASYNCIO_TEST_LOOP_SCOPE.to_string(),
        }
    }

    fn inputs<'a>(rel_path: &'a str, content: &'a [u8]) -> KeyInputs<'a> {
        KeyInputs {
            version: "0.0.0+1",
            config: [1; 32],
            shadows: [2; 32],
            chain: [3; 32],
            rel_path,
            content,
        }
    }

    fn test_entry(id: &str) -> CollectedTest {
        CollectedTest {
            id: id.to_string(),
            path: "test_a.py".to_string(),
            qualname: "test_one".to_string(),
            class_name: None,
            param_id: None,
            marks: Vec::new(),
            fixtures: Vec::new(),
            tier: Tier::Static,
        }
    }

    // -- the mutation table ------------------------------------------------
    //
    // One test per key component.  Each builds two states that MUST NOT share a key, asserts
    // that the real key separates them (the control -- without it the row proves nothing) and
    // then asserts that **dropping that one component makes them collide**.  A collision is
    // precisely a stale hit: the second state would be served the first state's answer.

    /// Drop [`KeyComponent::Version`] and a store written by rules that no longer exist is
    /// served to the rules that replaced them.
    #[test]
    fn dropping_the_version_component_serves_another_builds_answers() {
        let a = KeyInputs {
            version: "0.16.2+1",
            ..inputs("test_a.py", b"x")
        };
        let b = KeyInputs {
            version: "0.17.0+2",
            ..inputs("test_a.py", b"x")
        };
        assert_ne!(cache_key(&a), cache_key(&b));
        assert_eq!(
            cache_key_without(&a, KeyComponent::Version),
            cache_key_without(&b, KeyComponent::Version)
        );
    }

    /// Drop [`KeyComponent::Config`] and `python_functions = check_*` reports the tests the
    /// previous setting collected.
    #[test]
    fn dropping_the_config_component_serves_another_configs_answers() {
        let a = KeyInputs {
            config: [10; 32],
            ..inputs("test_a.py", b"x")
        };
        let b = KeyInputs {
            config: [11; 32],
            ..inputs("test_a.py", b"x")
        };
        assert_ne!(cache_key(&a), cache_key(&b));
        assert_eq!(
            cache_key_without(&a, KeyComponent::Config),
            cache_key_without(&b, KeyComponent::Config)
        );
    }

    /// Drop [`KeyComponent::Shadows`] and a `queue.py` appearing next to a cached file leaves
    /// it answered statically when its `import queue` has become user code.
    #[test]
    fn dropping_the_shadows_component_serves_an_answer_a_new_local_module_invalidated() {
        let a = KeyInputs {
            shadows: [20; 32],
            ..inputs("test_a.py", b"x")
        };
        let b = KeyInputs {
            shadows: [21; 32],
            ..inputs("test_a.py", b"x")
        };
        assert_ne!(cache_key(&a), cache_key(&b));
        assert_eq!(
            cache_key_without(&a, KeyComponent::Shadows),
            cache_key_without(&b, KeyComponent::Shadows)
        );
    }

    /// Drop [`KeyComponent::ConftestChain`] and a conftest change can never invalidate an
    /// entry, however the chain rules are later relaxed.
    #[test]
    fn dropping_the_conftest_chain_component_serves_an_answer_a_conftest_edit_invalidated() {
        let a = KeyInputs {
            chain: [30; 32],
            ..inputs("test_a.py", b"x")
        };
        let b = KeyInputs {
            chain: [31; 32],
            ..inputs("test_a.py", b"x")
        };
        assert_ne!(cache_key(&a), cache_key(&b));
        assert_eq!(
            cache_key_without(&a, KeyComponent::ConftestChain),
            cache_key_without(&b, KeyComponent::ConftestChain)
        );
    }

    /// Drop [`KeyComponent::Path`] and identical bytes at another path are served an answer
    /// whose every node id starts with the wrong file.
    #[test]
    fn dropping_the_path_component_serves_an_answer_computed_for_another_file() {
        let a = inputs("pkg/test_a.py", b"def test_one(): pass");
        let b = inputs("pkg/test_b.py", b"def test_one(): pass");
        assert_ne!(cache_key(&a), cache_key(&b));
        assert_eq!(
            cache_key_without(&a, KeyComponent::Path),
            cache_key_without(&b, KeyComponent::Path)
        );
    }

    /// Drop [`KeyComponent::Content`] and editing a test file changes nothing.
    #[test]
    fn dropping_the_content_component_serves_the_answer_for_the_previous_source() {
        let a = inputs("test_a.py", b"def test_one(): pass");
        let b = inputs("test_a.py", b"def test_one(): pass\ndef test_two(): pass");
        assert_ne!(cache_key(&a), cache_key(&b));
        assert_eq!(
            cache_key_without(&a, KeyComponent::Content),
            cache_key_without(&b, KeyComponent::Content)
        );
    }

    /// The table above is only as complete as [`KeyComponent::ALL`].  A variant added without
    /// being listed there would be hashed by nothing and tested by nothing, so the list is
    /// checked against an exhaustive match — which is a compile error until the new variant is
    /// classified, and a test failure until it is added.
    #[test]
    fn the_component_list_is_exhaustive() {
        for component in KeyComponent::ALL {
            // Exhaustive on purpose: no `_` arm.
            let known = match component {
                KeyComponent::Version
                | KeyComponent::Config
                | KeyComponent::Shadows
                | KeyComponent::ConftestChain
                | KeyComponent::Path
                | KeyComponent::Content => true,
            };
            assert!(known);
        }
        assert_eq!(KeyComponent::ALL.len(), 6);
    }

    /// Length prefixing, asserted rather than assumed: without it `("ab", "c")` and
    /// `("a", "bc")` would be the same byte stream and therefore the same key.
    #[test]
    fn adjacent_components_cannot_be_confused_by_shifting_a_byte() {
        assert_ne!(
            cache_key(&inputs("ab", b"c")),
            cache_key(&inputs("a", b"bc"))
        );
    }

    // -- the config digest -------------------------------------------------

    /// Every [`ResolvedConfig`] field moves the digest.  The exhaustive destructuring in
    /// [`digest_of_config`] is what makes a *new* field a compile error; this is what makes
    /// the existing ones load-bearing.
    #[test]
    fn every_config_field_changes_the_digest() {
        let tmp = TempDir::new().unwrap();
        let base = config_at(tmp.path());
        let baseline = digest_of_config(&base);

        let mutations: Vec<(&str, ResolvedConfig)> = vec![
            (
                "rootdir",
                ResolvedConfig {
                    rootdir: tmp.path().join("other"),
                    ..base.clone()
                },
            ),
            (
                "config_file",
                ResolvedConfig {
                    config_file: Some(tmp.path().join("pytest.ini")),
                    ..base.clone()
                },
            ),
            (
                "testpaths",
                ResolvedConfig {
                    testpaths: owned(&["tests"]),
                    ..base.clone()
                },
            ),
            (
                "python_files",
                ResolvedConfig {
                    python_files: owned(&["check_*.py"]),
                    ..base.clone()
                },
            ),
            (
                "python_classes",
                ResolvedConfig {
                    python_classes: owned(&["Check"]),
                    ..base.clone()
                },
            ),
            (
                "python_functions",
                ResolvedConfig {
                    python_functions: owned(&["check"]),
                    ..base.clone()
                },
            ),
            (
                "norecursedirs",
                ResolvedConfig {
                    norecursedirs: owned(&["build"]),
                    ..base.clone()
                },
            ),
            (
                "addopts",
                ResolvedConfig {
                    addopts: owned(&["-q"]),
                    ..base.clone()
                },
            ),
            (
                "markers",
                ResolvedConfig {
                    markers: owned(&["slow: it is"]),
                    ..base.clone()
                },
            ),
        ];
        for (field, mutated) in mutations {
            assert_ne!(
                digest_of_config(&mutated),
                baseline,
                "changing {field} left the config digest unchanged"
            );
        }
    }

    /// Two different list *shapes* with the same concatenation are different configs.
    #[test]
    fn config_list_boundaries_are_part_of_the_digest() {
        let tmp = TempDir::new().unwrap();
        let split = ResolvedConfig {
            python_files: owned(&["te", "st_*.py"]),
            ..config_at(tmp.path())
        };
        let joined = ResolvedConfig {
            python_files: owned(&["test_*.py"]),
            ..config_at(tmp.path())
        };
        assert_ne!(digest_of_config(&split), digest_of_config(&joined));
    }

    #[test]
    fn the_shadow_digest_is_order_independent_but_content_sensitive() {
        let one: BTreeSet<String> = ["queue".to_string(), "json".to_string()].into();
        let same: BTreeSet<String> = ["json".to_string(), "queue".to_string()].into();
        let more: BTreeSet<String> = ["json".to_string(), "queue".to_string(), "io".to_string()]
            .into_iter()
            .collect();
        assert_eq!(digest_of_shadows(&one), digest_of_shadows(&same));
        assert_ne!(digest_of_shadows(&one), digest_of_shadows(&more));
    }

    #[test]
    fn the_chain_digest_moves_when_a_conftest_appears_changes_or_vanishes() {
        let empty = empty_chain_digest();
        let one = digest_of_chain([("conftest.py", Some(&b"x = 1"[..]))]);
        let edited = digest_of_chain([("conftest.py", Some(&b"x = 2"[..]))]);
        let unreadable = digest_of_chain([("conftest.py", None)]);
        let two = digest_of_chain([
            ("conftest.py", Some(&b"x = 1"[..])),
            ("sub/conftest.py", Some(&b"y = 1"[..])),
        ]);
        assert_ne!(empty, one);
        assert_ne!(one, edited);
        assert_ne!(one, two);
        // An unreadable conftest is not an empty one, and neither is an absent one.
        assert_ne!(unreadable, empty);
        assert_ne!(
            unreadable,
            digest_of_chain([("conftest.py", Some(&b""[..]))])
        );
    }

    /// A one-entry chain cannot be spelled as a two-entry one by moving a separator: the
    /// count is hashed as well as the entries.
    #[test]
    fn chain_entries_cannot_be_merged_by_shifting_bytes() {
        assert_ne!(
            digest_of_chain([("a", Some(&b"bc"[..]))]),
            digest_of_chain([("a", Some(&b"b"[..])), ("", Some(&b"c"[..]))])
        );
    }

    // -- the store ---------------------------------------------------------

    #[test]
    fn a_stored_entry_round_trips() {
        let tmp = TempDir::new().unwrap();
        std::fs::write(tmp.path().join("test_a.py"), "def test_one(): pass").unwrap();
        let cache = cache_for(tmp.path());
        let dir = cache.load_dir("", empty_chain_digest());
        let key = cache.key(&dir, "test_a.py", b"def test_one(): pass");

        let mut fresh = BTreeMap::new();
        let _ = fresh.insert(
            "test_a.py".to_string(),
            (key, vec![test_entry("test_a.py::test_one")]),
        );
        assert!(cache.store_dir(&dir, tmp.path(), fresh));

        let reopened = cache_for(tmp.path());
        let dir = reopened.load_dir("", empty_chain_digest());
        let hit = reopened.get(&dir, "test_a.py", &key).expect("a hit");
        assert_eq!(hit[0].id, "test_a.py::test_one");
        assert_eq!(reopened.stats().hits(), 1);
    }

    /// The cache lives under the directory that is already gitignored.
    #[test]
    fn the_store_is_under_the_gitignored_cache_directory() {
        let tmp = TempDir::new().unwrap();
        std::fs::write(tmp.path().join("test_a.py"), "x").unwrap();
        let cache = cache_for(tmp.path());
        let dir = cache.load_dir("", empty_chain_digest());
        let key = cache.key(&dir, "test_a.py", b"x");
        let mut fresh = BTreeMap::new();
        let _ = fresh.insert("test_a.py".to_string(), (key, Vec::new()));
        assert!(cache.store_dir(&dir, tmp.path(), fresh));

        let store = tmp.path().join(".rustest_cache").join("v2-manifest");
        assert!(store.is_dir(), "{store:?}");
        assert_eq!(std::fs::read_dir(&store).unwrap().count(), 1);
    }

    /// A different key for the same file is never served.
    #[test]
    fn a_key_mismatch_is_not_served() {
        let tmp = TempDir::new().unwrap();
        std::fs::write(tmp.path().join("test_a.py"), "x").unwrap();
        let cache = cache_for(tmp.path());
        let dir = cache.load_dir("", empty_chain_digest());
        let key = cache.key(&dir, "test_a.py", b"x");
        let mut fresh = BTreeMap::new();
        let _ = fresh.insert("test_a.py".to_string(), (key, vec![test_entry("a")]));
        assert!(cache.store_dir(&dir, tmp.path(), fresh));

        let reopened = cache_for(tmp.path());
        let dir = reopened.load_dir("", empty_chain_digest());
        let other = reopened.key(&dir, "test_a.py", b"y");
        assert!(reopened.get(&dir, "test_a.py", &other).is_none());
        assert_eq!(reopened.stats().hits(), 0);
    }

    /// Every flavour of damaged shard loads as empty rather than failing the run.
    #[test]
    fn a_damaged_shard_is_a_miss_not_an_error() {
        for (label, bytes) in [
            ("truncated mid-object", &br#"{"schema":1,"dir":"","entr"#[..]),
            ("not json at all", &b"\x00\x01\x02not json"[..]),
            ("empty", &b""[..]),
            ("valid json, wrong shape", &br#"{"tests":[]}"#[..]),
            (
                "valid shard, unknown schema",
                &br#"{"schema":99,"dir":"","entries":{}}"#[..],
            ),
            (
                "entry whose tests are not tests",
                &br#"{"schema":1,"dir":"","entries":{"test_a.py":{"key":"ff","tests":[{"nope":1}]}}}"#[..],
            ),
        ] {
            let tmp = TempDir::new().unwrap();
            std::fs::write(tmp.path().join("test_a.py"), "x").unwrap();
            let cache = cache_for(tmp.path());
            let dir = cache.load_dir("", empty_chain_digest());
            std::fs::create_dir_all(dir.shard_path.parent().unwrap()).unwrap();
            std::fs::write(&dir.shard_path, bytes).unwrap();

            let reopened = cache_for(tmp.path());
            let dir = reopened.load_dir("", empty_chain_digest());
            let key = reopened.key(&dir, "test_a.py", b"x");
            assert!(
                reopened.get(&dir, "test_a.py", &key).is_none(),
                "{label} produced a hit"
            );
            // ...and the damaged file is replaced rather than left to fail forever.
            let mut fresh = BTreeMap::new();
            let _ = fresh.insert("test_a.py".to_string(), (key, vec![test_entry("a")]));
            assert!(reopened.store_dir(&dir, tmp.path(), fresh), "{label}");
            let recovered = cache_for(tmp.path());
            let dir = recovered.load_dir("", empty_chain_digest());
            assert!(recovered.get(&dir, "test_a.py", &key).is_some(), "{label}");
        }
    }

    /// A cached test whose `tier` was lost decodes as Tier D; serving it would corrupt the
    /// attribution the three-way differential reads, so the label is re-asserted on read.
    #[test]
    fn a_cached_entry_missing_its_tier_is_still_served_as_static() {
        let tmp = TempDir::new().unwrap();
        std::fs::write(tmp.path().join("test_a.py"), "x").unwrap();
        let cache = cache_for(tmp.path());
        let dir = cache.load_dir("", empty_chain_digest());
        let key = cache.key(&dir, "test_a.py", b"x");
        std::fs::create_dir_all(dir.shard_path.parent().unwrap()).unwrap();
        std::fs::write(
            &dir.shard_path,
            format!(
                r#"{{"schema":1,"dir":"","entries":{{"test_a.py":{{"key":"{}","tests":[{{"id":"test_a.py::test_one","path":"test_a.py","qualname":"test_one"}}]}}}}}}"#,
                hex(&key)
            ),
        )
        .unwrap();

        let reopened = cache_for(tmp.path());
        let dir = reopened.load_dir("", empty_chain_digest());
        let hit = reopened.get(&dir, "test_a.py", &key).expect("a hit");
        assert_eq!(hit[0].tier, Tier::Static);
    }

    /// An entry whose file has been deleted is dropped on the next write, so a shard is
    /// bounded by the directory's contents rather than by its history.
    #[test]
    fn entries_for_deleted_files_are_pruned_on_write() {
        let tmp = TempDir::new().unwrap();
        std::fs::write(tmp.path().join("test_a.py"), "x").unwrap();
        std::fs::write(tmp.path().join("test_b.py"), "y").unwrap();
        let cache = cache_for(tmp.path());
        let dir = cache.load_dir("", empty_chain_digest());
        let mut fresh = BTreeMap::new();
        let _ = fresh.insert(
            "test_a.py".to_string(),
            (cache.key(&dir, "test_a.py", b"x"), vec![test_entry("a")]),
        );
        let _ = fresh.insert(
            "test_b.py".to_string(),
            (cache.key(&dir, "test_b.py", b"y"), vec![test_entry("b")]),
        );
        assert!(cache.store_dir(&dir, tmp.path(), fresh));

        std::fs::remove_file(tmp.path().join("test_b.py")).unwrap();
        let next = cache_for(tmp.path());
        let dir = next.load_dir("", empty_chain_digest());
        let mut fresh = BTreeMap::new();
        let _ = fresh.insert(
            "test_a.py".to_string(),
            (
                next.key(&dir, "test_a.py", b"changed"),
                vec![test_entry("a2")],
            ),
        );
        assert!(next.store_dir(&dir, tmp.path(), fresh));

        let raw = std::fs::read_to_string(&dir.shard_path).unwrap();
        assert!(!raw.contains("test_b.py"), "{raw}");
    }

    /// An unchanged shard is not rewritten: the common warm run touches no file at all, which
    /// is both a latency property and what keeps two concurrent readers from racing to write
    /// identical bytes.
    #[test]
    fn an_unchanged_shard_is_not_rewritten() {
        let tmp = TempDir::new().unwrap();
        std::fs::write(tmp.path().join("test_a.py"), "x").unwrap();
        let cache = cache_for(tmp.path());
        let dir = cache.load_dir("", empty_chain_digest());
        let key = cache.key(&dir, "test_a.py", b"x");
        let entry = || {
            let mut fresh = BTreeMap::new();
            let _ = fresh.insert("test_a.py".to_string(), (key, vec![test_entry("a")]));
            fresh
        };
        assert!(cache.store_dir(&dir, tmp.path(), entry()));

        let next = cache_for(tmp.path());
        let dir = next.load_dir("", empty_chain_digest());
        assert!(!next.store_dir(&dir, tmp.path(), entry()));
        assert_eq!(next.stats().writes(), 0);
    }

    /// Concurrent writers never leave a shard a reader cannot parse.  The rename is what makes
    /// that true; without it a reader would see a partially written document.
    #[test]
    fn concurrent_writes_never_leave_an_unparsable_shard() {
        let tmp = TempDir::new().unwrap();
        for index in 0..8 {
            std::fs::write(tmp.path().join(format!("test_{index}.py")), "x").unwrap();
        }
        let root = tmp.path();
        std::thread::scope(|scope| {
            for round in 0..8 {
                let _ = scope.spawn(move || {
                    let cache = cache_for(root);
                    let dir = cache.load_dir("", empty_chain_digest());
                    let mut fresh = BTreeMap::new();
                    for index in 0..8 {
                        let name = format!("test_{index}.py");
                        let key = cache.key(&dir, &name, format!("{round}").as_bytes());
                        let _ = fresh.insert(name, (key, vec![test_entry("a")]));
                    }
                    let _ = cache.store_dir(&dir, root, fresh);
                    // Read it back immediately: a torn document would be observed here.
                    let reader = cache_for(root);
                    let _ = reader.load_dir("", empty_chain_digest());
                });
            }
        });
        let cache = cache_for(tmp.path());
        let dir = cache.load_dir("", empty_chain_digest());
        assert_eq!(dir.loaded.len(), 8);
        // No temporary files left behind.
        let leftovers: Vec<_> = std::fs::read_dir(dir.shard_path.parent().unwrap())
            .unwrap()
            .flatten()
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .filter(|name| name.starts_with(".tmp-"))
            .collect();
        assert!(leftovers.is_empty(), "{leftovers:?}");
    }

    fn cache_for(rootdir: &Path) -> ManifestCache {
        ManifestCache::open(rootdir, &config_at(rootdir), &BTreeSet::new())
    }

    /// An unwritable cache directory is a cache that misses, not a run that fails.
    #[test]
    fn an_unwritable_store_is_survivable() {
        let tmp = TempDir::new().unwrap();
        std::fs::write(tmp.path().join("test_a.py"), "x").unwrap();
        // A *file* where the cache directory must go: `create_dir_all` cannot succeed.
        std::fs::create_dir_all(tmp.path().join(CACHE_DIR)).unwrap();
        std::fs::write(tmp.path().join(CACHE_DIR).join(MANIFEST_CACHE_DIR), "no").unwrap();

        let cache = cache_for(tmp.path());
        let dir = cache.load_dir("", empty_chain_digest());
        let key = cache.key(&dir, "test_a.py", b"x");
        assert!(cache.get(&dir, "test_a.py", &key).is_none());
        let mut fresh = BTreeMap::new();
        let _ = fresh.insert("test_a.py".to_string(), (key, vec![test_entry("a")]));
        assert!(!cache.store_dir(&dir, tmp.path(), fresh));
    }
}
