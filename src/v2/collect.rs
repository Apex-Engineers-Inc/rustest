//! The v2 orchestrator: walk files, drive a pool of spawn workers, assemble the manifest.
//!
//! [`collect`] is the whole of v2 collection seen from outside: it resolves config,
//! decides which files pytest would look at, hands each one to a Python worker process
//! (`python -m rustest._v2_worker`, [`crate::v2::protocol`]), and returns a
//! [`CollectionManifest`].  No live Python object crosses the boundary — that is the
//! organising rule of the v2 spine, and it is what makes the pool possible at all.
//!
//! **pytest is the oracle for the walk.**  Every traversal rule below cites the installed
//! pytest source (`.venv/Lib/site-packages/_pytest/`, pytest 8.4.2) it ports.  The v2 walk
//! deliberately reads **no `.gitignore`** — that is a v1-ism; pytest prunes with
//! `norecursedirs` plus two hard-coded rules (`__pycache__`, virtualenv roots) and nothing
//! else.
//!
//! # Three properties this module owns
//!
//! **Determinism.**  The walk is name-sorted (`_pytest/pathlib.py::scandir`) and the
//! manifest is assembled by *dispatch index*, never in completion order, so the output is
//! byte-identical whichever worker answers first and however many workers there are.
//!
//! **Same-stem routing.**  Files are routed to workers by a hash of the file **stem**, not
//! round-robin.  Two `test_dup.py` files in different non-package directories must land on
//! the *same* interpreter, because that is the only way the second import hits Python's
//! module cache and reproduces pytest's `import file mismatch` collection error
//! (`_pytest/python.py::importtestmodule`).  Round-robin would put them in different
//! processes, where both import cleanly and the manifest silently disagrees with pytest.
//! Distinct stems still spread across the pool, so balance survives.
//!
//! **Loud on drift.**  The protocol is internal — orchestrator and worker ship in the same
//! wheel — so any deviation is a bug, and a bug must fail the run rather than half-work:
//!
//! * an undecodable line is fatal, and the error **quotes the raw line** (the op name is
//!   the only clue to what a skewed peer was saying);
//! * a `collected` carrying both `tests` and `error` — the one malformed shape serde
//!   cannot reject, documented in [`crate::v2::protocol`] — is treated exactly like a
//!   decode error;
//! * a response naming a file other than the one requested is fatal (accepting it would
//!   attribute one file's tests to another);
//! * EOF mid-protocol names the file that was in flight and surfaces the worker's stderr.

use std::collections::{HashMap, HashSet};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::mpsc::Receiver;
use std::sync::{Arc, Mutex};

use crate::v2::config::{
    matches_file_pattern, normpath, resolve_config, ConfigError, ResolvedConfig,
};
use crate::v2::execute::{TestOutcome, TestStatus, SESSION_EXIT_EXIT, SHUTDOWN_TEARDOWN_EXIT};
use crate::v2::manifest::{
    CollectedTest, CollectionErrorEntry, CollectionManifest, MANIFEST_SCHEMA_VERSION,
};
#[cfg(test)]
use crate::v2::manifest_cache::ManifestCache;
use crate::v2::protocol::{CoverageWire, WorkerRequest, WorkerResponse, PROTOCOL_VERSION};
use crate::v2::selection::{select_mask, SelectionError};
use crate::v2::static_collect::{rewrite_plan, static_pass_cached, CacheMode};
use crate::v2::to_posix;

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/// Everything that can abort a collection run.
///
/// Note what is *not* here: a file that fails to import.  That is data — it becomes a
/// [`CollectionErrorEntry`] in the manifest — because one unimportable file must not lose
/// the whole run, exactly as under pytest.  Every variant below is instead a failure of
/// the orchestration itself.
#[derive(Debug)]
pub enum CollectError {
    /// Config resolution failed (pytest raises `UsageError` for these).
    Config(ConfigError),
    /// A CLI path argument does not exist.  Mirrors
    /// `_pytest/main.py::resolve_collection_argument`'s `UsageError`.
    ArgNotFound(PathBuf),
    /// A CLI path argument exists but nothing can collect it — pytest's
    /// `_pytest/main.py::perform_collect` `found no collectors for {arg}` `UsageError`
    /// (exit 4).  See [`initial_file_target`] for which suffixes reach it.
    NoCollectors(PathBuf),
    /// The worker process could not be started at all.
    Spawn { program: String, message: String },
    /// An I/O failure on a live worker's pipes.
    Io {
        worker: usize,
        context: String,
        message: String,
    },
    /// `init` was not answered with a matching `ready`.
    Handshake {
        worker: usize,
        detail: String,
        stderr: String,
    },
    /// A decodable-but-wrong or undecodable response; carries the raw line.
    Protocol {
        worker: usize,
        path: PathBuf,
        detail: String,
        line: String,
    },
    /// The execute-phase twin of [`CollectError::Protocol`].  A separate variant rather
    /// than a reused one because the unit in flight is a **test id**, not a file, and an
    /// error that says "while collecting tests/test_a.py::test_one" would send a reader to
    /// the wrong phase of the run.
    ExecuteProtocol {
        worker: usize,
        id: String,
        detail: String,
        line: String,
    },
    /// EOF (or a broken pipe) mid-execute, naming the test in flight.
    ExecuteWorkerDied {
        worker: usize,
        id: String,
        status: String,
        stderr: String,
    },
    /// EOF (or a broken pipe) mid-protocol, naming the file in flight.
    WorkerDied {
        worker: usize,
        path: PathBuf,
        status: String,
        stderr: String,
    },
    /// `shutdown` was not answered with `bye`, or the process exited non-zero.
    Shutdown {
        worker: usize,
        detail: String,
        stderr: String,
    },
    /// A test called `pytest.exit()`: the **session** is over by request, not broken.
    ///
    /// Carried as a `CollectError` variant so it travels the same channel every other
    /// mid-execute outcome does, and intercepted by [`crate::v2::execute::worker_life`]
    /// before it can be reported as a failure — the results already produced are kept, which
    /// is what pytest does (probed: `1 passed`, exit 2, the exiting test unreported).
    SessionExit {
        worker: usize,
        id: String,
        stderr: String,
    },
    /// A dispatch thread panicked — a bug in this module, never a worker's doing.
    WorkerPanicked { worker: usize },
    /// Unreachable by construction, loud rather than silent: a dispatched file that no
    /// worker returned a result for would otherwise vanish from the manifest.
    MissingResponse { path: PathBuf },
    /// The execute-phase twin: a selected test no worker answered for.  Unreachable by
    /// construction too, and loud for the same reason — a silently missing test is a
    /// shorter report that still exits 0.
    MissingResult { id: String },
    /// A malformed `-k`/`-m` expression.
    ///
    /// Collection owns this now because selection *runs* here: `-k` is applied to the static
    /// (and cached) half of the manifest **before** a worker is spawned, so the expression has
    /// to be compiled before there is a manifest to hand back to the caller.  pytest agrees
    /// on the classification — `_pytest/mark/__init__.py::_parse_expression` raises
    /// `UsageError` — and [`crate::v2::py::collect_error_to_py`] maps it to exit 4.
    Selection(SelectionError),
}

impl std::fmt::Display for CollectError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CollectError::Config(err) => write!(f, "{err}"),
            CollectError::ArgNotFound(path) => {
                write!(f, "file or directory not found: {}", path.display())
            }
            CollectError::NoCollectors(path) => {
                write!(f, "found no collectors for {}", path.display())
            }
            CollectError::Spawn { program, message } => write!(
                f,
                "could not spawn the collection worker `{program}`: {message}"
            ),
            CollectError::Io {
                worker,
                context,
                message,
            } => write!(f, "worker {worker}: I/O failure while {context}: {message}"),
            CollectError::Handshake {
                worker,
                detail,
                stderr,
            } => write!(
                f,
                "worker {worker} failed the protocol handshake: {detail}{}",
                stderr_block(stderr)
            ),
            CollectError::Protocol {
                worker,
                path,
                detail,
                line,
            } => write!(
                f,
                "worker {worker} sent an invalid response while collecting {}: {detail}\n  raw line: {line}",
                path.display()
            ),
            CollectError::ExecuteProtocol {
                worker,
                id,
                detail,
                line,
            } => write!(
                f,
                "worker {worker} sent an invalid response while running {id}: {detail}\n  raw line: {line}"
            ),
            CollectError::ExecuteWorkerDied {
                worker,
                id,
                status,
                stderr,
            } => write!(
                f,
                "worker {worker} died while running {id}: {status}{}",
                stderr_block(stderr)
            ),
            CollectError::WorkerDied {
                worker,
                path,
                status,
                stderr,
            } => write!(
                f,
                "worker {worker} died while collecting {}: {status}{}",
                path.display(),
                stderr_block(stderr)
            ),
            CollectError::Shutdown {
                worker,
                detail,
                stderr,
            } => write!(
                f,
                "worker {worker} did not shut down cleanly: {detail}{}",
                stderr_block(stderr)
            ),
            CollectError::SessionExit { worker, id, stderr } => write!(
                f,
                "worker {worker} ended the session from {id} (pytest.exit){}",
                stderr_block(stderr)
            ),
            CollectError::WorkerPanicked { worker } => {
                write!(f, "the dispatch thread for worker {worker} panicked")
            }
            CollectError::MissingResponse { path } => {
                write!(f, "no worker returned a result for {}", path.display())
            }
            CollectError::MissingResult { id } => {
                write!(f, "no worker returned a result for the test {id}")
            }
            CollectError::Selection(err) => write!(f, "{err}"),
        }
    }
}

impl std::error::Error for CollectError {}

/// Render a worker's captured stderr as an indented block, or nothing when it is empty.
fn stderr_block(stderr: &str) -> String {
    if stderr.is_empty() {
        return String::new();
    }
    let indented: Vec<String> = stderr.lines().map(|line| format!("  | {line}")).collect();
    format!("\n  worker stderr:\n{}", indented.join("\n"))
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// How to start one worker process.
///
/// Production always uses [`WorkerLauncher::module`]; the constructor exists as a named
/// seam so tests can drive the orchestrator with a scripted stand-in worker without
/// mutating process-global environment state.
#[derive(Debug, Clone)]
pub struct WorkerLauncher {
    program: String,
    args: Vec<String>,
    /// Extra environment for the child, layered over the parent's.
    ///
    /// The one thing that travels this way rather than on the wire is **capture**
    /// ([`Self::without_capture`]).  It is spawn configuration, constant for a worker's whole
    /// life and identical for every worker in the pool — exactly like `program` and `args` —
    /// so putting it here keeps [`crate::v2::protocol::PROTOCOL_VERSION`] where it is.  A
    /// per-message option would have to be re-sent with every request it cannot vary across.
    envs: Vec<(String, String)>,
}

/// Set in a worker's environment by [`WorkerLauncher::without_capture`]; read by
/// `python/rustest/_v2_worker.py`.  Mirrored there and **must be renamed in the same
/// commit**.
pub const CAPTURE_ENV: &str = "RUSTEST_V2_CAPTURE";

/// v1's "a rustest run is in progress" flag, set for every worker.
///
/// v1 sets it around `rust.run` (`python/rustest/core.py`), and real suites branch on it —
/// rustest's own `tests/integration/*` skip themselves under it, and
/// `tests/test_conftest_nested/*` *enable* themselves with it.  The v2 worker is where user
/// code runs, so the worker process is where it has to be set; leaving it unset made every
/// one of those files take the wrong branch the moment v2 became the default.
pub const RUNNING_ENV: &str = "RUSTEST_RUNNING";

/// Which engine a test body is running under: `"v2"` in every v2 worker, unset under v1.
///
/// A transition-period affordance, and a deliberate one.  The two engines are not feature
/// identical yet (see the Phase 3 list in `.superpowers/sdd/p1c-task-1-report.md`), and a
/// suite that needs to branch — rustest's own does, for the async-batching gap — otherwise
/// has to sniff behaviour.  Reading an environment variable is what `RUSTEST_RUNNING` already
/// taught every rustest suite to do.
pub const ENGINE_ENV: &str = "RUSTEST_ENGINE";

impl WorkerLauncher {
    /// `python_executable -m rustest._v2_worker` — the real worker.
    pub fn module(python_executable: &str) -> Self {
        Self {
            program: python_executable.to_string(),
            args: vec!["-m".to_string(), "rustest._v2_worker".to_string()],
            envs: vec![
                (RUNNING_ENV.to_string(), "1".to_string()),
                (ENGINE_ENV.to_string(), "v2".to_string()),
            ],
        }
    }

    /// Ask the worker not to redirect a test's `sys.stdout`/`sys.stderr` — `-s` / `--no-capture`.
    ///
    /// The worker's stdout is the protocol channel, so "not captured" cannot mean "goes to
    /// this process's stdout": `main` has already rebound `sys.stdout` to stderr before any
    /// test module is imported.  What `-s` switches off is the *second* redirect, the
    /// per-test `_Capture`, so output flows to the worker's stderr and reaches the user
    /// through `RunReport::worker_stderr` — live-ordered within a worker, interleaved across
    /// a pool.  Documented divergence from pytest, which writes straight to the terminal.
    pub fn without_capture(mut self) -> Self {
        self.envs.push((CAPTURE_ENV.to_string(), "no".to_string()));
        self
    }

    /// A stand-in worker: an arbitrary program and argv, so a test can drive the
    /// orchestrator with a script that mis-speaks the protocol on purpose.
    ///
    /// An argument rather than an environment variable: `std::env::set_var` is
    /// process-global and cargo runs tests on parallel threads, so an env-var switch would
    /// be a data race between tests.  The seam exercises exactly the code path production
    /// uses.
    #[cfg(test)]
    pub(crate) fn scripted(program: &str, args: Vec<String>) -> Self {
        Self {
            program: program.to_string(),
            args,
            envs: Vec::new(),
        }
    }

    fn describe(&self) -> String {
        std::iter::once(self.program.clone())
            .chain(self.args.iter().cloned())
            .collect::<Vec<_>>()
            .join(" ")
    }
}

/// Everything that varies between one collection and another, apart from the paths.
///
/// A struct rather than eight positional arguments because the last four are all "which
/// engine behaviour do you want" knobs and a call site reading
/// `collect(dir, args, exe, 8, true, Auto, Auto, None, None)` says nothing about which `true`
/// is which.
#[derive(Debug, Clone, Default)]
pub struct CollectOptions {
    /// Collect python fences out of a `.md` file **named as an argument** —
    /// `--no-codeblocks` turns even that off.
    ///
    /// It has never applied to a *directory walk* since Phase 4 Task 1's review: pytest
    /// collects no markdown at all, so walking one in meant `rustest tests/` found tests
    /// `pytest tests/` never sees, in any repo with python in its docs.
    pub codeblocks: bool,
    pub tier: TierMode,
    pub cache: CacheMode,
    /// The raw `-k` expression.
    pub keyword: Option<String>,
    /// The raw `-m` expression.
    pub mark: Option<String>,
    /// Compute the assertion-rewrite plan ([`crate::v2::static_collect::rewrite_plan`]).
    ///
    /// **Off by default**, and the default is the interesting half: only a *run* imports the
    /// modules, so only a run can benefit from rewritten assertions. `--v2-collect-only`
    /// would pay a read and a parse per file for a plan it then throws away, on the one
    /// surface whose entire point is latency.
    pub assert_rewrite: bool,
    /// `--cov`: forwarded verbatim onto every worker's `init` line, or `None`.
    ///
    /// Verbatim is the whole contract. The sources are already absolute and already resolved
    /// (a bare `--cov` became the rootdir on the Python side, where the report is rendered and
    /// therefore where the same list has to be known anyway); re-deriving or re-validating
    /// them here would be a second implementation of a rule whose only other implementation
    /// lives next to coverage.py's own `source` handling.
    ///
    /// `--v2-collect-only` never sets it: collect-only imports nothing in the sense that
    /// matters — it does import modules, but it runs no test and reports no coverage, so
    /// measuring would cost every worker a `sys.monitoring` registration for data nothing
    /// reads.
    pub coverage: Option<CoverageWire>,
}

impl CollectOptions {
    /// The production default: codeblocks on, both tiers, cache on, no selection.
    pub fn new() -> Self {
        Self {
            codeblocks: true,
            ..Self::default()
        }
    }
}

/// Collect `args` (or `testpaths`, or `invocation_dir`) into a [`CollectionManifest`].
///
/// `python_executable` is the interpreter the workers run under — `sys.executable` in
/// production, resolved by the caller so this module never guesses. `workers` is the pool
/// size; it is clamped to at least one and to at most the number of files found, so an
/// empty tree spawns no process at all.
pub fn collect(
    invocation_dir: &Path,
    args: &[PathBuf],
    python_executable: &str,
    workers: usize,
    options: &CollectOptions,
) -> Result<CollectionManifest, CollectError> {
    collect_with_launcher(
        invocation_dir,
        args,
        &WorkerLauncher::module(python_executable),
        workers,
        options,
    )
}

/// Which collection tiers a run is allowed to use.
///
/// [`TierMode::DynamicOnly`] is not a fallback, it is the **control leg of the differential**:
/// Tier D is the oracle, so "collect this tree twice, once with Tier S enabled and once
/// without, and diff the manifests" is the only test that can catch a static answer that is
/// subtly wrong.  It is also what the *run* path always uses -- see [`plan`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum TierMode {
    /// Tier S answers what it can; everything else goes to a worker.
    #[default]
    Auto,
    /// Every file goes to a worker.
    DynamicOnly,
}

impl TierMode {
    /// Parse the wire spelling used by the `v2_collect` boundary and the
    /// `RUSTEST_V2_COLLECT_TIER` escape hatch.  An unknown value is **not** an error: this is
    /// a debug knob, and a typo must not turn a user's run into a usage error.  Anything but
    /// the dynamic spellings means the default.
    pub fn from_wire(value: &str) -> Self {
        match value {
            "d" | "dynamic" => TierMode::DynamicOnly,
            _ => TierMode::Auto,
        }
    }
}

/// One file's worth of worker output.
pub(crate) struct FileOutcome {
    pub(crate) tests: Vec<CollectedTest>,
    pub(crate) error: Option<CollectionErrorEntry>,
    /// The file was skipped at module level (`pytest.skip(allow_module_level=True)` /
    /// `importorskip`). Mutually exclusive with both other fields; contributes one
    /// `skipped` to the tally and no ids.
    pub(crate) skipped: Option<String>,
}

/// Everything a run needs before a single process is spawned: the files, the pool size,
/// which worker owns which file, and the handshake payload.
///
/// Extracted so [`crate::v2::execute`] drives the *same* walk, the *same* routing and the
/// *same* `init` as collection does.  A second copy of this in the execute path would be
/// free to drift, and the one property that must not drift is the file→worker mapping:
/// execution dispatches each test to the worker that collected its file, which is only
/// sound while both halves compute it identically.
pub(crate) struct Dispatch {
    pub(crate) rootdir: String,
    /// Per target, in walk order: `Some(tests)` when Tier S answered, `None` when the file is
    /// a worker's job.  Also the tier-attribution surface the differential asserts on.
    pub(crate) static_tests: Vec<Option<Vec<CollectedTest>>>,
    /// How many of Tier S's tests `-k`/`-m` removed **before any process was started**.
    ///
    /// Selection is applied to the static half here rather than to the whole manifest
    /// afterwards, which is what lets a fully static tree answer a `-k` that matches nothing
    /// without spawning an interpreter.  The dynamic half's share is added by
    /// [`collect_with_launcher`] once the workers have reported, because a file nobody has
    /// imported has no tests to deselect yet.  The sum is pytest's `N deselected` either way:
    /// the predicate is per test, so partitioning the tests changes nothing.
    pub(crate) deselected: usize,
    /// The manifest cache this run used, when one was enabled.
    ///
    /// `#[cfg(test)]`: production has no use for it — the pass has already read and written
    /// everything by the time it returns — but its [`crate::v2::manifest_cache::CacheStats`]
    /// are the instrument for the claim that a warm collection *parses nothing*, and that
    /// claim has to be checkable through the same `collect` entry point a user calls.
    #[cfg(test)]
    pub(crate) cache: Option<ManifestCache>,
    /// The same rootdir as a native path.  Carried rather than rebuilt from the posix
    /// string, because anything that writes *next to* the rootdir — the last-failed cache —
    /// needs a path the platform's own APIs accept.
    pub(crate) rootdir_path: PathBuf,
    pub(crate) targets: Vec<PathBuf>,
    /// Per target, in walk order: the Tier S manifest cache key for a file whose assertions
    /// may be rewritten, or `None`.
    ///
    /// Empty (rather than all-`None`) when the caller did not ask for a plan, which is every
    /// path except a run — `--v2-collect-only` imports nothing, so there is nothing to
    /// rewrite and nothing to pay for computing.  [`Dispatch::assert_key`] reads it, and
    /// treats short and empty the same way, so a caller cannot get a *wrong* key by
    /// forgetting to fill it — only no key.
    pub(crate) assert_keys: Vec<Option<String>>,
    /// Per worker, in walk order: `(target index, path)`.  The target index is what puts
    /// the manifest back into walk order however the pool interleaves.
    pub(crate) assignments: Vec<Vec<(usize, PathBuf)>>,
    init: WorkerRequest,
}

