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

use std::collections::HashSet;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::mpsc::Receiver;
use std::sync::{Arc, Mutex};

use crate::v2::config::{
    matches_file_pattern, normpath, resolve_config, ConfigError, ResolvedConfig,
};
use crate::v2::manifest::{
    CollectedTest, CollectionErrorEntry, CollectionManifest, MANIFEST_SCHEMA_VERSION,
};
use crate::v2::protocol::{WorkerRequest, WorkerResponse, PROTOCOL_VERSION};

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
    /// A dispatch thread panicked — a bug in this module, never a worker's doing.
    WorkerPanicked { worker: usize },
    /// Unreachable by construction, loud rather than silent: a dispatched file that no
    /// worker returned a result for would otherwise vanish from the manifest.
    MissingResponse { path: PathBuf },
}

impl std::fmt::Display for CollectError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CollectError::Config(err) => write!(f, "{err}"),
            CollectError::ArgNotFound(path) => {
                write!(f, "file or directory not found: {}", path.display())
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
            CollectError::WorkerPanicked { worker } => {
                write!(f, "the dispatch thread for worker {worker} panicked")
            }
            CollectError::MissingResponse { path } => {
                write!(f, "no worker returned a result for {}", path.display())
            }
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
}

impl WorkerLauncher {
    /// `python_executable -m rustest._v2_worker` — the real worker.
    pub fn module(python_executable: &str) -> Self {
        Self {
            program: python_executable.to_string(),
            args: vec!["-m".to_string(), "rustest._v2_worker".to_string()],
        }
    }