/// Resolve config, walk, and route — the whole pre-spawn half of a run, Tier D only.
///
/// **The run path is Tier D only, and that is not a stopgap.**  A worker answers
/// `ExecuteTest` out of the `ExecutionPlan` table `collect_file` filled *while importing the
/// module*; a file Tier S answered was never imported anywhere, so no worker holds a plan for
/// its tests and every one of them would come back `unknown test`.  Tier S therefore belongs
/// to [`collect`] — the `--v2-collect-only` surface, and from Task 2 the manifest cache — and
/// [`crate::v2::execute`] keeps calling this.
pub(crate) fn plan(
    invocation_dir: &Path,
    args: &[PathBuf],
    workers: usize,
    codeblocks: bool,
    assert_rewrite: bool,
    coverage: Option<CoverageWire>,
) -> Result<Dispatch, CollectError> {
    plan_with_options(
        invocation_dir,
        args,
        workers,
        &CollectOptions {
            codeblocks,
            coverage,
            tier: TierMode::DynamicOnly,
            // Belt and braces: `TierMode::DynamicOnly` never reaches the static pass, so the
            // cache is unreachable from the run path anyway.  Saying so explicitly is what
            // makes "a Tier D result is never cached" readable at the call site rather than
            // inferable from two files away.
            cache: CacheMode::Off,
            // The one thing a run needs from the static tier: which files may have their
            // assertions rewritten, and under which key.  Not a collection answer — the
            // manifest still comes from the workers — so it does not weaken the
            // `DynamicOnly` rule above; see `static_collect::rewrite_plan`.
            assert_rewrite,
            ..CollectOptions::default()
        },
    )
}

/// [`plan`], with the tier, cache and selection choices made by the caller.
pub(crate) fn plan_with_options(
    invocation_dir: &Path,
    args: &[PathBuf],
    workers: usize,
    options: &CollectOptions,
) -> Result<Dispatch, CollectError> {
    let (config, targets) = discover(invocation_dir, args, options.codeblocks)?;
    let rootdir = to_posix(&config.rootdir);

    // The Tier S pass, before a single process is spawned.  It is pure computation over files
    // already on disk (plus the manifest cache), so it parallelises and shares nothing.
    let (scanned, _cache) = match options.tier {
        TierMode::DynamicOnly => (targets.iter().map(|_| None).collect::<Vec<_>>(), None),
        TierMode::Auto => {
            let pass = static_pass_cached(&targets, &config.rootdir, &config, options.cache);
            (pass.outcomes.into_iter().map(Some).collect(), pass.cache)
        }
    };
    // `Err` carries the refusal *reason*, which nothing downstream consumes: the manifest is
    // identical either way, and a file that fell back to a worker is not a diagnostic a user
    // asked for.  It is asserted on directly in the tests, which call `static_pass` themselves
    // — carrying it through `Dispatch` unread would be a field the compiler cannot check.
    let mut static_tests: Vec<Option<Vec<CollectedTest>>> = scanned
        .into_iter()
        .map(|outcome| outcome.and_then(Result::ok))
        .collect();

    // Selection, applied to the static half **before** the pool is sized.  A file Tier S
    // answered is a file whose tests are known without running anything, so `-k`/`-m` can be
    // decided on it here; a file Tier D owns cannot be, and must not be — its tests are
    // unknown until it is imported, and its *import error* has to be reported however
    // aggressively the expression deselects (`-k` never hides a broken file).  So there is no
    // "skip the worker because nothing in it matches" case, and there cannot be one.
    let mut deselected = 0;
    for slot in static_tests.iter_mut().flatten() {
        let mask = select_mask(slot, options.keyword.as_deref(), options.mark.as_deref())
            .map_err(CollectError::Selection)?;
        deselected += mask.iter().filter(|keep| !**keep).count();
        let mut keep = mask.into_iter();
        slot.retain(|_| keep.next().unwrap_or(true));
    }

    // Only the files Tier S could **not** answer reach a worker.  Routing stays the stem hash
    // over that subset: same-stem files are always dynamic together (Tier S refuses a shared
    // stem outright), so the property the hash exists for — one interpreter per stem, which is
    // what reproduces pytest's `import file mismatch` — survives the filtering.
    let dynamic: Vec<usize> = (0..targets.len())
        .filter(|index| static_tests[*index].is_none())
        .collect();

    // More workers than files would spawn interpreters with nothing to do; fewer than one
    // could not collect at all.  A fully-static tree gets an empty pool and spawns nothing —
    // which is the whole point of the tier.
    let pool_size = if dynamic.is_empty() {
        0
    } else {
        workers.clamp(1, dynamic.len())
    };

    // The dispatch index recorded here is what puts the manifest back into walk order
    // afterwards, whichever tier produced each file's tests.
    let assignments = route(&targets, &dynamic, pool_size);

    // Computed **after** selection, so a `-k` that empties the tree does not pay for a plan
    // nobody will use, and over `targets` rather than the dynamic subset, so the vector stays
    // indexed by target like every other per-file vector here.
    let assert_keys = if options.assert_rewrite {
        rewrite_plan(&targets, &config.rootdir, &config)
    } else {
        Vec::new()
    };

    let init = WorkerRequest::Init {
        protocol_version: PROTOCOL_VERSION,
        rootdir: rootdir.clone(),
        // Normalised for the same reason `discover` normalises it before resolving config:
        // a `.`/`..` segment would otherwise reach the worker verbatim, and an
        // invocation-relative path would resolve differently there than here.
        invocation_dir: to_posix(&normpath(invocation_dir)),
        python_files: config.python_files.clone(),
        python_classes: config.python_classes.clone(),
        python_functions: config.python_functions.clone(),
        // Already validated by `resolve_config`, which exits 4 before a worker is spawned;
        // passed through verbatim so there is exactly one implementation of the rules.
        asyncio_mode: config.asyncio_mode.clone(),
        asyncio_default_fixture_loop_scope: config.asyncio_default_fixture_loop_scope.clone(),
        asyncio_default_test_loop_scope: config.asyncio_default_test_loop_scope.clone(),
        // Already absolute (`type="paths"`); rendered posix like every other path on the
        // wire. Empty for the overwhelming majority of projects, and then omitted entirely.
        pythonpath: config
            .pythonpath
            .iter()
            .map(|path| to_posix(path))
            .collect(),
        // Cloned rather than moved because `options` is borrowed; `None` here is what makes a
        // run without `--cov` register no monitoring tool in any worker.
        coverage: options.coverage.clone(),
    };

    Ok(Dispatch {
        rootdir,
        static_tests,
        deselected,
        #[cfg(test)]
        cache: _cache,
        rootdir_path: config.rootdir.clone(),
        targets,
        assert_keys,
        assignments,
        init,
    })
}

/// Spawn and handshake the whole pool up front, so a bad interpreter or a version skew is
/// reported before any file is collected.  A failure here drops the workers already
/// started, and `Worker::drop` kills them.
///
/// **Three phases, not one loop, and that is the single largest speed property of a run.**
/// A worker's first ~0.7 s is CPython booting and importing `rustest` — work the parent
/// cannot help with and every worker does identically.  Interleaving `spawn` with a
/// blocking `handshake` serialised all of it: worker *k+1*'s `CreateProcess` was not
/// issued until worker *k* had finished booting, so pool startup cost N x per-worker
/// startup on **every** run (measured at 0.63-0.75 s per worker on every suite and worker
/// count in the Phase 4b Task 1 profile — 74% of the whole run on sqlparse `-n 8`).
/// Issuing all N spawns first lets the N interpreters boot concurrently; sending all N
/// `init`s before reading any `ready` does the same for [`handle_init`]'s work
/// (`_assertion_rewrite`'s hook, `pythonpath`, the coverage tool).
///
/// **The semantics the one-loop version had are preserved exactly**, and they are the
/// reason this function exists at all: every spawn and every handshake still completes
/// before the first `collect_file` is sent, so a bad interpreter or a version skew is
/// still reported before any file is collected, and the *first* failing worker — in index
/// order — is still the one whose error the user sees.  Only the overlap changed.
///
/// [`handle_init`]: python/rustest/_v2_worker.py
pub(crate) fn spawn_pool(
    dispatch: &Dispatch,
    launcher: &WorkerLauncher,
) -> Result<Vec<Worker>, CollectError> {
    // Phase 1 — every `CreateProcess`, back to back.  `?` drops the vector, and
    // `Worker::drop` kills whatever had already started.
    let mut pool: Vec<Worker> = Vec::with_capacity(dispatch.assignments.len());
    for index in 0..dispatch.assignments.len() {
        pool.push(Worker::spawn(index, launcher)?);
    }

    // Phase 2 — every `init` on the wire.  A worker still booting has not read its pipe
    // yet; the write lands in the pipe buffer and the worker picks it up when it gets
    // there, so this loop does not wait for anybody.
    for worker in &mut pool {
        if let Err(err) = worker.send(&dispatch.init) {
            return Err(worker.handshake_error(format!("could not send init: {err}")));
        }
    }

    // Phase 3 — collect the `ready` lines, in index order, so the diagnosis a user gets
    // for a broken pool is the same one the serial loop gave.
    for worker in &mut pool {
        worker.await_ready()?;
    }
    Ok(pool)
}

/// Put per-file outcomes back into walk order and split them into tests and errors.
///
/// `origin[i]` is the **target index** the i-th test came from, which the execute half
/// needs in order to route a test back to the worker that collected its file.  Collection
/// discards it.
pub(crate) struct Assembled {
    pub(crate) tests: Vec<CollectedTest>,
    pub(crate) errors: Vec<CollectionErrorEntry>,
    pub(crate) origin: Vec<usize>,
    /// How many targets answered "module-level skip" — a count, because that is all pytest
    /// exposes for it (no ids).
    pub(crate) module_skipped: usize,
}

pub(crate) fn assemble(
    dispatch: &Dispatch,
    outcomes: Vec<(usize, FileOutcome)>,
) -> Result<Assembled, CollectError> {
    let targets = &dispatch.targets;
    // Reassemble by dispatch index — NOT in completion order.  Tier S's answers seed the
    // slots and the workers' fill the holes; a target can never have both, because `plan`
    // only assigns the ones Tier S left `None`.
    let mut slots: Vec<Option<FileOutcome>> = dispatch
        .static_tests
        .iter()
        .map(|tests| {
            tests.as_ref().map(|tests| FileOutcome {
                tests: tests.clone(),
                // Tier S never produces a collection error: a file it cannot parse, or one
                // whose import could fail, is refused as dynamic and the worker reports the
                // error with pytest's own wording.
                error: None,
                // ...and for the same reason it never produces a module-level skip: the
                // skip is raised *by running* the module, which Tier S does not do. A file
                // whose top level calls anything is refused as dynamic long before this.
                skipped: None,
            })
        })
        .collect();
    for (index, outcome) in outcomes {
        slots[index] = Some(outcome);
    }

    let mut tests = Vec::new();
    let mut errors = Vec::new();
    let mut origin = Vec::new();
    let mut module_skipped = 0usize;
    for (index, (path, slot)) in targets.iter().zip(slots).enumerate() {
        let Some(outcome) = slot else {
            return Err(CollectError::MissingResponse { path: path.clone() });
        };
        origin.extend(std::iter::repeat_n(index, outcome.tests.len()));
        tests.extend(outcome.tests);
        errors.extend(outcome.error);
        module_skipped += usize::from(outcome.skipped.is_some());
    }

    Ok(Assembled {
        tests,
        errors,
        origin,
        module_skipped,
    })
}

/// Collapse per-worker results, keeping the **first** failure in worker order so the
/// reported error is deterministic however the pool interleaves.
pub(crate) fn join_pool<T>(
    results: Vec<Result<Vec<T>, CollectError>>,
) -> Result<Vec<T>, CollectError> {
    let mut merged = Vec::new();
    let mut failure: Option<CollectError> = None;
    for result in results {
        match result {
            Ok(items) => merged.extend(items),
            Err(err) => failure = failure.or(Some(err)),
        }
    }
    match failure {
        Some(err) => Err(err),
        None => Ok(merged),
    }
}

fn collect_with_launcher(
    invocation_dir: &Path,
    args: &[PathBuf],
    launcher: &WorkerLauncher,
    workers: usize,
    options: &CollectOptions,
) -> Result<CollectionManifest, CollectError> {
    // Compile the expressions before anything else, so a malformed `-k` is exit 4 on an empty
    // tree exactly as it is on a full one.  `plan_with_options` only compiles them when there
    // is a static file to apply them to, and pytest's `UsageError` does not wait for a test
    // to exist (`_pytest/mark/__init__.py::_parse_expression` runs from
    // `pytest_collection_modifyitems`, which is called for every session).
    let _ = select_mask(&[], options.keyword.as_deref(), options.mark.as_deref())
        .map_err(CollectError::Selection)?;

    let dispatch = plan_with_options(invocation_dir, args, workers, options)?;
    let mut deselected = dispatch.deselected;

    // No worker has anything to do — an empty tree, or one Tier S answered in full (including
    // one where `-k` deselected every one of its tests).  All of them reach the manifest
    // without a single process being spawned.
    if dispatch.assignments.is_empty() {
        let assembled = assemble(&dispatch, Vec::new())?;
        return Ok(CollectionManifest {
            schema_version: MANIFEST_SCHEMA_VERSION,
            rootdir: dispatch.rootdir,
            tests: assembled.tests,
            errors: assembled.errors,
            deselected,
            module_skipped: assembled.module_skipped,
        });
    }

    let pool = spawn_pool(&dispatch, launcher)?;

    let results = std::thread::scope(|scope| {
        let handles: Vec<_> = pool
            .into_iter()
            .zip(dispatch.assignments.iter().cloned())
            .map(|(worker, files)| {
                let keys = &dispatch.assert_keys;
                scope.spawn(move || run_worker(worker, files, keys))
            })
            .collect();
        handles
            .into_iter()
            .enumerate()
            .map(|(index, handle)| {
                handle
                    .join()
                    .unwrap_or(Err(CollectError::WorkerPanicked { worker: index }))
            })
            .collect::<Vec<_>>()
    });

    // The dynamic half's selection, which could not happen before its files were imported.
    // Applied per outcome so the count is the sum of the two halves; the predicate is per
    // test, so that is the same number pytest reports for the whole list at once.
    let mut outcomes = join_pool(results)?;
    for (_, outcome) in &mut outcomes {
        let mask = select_mask(
            &outcome.tests,
            options.keyword.as_deref(),
            options.mark.as_deref(),
        )
        .map_err(CollectError::Selection)?;
        deselected += mask.iter().filter(|keep| !**keep).count();
        let mut keep = mask.into_iter();
        outcome.tests.retain(|_| keep.next().unwrap_or(true));
    }

    let assembled = assemble(&dispatch, outcomes)?;

    Ok(CollectionManifest {
        schema_version: MANIFEST_SCHEMA_VERSION,
        rootdir: dispatch.rootdir,
        tests: assembled.tests,
        errors: assembled.errors,
        deselected,
        module_skipped: assembled.module_skipped,
    })
}

// ---------------------------------------------------------------------------
// The walk — `_pytest/main.py`, `_pytest/python.py`, `_pytest/pathlib.py`
// ---------------------------------------------------------------------------

/// Resolve config and produce the files to collect, in walk order.
fn discover(
    invocation_dir: &Path,
    args: &[PathBuf],
    codeblocks: bool,
) -> Result<(ResolvedConfig, Vec<PathBuf>), CollectError> {
    // `os.getcwd()` — what pytest's `invocation_params.dir` always is — is absolute and
    // free of `.`/`..`; normalising here gives callers the same guarantee for free.
    let invocation_dir = normpath(invocation_dir);
    let config = resolve_config(&invocation_dir, args).map_err(CollectError::Config)?;
    let roots = initial_paths(&invocation_dir, args, &config)?;

    let mut targets = Vec::new();
    let mut seen = Seen::default();
    for root in &roots {
        if root.is_dir() {
            walk(root, &config, &mut targets, &mut seen);
        } else if initial_file_target(root, codeblocks)? {
            seen.push_file_arg(root, &mut targets);
        }
    }
    Ok((config, targets))
}

/// What to do with an initial path that is a **file**, and the one place pytest's exit-4
/// `found no collectors` is decided.
///
/// An initial path skips the `python_files` filter entirely:
/// `_pytest/python.py::pytest_collect_file` only consults `path_matches_patterns` when
/// `not parent.session.isinitpath(file_path)`.  The `.py` suffix test is *outside* that
/// guard, so it still applies — which leaves the question of what happens to everything
/// else.  **Probed** (pytest 8.4.2, file named on the command line, no other args):
///
/// | argument | pytest exit | why |
/// |---|---|---|
/// | `notes.py` | 0 / 5 | collected as a Module |
/// | `notes.txt` | **5** | `_pytest/doctest.py::_is_doctest` — `path.suffix in (".txt", ".rst") and parent.session.isinitpath(path)` — collects a `DoctestTextfile`, which finds no doctests |
/// | `notes.rst` | **5** | same branch |
/// | `notes.dat`, `notes.md` | **4** | no collector claims it; `perform_collect` raises `UsageError("found no collectors for ...")` |
///
/// The `.txt`/`.rst` row is the one that would be got wrong by intuition: those are *not*
/// usage errors, because pytest's doctest tier claims them unconditionally for an initial
/// path (the `--doctest-glob` default of `test*.txt` governs only files found by *walking*).
/// v2 has no doctest tier, so it produces no tests for them — the same observable answer,
/// reached a different way, and recorded here rather than left to coincidence.
///
/// Returns `Ok(true)` when the file is a collection target, `Ok(false)` when it is
/// legitimately empty, and the usage error otherwise.
///
/// `.md` is rustest's own row and the only departure from the table: pytest answers **4**
/// for `notes.md`, rustest collects its python fences (see [`is_markdown`]).  With
/// `--no-codeblocks` the pytest answer is restored exactly.
fn initial_file_target(path: &Path, codeblocks: bool) -> Result<bool, CollectError> {
    if is_python_source(path) {
        return Ok(true);
    }
    if codeblocks && is_markdown(path) {
        return Ok(true);
    }
    let suffix = path.extension().and_then(|ext| ext.to_str()).unwrap_or("");
    if suffix == "txt" || suffix == "rst" {
        return Ok(false);
    }
    Err(CollectError::NoCollectors(path.to_path_buf()))
}

/// The roots of the walk: CLI args, else `testpaths`, else the invocation dir.
///
/// Port of `_pytest/config/__init__.py::Config._decide_args`, with argument parsing from
/// `_pytest/main.py::resolve_collection_argument` (`::` selection parts split off) and
/// `_pytest/config/findpaths.py::get_dirs_from_args` (option-looking args dropped).
///
/// Two deliberate scope limits, both 1b.2 territory:
/// * a `::` selection part is stripped and ignored, so `test_a.py::test_one` collects the
///   whole file — selection lands with the execution engine;
/// * `testpaths` entries are treated as plain paths; pytest additionally expands them as
///   recursive globs (`glob.iglob(path, recursive=True)`).
fn initial_paths(
    invocation_dir: &Path,
    args: &[PathBuf],
    config: &ResolvedConfig,
) -> Result<Vec<PathBuf>, CollectError> {
    let mut roots = Vec::new();
    for arg in args {
        let raw = arg.to_string_lossy();
        if raw.starts_with('-') {
            continue;
        }
        let file_part = raw.split("::").next().unwrap_or("");
        let path = absolutepath(invocation_dir, Path::new(file_part));
        if !path.exists() {
            return Err(CollectError::ArgNotFound(PathBuf::from(file_part)));
        }
        roots.push(path);
    }
    if !roots.is_empty() {
        return Ok(roots);
    }

    // `_decide_args`: testpaths are consulted only when the invocation dir *is* the
    // rootdir, so `cd subdir && pytest` never silently re-runs the whole suite.
    if invocation_dir == config.rootdir {
        for testpath in &config.testpaths {
            let path = absolutepath(invocation_dir, Path::new(testpath));
            if path.exists() {
                roots.push(path);
            }
        }
    }
    if roots.is_empty() {
        // The `if not result:` fallback: collect from the invocation dir.
        roots.push(invocation_dir.to_path_buf());
    }
    Ok(roots)
}

/// `_pytest/pathlib.py::absolutepath` — join against the invocation dir, then normalise
/// lexically (no symlink resolution).
fn absolutepath(invocation_dir: &Path, path: &Path) -> PathBuf {
    if path.is_absolute() {
        normpath(path)
    } else {
        normpath(&invocation_dir.join(path))
    }
}

/// Depth-first, name-sorted traversal.
///
/// Source: `_pytest/main.py::Dir.collect` — `for direntry in scandir(self.path)` over
/// `_pytest/pathlib.py::scandir`, whose default `sort_key` is `entry.name`; directories
/// and files are visited in one interleaved sorted pass, and `Session.genitems` recurses
/// into each yielded collector immediately.  So a subdirectory is descended at the
/// position its *name* sorts to — not before all files, not after them.
///
/// The sort key is `(name != "__init__.py", name)`, which is `_pytest/python.py::Package.collect`'s
/// key ("always collect `__init__.py` first").  Applying it unconditionally is equivalent:
/// a directory holding an `__init__.py` *is* a Package, and in any other directory the
/// first tuple element is constant.
fn walk(dir: &Path, config: &ResolvedConfig, out: &mut Vec<PathBuf>, seen: &mut Seen) {
    // `scandir` returns `[]` for a directory that cannot be opened, rather than raising.
    let Ok(reader) = std::fs::read_dir(dir) else {
        return;
    };
    let mut entries: Vec<(bool, String, PathBuf, Option<std::fs::FileType>)> = reader
        .flatten()
        .map(|entry| {
            let name = entry.file_name().to_string_lossy().into_owned();
            // The directory enumeration already carries each entry's type on both platforms
            // this runs on, so `file_type()` is free where `is_dir()`/`is_file()` are a
            // `stat` apiece.  On a 500-file tree that is 1 000 syscalls, and on Windows —
            // where every one is intercepted by whatever scanner is installed — it was the
            // single largest term in a warm collection (see the Phase 2 Task 2 report).
            (
                name != "__init__.py",
                name,
                entry.path(),
                entry.file_type().ok(),
            )
        })
        .collect();
    // Python compares `str` by code point, which for UTF-8 is byte order — Rust's `Ord`
    // for `String` agrees.  The file type is not part of the key and never reached, because
    // no two entries in one directory share a name.
    entries.sort_by(|left, right| (&left.0, &left.1).cmp(&(&right.0, &right.1)));

    for (_, name, path, file_type) in entries {
        // `is_dir`/`is_file` follow symlinks, matching `os.DirEntry.is_dir()`'s default;
        // `DirEntry::file_type` does **not**, so a symlink falls back to the following call.
        if is_dir(&path, file_type) {
            if should_prune(&name, &path, config) {
                continue;
            }
            walk(&path, config, out, seen);
        } else if is_file(&path, file_type)
            && is_python_source(&path)
            && matches_python_files(&path, config)
        {
            // **No `.md` here, deliberately.** A markdown file is collected only when it is
            // named as an *argument* (`initial_file_target`), never when a directory walk
            // happens to pass over it. pytest walks the same tree and collects no markdown
            // at all, so a repo with python fences in its docs got extra tests under
            // flagless rustest and nowhere else -- measured on Apex Member Designer, whose
            // `tests/fixtures/SCENARIOS.md` contributed 13 ids pytest never sees, 4 of them
            // failing because documentation snippets do not import what they reference.
            // Testing the docs is still one command away (`rustest README.md docs/`), which
            // is how this repo tests its own; it just is not something `rustest tests/`
            // does behind the user's back.
            seen.push_walked(&path, out);
        }
    }
}

/// `path.is_dir()` — answered from the directory entry when that is the same answer.
///
/// `DirEntry::file_type` is the type of the entry *itself*, so it disagrees with `Path::is_dir`
/// for exactly one case: a symlink, which `Path::is_dir` follows.  That case falls through to
/// the real call, so this is `is_dir` with the syscall removed rather than a different
/// predicate.  An entry whose type could not be read falls through too.
pub(crate) fn is_dir(path: &Path, file_type: Option<std::fs::FileType>) -> bool {
    match file_type {
        Some(file_type) if !file_type.is_symlink() => file_type.is_dir(),
        _ => path.is_dir(),
    }
}

/// The twin of [`is_dir`] for `path.is_file()`.
pub(crate) fn is_file(path: &Path, file_type: Option<std::fs::FileType>) -> bool {
    match file_type {
        Some(file_type) if !file_type.is_symlink() => file_type.is_file(),
        _ => path.is_file(),
    }
}

/// pytest's directory-level ignore rules, in `_pytest/main.py::pytest_ignore_collect` order.
///
/// The initial-path exemption (`Dir.collect` skips this hook for an initial path) is
/// structural here: the walk starts *at* each root, so a root is never tested — which is
/// what makes `rustest build/` collect a directory that `norecursedirs` would otherwise
/// prune.
fn should_prune(name: &str, path: &Path, config: &ResolvedConfig) -> bool {
    // `if collection_path.name == "__pycache__": return True`
    if name == "__pycache__" {
        return true;
    }
    // `if not allow_in_venv and _in_venv(collection_path): return True` — pytest never
    // descends into a virtualenv unless `--collect-in-virtualenv` is given (not a v2 flag
    // yet), however the environment is named.
    if is_virtualenv(path) {
        return true;
    }
    // `if any(fnmatch_ex(pat, collection_path) for pat in norecursepatterns)`
    config
        .norecursedirs
        .iter()
        .any(|pattern| fnmatch_ex(pattern, path))
}

/// `_pytest/main.py::_in_venv` — a virtualenv root carries `pyvenv.cfg` (PEP 405), or
/// `conda-meta/history` for conda environments that predate it.
fn is_virtualenv(path: &Path) -> bool {
    path.join("pyvenv.cfg").is_file() || path.join("conda-meta").join("history").is_file()
}

/// `if file_path.suffix == ".py"` in `_pytest/python.py::pytest_collect_file`.  Python's
/// `.suffix` is case-sensitive, so `.PY` is not a Python file even on Windows.
fn is_python_source(path: &Path) -> bool {
    path.extension().is_some_and(|ext| ext == "py")
}

/// A markdown file, i.e. a **code-block** target.
///
/// This is the one place v2 collects something pytest would not: rustest has tested the
/// python fences in `.md` files since v1 (`src/discovery.rs::collect_from_markdown`, reached
/// through `build_markdown_glob` whenever `enable_codeblocks` is on — which is the default),
/// and the project's own docs suite depends on it.  Dropping the feature at the flip would
/// have been a silent regression for every user testing their README.
///
/// Kept as a *separate* predicate rather than folded into `python_files` because the two
/// answer different questions: `python_files` is pytest's ini-configurable pattern list and
/// must stay a faithful port, while this is a rustest extension governed by
/// `--no-codeblocks`.  A `.md` file is never matched against `python_files`, so a project
/// setting `python_files = *` does not suddenly acquire two collectors for the same file.
fn is_markdown(path: &Path) -> bool {
    path.extension().is_some_and(|ext| ext == "md")
}

/// `_pytest/python.py::path_matches_patterns` over the config's `python_files`.
fn matches_python_files(path: &Path, config: &ResolvedConfig) -> bool {
    config
        .python_files
        .iter()
        .any(|pattern| fnmatch_ex(pattern, path))
}

/// Duplicate bookkeeping for the walk — **pytest collects some duplicates and not others**,
/// and this is where that asymmetry is reproduced.
///
/// pytest has no dedup rule as such: it caches collection *per collector node*
/// (`Session._collection_cache`) and then punches one hole in the cache
/// (`_pytest/main.py::Session.collect` l. 908-913):
///
/// ```text
/// # For backward compat, files given directly multiple
/// # times on the command line should not be deduplicated.
/// handle_dupes = not (
///     len(matchparts) == 1
///     and isinstance(matchparts[0], Path)
///     and matchparts[0].is_file()
/// )
/// ```
///
/// **Probed** (`--collect-only -q`, tree = `test_a.py` with two tests, `pkg/test_b.py`
/// with one):
///
/// | arguments | pytest collects | v2 |
/// |---|---|---|
/// | `test_a.py test_a.py` | `one two one two` (4) | same |
/// | `test_a.py ./test_a.py` | `one two one two` (4) | same |
/// | `test_a.py test_a.py test_a.py` | 6 | same |
/// | `pkg pkg/test_b.py` | `three` (1) | same |
/// | `pkg/test_b.py pkg` | `three` (1) | same |
/// | `pkg pkg` | `three` (1) | same |
/// | `. .` | 3 | same |
/// | `. test_a.py` | `one two` (2) | **`one two three` (3)** |
///
/// The rule that reproduces seven of those eight rows is: a **file named directly as an
/// argument** is collected once per occurrence, *unless* a directory argument already
/// emitted it; anything reached by walking is deduplicated against everything emitted so
/// far.  One pass in argument order is enough, because both orders of the mixed case are
/// covered by the two halves of that rule.
///
/// The last row is a recorded divergence, and it is the one where pytest's mechanism
/// leaks: re-collecting `Dir(.)` for the file argument overwrites the cache entry, so when
/// `genitems` later walks the `Dir(.)` node it sees a cache *hit*, treats it as a duplicate
/// and yields **nothing** — silently dropping `pkg/test_b.py::test_three`, a test the user
/// asked for by naming `.`.  v2 keeps that test.  Reproducing the drop would mean porting
/// the node cache, and the behaviour being ported is a bug.
#[derive(Default)]
struct Seen {
    /// Every path already emitted, whatever produced it.
    emitted: HashSet<PathBuf>,
    /// The subset emitted by a **directory walk**, which is what suppresses a later file
    /// argument for the same path.
    walked: HashSet<PathBuf>,
}

impl Seen {
    /// A file found by walking a directory: first sighting wins, which also keeps walk
    /// order intact.
    fn push_walked(&mut self, path: &Path, out: &mut Vec<PathBuf>) {
        self.walked.insert(path.to_path_buf());
        if self.emitted.insert(path.to_path_buf()) {
            out.push(path.to_path_buf());
        }
    }

    /// A file named directly on the command line: emitted again for every occurrence,
    /// because pytest's cache hole means each such argument re-collects the file.
    fn push_file_arg(&mut self, path: &Path, out: &mut Vec<PathBuf>) {
        if self.walked.contains(path) {
            return;
        }
        self.emitted.insert(path.to_path_buf());
        out.push(path.to_path_buf());
    }
}

/// Port of `_pytest/pathlib.py::fnmatch_ex`, the matcher pytest uses for both
/// `python_files` and `norecursedirs`.
///
/// A separator-free pattern matches the **basename**; a pattern containing a separator
/// matches the **whole path**, and is anchored with a leading `*<sep>` when the path is
/// absolute and the pattern is not.  The Windows branch that rewrites posix separators in
/// the pattern is subsumed by [`matches_file_pattern`], whose `fnmatch` applies
/// `os.path.normcase` — which on Windows already rewrites `/` to `\` in both operands.
fn fnmatch_ex(pattern: &str, path: &Path) -> bool {
    let has_separator =
        pattern.contains(std::path::MAIN_SEPARATOR) || (cfg!(windows) && pattern.contains('/'));

    if !has_separator {
        let name = path
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_default();
        return matches_file_pattern(&name, &[pattern.to_string()]);
    }

    let anchored = if path.is_absolute() && !is_absolute_pattern(pattern) {
        format!("*{}{pattern}", std::path::MAIN_SEPARATOR)
    } else {
        pattern.to_string()
    };
    matches_file_pattern(&path.to_string_lossy(), &[anchored])
}

/// `os.path.isabs` for the pattern side of [`fnmatch_ex`].
///
/// `Path::is_absolute` on Windows requires a prefix (`C:\`, `\\server\share`), so it needs
/// help for the one shape `ntpath.isabs` treats differently: a **single leading
/// separator**.  And CPython changed its mind about that shape mid-support-window, so this
/// is a version split, not a constant.  Probed directly (`ntpath.isabs`, Windows):
///
/// | pattern | 3.12.12 | 3.13.11 | 3.14.2 | `Path::is_absolute` |
/// |---|---|---|---|---|
/// | `\tests` | **True** | False | False | false |
/// | `/tests` | **True** | False | False | false |
/// | `\\srv\share` | True | True | True | true |
/// | `C:\tests`, `C:/tests` | True | True | True | true |
/// | `C:tests`, `tests/data` | False | False | False | false |
///
/// The 3.13 What's New records the change ("`os.path.isabs()` no longer considers a path
/// starting with exactly one (back)slash to be absolute" on Windows).
///
/// **We follow the 3.12 rule, because 3.12 is this project's floor** (`requires-python`),
/// so it is the oldest oracle a user can hold us to.
///
/// What that costs is *almost* nothing, and the "almost" is worth stating exactly, because
/// it is not obvious.  Under the 3.13+ rule such a pattern is relative, so [`fnmatch_ex`]
/// anchors it: `\tests\data` becomes `*` + `\` + `\tests\data` = `*\\tests\data`, whose
/// **doubled** separator no drive-letter path contains anywhere — so both rules end up
/// matching nothing and agree by accident.  They part company only where a path really does
/// contain a doubled separator: a UNC path, at position 0.  So the whole observable
/// divergence is a `norecursedirs`/`python_files` entry written with exactly one leading
/// separator *whose first segment is a UNC server name* (`\srv\share\...` against
/// `\\srv\share\...`), which the 3.13+ rule matches and we do not.  Pinned by
/// `a_single_leading_separator_pattern_follows_the_312_isabs_rule`, and flagged for the
/// final review as the module's one known pytest divergence.
fn is_absolute_pattern(pattern: &str) -> bool {
    if cfg!(windows) && (pattern.starts_with('/') || pattern.starts_with('\\')) {
        return true;
    }
    Path::new(pattern).is_absolute()
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------

/// FNV-1a, 64-bit.  Chosen over `DefaultHasher` because its output is specified rather
/// than "unspecified and subject to change", which keeps the file→worker mapping stable
/// across toolchain upgrades and therefore reproducible when debugging a collision report.
fn fnv1a(bytes: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

/// Which worker collects `path`: a hash of the file **stem**, so every same-stem file
/// lands on one interpreter (see the module docs for why that is load-bearing).
///
/// Retained as the **tie-break** inside [`route`] rather than as the router itself: a hash
/// is a fine way to break a tie between two equal-weight groups and a poor way to balance a
/// pool, because `hash % workers` is not a permutation and its collisions are what left two
/// of Pynite's sixteen workers with no files at all.
fn worker_for(path: &Path, workers: usize) -> usize {
    let stem = path
        .file_stem()
        .map(|stem| stem.to_string_lossy().into_owned())
        .unwrap_or_default();
    (fnv1a(stem.as_bytes()) % workers.max(1) as u64) as usize
}

/// What a file is assumed to cost a worker beyond its own bytes: one module import.
///
/// Bin-packing on raw file size alone would put ten one-line files on one worker and a
/// single 40 KB file on another and call that balanced, when the ten cost ten imports.
/// The Phase 4b profile measured the per-file term directly — 5 000 tests in 100 files ran
/// in 5.64 s and the same 5 000 in 500 files took 21.46 s, i.e. **~40 ms per file** — which
/// is real work no byte count sees.  Expressed in bytes-equivalent so the two terms add:
/// 8 KB is roughly the median test module in the conformance corpus, so a file's fixed cost
/// and its content cost are comparable at the median, and size only dominates for the
/// genuine outliers (jsonschema's 93.8%-of-tests mega-file) where it should.
const FILE_IMPORT_WEIGHT: u64 = 8 * 1024;

/// Assign every dynamic file to a worker, balancing the pool by estimated cost.
///
/// **Longest-processing-time-first bin packing**, which is the standard 4/3-approximation
/// for exactly this problem: sort the items heaviest-first and put each on whichever bin is
/// currently lightest.  It replaces `fnv1a(stem) % pool_size`, and the profile is unambiguous
/// about why — a static hash is not a permutation, so at Pynite's `-n 16` two workers were
/// handed no files at all while the top three held 67% of the pool's CPU, and each of those
/// two still cost a full interpreter startup to do nothing.
///
/// **The item is a stem group, not a file.**  Same-stem files must share an interpreter —
/// that is what reproduces pytest's `import file mismatch`, and Tier S refuses a shared stem
/// outright so the group is always entirely dynamic — so a group is indivisible here and is
/// weighed as the sum of its members.
///
/// **The weight is file size plus [`FILE_IMPORT_WEIGHT`] per file.**  Size is the only
/// signal available at plan time: a Tier D file's test count is unknown until a worker
/// imports it, which is the very work being scheduled.  It is a proxy, not a measurement,
/// and it is a good one for the failure this fixes — the pathological case is always one
/// enormous file, and enormous files are large.
///
/// **Deterministic**, and deliberately so: the same tree must produce the same plan on
/// every run or a `-n 4` reproduction of a routing bug is not a reproduction.  Ties in
/// weight break on [`worker_for`]'s stem hash and then on the stem itself, neither of which
/// depends on walk order or filesystem timing.
fn route(targets: &[PathBuf], dynamic: &[usize], pool_size: usize) -> Vec<Vec<(usize, PathBuf)>> {
    let mut assignments: Vec<Vec<(usize, PathBuf)>> = vec![Vec::new(); pool_size];
    if pool_size == 0 {
        return assignments;
    }

    // Group by stem, keeping each group's members in walk order so a worker still collects
    // its files in the order the tree presented them.
    let mut order: Vec<String> = Vec::new();
    let mut groups: HashMap<String, (u64, Vec<usize>)> = HashMap::new();
    for index in dynamic {
        let path = &targets[*index];
        let stem = path
            .file_stem()
            .map(|stem| stem.to_string_lossy().into_owned())
            .unwrap_or_default();
        let weight = std::fs::metadata(path).map_or(0, |meta| meta.len()) + FILE_IMPORT_WEIGHT;
        let entry = groups.entry(stem.clone()).or_insert_with(|| {
            order.push(stem);
            (0, Vec::new())
        });
        entry.0 += weight;
        entry.1.push(*index);
    }

    // Heaviest first — the half of LPT that does the work.  A light group placed early can
    // be corrected by the groups after it; a heavy one placed last cannot.
    order.sort_by(|left, right| {
        let (left_weight, _) = &groups[left];
        let (right_weight, _) = &groups[right];
        right_weight
            .cmp(left_weight)
            .then_with(|| {
                worker_for(Path::new(left), pool_size).cmp(&worker_for(Path::new(right), pool_size))
            })
            .then_with(|| left.cmp(right))
    });

    let mut load = vec![0u64; pool_size];
    for stem in order {
        let (weight, members) = &groups[&stem];
        // `min_by_key` returns the **first** minimum, so an empty pool fills 0, 1, 2 … and
        // the plan for N equal files on N workers is the identity rather than a scramble.
        let worker = (0..pool_size)
            .min_by_key(|index| load[*index])
            .unwrap_or_default();
        load[worker] += weight;
        for member in members {
            assignments[worker].push((*member, targets[*member].clone()));
        }
    }
    assignments
}

// ---------------------------------------------------------------------------
// The pool
// ---------------------------------------------------------------------------

/// A Windows job object holding exactly one worker, configured to kill everything in it
/// when the last handle closes.
///
/// **This is the whole process-tree lifetime story, and it fixes two separate leaks.**
///
/// * `Child::kill` terminates the worker and *only* the worker. A test that started a
///   server, a reloader or a helper interpreter leaves that grandchild running: the Phase
///   4b profile found four of werkzeug's `live_apps/run.py` reloaders alive eleven hours
///   after the sweep that started them, burning ~0.2 cores apiece and quietly poisoning
///   every subsequent measurement. Closing a job with `KILL_ON_JOB_CLOSE` terminates every
///   process in it, and a process added to a job cannot escape it — the grandchildren are
///   in the job because their parent was.
/// * A killed, crashed or `Ctrl-C`'d orchestrator never runs [`Worker::drop`] at all. The
///   OS closes its handles regardless, so the job closes, so the pool dies with it. That
///   is issue #140, and it is a property of the handle rather than of any cleanup code
///   this crate could be trusted to reach.
///
/// Failure to create or assign is **not** fatal: the job is a containment improvement over
/// `kill`, not a prerequisite for running tests, and an environment that refuses it (an
/// outer job that forbids nesting, a sandbox) must still be able to run a suite.
#[cfg(windows)]
struct JobObject(std::os::windows::io::RawHandle);

/// SAFETY: a Win32 `HANDLE` is a process-wide token, not a thread-affine resource, and
/// this one is owned exclusively by the [`Worker`] that holds it — which `execute` moves
/// into a scoped thread.  Only `Send` is claimed: the handle is never shared by reference.
#[cfg(windows)]
unsafe impl Send for JobObject {}

#[cfg(windows)]
impl JobObject {
    /// Put `child` in a fresh kill-on-close job. `None` if the OS refused at any step, in
    /// which case the worker is reaped the old way and nothing else changes.
    fn adopt(child: &Child) -> Option<Self> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };

        // SAFETY: every call below is a documented Win32 entry point given a handle this
        // function owns (or, for `child`, one that outlives the call). The
        // `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` passed to `SetInformationJobObject` is
        // zeroed and sized by `size_of`, which is exactly the contract that API states.
        unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() {
                return None;
            }
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let set = SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                std::ptr::from_mut(&mut limits).cast(),
                u32::try_from(std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>())
                    .unwrap_or(0),
            );
            // Without the limit the job would be an inert grouping that kills nothing, so
            // it is not worth keeping: drop back to plain `kill` rather than pretend.
            if set == 0 || AssignProcessToJobObject(job, child.as_raw_handle() as _) == 0 {
                windows_sys::Win32::Foundation::CloseHandle(job);
                return None;
            }
            Some(Self(job as _))
        }
    }

    /// Close the handle, which is what terminates the job's processes.
    fn close(&mut self) {
        if !self.0.is_null() {
            // SAFETY: the handle came from `CreateJobObjectW` above, is owned here, and is
            // nulled immediately so it can never be closed twice.
            unsafe {
                windows_sys::Win32::Foundation::CloseHandle(self.0 as _);
            }
            self.0 = std::ptr::null_mut();
        }
    }
}