    fn describe(&self) -> String {
        std::iter::once(self.program.clone())
            .chain(self.args.iter().cloned())
            .collect::<Vec<_>>()
            .join(" ")
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
) -> Result<CollectionManifest, CollectError> {
    collect_with_launcher(
        invocation_dir,
        args,
        &WorkerLauncher::module(python_executable),
        workers,
    )
}

/// One file's worth of worker output.
struct FileOutcome {
    tests: Vec<CollectedTest>,
    error: Option<CollectionErrorEntry>,
}

fn collect_with_launcher(
    invocation_dir: &Path,
    args: &[PathBuf],
    launcher: &WorkerLauncher,
    workers: usize,
) -> Result<CollectionManifest, CollectError> {
    let (config, targets) = discover(invocation_dir, args)?;
    let rootdir = to_posix(&config.rootdir);

    if targets.is_empty() {
        return Ok(CollectionManifest {
            schema_version: MANIFEST_SCHEMA_VERSION,
            rootdir,
            tests: Vec::new(),
            errors: Vec::new(),
        });
    }

    // More workers than files would spawn interpreters with nothing to do; fewer than one
    // could not collect at all.
    let pool_size = workers.clamp(1, targets.len());

    // Routing is by stem hash (see the module docs), and the dispatch index recorded here
    // is what puts the manifest back into walk order afterwards.
    let mut assignments: Vec<Vec<(usize, PathBuf)>> = vec![Vec::new(); pool_size];
    for (index, path) in targets.iter().enumerate() {
        assignments[worker_for(path, pool_size)].push((index, path.clone()));
    }

    let init = WorkerRequest::Init {
        protocol_version: PROTOCOL_VERSION,
        rootdir: rootdir.clone(),
        python_files: config.python_files.clone(),
        python_classes: config.python_classes.clone(),
        python_functions: config.python_functions.clone(),
    };

    // Spawn and handshake the whole pool up front, so a bad interpreter or a version skew
    // is reported before any file is collected.  A failure here drops the workers already
    // started, and `Worker::drop` kills them.
    let mut pool = Vec::with_capacity(pool_size);
    for index in 0..pool_size {
        let mut worker = Worker::spawn(index, launcher)?;
        worker.handshake(&init)?;
        pool.push(worker);
    }

    let results = std::thread::scope(|scope| {
        let handles: Vec<_> = pool
            .into_iter()
            .zip(assignments)
            .map(|(worker, files)| scope.spawn(move || run_worker(worker, files)))
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

    // Reassemble by dispatch index — NOT in completion order.
    let mut slots: Vec<Option<FileOutcome>> = targets.iter().map(|_| None).collect();
    let mut failure: Option<CollectError> = None;
    for result in results {
        match result {
            Ok(outcomes) => {
                for (index, outcome) in outcomes {
                    slots[index] = Some(outcome);
                }
            }
            // Worker order is deterministic, so the reported failure is too.
            Err(err) => failure = failure.or(Some(err)),
        }
    }
    if let Some(err) = failure {
        return Err(err);
    }

    let mut tests = Vec::new();
    let mut errors = Vec::new();
    for (path, slot) in targets.iter().zip(slots) {
        let Some(outcome) = slot else {
            return Err(CollectError::MissingResponse { path: path.clone() });
        };
        tests.extend(outcome.tests);
        errors.extend(outcome.error);
    }

    Ok(CollectionManifest {
        schema_version: MANIFEST_SCHEMA_VERSION,
        rootdir,
        tests,
        errors,
    })
}

// ---------------------------------------------------------------------------
// The walk — `_pytest/main.py`, `_pytest/python.py`, `_pytest/pathlib.py`
// ---------------------------------------------------------------------------

/// Resolve config and produce the files to collect, in walk order.
fn discover(
    invocation_dir: &Path,
    args: &[PathBuf],
) -> Result<(ResolvedConfig, Vec<PathBuf>), CollectError> {
    // `os.getcwd()` — what pytest's `invocation_params.dir` always is — is absolute and
    // free of `.`/`..`; normalising here gives callers the same guarantee for free.
    let invocation_dir = normpath(invocation_dir);
    let config = resolve_config(&invocation_dir, args).map_err(CollectError::Config)?;
    let roots = initial_paths(&invocation_dir, args, &config)?;

    let mut targets = Vec::new();
    let mut seen = HashSet::new();
    for root in &roots {
        if root.is_dir() {
            walk(root, &config, &mut targets, &mut seen);
        } else if is_python_source(root) {
            // An initial path skips the `python_files` filter entirely:
            // `_pytest/python.py::pytest_collect_file` only consults
            // `path_matches_patterns` when `not parent.session.isinitpath(file_path)`.
            // The `.py` suffix test is *outside* that guard, so it still applies — pytest
            // reports "found no collectors" (exit 4) for a non-Python file argument, which
            // is exit-code shaping and therefore Task 4's concern, not the walk's.
            push_target(root, &mut targets, &mut seen);
        }
    }
    Ok((config, targets))
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
fn walk(dir: &Path, config: &ResolvedConfig, out: &mut Vec<PathBuf>, seen: &mut HashSet<PathBuf>) {
    // `scandir` returns `[]` for a directory that cannot be opened, rather than raising.
    let Ok(reader) = std::fs::read_dir(dir) else {
        return;
    };
    let mut entries: Vec<(bool, String, PathBuf)> = reader
        .flatten()
        .map(|entry| {
            let name = entry.file_name().to_string_lossy().into_owned();
            (name != "__init__.py", name, entry.path())
        })
        .collect();
    // Python compares `str` by code point, which for UTF-8 is byte order — Rust's `Ord`
    // for `String` agrees.
    entries.sort();

    for (_, name, path) in entries {
        // `is_dir`/`is_file` follow symlinks, matching `os.DirEntry.is_dir()`'s default.
        if path.is_dir() {
            if should_prune(&name, &path, config) {
                continue;
            }
            walk(&path, config, out, seen);
        } else if path.is_file() && is_python_source(&path) && matches_python_files(&path, config) {
            push_target(&path, out, seen);
        }
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

/// `_pytest/python.py::path_matches_patterns` over the config's `python_files`.
fn matches_python_files(path: &Path, config: &ResolvedConfig) -> bool {
    config
        .python_files
        .iter()
        .any(|pattern| fnmatch_ex(pattern, path))
}

fn push_target(path: &Path, out: &mut Vec<PathBuf>, seen: &mut HashSet<PathBuf>) {
    // Two args can reach the same file (`rustest tests tests/test_a.py`).  Collecting it
    // twice would put duplicate nodeids in the manifest and break its addressability
    // contract, so first sighting wins — which also keeps walk order intact.
    if seen.insert(path.to_path_buf()) {
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

/// Render a path with posix separators — the manifest and protocol path contract.
#[cfg(windows)]
fn to_posix(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

#[cfg(not(windows))]
fn to_posix(path: &Path) -> String {
    path.to_string_lossy().into_owned()
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
fn worker_for(path: &Path, workers: usize) -> usize {
    let stem = path
        .file_stem()
        .map(|stem| stem.to_string_lossy().into_owned())
        .unwrap_or_default();
    (fnv1a(stem.as_bytes()) % workers.max(1) as u64) as usize
}

// ---------------------------------------------------------------------------
// The pool
// ---------------------------------------------------------------------------

/// One worker process and its pipes.
struct Worker {
    index: usize,
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    stderr: Arc<Mutex<Vec<u8>>>,
    /// Disconnects when the stderr drain thread finishes; see [`Worker::diagnostics`].
    stderr_done: Receiver<()>,
    reaped: bool,
}

impl Worker {
    fn spawn(index: usize, launcher: &WorkerLauncher) -> Result<Self, CollectError> {
        let mut child = Command::new(&launcher.program)
            .args(&launcher.args)
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

        Ok(Self {
            index,
            child,
            stdin,
            stdout,
            stderr,
            stderr_done,
            reaped: false,
        })
    }

    fn send(&mut self, request: &WorkerRequest) -> std::io::Result<()> {
        let line = serde_json::to_string(request).expect("a WorkerRequest always serializes");
        self.stdin.write_all(line.as_bytes())?;
        self.stdin.write_all(b"\n")?;
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
    fn diagnostics(&mut self) -> String {
        if !self.reaped {
            let _ = self.child.kill();
            let _ = self.child.wait();
            self.reaped = true;
        }
        let _ = self
            .stderr_done
            .recv_timeout(std::time::Duration::from_millis(500));
        self.stderr
            .lock()
            .map(|buffer| String::from_utf8_lossy(&buffer).trim_end().to_string())
            .unwrap_or_default()
    }

    /// `init` -> `ready`.  A `ready` declares the protocol the worker *speaks*, so a
    /// mismatch here is real skew and is fatal.
    fn handshake(&mut self, init: &WorkerRequest) -> Result<(), CollectError> {
        if let Err(err) = self.send(init) {
            return Err(self.handshake_error(format!("could not send init: {err}")));
        }
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
    fn collect_one(&mut self, path: &Path) -> Result<FileOutcome, CollectError> {
        let posix = to_posix(path);
        let request = WorkerRequest::CollectFile {
            path: posix.clone(),
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
            } => {
                if echoed != posix {
                    return Err(self.protocol(
                        path,
                        format!("the response names `{echoed}`, not the requested file"),
                        line,
                    ));
                }
                // The one malformed shape serde cannot reject (see `protocol.rs`): treated
                // exactly like a decode error.
                if !tests.is_empty() && error.is_some() {
                    return Err(self.protocol(
                        path,
                        "the response carries both tests and an error".to_string(),
                        line,
                    ));
                }
                Ok(FileOutcome { tests, error })
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

    /// `shutdown` -> `bye` -> exit 0.  Anything else is a failed run: the worker exits
    /// non-zero precisely when it hit protocol drift, and swallowing that would turn a
    /// bug into a quietly short manifest.
    fn shutdown(&mut self) -> Result<(), CollectError> {
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
        if !status.success() {
            return Err(self.shutdown_error(format!("the worker exited with {status}")));
        }
        Ok(())
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
    /// Never leave an orphan interpreter behind when a run aborts early.  The drain thread
    /// is left detached: it ends on its own when the pipe closes, and waiting for it here
    /// would reintroduce the hang [`Worker::diagnostics`] exists to avoid.
    fn drop(&mut self) {
        if !self.reaped {
            let _ = self.child.kill();
            let _ = self.child.wait();
            self.reaped = true;
        }
    }
}

/// The `op` a response would carry on the wire, for error messages.
fn response_op(response: &WorkerResponse) -> &'static str {
    match response {
        WorkerResponse::Ready { .. } => "ready",
        WorkerResponse::Collected { .. } => "collected",
        WorkerResponse::Bye => "bye",
    }
}

/// One worker's whole life: collect its files in order, then shut it down.
fn run_worker(
    mut worker: Worker,
    files: Vec<(usize, PathBuf)>,
) -> Result<Vec<(usize, FileOutcome)>, CollectError> {
    let mut outcomes = Vec::with_capacity(files.len());
    for (index, path) in &files {
        outcomes.push((*index, worker.collect_one(path)?));
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

    // --- fixtures ---------------------------------------------------------

    /// The interpreter that runs the **real** worker in the end-to-end tests.
    ///
    /// `RUSTEST_TEST_PYTHON` wins when set (CI, or a dev pointing at another
    /// interpreter).  Otherwise the repo's own `.venv` is used, because that is the one
    /// environment guaranteed to have `rustest` importable — `python` on `PATH` usually
    /// is not, and a bare fallback would fail with a confusing `No module named rustest`.
    /// `python` remains the last resort so the suite still runs from an activated venv.
    fn worker_python() -> String {
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

    fn real_worker() -> WorkerLauncher {
        WorkerLauncher::module(&worker_python())
    }

    /// A stand-in worker: a `python -c` script speaking (or mis-speaking) the protocol.
    ///
    /// This is the seam the crash/drift tests need.  It is an argument rather than an
    /// environment variable on purpose: `std::env::set_var` is process-global, and cargo
    /// runs the test binary's tests on parallel threads, so an env-var switch would be a
    /// data race between tests (and is `unsafe` from edition 2024 onwards).  The seam
    /// exercises exactly the same `collect_with_launcher` path production uses.
    fn scripted_worker(script: &str) -> WorkerLauncher {
        WorkerLauncher {
            program: worker_python(),
            args: vec!["-c".to_string(), script.to_string()],
        }
    }

    const READY: &str = r#"sys.stdout.write('{"op":"ready","protocol_version":1}\n')"#;

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
        let (config, targets) = discover(tmp.path(), args).unwrap();
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

        let err = discover(tmp.path(), &[tmp.path().join("nope")]).unwrap_err();
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

    /// Two args reaching the same file must not collect it twice: a duplicate nodeid
    /// breaks the manifest's addressability contract.
    #[test]
    fn a_file_reached_twice_is_collected_once() {
        let tmp = tree(&[("sub/test_a.py", &module("a"))]);

        assert_eq!(
            discovered(
                &tmp,
                &[tmp.path().join("sub"), tmp.path().join("sub/test_a.py")]
            ),
            vec!["sub/test_a.py"]
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
        let (_, targets) = discover(tmp.path(), &[]).unwrap();
        let used: HashSet<usize> = targets.iter().map(|path| worker_for(path, 2)).collect();
        assert!(
            used.len() >= 2,
            "the fixture must actually spread over both workers, got {used:?}"
        );

        let manifest = collect_with_launcher(tmp.path(), &[], &real_worker(), 2).unwrap();

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

        let manifest = collect_with_launcher(tmp.path(), &[], &real_worker(), 2).unwrap();

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

        let manifest = collect_with_launcher(tmp.path(), &[], &real_worker(), 1).unwrap();

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

        let manifest =
            collect_with_launcher(tmp.path(), &[], &counting_worker(logs.path()), 8).unwrap();
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

    /// Nothing to collect means no process is spawned at all — asserted with a launcher
    /// that could not possibly start.
    #[test]
    fn an_empty_tree_spawns_no_worker() {
        let tmp = tree(&[("notes.txt", "nothing here")]);
        let impossible = WorkerLauncher {
            program: "rustest-no-such-interpreter".to_string(),
            args: Vec::new(),
        };

        let manifest = collect_with_launcher(tmp.path(), &[], &impossible, 4).unwrap();

        assert!(manifest.tests.is_empty());
        assert!(manifest.errors.is_empty());
    }

    /// A worker that cannot be started is loud, and names the program.
    #[test]
    fn a_worker_that_cannot_spawn_is_loud() {
        let tmp = tree(&[("test_a.py", &module("a"))]);
        let impossible = WorkerLauncher {
            program: "rustest-no-such-interpreter".to_string(),
            args: Vec::new(),
        };

        let err = collect_with_launcher(tmp.path(), &[], &impossible, 1).unwrap_err();
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

        let err = collect_with_launcher(tmp.path(), &[], &scripted_worker(script), 1).unwrap_err();
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

        let err = collect_with_launcher(tmp.path(), &[], &scripted_worker(&script), 1).unwrap_err();
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

        let err = collect_with_launcher(tmp.path(), &[], &scripted_worker(&script), 1).unwrap_err();
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

        let err = collect_with_launcher(tmp.path(), &[], &scripted_worker(&script), 1).unwrap_err();
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

        let err = collect_with_launcher(tmp.path(), &[], &scripted_worker(&script), 1).unwrap_err();
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

        let err = collect_with_launcher(tmp.path(), &[], &scripted_worker(&script), 1).unwrap_err();
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

        let err = collect_with_launcher(tmp.path(), &[], &scripted_worker(&script), 1).unwrap_err();
        let message = err.to_string();
        assert!(
            message.contains(&exit_status_phrase(9)),
            "unexpected message: {message}"
        );
    }
}