/// A stand-in on every other platform, so the call sites need no `cfg`.
///
/// Not an oversight and not a TODO: rustest's process-tree problem is a Windows one.
/// `Child::kill` is a `SIGKILL` to a process group leader nowhere, but the harness and the
/// conformance corpus that exposed both leaks run on Windows, and inventing a
/// `PR_SET_PDEATHSIG`/`setsid` design with no failing case to test it against would be
/// speculative plumbing.
#[cfg(not(windows))]
struct JobObject;

#[cfg(not(windows))]
impl JobObject {
    fn adopt(_child: &Child) -> Option<Self> {
        None
    }

    fn close(&mut self) {}
}

/// One worker process and its pipes.
pub(crate) struct Worker {
    index: usize,
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    stderr: Arc<Mutex<Vec<u8>>>,
    /// Disconnects when the stderr drain thread finishes; see [`Worker::diagnostics`].
    stderr_done: Receiver<()>,
    /// The job this worker (and anything it spawns) lives in; see [`JobObject`].
    job: Option<JobObject>,
    reaped: bool,
}

impl Worker {
    fn spawn(index: usize, launcher: &WorkerLauncher) -> Result<Self, CollectError> {
        let mut child = Command::new(&launcher.program)
            .args(&launcher.args)
            .envs(launcher.envs.iter().map(|(key, value)| (key, value)))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|err| CollectError::Spawn {
                program: launcher.describe(),
                message: err.to_string(),
            })?;

        let stdin = child.stdin.take().expect("stdin was piped");
        let stdout = BufReader::new(child.stdout.take().expect("stdout was piped"));
        let mut pipe = child.stderr.take().expect("stderr was piped");

        // stderr is drained on its own thread rather than left in the pipe.  The worker
        // rebinds `sys.stdout` to stderr before importing anything, so a test module that
        // prints at import time writes here; an undrained pipe would fill and deadlock the
        // worker mid-collection.  It is also what makes a crash diagnosable at all.
        //
        // The drain appends incrementally instead of `read_to_end`-ing, so a reader that
        // gives up early (below) still sees everything that arrived.
        let stderr = Arc::new(Mutex::new(Vec::new()));
        let sink = Arc::clone(&stderr);
        let (done, stderr_done) = std::sync::mpsc::channel::<()>();
        std::thread::spawn(move || {
            let mut chunk = [0u8; 4096];
            while let Ok(read) = pipe.read(&mut chunk) {
                if read == 0 {
                    break;
                }
                if let Ok(mut slot) = sink.lock() {
                    slot.extend_from_slice(&chunk[..read]);
                }
            }
            drop(done);
        });

        // After `spawn` rather than before, because there is no portable way to create a
        // process suspended.  The window is the microseconds between `CreateProcess`
        // returning and this call, during which the child has not finished loading
        // CPython, let alone run a test that could spawn anything.
        let job = JobObject::adopt(&child);

        Ok(Self {
            index,
            child,
            stdin,
            stdout,
            stderr,
            stderr_done,
            job,
            reaped: false,
        })
    }

    /// Write one request as a **single** `write_all`.
    ///
    /// `ChildStdin` is unbuffered — every `write_all` is a syscall — so the newline used to
    /// cost a second one on every message. On the execute path that is one extra syscall per
    /// *test*: at 5 000 tests, 5 000 avoidable trips through whatever filter driver Windows
    /// has installed. Appending to the serialised string and writing once puts identical
    /// bytes on the wire. Measured at Phase 2 Task 3.
    fn send(&mut self, request: &WorkerRequest) -> std::io::Result<()> {
        let mut line = serde_json::to_string(request).expect("a WorkerRequest always serializes");
        line.push('\n');
        self.stdin.write_all(line.as_bytes())?;
        self.stdin.flush()
    }

    /// Read one protocol line, skipping blank ones.  `Ok(None)` is EOF — the worker is gone.
    fn receive(&mut self) -> std::io::Result<Option<String>> {
        let mut line = String::new();
        loop {
            line.clear();
            if self.stdout.read_line(&mut line)? == 0 {
                return Ok(None);
            }
            let trimmed = line.trim_end_matches(['\r', '\n']);
            if trimmed.trim().is_empty() {
                continue;
            }
            return Ok(Some(trimmed.to_string()));
        }
    }

    /// Reap the process (killing it first if it is somehow still alive) and return
    /// everything it wrote to stderr.
    ///
    /// Safe to call after EOF on stdout: the worker closes stdout only by exiting.
    ///
    /// The wait for the drain thread is **bounded**, and deliberately so.  The channel
    /// disconnects the instant the thread ends, so the normal path costs nothing; but a
    /// grandchild process that inherited the worker's stderr keeps the pipe open after the
    /// worker itself is gone, and joining outright would hang the orchestrator forever on
    /// the one path that exists to report a failure.  A truncated diagnostic beats a hung
    /// test run.
    ///
    /// The job is closed **before** that wait, not after, and the order is the point: the
    /// grandchildren holding the stderr pipe die with the job, so the drain thread reaches
    /// EOF on its own and the bounded wait stops being the thing that saves the run.  It
    /// is a fallback for the platforms and the environments with no job, not the design.
    fn diagnostics(&mut self) -> String {
        if !self.reaped {
            let _ = self.child.kill();
            let _ = self.child.wait();
            self.reaped = true;
        }
        self.reap_tree();
        let _ = self
            .stderr_done
            .recv_timeout(std::time::Duration::from_millis(500));
        self.stderr
            .lock()
            .map(|buffer| String::from_utf8_lossy(&buffer).trim_end().to_string())
            .unwrap_or_default()
    }

    /// Kill everything the worker started, by closing its job.
    ///
    /// Idempotent, and a no-op where there is no job.  Called from both reaping paths, so
    /// a worker that shut down cleanly leaves no more behind than one that was killed —
    /// which is the case that actually leaks, because a test's server or reloader outlives
    /// the *successful* run that started it.
    fn reap_tree(&mut self) {
        if let Some(job) = self.job.as_mut() {
            job.close();
        }
    }

    /// The receiving half of the handshake: read one `ready` and validate it.
    ///
    /// A `ready` declares the protocol the worker *speaks*, so a mismatch here is real skew
    /// and is fatal.  Separate from the `init` write because [`spawn_pool`] puts every
    /// worker's `init` on the wire before it waits for anybody's answer.
    fn await_ready(&mut self) -> Result<(), CollectError> {
        let line = match self.receive() {
            Ok(Some(line)) => line,
            Ok(None) => {
                return Err(self.handshake_error("the worker exited before answering".to_string()))
            }
            Err(err) => return Err(self.handshake_error(format!("could not read ready: {err}"))),
        };
        let response: WorkerResponse = match serde_json::from_str(&line) {
            Ok(response) => response,
            Err(err) => {
                return Err(
                    self.handshake_error(format!("undecodable answer: {err}\n  raw line: {line}"))
                )
            }
        };
        match response {
            WorkerResponse::Ready { protocol_version } if protocol_version == PROTOCOL_VERSION => {
                Ok(())
            }
            WorkerResponse::Ready { protocol_version } => Err(self.handshake_error(format!(
                "the worker speaks protocol {protocol_version}, this build requires {PROTOCOL_VERSION}"
            ))),
            other => Err(self.handshake_error(format!(
                "expected `ready`, got `{}`",
                response_op(&other)
            ))),
        }
    }

    fn handshake_error(&mut self, detail: String) -> CollectError {
        let stderr = self.diagnostics();
        CollectError::Handshake {
            worker: self.index,
            detail,
            stderr,
        }
    }

    /// `collect_file` -> `collected`, validated on receipt.
    pub(crate) fn collect_one(
        &mut self,
        path: &Path,
        assert_key: Option<&str>,
    ) -> Result<FileOutcome, CollectError> {
        let posix = to_posix(path);
        let request = WorkerRequest::CollectFile {
            path: posix.clone(),
            assert_key: assert_key.map(str::to_string),
        };
        if let Err(err) = self.send(&request) {
            return Err(self.died(path, format!("could not send collect_file: {err}")));
        }

        let line = match self.receive() {
            Ok(Some(line)) => line,
            Ok(None) => return Err(self.died(path, "the worker exited without answering".into())),
            Err(err) => {
                return Err(CollectError::Io {
                    worker: self.index,
                    context: format!("reading the response for {}", path.display()),
                    message: err.to_string(),
                })
            }
        };

        let response: WorkerResponse = match serde_json::from_str(&line) {
            Ok(response) => response,
            Err(err) => {
                return Err(self.protocol(path, format!("undecodable response: {err}"), line))
            }
        };

        match response {
            WorkerResponse::Collected {
                path: echoed,
                tests,
                error,
                skipped,
            } => {
                if echoed != posix {
                    return Err(self.protocol(
                        path,
                        format!("the response names `{echoed}`, not the requested file"),
                        line,
                    ));
                }
                // The malformed shapes serde cannot reject (see `protocol.rs`): treated
                // exactly like a decode error.  The three fields are exclusive by contract
                // and only by contract, so every pair is checked — a line carrying both
                // tests and a module-level skip would otherwise be absorbed as "tests", and
                // the tally would be silently one short.
                let present = usize::from(!tests.is_empty())
                    + usize::from(error.is_some())
                    + usize::from(skipped.is_some());
                if present > 1 {
                    return Err(self.protocol(
                        path,
                        "the response carries more than one of tests, error and skipped"
                            .to_string(),
                        line,
                    ));
                }
                Ok(FileOutcome {
                    tests,
                    error,
                    skipped,
                })
            }
            other => Err(self.protocol(
                path,
                format!("expected `collected`, got `{}`", response_op(&other)),
                line,
            )),
        }
    }

    fn protocol(&self, path: &Path, detail: String, line: String) -> CollectError {
        CollectError::Protocol {
            worker: self.index,
            path: path.to_path_buf(),
            detail,
            line,
        }
    }

    /// `execute_batch` -> N x `test_result` + `batch_done`, validated on receipt.
    ///
    /// Results are **appended to `out` as they arrive**, and the return value is only the
    /// worker's `stopped` flag. The out-parameter is not a style choice: a worker can die
    /// mid-batch on `pytest.exit()`, and pytest keeps every result it had already reported
    /// before the bail-out. Returning `Result<Vec<_>, _>` would drop exactly those results on
    /// the one path that must preserve them — pinned by
    /// `a_pytest_exit_stops_the_session_and_keeps_the_results_so_far`.
    ///
    /// Three per-result checks, each because the alternative is a *silently wrong report*
    /// rather than a loud failure:
    ///
    /// * the echoed `id` must be the one at this position — a worker answering out of order
    ///   would otherwise attribute one test's outcome to another. Name and position are both
    ///   checked, because they fail differently: a wrong name is a worker answering the wrong
    ///   test, while matching names in the wrong *order* would be a reordering the report
    ///   absorbs without trace;
    /// * the `status` must be one of the documented six. `src/v2/protocol.rs` leaves the
    ///   field an unvalidated `String` **on purpose** and says so: an enum would reject the
    ///   line inside serde with no test id in hand, so the check belongs here, where the
    ///   request that caused it and the worker's stderr are both available;
    /// * anything that is neither a `test_result` nor the terminator is drift by op.
    ///
    /// Three terminator checks close the batch:
    ///
    /// * `executed` must equal the number of results seen. This is the only place a *lost*
    ///   result is detectable at all — the stream still ends, the ids still line up, and the
    ///   missing test would simply be absent from the report;
    /// * a batch that ended without `stopped` must have executed every id. Anything else is a
    ///   worker that dropped work without saying so;
    /// * a batch may only report `stopped` when this request asked for it -- `-x` **or**
    ///   `--maxfail`. `stopped` is the
    ///   one field a worker can set that legitimately *shrinks* the report, and the
    ///   orchestrator acts on it by setting the pool-wide stop flag — so an unsolicited
    ///   `stopped` would truncate a run that never asked to stop, and every test after it
    ///   would be missing rather than reported.
    ///
    /// Errors name the batch's **first unanswered test** rather than the whole file: the
    /// results already received are attributed and correct, so the id a reader needs is the
    /// one the stream stalled on. When the batch is complete and the terminator itself is
    /// the problem, it falls back to the first id, which is the only one still in scope.
    pub(crate) fn execute_batch(
        &mut self,
        ids: &[String],
        stop_on_failure: bool,
        max_fail: Option<usize>,
        out: &mut Vec<TestOutcome>,
    ) -> Result<bool, CollectError> {
        let first = ids.first().map(String::as_str).unwrap_or("<empty batch>");
        let base = out.len();
        let request = WorkerRequest::ExecuteBatch {
            ids: ids.to_vec(),
            stop_on_failure,
            max_fail,
        };
        if let Err(err) = self.send(&request) {
            return Err(self.execute_died(first, format!("could not send execute_batch: {err}")));
        }

        out.reserve(ids.len());
        loop {
            // The id this line is *about*, for diagnostics: the one we are still waiting for,
            // or the last one if the batch is complete and this is the terminator.
            let seen = out.len() - base;
            let expected = ids.get(seen).map(String::as_str).unwrap_or(first);

            let line = match self.receive() {
                Ok(Some(line)) => line,
                Ok(None) => {
                    return Err(self.execute_died(expected, "the worker exited mid-batch".into()))
                }
                Err(err) => {
                    return Err(CollectError::Io {
                        worker: self.index,
                        context: format!("reading a batch result for {expected}"),
                        message: err.to_string(),
                    })
                }
            };

            let response: WorkerResponse = match serde_json::from_str(&line) {
                Ok(response) => response,
                Err(err) => {
                    return Err(self.execute_protocol(
                        expected,
                        format!("undecodable response: {err}"),
                        line,
                    ))
                }
            };

            match response {
                WorkerResponse::TestResult {
                    id: echoed,
                    status,
                    duration_s,
                    message,
                    stdout,
                    stderr,
                } => {
                    let Some(wanted) = ids.get(seen) else {
                        return Err(self.execute_protocol(
                            expected,
                            format!("a {}th result for a batch of {}", seen + 1, ids.len()),
                            line,
                        ));
                    };
                    if &echoed != wanted {
                        return Err(self.execute_protocol(
                            wanted,
                            format!("the response names `{echoed}`, not the test at this position"),
                            line,
                        ));
                    }
                    let Some(status) = TestStatus::parse(&status) else {
                        return Err(self.execute_protocol(
                            wanted,
                            format!("unknown status `{status}`"),
                            line,
                        ));
                    };
                    out.push(TestOutcome {
                        id: echoed,
                        status,
                        duration_s,
                        message,
                        stdout,
                        stderr,
                    });
                }
                WorkerResponse::BatchDone { executed, stopped } => {
                    if executed != seen {
                        return Err(self.execute_protocol(
                            expected,
                            format!(
                                "the worker says it executed {executed} tests, {seen} results arrived"
                            ),
                            line,
                        ));
                    }
                    // `stopped` is only *believable* when this batch asked for it.  A worker
                    // that raises the flag on a run with no `-x` truncates the whole pool's
                    // report — `worker_life` sets the shared stop flag on it, and the main
                    // thread then treats every missing slot as expected rather than as the
                    // lost result it is.  So the one claim a batch can make that silently
                    // shrinks a green run is exactly the one that must be checked against
                    // what was asked, not taken on the worker's word.
                    // `--maxfail` is the second thing that legitimately stops a batch, and
                    // the check has to know about it or a `--maxfail=2` run is a protocol
                    // error instead of a short run.
                    if stopped && !stop_on_failure && max_fail.is_none() {
                        return Err(self.execute_protocol(
                            expected,
                            "the batch reports stopping early, but neither `-x` nor `--maxfail` was in effect"
                                .to_string(),
                            line,
                        ));
                    }
                    if !stopped && seen != ids.len() {
                        return Err(self.execute_protocol(
                            expected,
                            format!(
                                "the batch ended after {seen} of {} tests without stopping early",
                                ids.len()
                            ),
                            line,
                        ));
                    }
                    return Ok(stopped);
                }
                other => {
                    return Err(self.execute_protocol(
                        expected,
                        format!(
                            "expected `test_result` or `batch_done`, got `{}`",
                            response_op(&other)
                        ),
                        line,
                    ))
                }
            }
        }
    }

    fn execute_protocol(&self, id: &str, detail: String, line: String) -> CollectError {
        CollectError::ExecuteProtocol {
            worker: self.index,
            id: id.to_string(),
            detail,
            line,
        }
    }

    fn execute_died(&mut self, id: &str, detail: String) -> CollectError {
        let (code, status) = match self.child.wait() {
            Ok(status) => {
                self.reaped = true;
                (status.code(), status.to_string())
            }
            Err(err) => (None, format!("could not wait for the process: {err}")),
        };
        let stderr = self.diagnostics();
        // `pytest.exit()` is the one way a worker legitimately stops mid-execute, and the
        // exit code is the whole signal — the same channel `SHUTDOWN_TEARDOWN_EXIT` uses, and
        // the reason this fix needs no new protocol op. Reporting it as `ExecuteWorkerDied`
        // would call a user's deliberate bail-out an orchestration failure (exit 3) and throw
        // away every result the worker had already produced.
        if code == Some(SESSION_EXIT_EXIT) {
            return CollectError::SessionExit {
                worker: self.index,
                id: id.to_string(),
                stderr,
            };
        }
        CollectError::ExecuteWorkerDied {
            worker: self.index,
            id: id.to_string(),
            status: format!("{detail} ({status})"),
            stderr,
        }
    }

    /// Build the "worker is gone" error for the file that was in flight.
    ///
    /// The `wait()` here is deliberately **unbounded**, unlike the stderr drain's bounded
    /// wait, and the asymmetry is the point: the exit status *is* the diagnosis here — the
    /// real worker exits 2 exactly on protocol drift and 0 otherwise (`_v2_worker.py::main`)
    /// — so killing it after a timeout would replace the one informative byte with our own
    /// signal.  It is also safe against the real worker: this path is reached only from EOF
    /// on stdout or a broken stdin pipe, and EOF means every handle to the write end is
    /// closed, i.e. the child (and any process that inherited its stdout) is gone, so the
    /// wait returns immediately.  A stand-in that closed stdout while staying alive could
    /// block here; that shape is a bug in a worker, and hanging visibly on it beats
    /// reporting a killed-by-us status that hides the real one.
    fn died(&mut self, path: &Path, detail: String) -> CollectError {
        let status = match self.child.wait() {
            Ok(status) => {
                self.reaped = true;
                status.to_string()
            }
            Err(err) => format!("could not wait for the process: {err}"),
        };
        let stderr = self.diagnostics();
        CollectError::WorkerDied {
            worker: self.index,
            path: path.to_path_buf(),
            status: format!("{detail} ({status})"),
            stderr,
        }
    }

    /// `shutdown` -> `bye`, then reap.  Returns the process's exit status.
    ///
    /// Split out from [`Worker::shutdown`] because the *meaning* of a non-zero status
    /// differs between the two phases (see [`Worker::shutdown_run`]) while the handshake
    /// does not, and two copies of a handshake are two chances to drift.
    fn shutdown_and_reap(&mut self) -> Result<std::process::ExitStatus, CollectError> {
        if let Err(err) = self.send(&WorkerRequest::Shutdown) {
            return Err(self.shutdown_error(format!("could not send shutdown: {err}")));
        }
        let line = match self.receive() {
            Ok(Some(line)) => line,
            Ok(None) => {
                return Err(self.shutdown_error("the worker exited without answering".to_string()))
            }
            Err(err) => return Err(self.shutdown_error(format!("could not read bye: {err}"))),
        };
        match serde_json::from_str::<WorkerResponse>(&line) {
            Ok(WorkerResponse::Bye) => {}
            Ok(other) => {
                return Err(self.shutdown_error(format!(
                    "expected `bye`, got `{}`\n  raw line: {line}",
                    response_op(&other)
                )))
            }
            Err(err) => {
                return Err(
                    self.shutdown_error(format!("undecodable answer: {err}\n  raw line: {line}"))
                )
            }
        }

        let status = match self.child.wait() {
            Ok(status) => status,
            Err(err) => {
                return Err(self.shutdown_error(format!("could not wait for the process: {err}")))
            }
        };
        self.reaped = true;
        Ok(status)
    }

    /// `shutdown` -> `bye` -> exit 0.  Anything else is a failed run: during **collection**
    /// the worker opens no fixtures, so the only way it exits non-zero after answering is
    /// protocol drift, and swallowing that would turn a bug into a quietly short manifest.
    fn shutdown(&mut self) -> Result<(), CollectError> {
        let status = self.shutdown_and_reap()?;
        if status.success() {
            return Ok(());
        }
        Err(self.shutdown_error(format!("the worker exited with {status}")))
    }

    /// The **execute** phase's shutdown, where exit 3 is data rather than a fault.
    ///
    /// `_v2_worker.py::SHUTDOWN_TEARDOWN_EXIT = 3` means: every response was written, `bye`
    /// was sent, and *then* a module- or session-scoped fixture teardown raised.  The
    /// stream is complete; a user's teardown is broken.  That is not orchestration drift
    /// (exit 2) and collapsing the two destroys the diagnosis — so this returns the failure
    /// instead of raising it, and the run reports it the way pytest reports a teardown
    /// error: as an **error, which makes the run exit 1** (probed: a `tearDownClass` that
    /// raises gives `1 passed, 1 error` and exit 1).
    ///
    /// It cannot be attributed to a test — the test that triggered it was already answered,
    /// correctly, because its body passed — so the worker's stderr, which carries the
    /// traceback, travels with it.
    pub(crate) fn shutdown_run(&mut self) -> Result<Option<String>, CollectError> {
        let status = self.shutdown_and_reap()?;
        if status.success() {
            return Ok(None);
        }
        if status.code() == Some(SHUTDOWN_TEARDOWN_EXIT) {
            let stderr = self.diagnostics();
            return Ok(Some(format!(
                "worker {}: a fixture teardown failed after the last test was reported{}",
                self.index,
                stderr_block(&stderr)
            )));
        }
        Err(self.shutdown_error(format!("the worker exited with {status}")))
    }

    /// Everything this worker wrote to stderr.  Not a failure signal: class- and
    /// module-scoped teardown output is drained at a boundary, outside the per-test capture
    /// window, so a *successful* run's stderr can carry legitimate user output (see the
    /// 1b.2 Task 3 divergence note).  The run surfaces it; it never grades it.
    pub(crate) fn take_stderr(&mut self) -> String {
        self.diagnostics()
    }

    fn shutdown_error(&mut self, detail: String) -> CollectError {
        let stderr = self.diagnostics();
        CollectError::Shutdown {
            worker: self.index,
            detail,
            stderr,
        }
    }
}

impl Drop for Worker {
    /// Never leave an orphan interpreter behind when a run aborts early — nor anything the
    /// worker's tests started.  The drain thread is left detached: it ends on its own when
    /// the pipe closes, and waiting for it here would reintroduce the hang
    /// [`Worker::diagnostics`] exists to avoid.
    fn drop(&mut self) {
        if !self.reaped {
            let _ = self.child.kill();
            let _ = self.child.wait();
            self.reaped = true;
        }
        self.reap_tree();
    }
}

/// The `op` a response would carry on the wire, for error messages.
fn response_op(response: &WorkerResponse) -> &'static str {
    match response {
        WorkerResponse::Ready { .. } => "ready",
        WorkerResponse::Collected { .. } => "collected",
        // Never sent during collection — a worker answering `collect_file` with one is
        // exactly the drift this function names in the error.
        WorkerResponse::TestResult { .. } => "test_result",
        WorkerResponse::BatchDone { .. } => "batch_done",
        WorkerResponse::Bye => "bye",
    }
}

/// One worker's whole life: collect its files in order, then shut it down.
///
/// `assert_keys` is indexed by **target**, so the lookup is by the target index that travels
/// with each file rather than by position in `files` — a worker holds an arbitrary subset of
/// the walk, and indexing it by its own position would hand every worker the first N keys of
/// the tree.
fn run_worker(
    mut worker: Worker,
    files: Vec<(usize, PathBuf)>,
    assert_keys: &[Option<String>],
) -> Result<Vec<(usize, FileOutcome)>, CollectError> {
    let mut outcomes = Vec::with_capacity(files.len());
    for (index, path) in &files {
        let key = assert_keys.get(*index).and_then(Option::as_deref);
        outcomes.push((*index, worker.collect_one(path, key)?));
    }
    worker.shutdown()?;
    Ok(outcomes)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    // The interpreter that runs the **real** worker in the end-to-end tests.  It lives in
    // `v2::mod` rather than here because `v2::py`'s tests need the same one.
    use crate::v2::test_python as worker_python;

    // --- fixtures ---------------------------------------------------------

    fn real_worker() -> WorkerLauncher {
        WorkerLauncher::module(&worker_python())
    }

    /// [`discover`] with code blocks **on**, which is the CLI default.  Every walk test below
    /// predates the `.md` tier and asserts on `.py` trees, so the flag is invisible to them;
    /// [`is_markdown`]'s own behaviour is pinned by the codeblock tests further down.
    fn discover_default(
        invocation_dir: &Path,
        args: &[PathBuf],
    ) -> Result<(ResolvedConfig, Vec<PathBuf>), CollectError> {
        discover(invocation_dir, args, true)
    }

    /// [`collect_with_launcher`] with code blocks on — same reasoning as [`discover_default`].
    fn collect_default(
        invocation_dir: &Path,
        args: &[PathBuf],
        launcher: &WorkerLauncher,
        workers: usize,
    ) -> Result<CollectionManifest, CollectError> {
        collect_with_launcher(
            invocation_dir,
            args,
            launcher,
            workers,
            &CollectOptions {
                codeblocks: true,
                tier: TierMode::DynamicOnly,
                cache: CacheMode::Off,
                ..CollectOptions::default()
            },
        )
    }

    /// A stand-in worker: a `python -c` script speaking (or mis-speaking) the protocol.
    ///
    /// This is the seam the crash/drift tests need.  It is an argument rather than an
    /// environment variable on purpose: `std::env::set_var` is process-global, and cargo
    /// runs the test binary's tests on parallel threads, so an env-var switch would be a
    /// data race between tests (and is `unsafe` from edition 2024 onwards).  The seam
    /// exercises exactly the same `collect_with_launcher` path production uses.
    fn scripted_worker(script: &str) -> WorkerLauncher {
        // Every well-behaved stand-in below hard-codes [`READY`].  Left stale after a
        // protocol bump it would fail the *handshake* in each of them, so seven unrelated
        // tests would report version skew instead of the crash or drift they exist to pin.
        assert!(
            READY.contains(&format!(r#""protocol_version":{PROTOCOL_VERSION}"#)),
            "the scripted `ready` line is stale for protocol {PROTOCOL_VERSION}: {READY}"
        );
        WorkerLauncher::scripted(&worker_python(), vec!["-c".to_string(), script.to_string()])
    }

    const READY: &str = r#"sys.stdout.write('{"op":"ready","protocol_version":7}\n')"#;

    /// A well-behaved stand-in worker that collects nothing but **records itself**: one log
    /// file per process (named by pid, created at startup) listing the files it was asked
    /// for.  Counting the files in `log_dir` counts the interpreters actually spawned.
    fn counting_worker(log_dir: &Path) -> WorkerLauncher {
        let script = format!(
            "import json, os, sys\n\
             log = open('{dir}/w-%d.txt' % os.getpid(), 'w', encoding='utf-8')\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   if message['op'] == 'init':\n\
             \x20       {READY}\n\
             \x20   elif message['op'] == 'collect_file':\n\
             \x20       log.write(message['path'] + chr(10))\n\
             \x20       log.flush()\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': message['path']}}) + chr(10))\n\
             \x20   else:\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n",
            dir = to_posix(log_dir)
        );
        scripted_worker(&script)
    }

    /// A well-behaved stand-in worker that **writes the `init` line it received** to *log*,
    /// so a test can assert on what the orchestrator actually put on the wire rather than on
    /// what it meant to.
    fn init_recording_worker(log: &Path) -> WorkerLauncher {
        let script = format!(
            "import json, sys\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   if message['op'] == 'init':\n\
             \x20       out = open('{log}', 'w', encoding='utf-8'); out.write(line); out.close()\n\
             \x20       {READY}\n\
             \x20   elif message['op'] == 'collect_file':\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': message['path']}}) + chr(10))\n\
             \x20   else:\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n",
            log = to_posix(log)
        );
        scripted_worker(&script)
    }

    /// The phrase `std::process::ExitStatus` renders for a plain exit code.  Asserting on it
    /// rather than on a bare digit keeps the exit-status assertions from passing by
    /// coincidence — a temp path or a pid can contain any digit.
    fn exit_status_phrase(code: i32) -> String {
        if cfg!(windows) {
            format!("exit code: {code}")
        } else {
            format!("exit status: {code}")
        }
    }

    fn write_file(path: &Path, content: &str) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, content).unwrap();
    }

    /// Build a temp tree.  Every tree gets a `pytest.ini`, which pins rootdir to the temp
    /// directory instead of letting `resolve_config` walk out into whatever happens to
    /// live above the system temp directory.
    fn tree(files: &[(&str, &str)]) -> TempDir {
        let tmp = TempDir::new().unwrap();
        write_file(&tmp.path().join("pytest.ini"), "[pytest]\n");
        for (rel, content) in files {
            write_file(&tmp.path().join(rel), content);
        }
        tmp
    }

    fn module(name: &str) -> String {
        format!("def test_{name}():\n    pass\n")
    }

    /// Rootdir-relative posix paths of everything the walk selected, in walk order.
    fn discovered(tmp: &TempDir, args: &[PathBuf]) -> Vec<String> {
        let (config, targets) = discover_default(tmp.path(), args).unwrap();
        targets
            .iter()
            .map(|path| {
                path.strip_prefix(&config.rootdir)
                    .unwrap_or(path)
                    .to_string_lossy()
                    .replace('\\', "/")
            })
            .collect()
    }

    fn ids(manifest: &CollectionManifest) -> Vec<String> {
        manifest.tests.iter().map(|t| t.id.clone()).collect()
    }

    // --- the walk ---------------------------------------------------------

    /// pytest walks a directory's entries **sorted by name**, descending into a
    /// subdirectory at the position its name sorts to — not all files first, not all
    /// directories first.  Source: `_pytest/main.py::Dir.collect` (`for direntry in
    /// scandir(self.path)`) over `_pytest/pathlib.py::scandir`, which sorts by
    /// `entry.name`; `Session.genitems` then recurses into each yielded collector in
    /// order, making the traversal a name-ordered depth-first walk.
    ///
    /// `sub` therefore comes before `test_a.py` and `zsub` after `test_b.py` — the whole
    /// point of this fixture.
    #[test]
    fn walk_is_name_sorted_and_depth_first() {
        let tmp = tree(&[
            ("test_b.py", &module("b")),
            ("test_a.py", &module("a")),
            ("helper.py", &module("helper")),
            ("sub/test_c.py", &module("c")),
            ("zsub/test_d.py", &module("d")),
        ]);

        assert_eq!(
            discovered(&tmp, &[]),
            vec!["sub/test_c.py", "test_a.py", "test_b.py", "zsub/test_d.py"]
        );
    }

    /// Inside a package, `__init__.py` is walked **first** whatever its name sorts to:
    /// `_pytest/python.py::Package.collect`'s `sort_key` is
    /// `(entry.name != "__init__.py", entry.name)` ("Always collect `__init__.py` first").
    ///
    /// `AAA.py` is the fixture's whole point — `'A'` (0x41) sorts before `'_'` (0x5F), so
    /// plain name order would put it ahead of `__init__.py` and only the package rule
    /// prevents that.  `python_files = *.py` is what makes both files visible at all.
    #[test]
    fn a_packages_init_is_walked_first() {
        let tmp = TempDir::new().unwrap();
        write_file(
            &tmp.path().join("pytest.ini"),
            "[pytest]\npython_files = *.py\n",
        );
        write_file(&tmp.path().join("pkg/__init__.py"), "");
        write_file(&tmp.path().join("pkg/AAA.py"), &module("aaa"));
        write_file(&tmp.path().join("pkg/zzz.py"), &module("zzz"));

        assert_eq!(
            discovered(&tmp, &[]),
            vec!["pkg/__init__.py", "pkg/AAA.py", "pkg/zzz.py"]
        );
    }

    /// `python_files` selects files by basename glob (`_pytest/python.py::pytest_collect_file`
    /// -> `path_matches_patterns`), and the patterns come from the config, not from a
    /// hard-coded list.
    #[test]
    fn python_files_is_config_driven() {
        let tmp = TempDir::new().unwrap();
        write_file(
            &tmp.path().join("pytest.ini"),
            "[pytest]\npython_files = check_*.py\n",
        );
        write_file(&tmp.path().join("check_one.py"), &module("one"));
        write_file(&tmp.path().join("test_two.py"), &module("two"));

        assert_eq!(discovered(&tmp, &[]), vec!["check_one.py"]);
    }

    /// The default `norecursedirs` list plus the two unconditional pytest prunes:
    /// `__pycache__` (`_pytest/main.py::pytest_ignore_collect` first branch) and virtualenv
    /// roots (`_in_venv`, detected by `pyvenv.cfg`).
    #[test]
    fn default_norecursedirs_and_pytest_prunes_are_honoured() {
        let tmp = tree(&[
            ("keep/test_keep.py", &module("keep")),
            ("build/test_build.py", &module("build")),
            ("dist/test_dist.py", &module("dist")),
            ("node_modules/test_nm.py", &module("nm")),
            (".hidden/test_hidden.py", &module("hidden")),
            ("__pycache__/test_cached.py", &module("cached")),
            ("myenv/test_venv.py", &module("venv")),
            ("myenv/pyvenv.cfg", "home = /usr\n"),
        ]);

        assert_eq!(discovered(&tmp, &[]), vec!["keep/test_keep.py"]);
    }

    /// Setting `norecursedirs` **replaces** the default list (it is a plain `args` ini),
    /// so `build/` becomes collectable and the configured name is pruned instead.
    #[test]
    fn custom_norecursedirs_replaces_the_defaults() {
        let tmp = TempDir::new().unwrap();
        write_file(
            &tmp.path().join("pytest.ini"),
            "[pytest]\nnorecursedirs = skipme *_data\n",
        );
        write_file(&tmp.path().join("build/test_build.py"), &module("build"));
        write_file(&tmp.path().join("skipme/test_skip.py"), &module("skip"));
        write_file(&tmp.path().join("raw_data/test_data.py"), &module("data"));

        assert_eq!(discovered(&tmp, &[]), vec!["build/test_build.py"]);
    }

    /// A `norecursedirs` pattern containing a separator is matched against the whole path,
    /// not the basename — `_pytest/pathlib.py::fnmatch_ex` (`if sep not in pattern: name =
    /// path.name else: name = str(path)`), with the relative-pattern rule
    /// `pattern = f"*{os.sep}{pattern}"` for an absolute path.
    #[test]
    fn separator_bearing_patterns_match_the_whole_path() {
        let tmp = TempDir::new().unwrap();
        write_file(
            &tmp.path().join("pytest.ini"),
            "[pytest]\nnorecursedirs = fixtures/data\n",
        );
        write_file(
            &tmp.path().join("fixtures/data/test_data.py"),
            &module("data"),
        );
        write_file(
            &tmp.path().join("fixtures/keep/test_keep.py"),
            &module("keep"),
        );
        write_file(
            &tmp.path().join("other/data/test_other.py"),
            &module("other"),
        );

        assert_eq!(
            discovered(&tmp, &[]),
            vec!["fixtures/keep/test_keep.py", "other/data/test_other.py"]
        );
    }

    /// `fnmatch_ex` unit coverage: basename branch, whole-path branch, and the
    /// absolute-path/relative-pattern rewrite.
    #[test]
    fn fnmatch_ex_matches_pytests_two_branches() {
        let root = if cfg!(windows) {
            Path::new(r"C:\repo\tests\data\test_a.py")
        } else {
            Path::new("/repo/tests/data/test_a.py")
        };

        assert!(fnmatch_ex("test_*.py", root), "basename branch");
        assert!(!fnmatch_ex("check_*.py", root), "basename branch, no match");
        assert!(fnmatch_ex("tests/data/*.py", root), "whole-path branch");
        assert!(
            !fnmatch_ex("tests/other/*.py", root),
            "whole-path branch, no match"
        );
        // A relative pattern is anchored with a leading `*<sep>` for an absolute path,
        // so it matches a *suffix* of the path rather than nothing at all.
        assert!(fnmatch_ex("data/test_a.py", root));
        // A separator-free pattern never sees the directories.
        assert!(!fnmatch_ex("data", root));
    }

    /// Pins the deliberate version choice in [`is_absolute_pattern`] — 3.12's `ntpath.isabs`
    /// rule, where a single leading separator means *absolute* — at the one input that can
    /// actually tell the two rules apart.
    ///
    /// On a drive path they agree by accident (see that function's docs: the 3.13+ rule
    /// anchors the pattern into a **doubled** separator, which no drive path contains), so
    /// asserting there would pin nothing — verified: that form of this test survived the
    /// "use the 3.13+ rule" mutation.  A UNC path carries a doubled separator at position 0,
    /// so a pattern whose first segment is the server name is where the rules diverge: the
    /// 3.13+ rule anchors `\srv\share\*.py` to `*\\srv\share\*.py` and matches; ours treats
    /// it as an absolute pattern and does not.
    #[cfg(windows)]
    #[test]
    fn a_single_leading_separator_pattern_follows_the_312_isabs_rule() {
        let unc = Path::new(r"\\srv\share\test_a.py");

        assert!(
            !fnmatch_ex(r"\srv\share\*.py", unc),
            "one leading separator is an absolute pattern on the 3.12 floor: no anchoring"
        );
        // Control: the same pattern without that separator is relative, gets anchored, and
        // does match — so the assertion above turns on the leading separator alone.
        assert!(fnmatch_ex(r"srv\share\*.py", unc));
    }

    /// An explicitly named file is an *initial path*, and pytest skips the `python_files`
    /// filter for those: `_pytest/python.py::pytest_collect_file` only consults
    /// `path_matches_patterns` when `not parent.session.isinitpath(file_path)`.
    #[test]
    fn an_explicit_file_arg_bypasses_python_files() {
        let tmp = tree(&[
            ("checks.py", &module("solo")),
            ("other.py", &module("other")),
        ]);

        assert_eq!(
            discovered(&tmp, &[tmp.path().join("checks.py")]),
            vec!["checks.py"]
        );
    }

    /// ...and an explicitly named directory is walked even when it matches
    /// `norecursedirs`: `Dir.collect` skips `pytest_ignore_collect` for an initial path.
    #[test]
    fn an_explicit_dir_arg_bypasses_norecursedirs() {
        let tmp = tree(&[("build/test_build.py", &module("build"))]);

        assert_eq!(
            discovered(&tmp, &[tmp.path().join("build")]),
            vec!["build/test_build.py"]
        );
    }

    /// A missing arg is pytest's `UsageError("file or directory not found: ...")`
    /// (`_pytest/main.py::resolve_collection_argument`) — never an empty, quietly
    /// successful collection.
    #[test]
    fn a_missing_arg_is_a_loud_error() {
        let tmp = tree(&[("test_a.py", &module("a"))]);

        let err = discover_default(tmp.path(), &[tmp.path().join("nope")]).unwrap_err();
        assert!(
            err.to_string().contains("not found"),
            "unexpected message: {err}"
        );
    }

    /// With no args and `invocation_dir == rootdir`, `testpaths` decides the roots
    /// (`_pytest/config/__init__.py::Config._decide_args`).
    #[test]
    fn testpaths_supply_the_roots_when_there_are_no_args() {
        let tmp = TempDir::new().unwrap();
        write_file(
            &tmp.path().join("pytest.ini"),
            "[pytest]\ntestpaths = suite\n",
        );
        write_file(&tmp.path().join("suite/test_in.py"), &module("in"));
        write_file(&tmp.path().join("outside/test_out.py"), &module("out"));

        assert_eq!(discovered(&tmp, &[]), vec!["suite/test_in.py"]);
    }

    // --- duplicate path arguments (see `Seen` for the probe table) ---------

    /// A directory argument suppresses a file argument for the same file, in **both**
    /// orders.  Probed: `pytest pkg pkg/test_b.py` and `pytest pkg/test_b.py pkg` each
    /// collect `test_three` exactly once.
    #[test]
    fn a_directory_arg_and_a_file_inside_it_collect_the_file_once() {
        let tmp = tree(&[("sub/test_a.py", &module("a"))]);
        let dir = tmp.path().join("sub");
        let file = tmp.path().join("sub/test_a.py");

        assert_eq!(
            discovered(&tmp, &[dir.clone(), file.clone()]),
            vec!["sub/test_a.py"]
        );
        assert_eq!(discovered(&tmp, &[file, dir]), vec!["sub/test_a.py"]);
    }

    /// The same **file** named twice is collected twice — pytest's documented backward-compat
    /// hole in its collection cache (`Session.collect`: *"files given directly multiple
    /// times on the command line should not be deduplicated"*).  Probed: `pytest test_a.py
    /// test_a.py` reports `4 tests collected` for a two-test file, and three occurrences
    /// give 6.
    ///
    /// Deduplicating here would be the *safer-looking* choice and would silently run half
    /// the tests a `pytest a.py a.py` invocation asks for.
    #[test]
    fn the_same_file_argument_twice_is_collected_twice() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        let file = tmp.path().join("test_a.py");

        assert_eq!(
            discovered(&tmp, &[file.clone(), file.clone()]),
            vec!["test_a.py", "test_a.py"]
        );
        assert_eq!(
            discovered(&tmp, &[file.clone(), file.clone(), file]),
            vec!["test_a.py", "test_a.py", "test_a.py"]
        );
    }

    /// ...and "the same file" is decided after normalisation, not by argument spelling:
    /// probed, `pytest test_a.py ./test_a.py` also collects 4.
    #[test]
    fn two_spellings_of_one_file_argument_still_collect_it_twice() {
        let tmp = tree(&[("test_a.py", &module("a"))]);

        assert_eq!(
            discovered(
                &tmp,
                &[tmp.path().join("test_a.py"), tmp.path().join("./test_a.py"),]
            ),
            vec!["test_a.py", "test_a.py"]
        );
    }

    /// A directory argument repeated is still walked once — the cache hole is about files,
    /// so nothing bypasses it here.  Probed: `pytest . .` and `pytest pkg pkg` each collect
    /// the tree once.
    #[test]
    fn a_repeated_directory_argument_is_walked_once() {
        let tmp = tree(&[("sub/test_a.py", &module("a")), ("test_b.py", &module("b"))]);
        let root = tmp.path().to_path_buf();

        assert_eq!(
            discovered(&tmp, &[root.clone(), root]),
            vec!["sub/test_a.py", "test_b.py"]
        );
    }

    // --- non-Python path arguments (see `initial_file_target`) -------------

    /// A `.txt` or `.rst` argument collects **nothing and is not an error**: pytest's
    /// doctest tier claims it unconditionally for an initial path
    /// (`_pytest/doctest.py::_is_doctest`), finds no doctests, and exits 5.  v2 has no
    /// doctest tier and reaches the same observable answer by producing no target.
    #[test]
    fn a_text_or_rst_argument_collects_nothing_without_failing() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        write_file(&tmp.path().join("notes.txt"), "hi\n");
        write_file(&tmp.path().join("notes.rst"), "hi\n");

        assert!(discovered(&tmp, &[tmp.path().join("notes.txt")]).is_empty());
        assert!(discovered(&tmp, &[tmp.path().join("notes.rst")]).is_empty());
    }

    /// Any other non-Python file argument is a **usage error** — pytest's `found no
    /// collectors for ...` (exit 4).  Probed with `.dat` and `.md`, both exit 4, against
    /// `.txt`/`.rst`'s exit 5: the two halves of the same suffix decision.
    #[test]
    fn any_other_non_python_argument_is_a_usage_error() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        write_file(&tmp.path().join("notes.dat"), "hi\n");

        let path = tmp.path().join("notes.dat");
        let err = discover_default(tmp.path(), &[path.clone()]).unwrap_err();
        assert!(
            matches!(&err, CollectError::NoCollectors(reported) if reported == &path),
            "unexpected error: {err}"
        );
        assert!(err.to_string().starts_with("found no collectors for"));
    }

    // --- markdown code blocks (rustest's own tier) ------------------------

    /// A `.md` argument **is** a target with code blocks on — the one row where rustest
    /// deliberately answers something pytest does not (pytest: exit 4, `found no collectors`).
    #[test]
    fn a_markdown_argument_is_a_target_when_codeblocks_are_on() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        write_file(
            &tmp.path().join("guide.md"),
            "```python\nassert True\n```\n",
        );

        let (_config, targets) =
            discover(tmp.path(), &[tmp.path().join("guide.md")], true).unwrap();
        assert_eq!(targets, vec![tmp.path().join("guide.md")]);
    }

    /// ...and with `--no-codeblocks` the pytest answer is restored exactly, which is what
    /// makes the extension opt-out rather than unavoidable.
    #[test]
    fn a_markdown_argument_is_a_usage_error_when_codeblocks_are_off() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        let path = tmp.path().join("guide.md");
        write_file(&path, "```python\nassert True\n```\n");

        let err = discover(tmp.path(), &[path.clone()], false).unwrap_err();
        assert!(
            matches!(&err, CollectError::NoCollectors(reported) if reported == &path),
            "unexpected error: {err}"
        );
    }

    /// A directory walk finds **no** markdown, whatever `--no-codeblocks` says.
    ///
    /// It used to, and Phase 4 Task 1's re-sweep measured what that costs: pytest walks a
    /// tree and collects no markdown at all, so `rustest tests/` on any repo with python
    /// fences in its docs found tests `pytest tests/` never sees -- 13 of them on the
    /// acceptance target, 4 failing because documentation snippets do not import what they
    /// reference. Testing the docs is still `rustest README.md docs/`, which is how this
    /// repo tests its own; it is no longer something a `tests/` run does unasked.
    #[test]
    fn the_walk_finds_no_markdown() {
        let tmp = tree(&[("test_b.py", &module("b"))]);
        write_file(
            &tmp.path().join("guide.md"),
            "```python
assert True
```
",
        );

        assert_eq!(discovered(&tmp, &[]), vec!["test_b.py"]);
    }

    /// ...and naming the file is still how you get it.
    #[test]
    fn a_named_markdown_file_is_still_collected() {
        let tmp = tree(&[("test_b.py", &module("b"))]);
        write_file(
            &tmp.path().join("guide.md"),
            "```python
assert True
```
",
        );

        assert_eq!(
            discovered(&tmp, &[tmp.path().join("guide.md")]),
            vec!["guide.md"]
        );
    }

    /// `python_files` governs `.py` and nothing else: a project narrowing it must not
    /// acquire a second collector for a `.md` file, and must not pick markdown up off a
    /// walk either.
    #[test]
    fn markdown_is_not_matched_against_python_files() {
        let tmp = TempDir::new().unwrap();
        write_file(
            &tmp.path().join("pytest.ini"),
            "[pytest]
python_files = check_*.py
",
        );
        write_file(
            &tmp.path().join("guide.md"),
            "```python
assert True
```
",
        );
        write_file(&tmp.path().join("test_a.py"), &module("a"));

        assert!(discovered(&tmp, &[]).is_empty());
        assert_eq!(
            discovered(&tmp, &[tmp.path().join("guide.md")]),
            vec!["guide.md"]
        );
    }

    // --- routing ----------------------------------------------------------

    /// Same stem, same worker — the rule that lets a worker reproduce pytest's
    /// "import file mismatch" for two identically named modules.
    #[test]
    fn routing_sends_every_same_stem_file_to_one_worker() {
        for workers in 1..8 {
            let a = worker_for(Path::new("/repo/alpha/test_dup.py"), workers);
            let b = worker_for(Path::new("/repo/beta/nested/test_dup.py"), workers);
            assert_eq!(a, b, "stem routing must ignore the directory ({workers})");
            assert!(a < workers);
        }
    }

    /// ...while distinct stems still spread over the pool, so routing by stem does not
    /// silently degenerate into a one-worker pool.
    #[test]
    fn routing_spreads_distinct_stems_across_the_pool() {
        let workers = 4;
        let mut counts = vec![0usize; workers];
        for i in 0..200 {
            let path = PathBuf::from(format!("/repo/test_mod_{i}.py"));
            counts[worker_for(&path, workers)] += 1;
        }
        let min = *counts.iter().min().unwrap();
        let max = *counts.iter().max().unwrap();
        assert!(min > 0, "a worker got nothing: {counts:?}");
        assert!(max < min * 3, "badly unbalanced: {counts:?}");
    }

    /// Build a tree of `(name, bytes-of-body)` and return the per-worker file lists the
    /// plan produced, as stems, so a routing assertion reads as the shape it is about.
    fn routed(files: &[(&str, usize)], workers: usize) -> Vec<Vec<String>> {
        let bodies: Vec<(String, String)> = files
            .iter()
            .map(|(name, bulk)| {
                // A real body, padded with a comment so the *file size* varies without the
                // test count doing so — which is exactly the signal `route` weighs.
                (
                    (*name).to_string(),
                    format!("{}\n# {}\n", module("x"), "p".repeat(*bulk)),
                )
            })
            .collect();
        let spec: Vec<(&str, &str)> = bodies
            .iter()
            .map(|(name, body)| (name.as_str(), body.as_str()))
            .collect();
        let tmp = tree(&spec);
        let dispatch = plan_with_options(
            tmp.path(),
            &[],
            workers,
            &CollectOptions {
                codeblocks: true,
                tier: TierMode::DynamicOnly,
                cache: CacheMode::Off,
                ..CollectOptions::default()
            },
        )
        .unwrap();
        dispatch
            .assignments
            .iter()
            .map(|files| {
                files
                    .iter()
                    .map(|(_, path)| path.file_stem().unwrap().to_string_lossy().into_owned())
                    .collect()
            })
            .collect()
    }

    /// **No worker gets nothing while another gets two.**  This is the shape the stem hash
    /// could not produce: `fnv1a(stem) % 16` is not a permutation, so at Pynite's `-n 16`
    /// two of sixteen workers were handed no files at all and still paid a full interpreter
    /// startup, while the top three held 67% of the pool's CPU.  Eight equal files into
    /// eight bins must be one apiece.
    #[test]
    fn routing_leaves_no_worker_empty_while_another_holds_two() {
        let files: Vec<(String, usize)> = (0..8)
            .map(|index| (format!("test_m{index}.py"), 100))
            .collect();
        let spec: Vec<(&str, usize)> = files
            .iter()
            .map(|(name, bulk)| (name.as_str(), *bulk))
            .collect();

        let plan = routed(&spec, 8);

        let sizes: Vec<usize> = plan.iter().map(Vec::len).collect();
        assert_eq!(sizes, vec![1; 8], "equal files must fill the pool evenly");
    }

    /// The pathological file gets a worker to itself.
    ///
    /// jsonschema is the worked example: 93.8% of its tests live in one module, and under
    /// the hash that module shared a worker with whatever else collided with it.  A
    /// bin-pack puts the heavy item down first and everything else goes elsewhere.
    #[test]
    fn routing_gives_the_heaviest_file_a_worker_of_its_own() {
        let plan = routed(
            &[
                ("test_huge.py", 200_000),
                ("test_a.py", 10),
                ("test_b.py", 10),
                ("test_c.py", 10),
            ],
            2,
        );

        let heavy = plan
            .iter()
            .find(|files| files.iter().any(|stem| stem == "test_huge"))
            .expect("the big file is routed somewhere");
        assert_eq!(
            heavy,
            &vec!["test_huge".to_string()],
            "the mega-file must not share a worker: {plan:?}"
        );
    }

    /// Same stem, same worker — through the **plan**, not through [`worker_for`] alone,
    /// because the bin-pack is what has to honour it now and it is the property pytest's
    /// `import file mismatch` reproduction rests on.  The two files differ wildly in size
    /// so a router weighing them independently would certainly split them.
    #[test]
    fn routing_keeps_a_same_stem_pair_together_however_they_are_weighed() {
        let plan = routed(
            &[
                ("alpha/test_dup.py", 60_000),
                ("beta/test_dup.py", 10),
                ("test_other.py", 10),
            ],
            3,
        );

        let together = plan
            .iter()
            .any(|files| files.iter().filter(|stem| *stem == "test_dup").count() == 2);
        assert!(
            together,
            "a same-stem pair was split across workers: {plan:?}"
        );
    }

    /// The same tree plans identically every time.  Bin packing walks a hash map, and a
    /// router that inherited that iteration order would produce a different plan per run —
    /// which would make a `-n 4` reproduction of a routing bug not a reproduction.
    #[test]
    fn routing_is_deterministic() {
        let spec = [
            ("test_a.py", 5_000),
            ("test_b.py", 5_000),
            ("test_c.py", 1_000),
            ("test_d.py", 1_000),
            ("test_e.py", 40_000),
        ];

        let first = routed(&spec, 3);
        for _ in 0..4 {
            assert_eq!(routed(&spec, 3), first, "the plan is not stable");
        }
    }

    // --- the init line ----------------------------------------------------

    /// `init` carries **both** directories, and they are not interchangeable: `rootdir` is
    /// wherever the config file was found by walking *up*, `invocation_dir` is where the run
    /// started.  Nothing else in this suite would notice a producer that sent `rootdir`
    /// twice — the worker ignores `invocation_dir` for now — so this asserts on the raw line
    /// a stand-in worker received, from a layout where the two genuinely differ.
    ///
    /// The invocation directory is handed over with a `.` segment on purpose: it must reach
    /// the worker normalised, exactly as `discover` normalises it before resolving config.
    #[test]
    fn init_carries_a_normalised_invocation_dir_distinct_from_the_rootdir() {
        let tmp = tree(&[("sub/test_a.py", &module("a"))]);
        let logs = TempDir::new().unwrap();
        let log = logs.path().join("init.json");
        let sub = tmp.path().join("sub");

        let manifest =
            collect_default(&sub.join("."), &[], &init_recording_worker(&log), 1).unwrap();
        assert!(manifest.tests.is_empty(), "the stand-in collects nothing");

        let line = fs::read_to_string(&log).unwrap();
        let init: WorkerRequest = serde_json::from_str(line.trim()).unwrap();
        let WorkerRequest::Init {
            rootdir,
            invocation_dir,
            ..
        } = init
        else {
            panic!("expected an init request, got: {line}");
        };

        assert_eq!(invocation_dir, to_posix(&sub));
        assert_eq!(rootdir, to_posix(tmp.path()));
        assert_ne!(
            rootdir, invocation_dir,
            "the fixture must keep the two apart, or this test proves nothing"
        );
    }

    /// `--cov` reaches the worker on `init`, and a run without it carries **no key at all**.
    ///
    /// Both halves are asserted against the raw line a stand-in worker received, because the
    /// absence is what the worker keys "register a `sys.monitoring` tool" on: a producer that
    /// always sent `"coverage":null` would still decode (the documented `Option` tolerance) and
    /// would still be off-contract, and nothing else in this suite would notice.
    #[test]
    fn init_carries_the_coverage_instruction_only_when_coverage_was_asked_for() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        let logs = TempDir::new().unwrap();

        let plain = logs.path().join("plain.json");
        let _ = collect_default(tmp.path(), &[], &init_recording_worker(&plain), 1).unwrap();
        let line = fs::read_to_string(&plain).unwrap();
        assert!(
            !line.contains(r#""coverage":"#),
            "a run without --cov must not carry the key: {line}"
        );

        let measured = logs.path().join("measured.json");
        let wire = CoverageWire {
            sources: vec![to_posix(&tmp.path().join("src"))],
            data_dir: to_posix(logs.path()),
        };
        let _ = collect_with_launcher(
            tmp.path(),
            &[],
            &init_recording_worker(&measured),
            1,
            &CollectOptions {
                codeblocks: true,
                tier: TierMode::DynamicOnly,
                cache: CacheMode::Off,
                coverage: Some(wire.clone()),
                ..CollectOptions::default()
            },
        )
        .unwrap();

        let line = fs::read_to_string(&measured).unwrap();
        let init: WorkerRequest = serde_json::from_str(line.trim()).unwrap();
        let WorkerRequest::Init { coverage, .. } = init else {
            panic!("expected an init request, got: {line}");
        };
        assert_eq!(coverage, Some(wire));
    }

    // --- end to end, with the real worker ---------------------------------

    /// The manifest is in **walk order**, whatever order the workers answered in, and the
    /// rootdir is posix with the pinned schema version.
    #[test]
    fn manifest_entries_follow_walk_order_across_two_workers() {
        let tmp = tree(&[
            ("test_b.py", &module("b")),
            ("test_a.py", &module("a")),
            ("sub/test_c.py", &module("c")),
            ("zsub/test_d.py", &module("d")),
        ]);

        // Without this the fixture could quietly become a one-worker test the day the hash
        // changes, and would then assert nothing about ordering across workers at all.
        let (_, targets) = discover_default(tmp.path(), &[]).unwrap();
        let used: HashSet<usize> = targets.iter().map(|path| worker_for(path, 2)).collect();
        assert!(
            used.len() >= 2,
            "the fixture must actually spread over both workers, got {used:?}"
        );

        let manifest = collect_default(tmp.path(), &[], &real_worker(), 2).unwrap();

        assert_eq!(
            ids(&manifest),
            vec![
                "sub/test_c.py::test_c",
                "test_a.py::test_a",
                "test_b.py::test_b",
                "zsub/test_d.py::test_d",
            ]
        );
        assert_eq!(
            manifest.schema_version,
            crate::v2::manifest::MANIFEST_SCHEMA_VERSION
        );
        assert!(!manifest.rootdir.contains('\\'), "{}", manifest.rootdir);
        assert!(manifest.errors.is_empty(), "{:?}", manifest.errors);
    }

    /// The reason routing is by stem: two `test_dup.py` in different non-package
    /// directories land on the **same** worker, so the second import hits Python's module
    /// cache and produces pytest's own "import file mismatch" collection error.  With
    /// round-robin they would land on different processes and both collect cleanly —
    /// silently disagreeing with pytest.
    #[test]
    fn same_stem_files_reproduce_pytests_import_file_mismatch() {
        let tmp = tree(&[
            ("alpha/test_dup.py", &module("alpha")),
            ("beta/test_dup.py", &module("beta")),
        ]);

        let manifest = collect_default(tmp.path(), &[], &real_worker(), 2).unwrap();

        assert_eq!(ids(&manifest), vec!["alpha/test_dup.py::test_alpha"]);
        assert_eq!(manifest.errors.len(), 1, "{:?}", manifest.errors);
        assert_eq!(manifest.errors[0].path, "beta/test_dup.py");
        assert!(
            manifest.errors[0].message.contains("import file mismatch"),
            "unexpected message: {}",
            manifest.errors[0].message
        );
    }

    /// A file that cannot be imported is **data**, not a run-ending failure: it becomes a
    /// manifest error entry while its neighbours still collect.
    #[test]
    fn an_unimportable_file_becomes_an_error_entry() {
        let tmp = tree(&[
            ("test_broken.py", "def test_a(:\n"),
            ("test_ok.py", &module("ok")),
        ]);

        let manifest = collect_default(tmp.path(), &[], &real_worker(), 1).unwrap();

        assert_eq!(ids(&manifest), vec!["test_ok.py::test_ok"]);
        assert_eq!(manifest.errors.len(), 1, "{:?}", manifest.errors);
        assert_eq!(manifest.errors[0].path, "test_broken.py");
        assert!(
            manifest.errors[0].message.contains("SyntaxError"),
            "unexpected message: {}",
            manifest.errors[0].message
        );
    }

    /// The pool is clamped to the number of files: eight workers for two files must start
    /// exactly **two** interpreters, and between them they must be asked for both files.
    /// Spawning six idle Pythons is pure latency on a small suite, and the clamp is
    /// invisible from the manifest — only a worker that can count itself can pin it.
    #[test]
    fn the_pool_is_clamped_to_the_number_of_files() {
        let tmp = tree(&[("test_a.py", &module("a")), ("test_b.py", &module("b"))]);
        let logs = TempDir::new().unwrap();

        let manifest = collect_default(tmp.path(), &[], &counting_worker(logs.path()), 8).unwrap();
        assert!(
            manifest.tests.is_empty() && manifest.errors.is_empty(),
            "the counting worker reports no tests of its own"
        );

        let mut spawned = 0;
        let mut requested = Vec::new();
        for entry in fs::read_dir(logs.path()).unwrap().flatten() {
            spawned += 1;
            requested.extend(
                fs::read_to_string(entry.path())
                    .unwrap()
                    .lines()
                    .map(str::to_string),
            );
        }
        assert_eq!(spawned, 2, "one interpreter per file, no more");

        requested.sort();
        assert_eq!(requested.len(), 2, "{requested:?}");
        assert!(requested[0].ends_with("/test_a.py"), "{requested:?}");
        assert!(requested[1].ends_with("/test_b.py"), "{requested:?}");
    }

    /// The whole pool is **running** before any of it is handshaken.
    ///
    /// This is the shape [`spawn_pool`] exists in two phases for, and it is asserted
    /// without a clock: each stand-in worker registers itself on disk at startup and then
    /// refuses to answer `init` until it can see all four registrations.  Under a loop that
    /// spawned-and-handshaked one worker at a time, worker 0 would wait for three siblings
    /// that are not spawned until it answers — a deadlock, which the worker's own 30 s
    /// bailout turns into a handshake failure rather than a hung test binary.  Passing
    /// therefore means the four interpreters really were alive at the same time.
    #[test]
    fn the_whole_pool_boots_before_any_handshake() {
        let tmp = tree(&[
            ("test_a.py", &module("a")),
            ("test_b.py", &module("b")),
            ("test_c.py", &module("c")),
            ("test_d.py", &module("d")),
        ]);
        let logs = TempDir::new().unwrap();
        let script = format!(
            "import glob, json, os, sys, time\n\
             open('{dir}/live-%d' % os.getpid(), 'w').close()\n\
             deadline = time.monotonic() + 30.0\n\
             while len(glob.glob('{dir}/live-*')) < 4:\n\
             \x20   if time.monotonic() > deadline:\n\
             \x20       sys.exit('only %d of 4 workers were alive' % len(glob.glob('{dir}/live-*')))\n\
             \x20   time.sleep(0.01)\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   if message['op'] == 'init':\n\
             \x20       {READY}\n\
             \x20   elif message['op'] == 'collect_file':\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': message['path']}}) + chr(10))\n\
             \x20   else:\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n",
            dir = to_posix(logs.path())
        );

        let manifest = collect_default(tmp.path(), &[], &scripted_worker(&script), 4)
            .expect("four workers must be alive at once");

        assert!(manifest.tests.is_empty() && manifest.errors.is_empty());
    }

    /// A process a *test* started dies when the worker that ran the test is reaped.
    ///
    /// The leak this pins is not hypothetical: the Phase 4b profile found four werkzeug
    /// `live_apps/run.py` reloaders still spinning eleven hours after the sweep that
    /// started them, and four more appeared during Task 2 itself.  `Child::kill` cannot
    /// see them — they are the worker's children, not the orchestrator's — so only the job
    /// object closes this.
    ///
    /// The grandchild appends a byte every 20 ms.  After collection returns (every worker
    /// dropped), the file stops growing iff it is dead; a live one would add ~50 bytes a
    /// second.  Both windows are 1 s, i.e. ~50x the heartbeat, so the assertion does not
    /// depend on scheduler luck — and the "it was really running" half is asserted too, so
    /// a grandchild that never started could not pass this by staying at zero.
    #[cfg(windows)]
    #[test]
    fn a_reaped_worker_takes_its_grandchildren_with_it() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        let beat = TempDir::new().unwrap();
        let beat_path = to_posix(&beat.path().join("heartbeat"));
        let gc_path = beat.path().join("grandchild.py");
        fs::write(
            &gc_path,
            "import sys, time\n\
             beat = open(sys.argv[1], 'ab', buffering=0)\n\
             while True:\n\
             \x20   beat.write(b'.')\n\
             \x20   time.sleep(0.02)\n",
        )
        .unwrap();
        // `close_fds=False` is the *hostile* setting on purpose: the grandchild inherits
        // whatever it can, exactly as a real test's `subprocess.Popen` would.  The worker
        // refuses to answer `init` until the heartbeat is ticking, so "it was alive during
        // the run" is established by the run itself rather than by a sleep.
        let script = format!(
            "import json, os, subprocess, sys, time\n\
             child = subprocess.Popen([sys.executable, r'{gc}', r'{beat}'], close_fds=False)\n\
             deadline = time.monotonic() + 30.0\n\
             while not os.path.exists(r'{beat}') or os.path.getsize(r'{beat}') == 0:\n\
             \x20   if time.monotonic() > deadline:\n\
             \x20       sys.exit('the grandchild never started')\n\
             \x20   time.sleep(0.01)\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   if message['op'] == 'init':\n\
             \x20       {READY}\n\
             \x20   elif message['op'] == 'collect_file':\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': message['path']}}) + chr(10))\n\
             \x20   else:\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n",
            gc = to_posix(&gc_path),
            beat = beat_path
        );

        let manifest = collect_default(tmp.path(), &[], &scripted_worker(&script), 1).unwrap();
        assert!(manifest.errors.is_empty(), "{:?}", manifest.errors);

        let size = || fs::metadata(beat.path().join("heartbeat")).map_or(0, |meta| meta.len());
        std::thread::sleep(std::time::Duration::from_millis(1000));
        let settled = size();
        assert!(
            settled > 0,
            "the grandchild never ran, so nothing was proved"
        );
        std::thread::sleep(std::time::Duration::from_millis(1000));
        assert_eq!(
            settled,
            size(),
            "the grandchild outlived the worker that spawned it"
        );
    }

    /// Nothing to collect means no process is spawned at all — asserted with a launcher
    /// that could not possibly start.
    #[test]
    fn an_empty_tree_spawns_no_worker() {
        let tmp = tree(&[("notes.txt", "nothing here")]);
        let impossible = WorkerLauncher::scripted("rustest-no-such-interpreter", Vec::new());

        let manifest = collect_default(tmp.path(), &[], &impossible, 4).unwrap();

        assert!(manifest.tests.is_empty());
        assert!(manifest.errors.is_empty());
    }

    /// A worker that cannot be started is loud, and names the program.
    #[test]
    fn a_worker_that_cannot_spawn_is_loud() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        let impossible = WorkerLauncher::scripted("rustest-no-such-interpreter", Vec::new());

        let err = collect_default(tmp.path(), &[], &impossible, 1).unwrap_err();
        assert!(
            err.to_string().contains("rustest-no-such-interpreter"),
            "unexpected message: {err}"
        );
    }

    // --- protocol drift ---------------------------------------------------

    /// A worker declaring another protocol version fails the handshake; the message
    /// carries both versions so the skew is diagnosable.
    #[test]
    fn a_protocol_version_mismatch_is_fatal() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        let script = "import sys\n\
             sys.stdin.readline()\n\
             sys.stdout.write('{\"op\":\"ready\",\"protocol_version\":999}\\n')\n\
             sys.stdout.flush()\n\
             sys.stdin.readline()\n";

        let err = collect_default(tmp.path(), &[], &scripted_worker(script), 1).unwrap_err();
        let message = err.to_string();
        assert!(message.contains("999"), "unexpected message: {message}");
        assert!(
            message.contains(&crate::v2::protocol::PROTOCOL_VERSION.to_string()),
            "unexpected message: {message}"
        );
    }

    /// A worker that dies mid-protocol names the file that was in flight.  Silence here
    /// would drop a whole file's tests from the manifest with nothing to show for it.
    #[test]
    fn a_dying_worker_names_the_file_in_flight() {
        let tmp = tree(&[("test_inflight.py", &module("inflight"))]);
        let script = format!(
            "import sys\n\
             sys.stdin.readline()\n\
             {READY}\n\
             sys.stdout.flush()\n\
             sys.stdin.readline()\n\
             sys.exit(3)\n"
        );

        let err = collect_default(tmp.path(), &[], &scripted_worker(&script), 1).unwrap_err();
        let message = err.to_string();
        assert!(
            message.contains("test_inflight.py"),
            "unexpected message: {message}"
        );
        // The exit status is the diagnosis (the real worker exits 2 only on drift), so it
        // has to survive into the message.
        assert!(
            message.contains(&exit_status_phrase(3)),
            "unexpected message: {message}"
        );
    }

    /// An undecodable line is fatal per worker and **surfaces the raw line** — the op name
    /// is the only clue to what a skewed peer was trying to say.
    #[test]
    fn an_undecodable_line_surfaces_itself() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        let script = format!(
            "import sys\n\
             sys.stdin.readline()\n\
             {READY}\n\
             sys.stdout.flush()\n\
             sys.stdin.readline()\n\
             sys.stdout.write('{{\"op\":\"progress\",\"done\":3}}\\n')\n\
             sys.stdout.flush()\n\
             sys.stdin.readline()\n"
        );

        let err = collect_default(tmp.path(), &[], &scripted_worker(&script), 1).unwrap_err();
        let message = err.to_string();
        assert!(
            message.contains(r#"{"op":"progress","done":3}"#),
            "the raw line must survive into the error: {message}"
        );
    }

    /// The one malformed shape serde cannot reject (`protocol.rs` documents the tolerance):
    /// a `collected` carrying both `tests` and `error`.  The orchestrator must treat it
    /// exactly like a decode error.
    #[test]
    fn a_hybrid_collected_is_protocol_fatal() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        let script = format!(
            "import sys, json\n\
             sys.stdin.readline()\n\
             {READY}\n\
             sys.stdout.flush()\n\
             path = json.loads(sys.stdin.readline())['path']\n\
             sys.stdout.write(json.dumps({{'op': 'collected', 'path': path,\n\
             'tests': [{{'id': 'test_a.py::test_a', 'path': 'test_a.py', 'qualname': 'test_a'}}],\n\
             'error': {{'path': 'test_a.py', 'message': 'boom'}}}}) + '\\n')\n\
             sys.stdout.flush()\n\
             sys.stdin.readline()\n"
        );

        let err = collect_default(tmp.path(), &[], &scripted_worker(&script), 1).unwrap_err();
        let message = err.to_string();
        assert!(
            message.contains("tests") && message.contains("error"),
            "unexpected message: {message}"
        );
        assert!(
            message.contains("test_a.py"),
            "the file must be named: {message}"
        );
    }

    /// A response for a file other than the one requested means the stream has slipped;
    /// accepting it would attribute one file's tests to another.
    #[test]
    fn a_mismatched_response_path_is_protocol_fatal() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        let script = format!(
            "import sys\n\
             sys.stdin.readline()\n\
             {READY}\n\
             sys.stdout.flush()\n\
             sys.stdin.readline()\n\
             sys.stdout.write('{{\"op\":\"collected\",\"path\":\"/elsewhere/test_z.py\"}}\\n')\n\
             sys.stdout.flush()\n\
             sys.stdin.readline()\n"
        );

        let err = collect_default(tmp.path(), &[], &scripted_worker(&script), 1).unwrap_err();
        let message = err.to_string();
        assert!(
            message.contains("test_z.py"),
            "unexpected message: {message}"
        );
    }

    /// A worker that answers every file but then refuses to shut down cleanly is still a
    /// failure: a non-zero exit means the process did not finish the protocol.
    #[test]
    fn a_worker_failing_shutdown_is_loud() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        let script = format!(
            "import sys, json\n\
             sys.stdin.readline()\n\
             {READY}\n\
             sys.stdout.flush()\n\
             path = json.loads(sys.stdin.readline())['path']\n\
             sys.stdout.write(json.dumps({{'op': 'collected', 'path': path}}) + '\\n')\n\
             sys.stdout.flush()\n\
             sys.stdin.readline()\n\
             sys.stderr.write('worker refused to say bye\\n')\n\
             sys.exit(7)\n"
        );

        let err = collect_default(tmp.path(), &[], &scripted_worker(&script), 1).unwrap_err();
        let message = err.to_string();
        // The status is genuinely absent on this path — the worker vanished before `bye`,
        // so nothing was waited for — which makes its stderr the only diagnosis there is.
        assert!(
            message.contains("refused to say bye"),
            "the worker's own stderr should reach the operator: {message}"
        );
    }

    /// ...and a worker that says `bye` politely and *then* exits non-zero has still
    /// failed.  The real worker exits 2 exactly when it hit protocol drift
    /// (`_v2_worker.py::main`), so swallowing the status would turn a bug into a quietly
    /// short manifest.
    #[test]
    fn a_nonzero_exit_after_bye_is_still_a_failure() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        let script = format!(
            "import sys, json\n\
             sys.stdin.readline()\n\
             {READY}\n\
             sys.stdout.flush()\n\
             path = json.loads(sys.stdin.readline())['path']\n\
             sys.stdout.write(json.dumps({{'op': 'collected', 'path': path}}) + '\\n')\n\
             sys.stdout.flush()\n\
             sys.stdin.readline()\n\
             sys.stdout.write('{{\"op\":\"bye\"}}\\n')\n\
             sys.stdout.flush()\n\
             sys.exit(9)\n"
        );

        let err = collect_default(tmp.path(), &[], &scripted_worker(&script), 1).unwrap_err();
        let message = err.to_string();
        assert!(
            message.contains(&exit_status_phrase(9)),
            "unexpected message: {message}"
        );
    }

    // ----------------------------------------------------------------------
    // The two-tier differential (Rust half)
    // ----------------------------------------------------------------------
    //
    // Tier D is the oracle, so the question these tests ask is always the same: **does
    // enabling Tier S change the manifest?**  Every one of them collects the identical tree
    // twice — once at `TierMode::Auto`, once at `TierMode::DynamicOnly` — and diffs the two.
    // The third leg (pytest itself) is the conformance harness's `--v2-collect` gate and
    // `python/tests/test_v2_static_tier.py`, which cannot run from here because it needs
    // pytest in a subprocess.
    //
    // Tier attribution is asserted alongside, because a differential on its own is satisfied
    // by a Tier S that refuses everything: the two manifests would agree and prove nothing.

    /// Collect `tmp` twice and assert the two manifests are identical **apart from `tier`**,
    /// returning the hybrid one so callers can assert attribution on it.
    fn differential(tmp: &tempfile::TempDir, workers: usize) -> CollectionManifest {
        let hybrid = collect(
            tmp.path(),
            &[],
            &crate::v2::test_python(),
            workers,
            &CollectOptions::new(),
        )
        .expect("hybrid collection succeeds");
        let oracle = collect(
            tmp.path(),
            &[],
            &crate::v2::test_python(),
            workers,
            &CollectOptions {
                tier: TierMode::DynamicOnly,
                ..CollectOptions::new()
            },
        )
        .expect("Tier D collection succeeds");

        let stripped = CollectionManifest {
            tests: hybrid
                .tests
                .iter()
                .cloned()
                .map(|mut test| {
                    test.tier = crate::v2::manifest::Tier::Dynamic;
                    test
                })
                .collect(),
            ..hybrid.clone()
        };
        assert_eq!(
            stripped, oracle,
            "Tier S changed the manifest; the hybrid was {hybrid:?}"
        );
        hybrid
    }

    /// Which tier answered each id.
    fn tiers(manifest: &CollectionManifest) -> Vec<(String, crate::v2::manifest::Tier)> {
        manifest
            .tests
            .iter()
            .map(|test| (test.id.clone(), test.tier))
            .collect()
    }

    /// The headline case: a mixed tree where every dynamism rule fires on a different file.
    ///
    /// The static half is not a formality — if it were empty the differential would pass
    /// vacuously — so the attribution assertion below names the tier of *every* id.
    #[test]
    fn the_hybrid_manifest_equals_the_tier_d_manifest_on_a_mixed_tree() {
        let tmp = tree(&[
            // static
            ("test_plain.py", "def test_one():\n    pass\n"),
            (
                "test_params.py",
                "import pytest\n\n\n@pytest.mark.parametrize(\"v\", [1, 2])\ndef test_p(v):\n    pass\n",
            ),
            (
                "test_klass.py",
                "class TestBox:\n    def test_m(self):\n        pass\n",
            ),
            // dynamic: a foreign import
            (
                "test_imports.py",
                "import itertools\n\ncounter = itertools.count()\n\n\ndef test_i():\n    pass\n",
            ),
            // dynamic: a parametrized fixture
            (
                "test_fixture_params.py",
                "import pytest\n\n\n@pytest.fixture(params=[1, 2])\ndef n(request):\n    return request.param\n\n\ndef test_f(n):\n    pass\n",
            ),
            // dynamic: unittest
            (
                "test_unit.py",
                "import unittest\n\n\nclass TestLegacy(unittest.TestCase):\n    def test_u(self):\n        pass\n",
            ),
            // dynamic: a non-literal mark argument
            (
                "test_skipif.py",
                "import pytest\n\n\n@pytest.mark.skipif(1 + 1 == 2, reason=\"x\")\ndef test_s():\n    pass\n",
            ),
        ]);

        let manifest = differential(&tmp, 2);

        use crate::v2::manifest::Tier::{Dynamic, Static};
        assert_eq!(
            tiers(&manifest),
            vec![
                ("test_fixture_params.py::test_f[1]".to_string(), Dynamic),
                ("test_fixture_params.py::test_f[2]".to_string(), Dynamic),
                ("test_imports.py::test_i".to_string(), Dynamic),
                ("test_klass.py::TestBox::test_m".to_string(), Static),
                ("test_params.py::test_p[1]".to_string(), Static),
                ("test_params.py::test_p[2]".to_string(), Static),
                ("test_plain.py::test_one".to_string(), Static),
                ("test_skipif.py::test_s".to_string(), Dynamic),
                ("test_unit.py::TestLegacy::test_u".to_string(), Dynamic),
            ]
        );
    }

    /// A tree Tier S answers **entirely** spawns no interpreter at all, and still produces the
    /// manifest Tier D would.
    #[test]
    fn a_fully_static_tree_is_collected_without_a_worker() {
        let tmp = tree(&[
            ("test_a.py", "def test_one():\n    pass\n"),
            ("sub/test_b.py", "def test_two():\n    pass\n"),
        ]);

        // A launcher that cannot possibly start: if any worker were spawned this would be a
        // `Spawn` error rather than a manifest.
        let manifest = collect_with_launcher(
            tmp.path(),
            &[],
            &WorkerLauncher::scripted("definitely-not-an-interpreter", Vec::new()),
            2,
            &CollectOptions::new(),
        )
        .expect("a fully-static tree needs no worker");

        assert_eq!(
            manifest
                .tests
                .iter()
                .map(|test| test.id.as_str())
                .collect::<Vec<_>>(),
            ["sub/test_b.py::test_two", "test_a.py::test_one"]
        );
        differential(&tmp, 2);
    }

    // --- the manifest cache and pre-spawn selection ------------------------

    fn unspawnable() -> WorkerLauncher {
        WorkerLauncher::scripted("definitely-not-an-interpreter", Vec::new())
    }

    /// The warm `--collect-only` claim, both halves at once: the second collection of a fully
    /// static tree **starts no process** (the launcher cannot start one) and **parses nothing**
    /// (`misses == 0`, counted where a file reaches the parser).
    #[test]
    fn a_warm_collect_of_a_static_tree_spawns_nothing_and_parses_nothing() {
        let tmp = tree(&[
            ("test_a.py", "def test_one():\n    pass\n"),
            ("sub/test_b.py", "def test_two():\n    pass\n"),
        ]);
        let options = CollectOptions::new();

        let cold = collect_with_launcher(tmp.path(), &[], &unspawnable(), 2, &options)
            .expect("a fully-static tree needs no worker");
        let warm = collect_with_launcher(tmp.path(), &[], &unspawnable(), 2, &options)
            .expect("...and neither does the warm one");
        assert_eq!(cold, warm);
        assert_eq!(
            ids(&warm),
            ["sub/test_b.py::test_two", "test_a.py::test_one"]
        );

        // The parse count, through the same entry point a user calls.
        let dispatch = plan_with_options(tmp.path(), &[], 2, &options).unwrap();
        let stats = dispatch.cache.as_ref().expect("the cache is on").stats();
        assert_eq!(stats.misses(), 0, "the warm collection parsed a file");
        assert_eq!(stats.hits(), 2);
    }

    /// The store is written where the gitignore already covers, under the rootdir the run
    /// resolved -- not under the invocation directory, which may be a subdirectory of it.
    #[test]
    fn the_store_lands_under_the_rootdirs_cache_directory() {
        let tmp = tree(&[("sub/test_a.py", "def test_one():\n    pass\n")]);
        let sub = tmp.path().join("sub");
        let _ =
            collect_with_launcher(&sub, &[], &unspawnable(), 1, &CollectOptions::new()).unwrap();

        assert!(tmp.path().join(".rustest_cache/v2-manifest").is_dir());
        assert!(!sub.join(".rustest_cache").exists());
    }

    /// A Tier D result is **never** cached: it is what a worker reported after importing the
    /// module, and an import depends on the interpreter, the installed packages and whatever
    /// the conftests did when they ran -- none of which is in the key.  Enforced structurally
    /// (only `static_pass_cached` writes), asserted here.
    #[test]
    fn a_dynamic_only_run_writes_nothing() {
        let tmp = tree(&[("test_a.py", "def test_one():\n    pass\n")]);

        let manifest = collect(
            tmp.path(),
            &[],
            &worker_python(),
            1,
            &CollectOptions {
                tier: TierMode::DynamicOnly,
                ..CollectOptions::new()
            },
        )
        .unwrap();
        assert_eq!(ids(&manifest), ["test_a.py::test_one"]);
        assert!(
            !tmp.path().join(".rustest_cache/v2-manifest").exists(),
            "the run path wrote a manifest cache entry"
        );
    }

    /// ...and a *mixed* tree caches only its static half, so a worker's answer can never end
    /// up in the store by way of a directory that also holds a static file.
    #[test]
    fn a_mixed_tree_caches_only_its_static_half() {
        let tmp = tree(&[
            ("test_static.py", "def test_one():\n    pass\n"),
            // Dynamic but perfectly importable: a module-level *call* is Tier S's rule 5.
            // A missing import here would make the file a collection error rather than a
            // Tier D answer, which is a different thing to be asserting about.
            (
                "test_dynamic.py",
                "_marker = list()\n\n\ndef test_two():\n    pass\n",
            ),
        ]);
        let options = CollectOptions::new();

        let cold = collect(tmp.path(), &[], &worker_python(), 2, &options).unwrap();
        let warm = collect(tmp.path(), &[], &worker_python(), 2, &options).unwrap();
        assert_eq!(cold, warm);

        let dispatch = plan_with_options(tmp.path(), &[], 2, &options).unwrap();
        let stats = dispatch.cache.as_ref().unwrap().stats();
        assert_eq!(stats.hits(), 1, "the static file was not served");
        assert_eq!(stats.misses(), 1, "the dynamic file was cached");

        let raw = std::fs::read_dir(tmp.path().join(".rustest_cache/v2-manifest"))
            .unwrap()
            .flatten()
            .map(|entry| fs::read_to_string(entry.path()).unwrap())
            .collect::<String>();
        assert!(raw.contains("test_static.py"), "{raw}");
        assert!(!raw.contains("test_dynamic.py"), "{raw}");
    }

    /// `-k` is evaluated against the (cached) static manifest **before the pool is sized**, so
    /// a fully static tree whose every test is deselected starts no interpreter -- proven by
    /// handing it a launcher that could not start one.
    #[test]
    fn a_fully_deselected_static_tree_spawns_nothing_and_counts_correctly() {
        let tmp = tree(&[
            ("test_a.py", "def test_one():\n    pass\n"),
            (
                "test_b.py",
                "def test_two():\n    pass\n\n\ndef test_three():\n    pass\n",
            ),
        ]);

        let manifest = collect_with_launcher(
            tmp.path(),
            &[],
            &unspawnable(),
            2,
            &CollectOptions {
                keyword: Some("matches_nothing_at_all".to_string()),
                ..CollectOptions::new()
            },
        )
        .expect("a deselected static tree needs no worker");

        assert!(manifest.tests.is_empty());
        // The seventh graded value: "0 collected" and "3 collected, 3 deselected" are
        // different sentences and the corpus grades both.
        assert_eq!(manifest.deselected, 3);
    }

    /// A partial `-k` keeps what matches and counts what it removed, on the static side.
    #[test]
    fn a_partial_selection_keeps_the_matching_static_tests() {
        let tmp = tree(&[(
            "test_a.py",
            "def test_keep():\n    pass\n\n\ndef test_drop():\n    pass\n",
        )]);

        let manifest = collect_with_launcher(
            tmp.path(),
            &[],
            &unspawnable(),
            1,
            &CollectOptions {
                keyword: Some("keep".to_string()),
                ..CollectOptions::new()
            },
        )
        .unwrap();

        assert_eq!(ids(&manifest), ["test_a.py::test_keep"]);
        assert_eq!(manifest.deselected, 1);
    }

    /// The count is the **sum of both tiers**, and it has to be: the predicate is per test, so
    /// partitioning the tests between a pre-spawn pass and a post-worker one changes nothing
    /// about the answer -- but only if both halves are actually counted.
    #[test]
    fn deselected_counts_both_tiers() {
        let tmp = tree(&[
            (
                "test_static.py",
                "def test_keep_s():\n    pass\n\n\ndef test_drop_s():\n    pass\n",
            ),
            (
                "test_dynamic.py",
                "_marker = list()\n\n\ndef test_keep_d():\n    pass\n\n\ndef test_drop_d():\n    pass\n",
            ),
        ]);
        let selected = CollectOptions {
            keyword: Some("keep".to_string()),
            ..CollectOptions::new()
        };

        let hybrid = collect(tmp.path(), &[], &worker_python(), 2, &selected).unwrap();
        // The oracle: the same selection with the static tier forbidden, so every test took
        // the post-worker path.  Identical ids and an identical count is what says the
        // pre-spawn pass changed nothing but the moment of the decision.
        let oracle = collect(
            tmp.path(),
            &[],
            &worker_python(),
            2,
            &CollectOptions {
                tier: TierMode::DynamicOnly,
                ..selected.clone()
            },
        )
        .unwrap();

        assert_eq!(ids(&hybrid), ids(&oracle));
        assert_eq!(hybrid.deselected, oracle.deselected);
        assert_eq!(hybrid.deselected, 2);
        assert_eq!(
            ids(&hybrid),
            [
                "test_dynamic.py::test_keep_d",
                "test_static.py::test_keep_s"
            ]
        );
    }

    /// A file that fails to import is still collected and still reported, however aggressively
    /// the expression deselects.  This is why a Tier D file can never be pruned before its
    /// worker runs, and it is the rule that keeps `-k` from hiding a broken file.
    #[test]
    fn selection_never_hides_a_collection_error() {
        let tmp = tree(&[(
            "test_broken.py",
            "import definitely_not_installed_anywhere\n\n\ndef test_one():\n    pass\n",
        )]);

        let manifest = collect(
            tmp.path(),
            &[],
            &worker_python(),
            1,
            &CollectOptions {
                keyword: Some("matches_nothing_at_all".to_string()),
                ..CollectOptions::new()
            },
        )
        .unwrap();

        assert!(manifest.tests.is_empty());
        assert_eq!(manifest.errors.len(), 1);
        assert_eq!(manifest.errors[0].path, "test_broken.py");
    }

    /// A malformed expression is a usage error even when there is nothing to apply it to --
    /// pytest compiles it from `pytest_collection_modifyitems`, which runs for every session.
    #[test]
    fn a_malformed_selection_expression_is_an_error_on_an_empty_tree() {
        let tmp = tree(&[]);

        let err = collect_with_launcher(
            tmp.path(),
            &[],
            &unspawnable(),
            1,
            &CollectOptions {
                keyword: Some("and".to_string()),
                ..CollectOptions::new()
            },
        )
        .expect_err("a malformed -k is a usage error");
        assert!(matches!(err, CollectError::Selection(_)), "{err:?}");
        assert!(err.to_string().contains("-k"), "{err}");
    }

    /// The cache never changes the answer -- the three-way differential's own property,
    /// applied to the cache: a warm hybrid manifest equals the Tier D oracle, `tier` aside.
    #[test]
    fn a_warm_manifest_still_equals_the_tier_d_oracle() {
        let tmp = tree(&[
            ("test_a.py", "def test_one():\n    pass\n"),
            (
                "test_p.py",
                "import pytest\n\n\n@pytest.mark.parametrize(\"x\", [1, \"a\"])\ndef test_p(x):\n    pass\n",
            ),
        ]);
        // Warm the cache first, then run the whole differential against the warm state.
        let _ = collect(tmp.path(), &[], &worker_python(), 2, &CollectOptions::new()).unwrap();
        differential(&tmp, 2);
    }

    /// Walk order is the manifest's order **whatever tier answered**, which is the property
    /// the mixed tree can hide: alphabetically interleaving static and dynamic files means a
    /// tier-grouped assembly would reorder them and the ids would still all be present.
    #[test]
    fn ids_stay_in_walk_order_across_the_tier_boundary() {
        let tmp = tree(&[
            ("test_a_static.py", "def test_a():\n    pass\n"),
            (
                "test_b_dynamic.py",
                "import itertools\n\nSEEN = []\nSEEN.append(1)\n\n\ndef test_b():\n    pass\n",
            ),
            ("test_c_static.py", "def test_c():\n    pass\n"),
            (
                "test_d_dynamic.py",
                "import itertools\n\nSEEN = []\nSEEN.append(1)\n\n\ndef test_d():\n    pass\n",
            ),
        ]);

        let manifest = differential(&tmp, 3);

        assert_eq!(
            manifest
                .tests
                .iter()
                .map(|test| test.id.as_str())
                .collect::<Vec<_>>(),
            [
                "test_a_static.py::test_a",
                "test_b_dynamic.py::test_b",
                "test_c_static.py::test_c",
                "test_d_dynamic.py::test_d"
            ]
        );
    }

    /// Each refused file is refused for the **rule it was written to trip**.
    ///
    /// Tier attribution alone cannot see this: a detector with one rule that fired on
    /// everything would produce an identical `Static`/`Dynamic` split and an identical
    /// manifest. Reading `Dispatch::dynamism` is what turns "it flagged" into "it flagged
    /// because", and it is the assertion that fails when a rule is relaxed by accident.
    #[test]
    fn each_refusal_names_the_rule_that_fired() {
        use crate::v2::static_collect::Reason;

        let tmp = tree(&[
            ("test_static.py", "def test_s():\n    pass\n"),
            ("test_foreign.py", "import numpy\n\n\ndef test_f():\n    pass\n"),
            ("test_star.py", "from pytest import *\n\n\ndef test_s2():\n    pass\n"),
            (
                "test_getattr.py",
                "def __getattr__(name):\n    raise AttributeError(name)\n",
            ),
            (
                "test_cond.py",
                "if True:\n\n    def test_c():\n        pass\n",
            ),
            (
                "test_bases.py",
                "class TestX(Base):\n    def test_b(self):\n        pass\n",
            ),
            (
                "test_unit.py",
                "class TestY(unittest.TestCase):\n    def test_u(self):\n        pass\n",
            ),
            (
                "test_param.py",
                "import pytest\n\nC = [1]\n\n\n@pytest.mark.parametrize(\"v\", C)\ndef test_p(v):\n    pass\n",
            ),
            (
                "test_fixparam.py",
                "import pytest\n\n\n@pytest.fixture(params=[1])\ndef n(request):\n    return request.param\n",
            ),
            ("test_syntax.py", "def test_x(:\n    pass\n"),
        ]);

        let (config, targets) = discover_default(tmp.path(), &[]).unwrap();
        let reasons: Vec<(String, Option<Reason>)> = targets
            .iter()
            .zip(crate::v2::static_collect::static_pass(
                &targets,
                &config.rootdir,
                &config,
            ))
            .map(|(path, outcome)| {
                (
                    path.file_name().unwrap().to_string_lossy().into_owned(),
                    outcome.err().map(|reason| reason.reason),
                )
            })
            .collect();

        assert_eq!(
            reasons,
            vec![
                ("test_bases.py".to_string(), Some(Reason::ClassBases)),
                ("test_cond.py".to_string(), Some(Reason::ConditionalDef)),
                (
                    "test_fixparam.py".to_string(),
                    Some(Reason::ParametrizedFixture)
                ),
                ("test_foreign.py".to_string(), Some(Reason::ForeignImport)),
                ("test_getattr.py".to_string(), Some(Reason::ModuleGetattr)),
                (
                    "test_param.py".to_string(),
                    Some(Reason::NonLiteralParametrize)
                ),
                ("test_star.py".to_string(), Some(Reason::StarImport)),
                ("test_static.py".to_string(), None),
                ("test_syntax.py".to_string(), Some(Reason::ParseError)),
                ("test_unit.py".to_string(), Some(Reason::UnittestCase)),
            ]
        );
    }

    /// A file Tier S cannot parse is a **collection error**, and only Tier D knows pytest's
    /// wording for it — so the file must route to D rather than vanish.
    #[test]
    fn an_unparsable_file_still_reaches_the_worker() {
        let tmp = tree(&[
            ("test_bad.py", "def test_x(:\n    pass\n"),
            ("test_ok.py", "def test_ok():\n    pass\n"),
        ]);

        let manifest = differential(&tmp, 2);

        assert_eq!(manifest.errors.len(), 1, "{:?}", manifest.errors);
        assert_eq!(manifest.errors[0].path, "test_bad.py");
        assert_eq!(
            manifest.tests[0].tier,
            crate::v2::manifest::Tier::Static,
            "the good file is still static"
        );
    }

    /// Two same-stem files in different non-package directories are pytest's `import file
    /// mismatch`: the second is a collection error, and reproducing it needs both of them in
    /// one interpreter.  Tier S refuses the pair outright, and the differential is what proves
    /// the refusal is not merely cautious but necessary.
    #[test]
    fn a_same_stem_pair_keeps_pytests_import_mismatch_error() {
        let tmp = tree(&[
            ("a/test_dup.py", "def test_one():\n    pass\n"),
            ("b/test_dup.py", "def test_two():\n    pass\n"),
        ]);

        let manifest = differential(&tmp, 2);

        assert_eq!(manifest.errors.len(), 1, "{:?}", manifest.errors);
        assert!(
            manifest.errors[0].message.contains("import file mismatch"),
            "unexpected message: {}",
            manifest.errors[0].message
        );
    }

    /// A conftest fixture with `params=` multiplies the ids of every test in the directory —
    /// including a test file that mentions no fixture at all.  Nothing in that file's own AST
    /// says so, which is why the rule is about the *chain* and not about the file.
    #[test]
    fn a_parametrized_conftest_fixture_routes_the_whole_directory_to_d() {
        let tmp = tree(&[
            (
                "conftest.py",
                "import pytest\n\n\n@pytest.fixture(params=[1, 2], autouse=True)\ndef flavour(request):\n    return request.param\n",
            ),
            ("test_quiet.py", "def test_q():\n    pass\n"),
        ]);

        let manifest = differential(&tmp, 1);

        assert_eq!(
            manifest
                .tests
                .iter()
                .map(|test| test.id.as_str())
                .collect::<Vec<_>>(),
            ["test_quiet.py::test_q[1]", "test_quiet.py::test_q[2]"],
            "the autouse fixture's parameters reached a file that never mentions it"
        );
        assert!(manifest
            .tests
            .iter()
            .all(|test| test.tier == crate::v2::manifest::Tier::Dynamic));
    }

    /// The whole point restated as a test: with `collect_tier = "d"` nothing is static, so a
    /// caller can always get the oracle leg.
    #[test]
    fn dynamic_only_mode_produces_no_static_entries() {
        let tmp = tree(&[("test_a.py", "def test_one():\n    pass\n")]);

        let manifest = collect(
            tmp.path(),
            &[],
            &crate::v2::test_python(),
            1,
            &CollectOptions {
                tier: TierMode::DynamicOnly,
                ..CollectOptions::new()
            },
        )
        .unwrap();

        assert!(manifest
            .tests
            .iter()
            .all(|test| test.tier == crate::v2::manifest::Tier::Dynamic));
    }
}
