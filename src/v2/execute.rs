//! The v2 **run** orchestrator: collect, select, dispatch, reassemble, and decide the exit
//! code.
//!
//! [`run`] is the whole of a v2 test run seen from outside. It reuses collection's walk,
//! routing and worker pool verbatim ([`crate::v2::collect::plan`] /
//! [`crate::v2::collect::spawn_pool`]) and adds the three things execution needs:
//!
//! 1. a **barrier** between collection and dispatch, because selection needs the complete
//!    manifest before any test may be dispatched;
//! 2. **warm dispatch** — every test goes to the worker that already imported its file;
//! 3. the **exit code**, which is a port of pytest's `_pytest/main.py::_main` and is the
//!    part of a run that tooling actually reads.
//!
//! # Why the workers stay alive across the barrier
//!
//! Collection ends with `shutdown`; a run must not. `WorkerRequest::ExecuteTest`'s contract
//! is that "the worker must have collected the test's file already in this session, so
//! imports are warm and the test object is the one enumeration saw". Respawning the pool
//! after collection would re-import every module — the cost collection just paid — and,
//! worse, would re-derive the test objects, reopening exactly the collect/execute drift the
//! manifest exists to close.
//!
//! So each worker thread collects its files, hands the outcomes to the main thread, and
//! then **blocks** on a channel until the main thread has assembled the manifest, applied
//! `-k`/`-m` and worked out which tests that worker owns. The main thread never touches a
//! worker's pipes; a worker thread never sees another worker's files.
//!
//! # Dispatch order: grouped by file
//!
//! Within a worker, tests are dispatched **grouped by file**, groups in first-appearance
//! order and manifest order inside each group. This is not a performance tweak, it is a
//! correctness one: `_v2_worker.py` detects module and class boundaries by comparing the
//! incoming test's file against the previous one, so interleaving files across a worker
//! would tear down and rebuild module-scoped fixtures at every switch. Grouping reproduces
//! pytest's setup counts for a same-file run. (The report is reassembled by manifest index,
//! so dispatch order is invisible in the output.)
//!
//! # What this module refuses to re-derive
//!
//! The worker owns the three-phase reduction — `setup`/`call`/`teardown` collapse to one
//! status before the result ever reaches the wire. This module *validates* that status
//! against the documented six and treats anything else as protocol drift, which is the
//! division of labour `src/v2/protocol.rs` states in `TestResult`'s field docs.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::time::Instant;

use serde::{Deserialize, Serialize};

pub use crate::v2::cache::LastFailedMode;
use crate::v2::cache::{last_failed_order, merge_last_failed, read_last_failed, write_last_failed};
use crate::v2::collect::{
    assemble, plan, spawn_pool, CollectError, Dispatch, FileOutcome, Worker, WorkerLauncher,
};
use crate::v2::manifest::{CollectedTest, CollectionErrorEntry};
use crate::v2::protocol::CoverageWire;
use crate::v2::selection::{select_mask, SelectionError};

/// `_v2_worker.py::SHUTDOWN_TEARDOWN_EXIT`.
///
/// The worker answers `bye`, then exits 3 because unwinding its still-open scopes raised.
/// The response stream is complete and the process is not confused — a user's fixture
/// teardown broke. Kept distinct from 2 (protocol drift) so "your fixture is broken" and
/// "the protocol drifted" stay tellable apart; see
/// [`crate::v2::collect::Worker::shutdown_run`].
pub const SHUTDOWN_TEARDOWN_EXIT: i32 = 3;

/// `_v2_worker.py::SESSION_EXIT_EXIT`.
///
/// A test called `pytest.exit()`. The worker wrote pytest's `Exit: <reason>` banner to its
/// stderr and stopped without answering the request in flight. Distinct from
/// [`SHUTDOWN_TEARDOWN_EXIT`] and from 2 (protocol drift) because the orchestrator's response
/// is different in kind: the results already reported are **kept** and the run exits 2
/// (pytest's `INTERRUPTED`), where a broken worker is an orchestration failure at exit 3.
///
/// The code is the entire channel — it carries the *fact* and no payload — which is why
/// `pytest.exit(returncode=N)`'s N is not honoured. A payload would need a wire op, and the
/// point of routing this through the exit status is that [`crate::v2::protocol`] does not
/// change.
pub const SESSION_EXIT_EXIT: i32 = 4;

/// Version of the run-report wire format (`--report-json`).
pub const REPORT_SCHEMA_VERSION: u32 = 2;

/// Everything about a run that is not "which tests", i.e. the flags that change *how* the
/// selected tests are executed rather than which ones are selected.
///
/// A struct rather than four positional `bool`s because every one of them would be a `bool`
/// and a transposed pair would compile silently.
///
/// `Copy` was dropped when `coverage` landed: the field owns two heap allocations, and the
/// alternative — borrowing the wire object through the run — would put a lifetime on a type
/// that is otherwise plain configuration. Cloning it happens exactly once per run.
#[derive(Debug, Clone, Default)]
pub struct RunOptions {
    /// `-x` / `--exitfirst`: stop dispatching once a test has failed.  See [`run_with_launcher`].
    ///
    /// `-x` is `--maxfail=1`, and that is how the CLI spells it: [`Self::max_fail`] carries
    /// the number and this stays the "stop the worker mid-batch" switch, which only the
    /// first failure can justify.
    pub fail_fast: bool,
    /// `--maxfail=N`: stop dispatching once **N** tests have failed. `0` means no limit.
    ///
    /// pytest counts failures in one process and raises `Failed` from
    /// `pytest_runtest_logreport` the moment the count is reached
    /// (`_pytest/main.py::pytest_runtest_logreport`), so it never starts test N+1. This pool
    /// counts across workers and stops **dispatching** at N, so with `-n>1` and `N>1` the
    /// tests already in flight still finish and the run can report more than N failures --
    /// the same granularity `pytest-xdist` has, and for the same reason. `N == 1` keeps the
    /// exact `-x` behaviour, because `fail_fast` also travels into the batch.
    pub max_fail: usize,
    /// `--lf` / `--ff`: how the last-failed cache reorders the selection.
    pub last_failed: LastFailedMode,
    /// `-s` / `--no-capture`: the worker does not redirect a test's streams.
    pub no_capture: bool,
    /// Collect python fences out of `.md` files (rustest's own extension; `--no-codeblocks`
    /// turns it off).
    pub codeblocks: bool,
    /// Rewrite the assertions of statically analysable files
    /// (`crate::v2::static_collect::rewrite_plan`, `python/rustest/_assertion_rewrite.py`).
    ///
    /// **On in production**; the `false` leg is the escape hatch
    /// (`RUSTEST_V2_ASSERT_REWRITE=off`) and the control leg of the differential. Rewriting
    /// changes the *bytecode a user's tests run as*, which is a larger claim than the
    /// manifest cache's, so "recompute the answer without it" has to be askable from a
    /// subprocess without editing anything.
    pub assert_rewrite: bool,
    /// `--cov`: the source trees to measure and the directory the workers write their
    /// per-process coverage data into, or `None` for a run that measures nothing.
    ///
    /// `None` is not "measure everything and report nothing": it means **no worker registers a
    /// `sys.monitoring` tool at all**, which is what keeps a plain run's per-test cost exactly
    /// what it was before this option existed (`python/rustest/_v2_coverage.py`).
    pub coverage: Option<CoverageWire>,
}

impl RunOptions {
    /// The defaults a plain `rustest <paths>` uses: capture on, codeblocks on, assertion
    /// rewriting on, no `-x`, no `--lf`.  `Default::default()` cannot be it, because
    /// `codeblocks` and `assert_rewrite` both default to *true* and `bool::default()` is
    /// false — a silent feature removal for anyone who built a `RunOptions` with
    /// `..Default::default()`.
    pub fn defaults() -> Self {
        Self {
            fail_fast: false,
            max_fail: 0,
            last_failed: LastFailedMode::None,
            no_capture: false,
            codeblocks: true,
            assert_rewrite: true,
            coverage: None,
        }
    }
}

// ---------------------------------------------------------------------------
// Outcomes
// ---------------------------------------------------------------------------

/// pytest's six **reporting categories** — the closed set `WorkerResponse::TestResult`
/// documents, and the reason this is an enum here while it stays a `String` on the wire.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TestStatus {
    Passed,
    Failed,
    Skipped,
    #[serde(rename = "xfailed")]
    XFailed,
    #[serde(rename = "xpassed")]
    XPassed,
    Error,
}

impl TestStatus {
    /// The wire spelling -> the category, or `None` for a value no worker should ever send.
    pub fn parse(status: &str) -> Option<Self> {
        match status {
            "passed" => Some(TestStatus::Passed),
            "failed" => Some(TestStatus::Failed),
            "skipped" => Some(TestStatus::Skipped),
            "xfailed" => Some(TestStatus::XFailed),
            "xpassed" => Some(TestStatus::XPassed),
            "error" => Some(TestStatus::Error),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            TestStatus::Passed => "passed",
            TestStatus::Failed => "failed",
            TestStatus::Skipped => "skipped",
            TestStatus::XFailed => "xfailed",
            TestStatus::XPassed => "xpassed",
            TestStatus::Error => "error",
        }
    }

    /// Does this outcome make the run exit 1?
    ///
    /// `_pytest/main.py::_main`: `if session.testsfailed: return ExitCode.TESTS_FAILED`,
    /// and `session.testsfailed` is incremented by `Session.pytest_runtest_logreport` for
    /// any report with `report.failed`. So the question is which categories carry a *failed*
    /// report, and every row below is probed rather than reasoned:
    ///
    /// | run | pytest summary | exit |
    /// |---|---|---|
    /// | one failing assert | `1 failed` | 1 |
    /// | a fixture that raises at setup | `1 error` | **1** |
    /// | a fixture that raises at teardown | `1 passed, 1 error` | **1** |
    /// | `tearDownClass` raises | `1 passed, 1 error` | **1** |
    /// | `@xfail`, body fails | `1 xfailed` | 0 |
    /// | `@xfail`, body passes | `1 xpassed` | **0** |
    /// | `@xfail(strict=True)`, body passes | `1 failed` | 1 |
    /// | `@skip` | `1 skipped` | 0 |
    ///
    /// The two rows that decide the shape of this function are `error` (an ERROR is a
    /// failure for exit-code purposes, even when the test's own body passed) and plain
    /// `xpassed` (an X is **not** a failure — a strict xpass never reaches this enum,
    /// because pytest rewrites it to an ordinary `failed` before reporting, so the worker
    /// sends `"failed"` and the `[XPASS(strict)]` prefix is the only trace).
    pub fn is_failure(self) -> bool {
        matches!(self, TestStatus::Failed | TestStatus::Error)
    }
}

/// One executed test, as it appears in the JSON report.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TestOutcome {
    pub id: String,
    pub status: TestStatus,
    #[serde(rename = "duration")]
    pub duration_s: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stdout: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stderr: Option<String>,
}

// ---------------------------------------------------------------------------
// The report
// ---------------------------------------------------------------------------

/// Counts, in the buckets pytest's terminal summary uses.
///
/// `xfailed` and `xpassed` are their own buckets rather than folded into `skipped` and
/// `passed`, which is the whole reason the report schema moves to v2: v1 had three statuses
/// and could not express either, so a suite's `X`s were invisible.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct RunSummary {
    pub total: usize,
    pub passed: usize,
    pub failed: usize,
    pub skipped: usize,
    pub xfailed: usize,
    pub xpassed: usize,
    pub error: usize,
    pub deselected: usize,
    pub duration: f64,
}

impl RunSummary {
    /// `selected` is how many tests the run *intended* to execute, which is `outcomes.len()`
    /// for every run that finished and larger for one `-x` or `pytest.exit()` truncated.
    ///
    /// That is what `total` means, and the distinction only became visible when truncation
    /// did: "how many rows follow" is trivially `tests.len()` and worth nothing, while "how
    /// many tests this run selected" is the denominator `-v`'s percent column needs (pytest's
    /// is `session.testscollected`, i.e. after deselection and before any truncation — probed:
    /// `pytest -x -v` on 3 tests stopping at the second prints `[ 33%]`, `[ 66%]`, and
    /// `pytest -v -k` selecting 2 of 4 prints `[ 50%]`, `[100%]`).
    fn tally(outcomes: &[TestOutcome], selection: Selection, duration: f64) -> Self {
        let mut summary = RunSummary {
            total: selection.kept,
            deselected: selection.deselected,
            // A module-level skip is one `skipped` with no id and no seat in `total`; see
            // `Selection::module_skipped` for the pytest probe that fixes both halves.
            skipped: selection.module_skipped,
            duration,
            ..RunSummary::default()
        };
        for outcome in outcomes {
            match outcome.status {
                TestStatus::Passed => summary.passed += 1,
                TestStatus::Failed => summary.failed += 1,
                TestStatus::Skipped => summary.skipped += 1,
                TestStatus::XFailed => summary.xfailed += 1,
                TestStatus::XPassed => summary.xpassed += 1,
                TestStatus::Error => summary.error += 1,
            }
        }
        summary
    }
}

/// The complete output of a v2 run — and the `--report-json` document verbatim.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunReport {
    pub version: u32,
    /// Absolute rootdir, posix separators.
    pub rootdir: String,
    /// pytest's exit code for this run; see [`exit_code`].
    pub exit_code: i32,
    pub summary: RunSummary,
    pub tests: Vec<TestOutcome>,
    #[serde(default)]
    pub collection_errors: Vec<CollectionErrorEntry>,
    /// Teardown failures that belong to no test: a module- or session-scoped fixture that
    /// raised while a worker unwound at shutdown, after the last test it owned had already
    /// been reported. Each one counts as a failure for [`exit_code`].
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub teardown_errors: Vec<String>,
    /// Whatever the workers wrote to stderr, never graded.
    ///
    /// Class- and module-scoped teardown output is drained at a *boundary*, outside the
    /// per-test capture window (1b.2 Task 3's documented divergence), so a completely
    /// successful run's stderr can carry legitimate user output. Discarding it would lose a
    /// `print` from a teardown; treating it as a failure signal would fail green runs.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub worker_stderr: Vec<String>,
    /// `-x` fired: dispatch stopped after a failure and the selection is only partly run.
    ///
    /// Omitted when false, so the schema-v2 golden document is byte-unchanged for every run
    /// that did not stop early — which is every run the conformance harness makes.
    #[serde(default, skip_serializing_if = "is_false")]
    pub stopped_early: bool,
    /// A test called `pytest.exit()`; the string is the worker's diagnostics, carrying
    /// pytest's `Exit: <reason>` banner. Omitted when absent, so every ordinary run's
    /// document is unchanged.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_exit: Option<String>,
}

/// `skip_serializing_if` needs a predicate over a reference; `bool::not` is not one.
#[allow(clippy::trivially_copy_pass_by_ref)]
fn is_false(value: &bool) -> bool {
    !*value
}

// ---------------------------------------------------------------------------
// Exit codes — `_pytest/main.py::_main` + `wrap_session`
// ---------------------------------------------------------------------------

/// pytest's exit code for a completed run.
///
/// Port of `_pytest/main.py::_main` (pytest 8.4.2), which is four lines and an ordering:
///
/// ```text
/// config.hook.pytest_collection(session=session)      # UsageError  -> 4
/// config.hook.pytest_runtestloop(session=session)     # Interrupted -> 2
/// if session.testsfailed:           return ExitCode.TESTS_FAILED         # 1
/// elif session.testscollected == 0: return ExitCode.NO_TESTS_COLLECTED   # 5
/// return None                                                            # 0
/// ```
///
/// **The order is the contract**, and each precedence is probed:
///
/// | run | pytest exit | which branch |
/// |---|---|---|
/// | one unimportable file, one passing file | **2** | `pytest_runtestloop` raises `Interrupted` before any test runs, so the passing file never runs either |
/// | one unimportable file, one *failing* file | **2** | same — collection errors outrank test failures |
/// | `-k keep` leaving one failing test | 1 | `testsfailed` |
/// | `-k nomatch` | 5 | `testscollected == 0`, counted **after** deselection |
/// | empty tree | 5 | same branch, no deselection |
/// | all passing, some skipped/xfailed/xpassed | 0 | nothing failed |
///
/// `collection_errors` maps to `session.testsfailed` *at the point `pytest_runtestloop`
/// checks it* (`Session.pytest_collectreport` increments it for every failed collect
/// report), which is why it is a separate argument and outranks `failures`: pytest raises
/// `Interrupted` — a `KeyboardInterrupt` subclass, caught by `wrap_session` as
/// `ExitCode.INTERRUPTED` — instead of running anything.
///
/// Two codes are deliberately **not** produced here, because neither is a property of a
/// completed run: **4** (usage error — a bad path argument, an unusable config file, a
/// malformed `-k`/`-m` expression) and **3** (internal error — the pool itself failed).
/// Both are raised as exceptions and classified by kind at the Python boundary
/// (`src/v2/py.rs`), exactly as `wrap_session` classifies `UsageError` versus any other
/// `BaseException`.
pub fn exit_code(
    collected: usize,
    collection_errors: usize,
    failures: usize,
    session_exit: bool,
) -> i32 {
    // `pytest.exit()` outranks everything, including failures already reported.
    // `_pytest/main.py::wrap_session` catches `Exit` in the same arm as `KeyboardInterrupt`
    // and sets `exitstatus = ExitCode.INTERRUPTED` regardless of `session.testsfailed`,
    // because the `except Failed` arm never runs — the exception that escaped is the `Exit`.
    // Probed on pytest 8.4.2: a file whose first test fails and whose second calls
    // `pytest.exit()` reports `1 failed` and exits **2**, not 1.
    if session_exit {
        return 2;
    }
    if collection_errors > 0 {
        return 2;
    }
    if failures > 0 {
        return 1;
    }
    if collected == 0 {
        return 5;
    }
    0
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/// Everything that can abort a run before it produces a report.
#[derive(Debug)]
pub enum RunError {
    /// Orchestration or walk failure — carries collection's own taxonomy so the
    /// usage-versus-internal split stays in one place.
    Collect(CollectError),
    /// A malformed `-k`/`-m` expression, or one `-k` refuses to evaluate. pytest's
    /// `UsageError`, i.e. exit 4.
    Selection(SelectionError),
}

impl std::fmt::Display for RunError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RunError::Collect(err) => write!(f, "{err}"),
            RunError::Selection(err) => write!(f, "{err}"),
        }
    }
}

impl std::error::Error for RunError {}

impl From<CollectError> for RunError {
    fn from(err: CollectError) -> Self {
        RunError::Collect(err)
    }
}

impl From<SelectionError> for RunError {
    fn from(err: SelectionError) -> Self {
        RunError::Selection(err)
    }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Collect, select and run `args`, returning the report.
///
/// `python_executable` is the interpreter the workers run under, resolved by the caller so
/// this crate never guesses one. `workers` is the pool size, clamped to `[1, files]`.
/// `keyword` and `mark` are the raw `-k` / `-m` option values.
pub fn run(
    invocation_dir: &Path,
    args: &[PathBuf],
    python_executable: &str,
    workers: usize,
    keyword: Option<&str>,
    mark: Option<&str>,
    options: RunOptions,
) -> Result<RunReport, RunError> {
    let launcher = WorkerLauncher::module(python_executable);
    let launcher = if options.no_capture {
        launcher.without_capture()
    } else {
        launcher
    };
    run_with_launcher(
        invocation_dir,
        args,
        &launcher,
        workers,
        keyword,
        mark,
        options,
    )
}

/// When a worker should stop dispatching, and how it says so.
///
/// The two flags mean different things and only one of them is opt-in, which is why they are
/// a named pair rather than two adjacent `&AtomicBool` arguments: `stop` is "a test failed"
/// and is acted on only under `-x`; `session_over` is "a test called `pytest.exit()`" and is
/// acted on always. Swapping them would silently make `-x` mandatory, or `pytest.exit()`
/// optional.
#[derive(Clone, Copy)]
struct StopSignals<'a> {
    fail_fast: bool,
    /// `--maxfail=N`; `0` is no limit.  Counted in [`StopSignals::failures`].
    max_fail: usize,
    stop: &'a AtomicBool,
    /// How many failures the whole pool has recorded, for `--maxfail`.
    failures: &'a AtomicUsize,
    session_over: &'a AtomicBool,
}

impl StopSignals<'_> {
    fn should_stop(&self) -> bool {
        self.session_over.load(Ordering::SeqCst)
            || ((self.fail_fast || self.max_fail > 0) && self.stop.load(Ordering::SeqCst))
    }

    /// `--maxfail`'s budget for the next batch, or `None` when there is no limit.
    fn remaining_budget(&self) -> Option<usize> {
        if self.max_fail == 0 {
            return None;
        }
        Some(
            self.max_fail
                .saturating_sub(self.failures.load(Ordering::SeqCst)),
        )
    }

    /// Record one failure and answer whether the run has hit `--maxfail`.
    fn note_failure(&self) -> bool {
        if self.max_fail == 0 {
            return false;
        }
        self.failures.fetch_add(1, Ordering::SeqCst) + 1 >= self.max_fail
    }
}

/// One worker's whole life in a run.
#[derive(Default)]
struct WorkerRun {
    /// `(report slot, outcome)` — the slot is the index into the *selected* manifest, so
    /// the report reassembles in manifest order however the pool interleaved.
    results: Vec<(usize, TestOutcome)>,
    teardown: Option<String>,
    stderr: String,
    /// Set when this worker's test called `pytest.exit()`; the string is the worker's
    /// diagnostics, carrying pytest's `Exit: <reason>` banner.
    session_exit: Option<String>,
}

/// What the main thread hands one worker across the barrier: its tests, grouped by file.
///
/// The outer `Vec` is one entry per **file**, and that is the unit of an `execute_batch`
/// request; the inner is `(report slot, test id)` in manifest order.
type Assignment = Vec<Vec<(usize, String)>>;

/// One worker's whole collection phase: its files' outcomes, or the failure that stopped it.
type CollectedFiles = Result<Vec<(usize, FileOutcome)>, CollectError>;
/// What a worker thread reports across the barrier: which worker, and what it collected.
type CollectMessage = (usize, CollectedFiles);
/// The main thread's slot per worker; `None` means that worker never reported (a panic).
type CollectInbox = Vec<Option<CollectedFiles>>;

/// The whole of a run, with the worker launcher injected.
///
/// # `-x` / `--exitfirst`
///
/// pytest's `-x` is `--maxfail=1`: `Session.pytest_runtest_logreport` increments
/// `testsfailed` and sets `shouldfail` once it reaches `maxfail`, and
/// `_pytest/main.py::pytest_runtestloop` raises `session.Failed` at the top of the *next*
/// iteration — so the failing test is reported and **nothing after it runs**.  `wrap_session`
/// catches `Failed` as `ExitCode.TESTS_FAILED`.  Probed (pytest 8.4.2) on a four-test file
/// whose second test fails: `1 failed, 1 passed`, exit **1**, and `test_c`/`test_d` appear
/// nowhere — not as skipped, not as anything.
///
/// Two properties follow, and the report reproduces both: the exit code is the ordinary
/// failure code (1), and the summary counts **only the tests that ran**.
///
/// **Parallel semantics, stated because they cannot be pytest's.**  With `-n 1` this is
/// exactly pytest: one worker, sequential dispatch, stop at the first failing result.  With a
/// pool, every worker checks the shared flag before each dispatch, so the tests already in
/// flight finish and *then* everything stops — "at most one failure" becomes "at least one
/// failure, and no test started after it was seen".  Which extra tests ran is therefore
/// timing-dependent under `-n >1`, and `rustest -x` is documented as sequential-exact.
fn run_with_launcher(
    invocation_dir: &Path,
    args: &[PathBuf],
    launcher: &WorkerLauncher,
    workers: usize,
    keyword: Option<&str>,
    mark: Option<&str>,
    options: RunOptions,
) -> Result<RunReport, RunError> {
    let started = Instant::now();
    let dispatch = plan(
        invocation_dir,
        args,
        workers,
        options.codeblocks,
        options.assert_rewrite,
        options.coverage.clone(),
    )?;
    let previously_failed = read_last_failed(&dispatch.rootdir_path);

    if dispatch.targets.is_empty() {
        // Still compile the expressions. `deselect_by_keyword` parses **before** it loops
        // (`_pytest/mark/__init__.py` l. 214), so `-k "and and"` is a usage error even when
        // there is nothing to match — exit 4, not a quiet exit 5.
        let _ = select_mask(&[], keyword, mark)?;
        return Ok(finish(
            dispatch.rootdir,
            Vec::new(),
            Vec::new(),
            Selection::default(),
            WorkerResidue::default(),
            false,
            started,
        ));
    }

    let pool = spawn_pool(&dispatch, launcher)?;
    let pool_size = pool.len();

    let (collect_tx, collect_rx) = channel::<CollectMessage>();
    let mut exec_txs: Vec<Sender<Assignment>> = Vec::with_capacity(pool_size);
    let mut exec_rxs: Vec<Receiver<Assignment>> = Vec::with_capacity(pool_size);
    for _ in 0..pool_size {
        let (tx, rx) = channel();
        exec_txs.push(tx);
        exec_rxs.push(rx);
    }

    // One flag for the whole pool: set by whichever worker first sees a failing result, read
    // by every worker before each dispatch.  `SeqCst` because the cost is irrelevant next to
    // a subprocess round trip and the ordering question is then not one anybody has to think
    // about again.
    let stop = AtomicBool::new(false);
    // `--maxfail`'s counter, shared across the pool.
    let failures = AtomicUsize::new(0);
    let stop = &stop;
    // A second flag, because `stop` alone is ambiguous: under `-x` it means "a test failed",
    // and only a `fail_fast` run acts on it.  A `pytest.exit()` must stop dispatch whether or
    // not `-x` was given, and must be told apart from `-x` when the report is assembled —
    // one is exit 1, the other exit 2.
    let session_over = AtomicBool::new(false);
    let session_over = &session_over;
    let signals = StopSignals {
        fail_fast: options.fail_fast,
        max_fail: options.max_fail,
        failures: &failures,
        stop,
        session_over,
    };

    std::thread::scope(|scope| {
        let handles: Vec<_> = pool
            .into_iter()
            .zip(dispatch.assignments.iter().cloned())
            .zip(exec_rxs)
            .enumerate()
            .map(|(index, ((worker, files), exec_rx))| {
                let collect_tx = collect_tx.clone();
                let keys = &dispatch.assert_keys;
                scope.spawn(move || {
                    worker_life(index, worker, files, keys, collect_tx, exec_rx, signals)
                })
            })
            .collect();
        // The clones live in the threads; this one would otherwise keep the channel open
        // forever and turn a dead worker into a hang.
        drop(collect_tx);

        // --- the barrier: every worker's collection, then selection ---------
        let mut inbox: CollectInbox = (0..pool_size).map(|_| None).collect();
        for _ in 0..pool_size {
            // `Err` means every sender is gone, i.e. a thread ended without reporting — a
            // panic. `join` below turns that into `WorkerPanicked`, a better diagnosis than
            // anything that could be invented here.
            let Ok((index, result)) = collect_rx.recv() else {
                break;
            };
            inbox[index] = Some(result);
        }

        let staged = stage(
            &dispatch,
            inbox,
            keyword,
            mark,
            &previously_failed,
            options.last_failed,
        );

        // Hand out the work — or drop every sender, which unblocks the workers to shut
        // down — *before* joining, or the join deadlocks on threads still at the barrier.
        if let Ok(staged) = &staged {
            for (tx, assignment) in exec_txs.iter().zip(&staged.per_worker) {
                let _ = tx.send(assignment.clone());
            }
        }
        drop(exec_txs);

        let runs: Vec<Result<WorkerRun, CollectError>> = handles
            .into_iter()
            .enumerate()
            .map(|(index, handle)| {
                handle
                    .join()
                    .unwrap_or(Err(CollectError::WorkerPanicked { worker: index }))
            })
            .collect();

        let staged = staged?;

        let mut slots: Vec<Option<TestOutcome>> = staged.selected.iter().map(|_| None).collect();
        let mut residue = WorkerResidue::default();
        let mut failure: Option<CollectError> = None;
        for run in runs {
            match run {
                Ok(run) => {
                    for (slot, outcome) in run.results {
                        slots[slot] = Some(outcome);
                    }
                    residue.teardown_errors.extend(run.teardown);
                    if !run.stderr.is_empty() {
                        residue.stderr.push(run.stderr);
                    }
                    // Worker order is deterministic, so "the first one" is too.
                    residue.session_exit = residue.session_exit.or(run.session_exit);
                }
                // Worker order is deterministic, so the reported failure is too.
                Err(err) => failure = failure.or(Some(err)),
            }
        }
        if let Some(err) = failure {
            return Err(RunError::Collect(err));
        }

        // A missing slot is normally a protocol bug — a test the pool never answered — and
        // saying so is the whole reason `MissingResult` exists.  Under a fired `-x` it is
        // instead the *expected* shape: dispatch stopped, so the tail of the selection has no
        // result and must be dropped from the report rather than invented.  The flag is the
        // only thing that tells the two apart, which is why the check is on it and not on
        // `options.fail_fast` (a `-x` run that never failed must still catch a lost result).
        let stopped_early = stop.load(Ordering::SeqCst);
        let session_ended = session_over.load(Ordering::SeqCst);
        let mut outcomes = Vec::with_capacity(slots.len());
        for (test, slot) in staged.selected.iter().zip(slots) {
            match slot {
                Some(outcome) => outcomes.push(outcome),
                None if stopped_early || session_ended => {}
                None => {
                    return Err(RunError::Collect(CollectError::MissingResult {
                        id: test.id.clone(),
                    }))
                }
            }
        }

        let report = finish(
            dispatch.rootdir.clone(),
            outcomes,
            staged.errors,
            Selection {
                kept: staged.selected.len(),
                deselected: staged.deselected,
                module_skipped: staged.module_skipped,
            },
            residue,
            stopped_early,
            started,
        );
        record_last_failed(&dispatch.rootdir_path, &previously_failed, &report);
        Ok(report)
    })
}

/// Fold this run's outcomes into the last-failed cache.
///
/// Unconditional: pytest writes `cache/lastfailed` on every session that has a cache
/// (`LFPlugin.pytest_sessionfinish`), not only when `--lf` was asked for — a cache written
/// only when read would never have anything in it.  `-p no:cacheprovider` is pytest's opt-out
/// and rustest has no plugin flags, so there is no way to suppress it yet; the file is inside
/// the already-gitignored `.rustest_cache/`.
fn record_last_failed(
    rootdir: &Path,
    previous: &std::collections::HashSet<String>,
    report: &RunReport,
) {
    let outcomes = report
        .tests
        .iter()
        .map(|test| (test.id.clone(), test.status.is_failure()));
    let errors = report
        .collection_errors
        .iter()
        .map(|entry| entry.path.clone());
    let merged = merge_last_failed(previous, outcomes, errors);
    // pytest's `if saved_lastfailed != self.lastfailed` — an unchanged cache is not rewritten,
    // so a green repeat run does not keep touching a file (and a read-only tree stays quiet).
    let unchanged = merged.len() == previous.len() && merged.keys().all(|id| previous.contains(id));
    if !unchanged {
        let _ = write_last_failed(rootdir, &merged);
    }
}

/// The selected manifest plus the per-worker dispatch lists derived from it.
struct Staged {
    selected: Vec<CollectedTest>,
    errors: Vec<CollectionErrorEntry>,
    deselected: usize,
    /// Modules that skipped themselves at import, straight through from
    /// [`crate::v2::collect::Assembled::module_skipped`]. Never selected against — a skip
    /// with no id cannot match `-k` or `-m`, and pytest does not try either.
    module_skipped: usize,
    /// Indexed by worker, then by **file**: `(report slot, test id)`.
    ///
    /// The file grouping used to be flattened away here and reconstructed nowhere, because
    /// dispatch was per test and only the *order* mattered. `WorkerRequest::ExecuteBatch`
    /// makes the grouping load-bearing: one batch is one file, because the worker detects
    /// module and class boundaries by comparing consecutive tests' files, so a batch
    /// spanning two files would rebuild module-scoped fixtures inside a single request.
    per_worker: Vec<Vec<Vec<(usize, String)>>>,
}

/// Assemble, select, and work out who runs what — the whole of the main thread's job at the
/// barrier.
fn stage(
    dispatch: &Dispatch,
    inbox: CollectInbox,
    keyword: Option<&str>,
    mark: Option<&str>,
    previously_failed: &std::collections::HashSet<String>,
    last_failed: LastFailedMode,
) -> Result<Staged, RunError> {
    let pool_size = inbox.len();
    let mut outcomes = Vec::new();
    let mut failure: Option<CollectError> = None;
    for slot in inbox {
        match slot {
            Some(Ok(items)) => outcomes.extend(items),
            Some(Err(err)) => failure = failure.or(Some(err)),
            None => {}
        }
    }
    if let Some(err) = failure {
        return Err(RunError::Collect(err));
    }

    let assembled = assemble(dispatch, outcomes)?;
    // Selection is compiled and applied first even when nothing will run, because pytest
    // does: `pytest_collection_modifyitems` is called from inside `perform_collect`, i.e.
    // *before* `pytest_runtestloop` can interrupt, so a malformed `-k` is a usage error (4)
    // that outranks the collection error (2).
    let keep = select_mask(&assembled.tests, keyword, mark)?;
    let mut deselected = keep.iter().filter(|keep| !**keep).count();

    // `--lf`/`--ff` run **after** `-k`/`-m`.  `LFPlugin.pytest_collection_modifyitems` is a
    // `wrapper=True, tryfirst=True` hookimpl whose body is *after* the `yield`, so it sees
    // the item list every other `pytest_collection_modifyitems` — the mark plugin's keyword
    // and mark deselection among them — has already filtered.  Applying it first would let
    // `--lf -k something-else` resurrect deselected tests.
    let surviving: Vec<usize> = (0..assembled.tests.len())
        .filter(|index| keep[*index])
        .collect();
    let order = last_failed_order(
        surviving
            .iter()
            .map(|index| assembled.tests[*index].id.clone()),
        previously_failed,
        last_failed,
    );
    // The tests `--lf` drops are **deselected**, not vanished: pytest calls
    // `config.hook.pytest_deselected(items=previously_passed)` on them, so they land in the
    // same bucket `-k` uses and the summary says `N deselected`.
    let ordered: Vec<usize> = match &order {
        Some(order) => order.iter().map(|slot| surviving[*slot]).collect(),
        None => surviving,
    };
    deselected += keep.iter().filter(|keep| **keep).count() - ordered.len();

    // `_pytest/main.py::pytest_runtestloop`: `if session.testsfailed and not
    // continue_on_collection_errors: raise session.Interrupted(...)`.  A single unimportable
    // file therefore means **nothing runs at all** — not "everything else runs and the run
    // exits 2".  Probed: a tree with one broken file and one passing file reports `1 error`
    // and exits 2, with the passing test never executed.  Running it anyway would put
    // outcomes in the report for a run pytest never performed, and would make every
    // execution-parity comparison disagree on counts.
    if !assembled.errors.is_empty() {
        return Ok(Staged {
            selected: Vec::new(),
            errors: assembled.errors,
            deselected,
            // Carried across the collection-error bail-out because pytest carries it:
            // probed, a tree with two module-skipped files and one unimportable file
            // prints `2 skipped, 1 error` — the skips survive the interrupt.
            module_skipped: assembled.module_skipped,
            per_worker: vec![Vec::new(); pool_size],
        });
    }

    // Which worker holds each target's warm import.  Read out of the dispatch plan rather
    // than recomputed from the stem hash, so the execute half cannot possibly disagree with
    // the collect half about who imported a file — the property `ExecuteTest` depends on,
    // and one whose violation surfaces as a worker exiting 2 (i.e. as protocol drift) rather
    // than as the orchestrator bug it would be.
    let mut owner = vec![0usize; dispatch.targets.len()];
    for (worker, files) in dispatch.assignments.iter().enumerate() {
        for (target, _) in files {
            owner[*target] = worker;
        }
    }

    // Group each worker's tests by file, groups in first-appearance order, manifest order
    // inside a group. See the module docs: the worker detects module and class boundaries
    // by comparing consecutive tests' files, so interleaving would churn module fixtures.
    let mut groups: Vec<HashMap<PathBuf, usize>> = vec![HashMap::new(); pool_size];
    let mut grouped: Vec<Vec<Vec<(usize, String)>>> = vec![Vec::new(); pool_size];
    let mut selected = Vec::new();

    for index in ordered {
        let test = assembled.tests[index].clone();
        let target = assembled.origin[index];
        let path = &dispatch.targets[target];
        let worker = owner[target];
        let slot = selected.len();
        let next = grouped[worker].len();
        let group = *groups[worker].entry(path.clone()).or_insert(next);
        if group == next {
            grouped[worker].push(Vec::new());
        }
        grouped[worker][group].push((slot, test.id.clone()));
        selected.push(test);
    }

    Ok(Staged {
        selected,
        errors: assembled.errors,
        deselected,
        module_skipped: assembled.module_skipped,
        per_worker: grouped,
    })
}

/// What the pool left behind that belongs to no single test.
///
/// The two fields are both `Vec<String>` and mean opposite things — one reddens the run, the
/// other is never graded — so they travel as a named pair rather than as two adjacent
/// positional arguments a transposition would silently swap.
#[derive(Default)]
struct WorkerResidue {
    /// Teardown failures with no test left to own them; each counts as a failure.
    teardown_errors: Vec<String>,
    /// Whatever the workers wrote to stderr. Carried, never graded.
    stderr: Vec<String>,
    /// The first `pytest.exit()` banner, if a test ended the session.
    session_exit: Option<String>,
}

/// What the selection pass decided: how many tests the run will execute, and how many it
/// dropped.  One concept, so one argument — and `kept` is the number a truncated run's report
/// can no longer derive from its own `tests` list.
#[derive(Debug, Clone, Copy, Default)]
struct Selection {
    kept: usize,
    deselected: usize,
    /// Modules that skipped themselves at import (`allow_module_level` /
    /// `importorskip`). Folded into `skipped` and **not** into `kept`, which is pytest's
    /// own split — probed on 8.4.2: a tree of two module-skipped files and no others
    /// prints `2 skipped` and exits **5** (`no tests ran`), so the skip counts in the
    /// tally and not in what was collected.
    module_skipped: usize,
}

fn finish(
    rootdir: String,
    tests: Vec<TestOutcome>,
    collection_errors: Vec<CollectionErrorEntry>,
    selection: Selection,
    residue: WorkerResidue,
    stopped_early: bool,
    started: Instant,
) -> RunReport {
    let summary = RunSummary::tally(&tests, selection, started.elapsed().as_secs_f64());
    // A teardown failure that belongs to no test is still a failure: pytest reports the
    // same shape (`1 passed, 1 error`) and exits 1.
    let failures = tests.iter().filter(|test| test.status.is_failure()).count()
        + residue.teardown_errors.len();
    let exit = exit_code(
        tests.len(),
        collection_errors.len(),
        failures,
        residue.session_exit.is_some(),
    );
    RunReport {
        version: REPORT_SCHEMA_VERSION,
        rootdir,
        exit_code: exit,
        summary,
        tests,
        collection_errors,
        teardown_errors: residue.teardown_errors,
        worker_stderr: residue.stderr,
        stopped_early,
        session_exit: residue.session_exit,
    }
}

/// Collect, wait at the barrier, execute, shut down.
fn worker_life(
    index: usize,
    mut worker: Worker,
    files: Vec<(usize, PathBuf)>,
    assert_keys: &[Option<String>],
    collect_tx: Sender<CollectMessage>,
    exec_rx: Receiver<Assignment>,
    signals: StopSignals<'_>,
) -> Result<WorkerRun, CollectError> {
    let mut outcomes = Vec::with_capacity(files.len());
    for (target, path) in &files {
        // Indexed by target, not by this worker's position in its own file list — see
        // `collect::run_worker`.
        let key = assert_keys.get(*target).and_then(Option::as_deref);
        match worker.collect_one(path, key) {
            Ok(outcome) => outcomes.push((*target, outcome)),
            Err(err) => {
                // The error travels to the main thread, which owns the "first failure in
                // worker order" rule; this thread's own result is then empty rather than a
                // second, racing copy of the same failure.
                let _ = collect_tx.send((index, Err(err)));
                return Ok(WorkerRun::default());
            }
        }
    }
    if collect_tx.send((index, Ok(outcomes))).is_err() {
        return Ok(WorkerRun::default());
    }

    let Ok(assignment) = exec_rx.recv() else {
        // The run was abandoned at the barrier (another worker failed, or selection was
        // rejected). Nothing to execute and nothing to say; `Worker::drop` reaps.
        return Ok(WorkerRun::default());
    };

    let mut results: Vec<(usize, TestOutcome)> = Vec::new();
    // One request per file, not per test.  Measured at Phase 2 Task 3: the per-test round
    // trip cost ~160 µs of orchestrator time against ~20 µs of in-worker execution, because
    // each test meant a pipe write and a blocking line read.  A file's tests share both.
    for group in assignment {
        // Checked *before* dispatch, not after: the point of `-x` is that nothing starts
        // after a failure is known.  `shutdown_run` below still happens, so the fixtures this
        // worker opened are unwound exactly as they would be on a complete run — an aborted
        // run must not leak a container or a temp tree.
        //
        // `session_over` is read whether or not `-x` was given: a `pytest.exit()` ends the
        // session for the whole pool, and that is not an opt-in.
        if signals.should_stop() {
            break;
        }
        if group.is_empty() {
            continue;
        }
        let ids: Vec<String> = group.iter().map(|(_, id)| id.clone()).collect();
        // Filled as the results stream in, so a batch cut short by `pytest.exit()` still
        // yields everything the worker answered before it went — pytest keeps those too.
        let mut outcomes: Vec<TestOutcome> = Vec::with_capacity(ids.len());
        // `-x` travels *into* the batch as well as being checked between batches. Without
        // it, `-x` would silently weaken from "nothing runs after the failing test" to
        // "nothing runs after the failing file" — see `WorkerRequest::ExecuteBatch`.
        // The **remaining** budget, not the configured N: a worker cannot know what the
        // rest of the pool has already failed, so the orchestrator subtracts and sends what
        // is left. `None` is "no limit"; a budget that has already been spent sends `Some(0)`
        // and the batch is never dispatched, because `should_stop` fired first.
        let budget = signals.remaining_budget();
        let batch = worker.execute_batch(&ids, signals.fail_fast, budget, &mut outcomes);
        // The results are folded in **before** the batch's own outcome is inspected, so the
        // `SessionExit` arm below returns with them already recorded.
        for ((slot, _), outcome) in group.iter().zip(outcomes) {
            if outcome.status.is_failure() {
                if signals.fail_fast {
                    signals.stop.store(true, Ordering::SeqCst);
                }
                // `--maxfail` stops dispatch through the *same* flag `-x` uses, so one check
                // in `should_stop` covers both. The counter is what makes N > 1 possible.
                if signals.note_failure() {
                    signals.stop.store(true, Ordering::SeqCst);
                }
            }
            results.push((*slot, outcome));
        }
        let stopped_in_batch = match batch {
            Ok(stopped) => stopped,
            // `pytest.exit()`.  The worker is gone and the test that called it gets no
            // result — pytest does not report it either — but everything this worker already
            // answered is kept (above, before this match), and `shutdown_run` is skipped
            // because there is no process left to shut down.
            Err(CollectError::SessionExit { stderr, .. }) => {
                signals.stop.store(true, Ordering::SeqCst);
                signals.session_over.store(true, Ordering::SeqCst);
                return Ok(WorkerRun {
                    results,
                    teardown: None,
                    stderr: String::new(),
                    session_exit: Some(stderr),
                });
            }
            Err(err) => return Err(err),
        };
        if stopped_in_batch {
            // The worker already stopped; the shared flag is what stops every *other*
            // worker, and what tells the main thread the missing slots are expected rather
            // than lost.
            signals.stop.store(true, Ordering::SeqCst);
            break;
        }
    }

    let teardown = worker.shutdown_run()?;
    let stderr = worker.take_stderr();
    Ok(WorkerRun {
        results,
        teardown,
        stderr,
        session_exit: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::v2::protocol::PROTOCOL_VERSION;
    use crate::v2::test_python;
    use std::fs;
    use tempfile::TempDir;

    fn outcome(id: &str, status: TestStatus) -> TestOutcome {
        TestOutcome {
            id: id.to_string(),
            status,
            duration_s: 0.0,
            message: None,
            stdout: None,
            stderr: None,
        }
    }

    fn report_of(
        tests: Vec<TestOutcome>,
        errors: Vec<CollectionErrorEntry>,
        teardown: Vec<String>,
        stderr: Vec<String>,
    ) -> RunReport {
        let selection = Selection {
            kept: tests.len(),
            deselected: 0,
            module_skipped: 0,
        };
        finish(
            "/repo".to_string(),
            tests,
            errors,
            selection,
            WorkerResidue {
                teardown_errors: teardown,
                stderr,
                session_exit: None,
            },
            false,
            Instant::now(),
        )
    }

    // --- the exit-code mapper --------------------------------------------

    #[test]
    fn a_clean_run_exits_zero() {
        assert_eq!(exit_code(3, 0, 0, false), 0);
    }

    #[test]
    fn any_failure_exits_one() {
        assert_eq!(exit_code(3, 0, 1, false), 1);
        assert_eq!(exit_code(3, 0, 3, false), 1);
    }

    /// Probed twice, because the interesting half is the second: a collection error wins
    /// even when tests also *failed*, since pytest never runs them at all.
    #[test]
    fn a_collection_error_exits_two_and_outranks_failures() {
        assert_eq!(exit_code(1, 1, 0, false), 2);
        assert_eq!(exit_code(1, 1, 5, false), 2);
    }

    /// Zero collected is 5 whether the tree was empty or selection emptied it — probed as
    /// `no tests ran` (exit 5) and `1 deselected` (exit 5).
    #[test]
    fn zero_collected_exits_five() {
        assert_eq!(exit_code(0, 0, 0, false), 5);
    }

    /// The precedence a naive "check `collected == 0` first" would get wrong: nothing
    /// collected *and* a collection error is 2, not 5. Probed — a tree whose only file
    /// fails to import collects nothing and exits 2.
    #[test]
    fn a_collection_error_outranks_zero_collected() {
        assert_eq!(exit_code(0, 1, 0, false), 2);
    }

    /// ...and the mirror image: failures outrank zero-collected. Unreachable in practice
    /// (a failure implies a test), pinned so the ordering cannot be rewritten as a lookup
    /// that happens to agree only on reachable inputs.
    #[test]
    fn failures_outrank_zero_collected() {
        assert_eq!(exit_code(0, 0, 1, false), 1);
    }

    // --- what counts as a failure ----------------------------------------

    /// Every row is a probed pytest run; see [`TestStatus::is_failure`]'s table.
    #[test]
    fn only_failed_and_error_are_failures() {
        assert!(TestStatus::Failed.is_failure());
        assert!(TestStatus::Error.is_failure());
        assert!(!TestStatus::Passed.is_failure());
        assert!(!TestStatus::Skipped.is_failure());
        assert!(!TestStatus::XFailed.is_failure());
        // The headline row: `1 xpassed` exits 0.  A strict xpass never gets here — pytest
        // rewrites it to `failed` before reporting.
        assert!(!TestStatus::XPassed.is_failure());
    }

    /// An `error` makes the run exit 1 even though the test's own body passed — probed: a
    /// fixture that raises at teardown gives `1 passed, 1 error` and exit 1.
    #[test]
    fn a_teardown_error_alone_exits_one() {
        let report = report_of(
            vec![outcome("t::a", TestStatus::Error)],
            Vec::new(),
            Vec::new(),
            Vec::new(),
        );
        assert_eq!(report.exit_code, 1);
        assert_eq!(report.summary.error, 1);
    }

    /// A run of nothing but non-failures is green, including the `xpassed` row that a
    /// "not passed means failed" rule would turn red.
    #[test]
    fn skips_xfails_and_xpasses_leave_a_run_green() {
        let report = report_of(
            vec![
                outcome("t::a", TestStatus::Passed),
                outcome("t::b", TestStatus::Skipped),
                outcome("t::c", TestStatus::XFailed),
                outcome("t::d", TestStatus::XPassed),
            ],
            Vec::new(),
            Vec::new(),
            Vec::new(),
        );
        assert_eq!(report.exit_code, 0);
    }

    /// The unattributable one: the worker answered every test, said `bye`, then exited 3
    /// because a module-scoped teardown raised. No test is `failed`, and the run must still
    /// be red — the false green this exists to prevent.
    #[test]
    fn a_shutdown_teardown_failure_fails_the_run_without_owning_a_test() {
        let report = report_of(
            vec![outcome("t::a", TestStatus::Passed)],
            Vec::new(),
            vec!["worker 0: a fixture teardown failed".to_string()],
            Vec::new(),
        );
        assert_eq!(report.exit_code, 1);
        assert_eq!(report.summary.passed, 1);
        assert_eq!(report.summary.failed, 0);
        assert_eq!(report.teardown_errors.len(), 1);
    }

    /// ...and it is exit **1**, not 3: pytest's exit 3 is an internal error, while a
    /// teardown that raises is a user's broken fixture and pytest exits 1 for it (probed:
    /// `tearDownClass` raising -> `1 passed, 1 error`, exit 1).
    #[test]
    fn a_shutdown_teardown_failure_is_not_an_internal_error() {
        let report = report_of(
            vec![outcome("t::a", TestStatus::Passed)],
            Vec::new(),
            vec!["boom".to_string()],
            Vec::new(),
        );
        assert_ne!(report.exit_code, SHUTDOWN_TEARDOWN_EXIT);
    }

    /// Worker stderr is carried, not graded: a run whose workers printed teardown output is
    /// still exit 0.
    #[test]
    fn worker_stderr_never_fails_a_run() {
        let report = report_of(
            vec![outcome("t::a", TestStatus::Passed)],
            Vec::new(),
            Vec::new(),
            vec!["TEARDOWN-MODULE\n".to_string()],
        );
        assert_eq!(report.exit_code, 0);
        assert_eq!(report.worker_stderr.len(), 1);
    }

    /// A collection error outranks a green run of everything that *did* import — pytest
    /// never runs those tests at all, and reporting 0 here would hide a broken file.
    #[test]
    fn a_collection_error_makes_the_whole_report_exit_two() {
        let report = report_of(
            vec![outcome("t::a", TestStatus::Passed)],
            vec![CollectionErrorEntry {
                path: "t_broken.py".to_string(),
                message: "ImportError".to_string(),
            }],
            Vec::new(),
            Vec::new(),
        );
        assert_eq!(report.exit_code, 2);
    }

    // --- the summary ------------------------------------------------------

    #[test]
    fn the_summary_has_a_bucket_for_every_status() {
        let tests = vec![
            outcome("t::a", TestStatus::Passed),
            outcome("t::b", TestStatus::Failed),
            outcome("t::c", TestStatus::Skipped),
            outcome("t::d", TestStatus::XFailed),
            outcome("t::e", TestStatus::XPassed),
            outcome("t::f", TestStatus::Error),
        ];
        let summary = RunSummary::tally(
            &tests,
            Selection {
                kept: tests.len(),
                deselected: 4,
                module_skipped: 0,
            },
            1.5,
        );
        assert_eq!(summary.total, 6);
        assert_eq!(summary.passed, 1);
        assert_eq!(summary.failed, 1);
        assert_eq!(summary.skipped, 1);
        assert_eq!(summary.xfailed, 1);
        assert_eq!(summary.xpassed, 1);
        assert_eq!(summary.error, 1);
        assert_eq!(summary.deselected, 4);
        assert_eq!(summary.duration, 1.5);
    }

    /// A module-level skip lands in `skipped` and **nowhere else** -- in particular not in
    /// `total`, which is what pytest collected.
    ///
    /// Both halves are pytest's, probed on 8.4.2: a tree of two self-skipping modules and
    /// no other file prints `2 skipped` (so the tally counts them) and exits **5**, `no
    /// tests ran` (so `total` does not). Getting only one half right would be worse than
    /// getting neither: counting them in `total` turns that exit 5 into a 0.
    #[test]
    fn a_module_level_skip_counts_as_skipped_but_not_as_collected() {
        let summary = RunSummary::tally(
            &[outcome("t::a", TestStatus::Passed)],
            Selection {
                kept: 1,
                deselected: 0,
                module_skipped: 2,
            },
            0.0,
        );
        assert_eq!(summary.total, 1);
        assert_eq!(summary.passed, 1);
        assert_eq!(summary.skipped, 2);
    }

    /// `xfailed`/`xpassed` are **not** folded into `skipped`/`passed`. That fold is exactly
    /// what schema v1 did by having only three statuses, and it makes an `X` invisible.
    #[test]
    fn xfailed_and_xpassed_do_not_leak_into_the_v1_buckets() {
        let tests = vec![
            outcome("t::a", TestStatus::XFailed),
            outcome("t::b", TestStatus::XPassed),
        ];
        let summary = RunSummary::tally(
            &tests,
            Selection {
                kept: tests.len(),
                deselected: 0,
                module_skipped: 0,
            },
            0.0,
        );
        assert_eq!(summary.skipped, 0);
        assert_eq!(summary.passed, 0);
        assert_eq!((summary.xfailed, summary.xpassed), (1, 1));
    }

    // --- the status wire mapping ------------------------------------------

    #[test]
    fn every_documented_status_parses_and_round_trips() {
        for name in ["passed", "failed", "skipped", "xfailed", "xpassed", "error"] {
            let status = TestStatus::parse(name).expect("documented status parses");
            assert_eq!(status.as_str(), name);
            assert_eq!(
                serde_json::to_string(&status).expect("serializes"),
                format!("\"{name}\"")
            );
        }
    }

    /// The orchestrator's half of the "`status` is an unvalidated `String` on the wire"
    /// contract. A value outside the six is protocol drift, not a new category to be
    /// silently bucketed.
    #[test]
    fn an_undocumented_status_is_rejected() {
        assert_eq!(TestStatus::parse("xpass"), None);
        assert_eq!(TestStatus::parse("PASSED"), None);
        assert_eq!(TestStatus::parse(""), None);
        assert_eq!(TestStatus::parse("deselected"), None);
    }

    // --- the report wire form ---------------------------------------------

    /// The `--report-json` document under `--v2`, pinned as bytes: the conformance harness
    /// reads `summary`, `tests[*].id`, `tests[*].status` and `collection_errors` out of it.
    #[test]
    fn the_report_json_matches_its_golden_contract() {
        let report = RunReport {
            version: REPORT_SCHEMA_VERSION,
            rootdir: "/repo".to_string(),
            exit_code: 1,
            summary: RunSummary {
                total: 2,
                passed: 1,
                failed: 1,
                skipped: 0,
                xfailed: 0,
                xpassed: 0,
                error: 0,
                deselected: 3,
                duration: 0.25,
            },
            tests: vec![
                outcome("tests/test_a.py::test_ok", TestStatus::Passed),
                TestOutcome {
                    id: "tests/test_a.py::test_bad".to_string(),
                    status: TestStatus::Failed,
                    duration_s: 0.5,
                    message: Some("assert 1 == 2".to_string()),
                    stdout: Some("computing\n".to_string()),
                    stderr: None,
                },
            ],
            collection_errors: Vec::new(),
            teardown_errors: Vec::new(),
            worker_stderr: Vec::new(),
            stopped_early: false,
            session_exit: None,
        };

        assert_eq!(
            serde_json::to_string(&report).expect("report serializes"),
            r#"{"version":2,"rootdir":"/repo","exit_code":1,"summary":{"total":2,"passed":1,"failed":1,"skipped":0,"xfailed":0,"xpassed":0,"error":0,"deselected":3,"duration":0.25},"tests":[{"id":"tests/test_a.py::test_ok","status":"passed","duration":0.0},{"id":"tests/test_a.py::test_bad","status":"failed","duration":0.5,"message":"assert 1 == 2","stdout":"computing\n"}],"collection_errors":[]}"#
        );
    }

    // =====================================================================
    // End-to-end, against the real worker
    // =====================================================================

    fn write_file(path: &Path, content: &str) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, content).unwrap();
    }

    fn tree(files: &[(&str, &str)]) -> TempDir {
        let tmp = TempDir::new().unwrap();
        write_file(&tmp.path().join("pytest.ini"), "[pytest]\n");
        for (rel, content) in files {
            write_file(&tmp.path().join(rel), content);
        }
        tmp
    }

    fn real_worker() -> WorkerLauncher {
        WorkerLauncher::module(&test_python())
    }

    /// [`run_with_launcher`] with the CLI's own defaults, which is the shape every test
    /// written before `RunOptions` existed assumes.  The option-carrying behaviours (`-x`,
    /// `--lf`/`--ff`, `-s`) have their own tests and pass their own options.
    fn run_default(
        dir: &Path,
        args: &[PathBuf],
        launcher: &WorkerLauncher,
        workers: usize,
        keyword: Option<&str>,
        mark: Option<&str>,
    ) -> Result<RunReport, RunError> {
        run_with_launcher(
            dir,
            args,
            launcher,
            workers,
            keyword,
            mark,
            RunOptions::defaults(),
        )
    }

    fn run_tree(tmp: &TempDir, workers: usize) -> RunReport {
        run_default(tmp.path(), &[], &real_worker(), workers, None, None)
            .expect("the run completes")
    }

    fn statuses(report: &RunReport) -> Vec<(String, &'static str)> {
        report
            .tests
            .iter()
            .map(|test| (test.id.clone(), test.status.as_str()))
            .collect()
    }

    /// `@pytest.mark.xfail` and friends must be **called**: rustest's v1 compat shim turns an
    /// uncalled `@pytest.mark.skip` into its inner decorator closure and the test body is
    /// lost (recorded as defect #137 in the 1b.2 Task 3 report).  Every corpus below calls
    /// its marks, so these tests exercise execution rather than that defect.
    const MIXED: &str = "\
import pytest


def test_pass():
    assert True


def test_fail():
    assert 1 == 2


@pytest.mark.skip(reason='nope')
def test_skip():
    pass


@pytest.mark.xfail(reason='known')
def test_xfail():
    assert False


@pytest.mark.xfail(reason='surprise')
def test_xpass():
    assert True


@pytest.fixture
def boom():
    raise ValueError('setup boom')


def test_error(boom):
    assert True
";

    /// The whole point of the run half, in one assertion: every one of the six statuses
    /// survives the round trip, in manifest order, and the run exits 1 because two of them
    /// are failures.
    #[test]
    fn a_mixed_tree_reports_all_six_statuses_in_manifest_order() {
        let tmp = tree(&[("test_mixed.py", MIXED)]);
        let report = run_tree(&tmp, 1);

        assert_eq!(
            statuses(&report),
            vec![
                ("test_mixed.py::test_pass".to_string(), "passed"),
                ("test_mixed.py::test_fail".to_string(), "failed"),
                ("test_mixed.py::test_skip".to_string(), "skipped"),
                ("test_mixed.py::test_xfail".to_string(), "xfailed"),
                ("test_mixed.py::test_xpass".to_string(), "xpassed"),
                ("test_mixed.py::test_error".to_string(), "error"),
            ]
        );
        assert_eq!(report.summary.total, 6);
        assert_eq!(report.summary.passed, 1);
        assert_eq!(report.summary.xfailed, 1);
        assert_eq!(report.summary.xpassed, 1);
        assert_eq!(report.exit_code, 1);
    }

    /// Report order is **manifest** order, not completion order — the same index-reassembly
    /// property collection has, now across a barrier as well as a pool.  Asserted for one
    /// and four workers over a tree whose files land on different workers.
    #[test]
    fn report_order_is_manifest_order_however_many_workers_run_it() {
        let tmp = tree(&[
            (
                "test_a.py",
                "def test_a1():\n    pass\ndef test_a2():\n    pass\n",
            ),
            ("sub/test_b.py", "def test_b1():\n    pass\n"),
            ("test_c.py", "def test_c1():\n    pass\n"),
        ]);

        let expected = statuses(&run_tree(&tmp, 1));
        assert_eq!(
            expected
                .iter()
                .map(|(id, _)| id.as_str())
                .collect::<Vec<_>>(),
            vec![
                "sub/test_b.py::test_b1",
                "test_a.py::test_a1",
                "test_a.py::test_a2",
                "test_c.py::test_c1",
            ]
        );
        for workers in 2..=4 {
            assert_eq!(
                statuses(&run_tree(&tmp, workers)),
                expected,
                "{workers} workers"
            );
        }
    }

    /// `_pytest/main.py::pytest_runtestloop` raises `Interrupted` when collection failed, so
    /// **nothing runs** — not "everything that imported runs and the exit code is 2".
    /// Probed: a tree with one broken and one passing file reports `1 error`, exits 2, and
    /// never executes the passing test.
    #[test]
    fn a_collection_error_stops_the_run_before_any_test() {
        let tmp = tree(&[
            ("test_broken.py", "import nope_does_not_exist\n"),
            ("test_ok.py", "def test_ok():\n    pass\n"),
        ]);
        let report = run_tree(&tmp, 1);

        assert!(report.tests.is_empty(), "{:?}", report.tests);
        assert_eq!(report.collection_errors.len(), 1);
        assert_eq!(report.exit_code, 2);
    }

    /// Selection happens between collection and dispatch: deselected tests are never
    /// dispatched at all, and the count reaches the report.
    #[test]
    fn selection_filters_before_dispatch_and_is_counted() {
        let tmp = tree(&[(
            "test_sel.py",
            "import pytest\n\n\n@pytest.mark.slow()\ndef test_one():\n    pass\n\n\ndef test_two():\n    pass\n",
        )]);

        let by_keyword =
            run_default(tmp.path(), &[], &real_worker(), 1, Some("one"), None).unwrap();
        assert_eq!(
            statuses(&by_keyword),
            vec![("test_sel.py::test_one".to_string(), "passed")]
        );
        assert_eq!(by_keyword.summary.deselected, 1);

        let by_mark = run_default(tmp.path(), &[], &real_worker(), 1, None, Some("slow")).unwrap();
        assert_eq!(
            statuses(&by_mark),
            vec![("test_sel.py::test_one".to_string(), "passed")]
        );

        let nothing =
            run_default(tmp.path(), &[], &real_worker(), 1, Some("nomatch"), None).unwrap();
        assert!(nothing.tests.is_empty());
        assert_eq!(nothing.summary.deselected, 2);
        assert_eq!(nothing.exit_code, 5);
    }

    /// A malformed expression is a usage error even with a tree full of tests, and the
    /// message is pytest's verbatim.
    #[test]
    fn a_malformed_expression_aborts_the_run_as_a_usage_error() {
        let tmp = tree(&[("test_a.py", "def test_a():\n    pass\n")]);

        let err = run_default(tmp.path(), &[], &real_worker(), 1, Some("and and"), None)
            .expect_err("invalid expression");
        assert!(matches!(err, RunError::Selection(_)), "{err}");
        assert_eq!(
            err.to_string(),
            "Wrong expression passed to '-k': and and: at column 1: expected not OR left parenthesis OR identifier; got and"
        );
    }

    /// ...and on an **empty** tree too, because pytest compiles the expression before it
    /// loops over items.  No worker is even spawned here, so this also pins that the
    /// no-targets short circuit does not swallow the error.
    #[test]
    fn a_malformed_expression_is_a_usage_error_on_an_empty_tree() {
        let tmp = tree(&[]);
        let err = run_default(tmp.path(), &[], &real_worker(), 1, None, Some("m("))
            .expect_err("invalid expression");
        assert!(err
            .to_string()
            .starts_with("Wrong expression passed to '-m'"));
    }

    #[test]
    fn an_empty_tree_runs_nothing_and_exits_five() {
        let tmp = tree(&[]);
        let report = run_tree(&tmp, 4);
        assert!(report.tests.is_empty());
        assert_eq!(report.exit_code, 5);
    }

    /// Duplicate file arguments run the tests twice — the walk's parity rule, carried
    /// through execution.  Both copies are reported, and the report keeps them adjacent
    /// because the manifest does.
    #[test]
    fn a_file_argument_given_twice_runs_its_tests_twice() {
        let tmp = tree(&[("test_dup.py", "def test_one():\n    pass\n")]);
        let file = tmp.path().join("test_dup.py");

        let report = run_default(
            tmp.path(),
            &[file.clone(), file],
            &real_worker(),
            1,
            None,
            None,
        )
        .expect("the run completes");

        assert_eq!(
            statuses(&report),
            vec![
                ("test_dup.py::test_one".to_string(), "passed"),
                ("test_dup.py::test_one".to_string(), "passed"),
            ]
        );
        assert_eq!(report.exit_code, 0);
    }

    /// A module-scoped fixture whose teardown raises has no test left to own it once the
    /// last test is answered: the worker says `bye` and exits 3.  The run must be **red**,
    /// at exit **1** (a broken teardown is pytest's `1 passed, 1 error`), and must not be
    /// mistaken for the internal error that exit 3 means at process level.
    #[test]
    fn a_shutdown_teardown_failure_reaches_the_report_and_reddens_the_run() {
        let tmp = tree(&[(
            "test_td.py",
            "import pytest\n\n\n@pytest.fixture(scope='module')\ndef broken():\n    yield 1\n    raise ValueError('teardown boom')\n\n\ndef test_one(broken):\n    assert broken == 1\n",
        )]);

        let report = run_tree(&tmp, 1);

        assert_eq!(
            statuses(&report),
            vec![("test_td.py::test_one".to_string(), "passed")]
        );
        assert_eq!(report.teardown_errors.len(), 1, "{:?}", report);
        assert!(
            report.teardown_errors[0].contains("teardown"),
            "{:?}",
            report
        );
        assert_eq!(report.exit_code, 1);
    }

    /// Boundary teardown output goes to worker stderr by design (Task 3's documented
    /// divergence).  It is carried into the report and it does **not** fail the run.
    #[test]
    fn boundary_teardown_output_is_carried_without_failing_the_run() {
        let tmp = tree(&[(
            "test_out.py",
            "import pytest\n\n\n@pytest.fixture(scope='module')\ndef noisy():\n    yield 1\n    print('TEARDOWN-MODULE')\n\n\ndef test_one(noisy):\n    assert noisy == 1\n",
        )]);

        let report = run_tree(&tmp, 1);
        assert_eq!(report.exit_code, 0);
        assert!(
            report
                .worker_stderr
                .iter()
                .any(|s| s.contains("TEARDOWN-MODULE")),
            "{:?}",
            report.worker_stderr
        );
    }

    // =====================================================================
    // -x / --exitfirst
    // =====================================================================

    /// Four tests, the second fails.  Probed against pytest 8.4.2 on this exact file:
    /// `pytest -x -q` prints `1 failed, 1 passed`, exits **1**, and never mentions `test_c`
    /// or `test_d`.  All three properties are asserted, because each is a different way to
    /// get `-x` wrong: run everything and report it (no stop), stop but report the tail as
    /// skipped (invented outcomes), or stop and exit 2 (wrong code).
    const FOUR_WITH_A_FAILURE: &str = "\
def test_a():
    pass


def test_b():
    assert 0


def test_c():
    assert 0


def test_d():
    pass
";

    fn run_options(tmp: &TempDir, workers: usize, options: RunOptions) -> RunReport {
        run_with_launcher(
            tmp.path(),
            &[],
            &real_worker(),
            workers,
            None,
            None,
            options,
        )
        .expect("the run completes")
    }

    #[test]
    fn fail_fast_stops_after_the_first_failure_and_reports_only_what_ran() {
        let tmp = tree(&[("test_x.py", FOUR_WITH_A_FAILURE)]);
        let report = run_options(
            &tmp,
            1,
            RunOptions {
                fail_fast: true,
                ..RunOptions::defaults()
            },
        );

        assert_eq!(
            statuses(&report),
            vec![
                ("test_x.py::test_a".to_string(), "passed"),
                ("test_x.py::test_b".to_string(), "failed"),
            ]
        );
        assert_eq!((report.summary.passed, report.summary.failed), (1, 1));
        assert_eq!(
            report.tests.len(),
            2,
            "the tail must not be reported as an outcome"
        );
        // `total` is what the run **selected**, not what it managed to run, so a truncated
        // run says 4 here and lists 2. That is the distinction `-v`'s percent column needs
        // (pytest never reaches 100% under `-x`), and it is the only place the two differ.
        assert_eq!(report.summary.total, 4);
        assert_eq!(report.exit_code, 1, "-x is TESTS_FAILED, not INTERRUPTED");
        assert!(report.stopped_early);
    }

    /// The same tree without `-x` — the control, so the test above cannot pass because the
    /// tree happens to be short.
    #[test]
    fn without_fail_fast_the_whole_file_runs() {
        let tmp = tree(&[("test_x.py", FOUR_WITH_A_FAILURE)]);
        let report = run_tree(&tmp, 1);
        assert_eq!(report.summary.total, 4);
        assert_eq!((report.summary.passed, report.summary.failed), (2, 2));
        assert!(!report.stopped_early);
    }

    /// `-x` on a green tree changes nothing at all: every test runs, and `stopped_early`
    /// stays false so a reader cannot mistake a clean run for a truncated one.
    #[test]
    fn fail_fast_on_a_green_tree_is_a_normal_run() {
        let tmp = tree(&[(
            "test_g.py",
            "def test_a():\n    pass\ndef test_b():\n    pass\n",
        )]);
        let report = run_options(
            &tmp,
            1,
            RunOptions {
                fail_fast: true,
                ..RunOptions::defaults()
            },
        );
        assert_eq!(report.summary.total, 2);
        assert_eq!(report.exit_code, 0);
        assert!(!report.stopped_early);
    }

    /// An `error` (a broken fixture) stops the run too: `TestStatus::is_failure` is what
    /// `-x` consults, and pytest's `maxfail` counter is driven by `report.failed`, which a
    /// setup error sets just as an assertion does.
    #[test]
    fn a_setup_error_trips_fail_fast() {
        let tmp = tree(&[(
            "test_e.py",
            "import pytest\n\n\n@pytest.fixture\ndef boom():\n    raise ValueError('x')\n\n\ndef test_a(boom):\n    pass\n\n\ndef test_b():\n    pass\n",
        )]);
        let report = run_options(
            &tmp,
            1,
            RunOptions {
                fail_fast: true,
                ..RunOptions::defaults()
            },
        );
        assert_eq!(
            statuses(&report),
            vec![("test_e.py::test_a".to_string(), "error")]
        );
        assert_eq!(report.exit_code, 1);
    }

    // =====================================================================
    // pytest.exit()
    // =====================================================================

    /// `pytest.exit()` mid-run: the session stops, the results already produced are kept,
    /// and the exit code is pytest's `INTERRUPTED`.
    ///
    /// All three are separate ways to get this wrong — report nothing (a dead worker),
    /// report the exiting test (a fabricated outcome), or exit 3 (an orchestration failure
    /// where the user asked to stop). Probed against pytest 8.4.2 on this exact shape:
    /// `1 passed`, exit 2, `test_never` never executed.
    #[test]
    fn a_pytest_exit_stops_the_session_and_keeps_the_results_so_far() {
        let tmp = tree(&[(
            "test_bail.py",
            "import pytest


def test_first():
    assert True


def test_bails():
    pytest.exit('stopping here')


def test_never():
    raise AssertionError('must not run')
",
        )]);

        let report = run_tree(&tmp, 1);

        assert_eq!(
            statuses(&report),
            vec![("test_bail.py::test_first".to_string(), "passed")]
        );
        assert_eq!(report.exit_code, 2, "pytest exits 2 for `Exit`");
        assert!(report.session_exit.is_some(), "{report:?}");
        assert!(
            report
                .session_exit
                .as_deref()
                .unwrap_or_default()
                .contains("stopping here"),
            "the user's reason must survive: {:?}",
            report.session_exit
        );
    }

    /// ...and it outranks a failure already reported. `wrap_session` catches `Exit` in the
    /// same arm as `KeyboardInterrupt` and sets `INTERRUPTED` regardless of
    /// `session.testsfailed` — the `except Failed` arm never runs. Probed: `1 failed`, exit 2.
    #[test]
    fn a_pytest_exit_outranks_an_earlier_failure() {
        let tmp = tree(&[(
            "test_bail.py",
            "import pytest


def test_fails():
    assert 0


def test_bails():
    pytest.exit('stopping here')
",
        )]);

        let report = run_tree(&tmp, 1);

        assert_eq!(report.summary.failed, 1);
        assert_eq!(report.exit_code, 2, "not 1: the Exit is what escaped");
    }

    /// The exit-code mapper's own precedence, pinned independently of the pool so a future
    /// reordering of the branches is a unit-test failure rather than an end-to-end one.
    #[test]
    fn a_session_exit_outranks_every_other_exit_code() {
        assert_eq!(exit_code(3, 0, 0, true), 2);
        assert_eq!(exit_code(3, 0, 9, true), 2, "failures do not win");
        assert_eq!(exit_code(0, 0, 0, true), 2, "nor does zero-collected");
        assert_eq!(
            exit_code(1, 1, 0, true),
            2,
            "and a collection error agrees anyway"
        );
    }

    // =====================================================================
    // --lf / --ff and the cache
    // =====================================================================

    /// Every run writes the cache, `--lf` or not — pytest's `LFPlugin.pytest_sessionfinish`
    /// is unconditional, and a cache written only when read would always be empty.
    #[test]
    fn an_ordinary_run_writes_the_last_failed_cache() {
        let tmp = tree(&[("test_x.py", FOUR_WITH_A_FAILURE)]);
        let _ = run_tree(&tmp, 1);

        assert_eq!(
            crate::v2::cache::read_last_failed(tmp.path()),
            [
                "test_x.py::test_b".to_string(),
                "test_x.py::test_c".to_string()
            ]
            .into_iter()
            .collect()
        );
    }

    /// `--lf` runs only what failed, and the rest are **deselected** (pytest calls
    /// `pytest_deselected` on them), so the summary accounts for them rather than losing
    /// them.
    #[test]
    fn last_failed_only_runs_the_previously_failed() {
        let tmp = tree(&[("test_x.py", FOUR_WITH_A_FAILURE)]);
        let _ = run_tree(&tmp, 1);

        let report = run_options(
            &tmp,
            1,
            RunOptions {
                last_failed: LastFailedMode::Only,
                ..RunOptions::defaults()
            },
        );
        assert_eq!(
            statuses(&report),
            vec![
                ("test_x.py::test_b".to_string(), "failed"),
                ("test_x.py::test_c".to_string(), "failed"),
            ]
        );
        assert_eq!(report.summary.deselected, 2);
        assert_eq!(report.exit_code, 1);
    }

    /// `--ff` keeps everything and moves the failures to the front — and the report is in
    /// *that* order, because pytest reorders `session.items` themselves.
    #[test]
    fn failed_first_reorders_without_dropping_anything() {
        let tmp = tree(&[("test_x.py", FOUR_WITH_A_FAILURE)]);
        let _ = run_tree(&tmp, 1);

        let report = run_options(
            &tmp,
            1,
            RunOptions {
                last_failed: LastFailedMode::First,
                ..RunOptions::defaults()
            },
        );
        assert_eq!(
            report
                .tests
                .iter()
                .map(|test| test.id.as_str())
                .collect::<Vec<_>>(),
            vec![
                "test_x.py::test_b",
                "test_x.py::test_c",
                "test_x.py::test_a",
                "test_x.py::test_d",
            ]
        );
        assert_eq!(report.summary.deselected, 0);
    }

    /// The cache converges: fix one of the two failures and only the other survives.
    #[test]
    fn a_fixed_test_leaves_the_cache() {
        let tmp = tree(&[("test_x.py", FOUR_WITH_A_FAILURE)]);
        let _ = run_tree(&tmp, 1);
        write_file(
            &tmp.path().join("test_x.py"),
            &FOUR_WITH_A_FAILURE.replace("def test_b():\n    assert 0", "def test_b():\n    pass"),
        );
        let _ = run_tree(&tmp, 1);

        assert_eq!(
            crate::v2::cache::read_last_failed(tmp.path()),
            ["test_x.py::test_c".to_string()].into_iter().collect()
        );
    }

    /// `--lf` runs **after** `-k`: a keyword that excludes every recorded failure leaves the
    /// `-k` selection intact (pytest's "known failures not in selected tests" branch) rather
    /// than resurrecting the deselected tests.
    #[test]
    fn last_failed_applies_after_keyword_selection() {
        let tmp = tree(&[("test_x.py", FOUR_WITH_A_FAILURE)]);
        let _ = run_tree(&tmp, 1);

        let report = run_with_launcher(
            tmp.path(),
            &[],
            &real_worker(),
            1,
            Some("test_a or test_d"),
            None,
            RunOptions {
                last_failed: LastFailedMode::Only,
                ..RunOptions::defaults()
            },
        )
        .expect("the run completes");

        assert_eq!(
            report
                .tests
                .iter()
                .map(|test| test.id.as_str())
                .collect::<Vec<_>>(),
            vec!["test_x.py::test_a", "test_x.py::test_d"]
        );
        assert_eq!(report.exit_code, 0);
    }

    /// A collection error is cached under the **file's** id, so `--lf` after a broken import
    /// has something to re-run.
    #[test]
    fn a_collection_error_is_cached_under_the_file() {
        let tmp = tree(&[("test_broken.py", "import nope_does_not_exist\n")]);
        let _ = run_tree(&tmp, 1);
        assert_eq!(
            crate::v2::cache::read_last_failed(tmp.path()),
            ["test_broken.py".to_string()].into_iter().collect()
        );
    }

    // =====================================================================
    // Scheduling and protocol, against scripted workers
    // =====================================================================

    const READY: &str = r#"sys.stdout.write('{"op":"ready","protocol_version":7}\n')"#;

    fn scripted(script: &str) -> WorkerLauncher {
        assert!(
            READY.contains(&format!(r#""protocol_version":{PROTOCOL_VERSION}"#)),
            "the scripted `ready` line is stale for protocol {PROTOCOL_VERSION}: {READY}"
        );
        WorkerLauncher::scripted(&test_python(), vec!["-c".to_string(), script.to_string()])
    }

    /// A stand-in that answers every op, collecting `per_file` tests named after the file's
    /// stem, and **logging the execute ids it is asked for, in order**, to `log`.
    fn recording_worker(log: &Path, per_file: usize) -> WorkerLauncher {
        let script = format!(
            "import json, os, sys\n\
             log = open('{log}/w-%d.txt' % os.getpid(), 'w', encoding='utf-8')\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   op = message['op']\n\
             \x20   if op == 'init':\n\
             \x20       {READY}\n\
             \x20   elif op == 'collect_file':\n\
             \x20       path = message['path']\n\
             \x20       rel = path.rsplit('/', 1)[-1]\n\
             \x20       tests = [\n\
             \x20           {{'id': '%s::test_%d' % (rel, i), 'path': rel,\n\
             \x20            'qualname': 'test_%d' % i}}\n\
             \x20           for i in range({per_file})\n\
             \x20       ]\n\
             \x20       sys.stdout.write(json.dumps(\n\
             \x20           {{'op': 'collected', 'path': path, 'tests': tests}}) + chr(10))\n\
             \x20   elif op == 'execute_batch':\n\
             \x20       for test_id in message['ids']:\n\
             \x20           log.write(test_id + chr(10))\n\
             \x20           log.flush()\n\
             \x20           sys.stdout.write(json.dumps(\n\
             \x20               {{'op': 'test_result', 'id': test_id,\n\
             \x20                'status': 'passed', 'duration_s': 0.0}}) + chr(10))\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'batch_done',\n\
             \x20           'executed': len(message['ids']), 'stopped': False}}) + chr(10))\n\
             \x20   elif op == 'shutdown':\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n",
            log = crate::v2::to_posix(log),
        );
        scripted(&script)
    }

    fn logged_dispatch(dir: &Path) -> Vec<Vec<String>> {
        let mut logs: Vec<Vec<String>> = fs::read_dir(dir)
            .unwrap()
            .flatten()
            .map(|entry| {
                fs::read_to_string(entry.path())
                    .unwrap()
                    .lines()
                    .map(str::to_string)
                    .collect()
            })
            .filter(|lines: &Vec<String>| !lines.is_empty())
            .collect();
        logs.sort();
        logs
    }

    /// Dispatch is **grouped by file**.  Two files on one worker, with the manifest
    /// interleaving them, must still be executed one file at a time — the worker detects
    /// module boundaries by comparing consecutive tests' files, so an interleaved dispatch
    /// would rebuild module fixtures on every id.
    ///
    /// The interleaving comes from a duplicate file argument: `a b a` puts `a`'s tests in
    /// two separate manifest blocks with `b`'s between them, all on one worker.
    #[test]
    fn one_workers_tests_are_dispatched_grouped_by_file() {
        let tmp = tree(&[("test_a.py", ""), ("test_b.py", "")]);
        let logs = TempDir::new().unwrap();
        let a = tmp.path().join("test_a.py");
        let b = tmp.path().join("test_b.py");

        let report = run_default(
            tmp.path(),
            &[a.clone(), b, a],
            &recording_worker(logs.path(), 2),
            1,
            None,
            None,
        )
        .expect("the run completes");

        // The report keeps manifest order: a, a, b, b, a, a.
        assert_eq!(
            report
                .tests
                .iter()
                .map(|t| t.id.as_str())
                .collect::<Vec<_>>(),
            vec![
                "test_a.py::test_0",
                "test_a.py::test_1",
                "test_b.py::test_0",
                "test_b.py::test_1",
                "test_a.py::test_0",
                "test_a.py::test_1",
            ]
        );
        // Dispatch does not: both of `a`'s blocks are executed before `b` is touched.
        assert_eq!(
            logged_dispatch(logs.path()),
            vec![vec![
                "test_a.py::test_0".to_string(),
                "test_a.py::test_1".to_string(),
                "test_a.py::test_0".to_string(),
                "test_a.py::test_1".to_string(),
                "test_b.py::test_0".to_string(),
                "test_b.py::test_1".to_string(),
            ]]
        );
    }

    /// Every test goes to the worker that collected its file.  With two workers and two
    /// stems, each log must contain only its own file's ids — the warm-import guarantee
    /// `ExecuteTest` depends on, and the one property that makes a mis-routed id a
    /// worker-side `exit 2` rather than a wrong answer.
    #[test]
    fn every_test_runs_on_the_worker_that_collected_its_file() {
        let tmp = tree(&[("test_a.py", ""), ("test_b.py", "")]);
        let logs = TempDir::new().unwrap();

        let report = run_default(
            tmp.path(),
            &[],
            &recording_worker(logs.path(), 1),
            2,
            None,
            None,
        )
        .expect("the run completes");
        assert_eq!(report.tests.len(), 2);

        for log in logged_dispatch(logs.path()) {
            let files: Vec<&str> = log
                .iter()
                .map(|id| id.split("::").next().unwrap())
                .collect();
            assert!(
                files.windows(2).all(|pair| pair[0] == pair[1]),
                "a worker was sent ids from more than one file: {log:?}"
            );
        }
    }

    /// A `test_result` carrying a status outside the documented six is **protocol drift**,
    /// and the error names the offending value.  `src/v2/protocol.rs` leaves `status` an
    /// unvalidated `String` precisely so this check can live here, with the id in hand.
    #[test]
    fn an_unknown_status_is_protocol_fatal_and_names_the_value() {
        let tmp = tree(&[("test_a.py", "")]);
        let script = format!(
            "import json, sys\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   op = message['op']\n\
             \x20   if op == 'init':\n\
             \x20       {READY}\n\
             \x20   elif op == 'collect_file':\n\
             \x20       path = message['path']\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': path,\n\
             \x20           'tests': [{{'id': 'test_a.py::test_one', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_one'}}]}}) + chr(10))\n\
             \x20   elif op == 'execute_batch':\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': message['ids'][0], 'status': 'xpass',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20   else:\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n"
        );

        let err = run_default(tmp.path(), &[], &scripted(&script), 1, None, None)
            .expect_err("an unknown status is fatal");
        let message = err.to_string();
        assert!(message.contains("unknown status `xpass`"), "{message}");
        assert!(message.contains("test_a.py::test_one"), "{message}");
    }

    /// A result naming a different test than the one requested is fatal: accepting it would
    /// attribute one test's outcome to another, which is worse than a failed run.
    #[test]
    fn a_result_for_the_wrong_test_is_protocol_fatal() {
        let tmp = tree(&[("test_a.py", "")]);
        let script = format!(
            "import json, sys\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   op = message['op']\n\
             \x20   if op == 'init':\n\
             \x20       {READY}\n\
             \x20   elif op == 'collect_file':\n\
             \x20       path = message['path']\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': path,\n\
             \x20           'tests': [{{'id': 'test_a.py::test_one', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_one'}}]}}) + chr(10))\n\
             \x20   elif op == 'execute_batch':\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': 'test_a.py::somebody_else', 'status': 'passed',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20   else:\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n"
        );

        let err = run_default(tmp.path(), &[], &scripted(&script), 1, None, None)
            .expect_err("a mis-addressed result is fatal");
        assert!(
            err.to_string().contains("not the test at this position"),
            "{err}"
        );
    }

    /// A worker that dies mid-execute names the **test** in flight, not the file: the run is
    /// past collection and a file name would send a reader to the wrong phase.
    #[test]
    fn a_worker_dying_mid_execute_names_the_test() {
        let tmp = tree(&[("test_a.py", "")]);
        let script = format!(
            "import json, sys\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   op = message['op']\n\
             \x20   if op == 'init':\n\
             \x20       {READY}\n\
             \x20   elif op == 'collect_file':\n\
             \x20       path = message['path']\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': path,\n\
             \x20           'tests': [{{'id': 'test_a.py::test_one', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_one'}}]}}) + chr(10))\n\
             \x20   elif op == 'execute_batch':\n\
             \x20       sys.stderr.write('the test took the worker with it' + chr(10))\n\
             \x20       sys.exit(7)\n\
             \x20   sys.stdout.flush()\n"
        );

        let err = run_default(tmp.path(), &[], &scripted(&script), 1, None, None)
            .expect_err("a dead worker is fatal");
        let message = err.to_string();
        assert!(message.contains("test_a.py::test_one"), "{message}");
        assert!(message.contains("took the worker with it"), "{message}");
    }

    /// The batch terminator's `executed` count is checked against the results that arrived.
    ///
    /// This is the **only** place a lost result is detectable: the stream still terminates,
    /// the ids that did arrive still match one-to-one, and the missing test would simply be
    /// absent from the report. A count the worker computes independently turns silence into
    /// a named error.
    #[test]
    fn a_batch_that_miscounts_its_results_is_protocol_fatal() {
        let tmp = tree(&[("test_a.py", "")]);
        let script = format!(
            "import json, sys\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   op = message['op']\n\
             \x20   if op == 'init':\n\
             \x20       {READY}\n\
             \x20   elif op == 'collect_file':\n\
             \x20       path = message['path']\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': path,\n\
             \x20           'tests': [{{'id': 'test_a.py::test_one', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_one'}},\n\
             \x20                     {{'id': 'test_a.py::test_two', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_two'}}]}}) + chr(10))\n\
             \x20   elif op == 'execute_batch':\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': message['ids'][0], 'status': 'passed',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': message['ids'][1], 'status': 'passed',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'batch_done',\n\
             \x20           'executed': 99, 'stopped': False}}) + chr(10))\n\
             \x20   else:\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n"
        );

        let err = run_default(tmp.path(), &[], &scripted(&script), 1, None, None)
            .expect_err("a miscounted batch is fatal");
        let message = err.to_string();
        assert!(message.contains("says it executed 99"), "{message}");
        assert!(message.contains("2 results arrived"), "{message}");
    }

    /// A batch that ends early **without** claiming to have stopped is a worker that dropped
    /// work. `-x` firing is the only legitimate short batch, and it says so in `stopped`;
    /// without that distinction a lost half-file would read as a successful run of fewer
    /// tests.
    #[test]
    fn a_short_batch_that_did_not_stop_early_is_protocol_fatal() {
        let tmp = tree(&[("test_a.py", "")]);
        let script = format!(
            "import json, sys\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   op = message['op']\n\
             \x20   if op == 'init':\n\
             \x20       {READY}\n\
             \x20   elif op == 'collect_file':\n\
             \x20       path = message['path']\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': path,\n\
             \x20           'tests': [{{'id': 'test_a.py::test_one', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_one'}},\n\
             \x20                     {{'id': 'test_a.py::test_two', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_two'}}]}}) + chr(10))\n\
             \x20   elif op == 'execute_batch':\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': message['ids'][0], 'status': 'passed',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'batch_done',\n\
             \x20           'executed': 1, 'stopped': False}}) + chr(10))\n\
             \x20   else:\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n"
        );

        let err = run_default(tmp.path(), &[], &scripted(&script), 1, None, None)
            .expect_err("a silently short batch is fatal");
        assert!(
            err.to_string().contains("ended after 1 of 2 tests"),
            "{err}"
        );
    }

    /// More results than ids is drift too, and it is caught on the *result* rather than on
    /// the terminator — by then the extra outcome would already be in the report, attributed
    /// to whatever slot happened to be next.
    #[test]
    fn a_batch_that_answers_more_tests_than_it_was_given_is_protocol_fatal() {
        let tmp = tree(&[("test_a.py", "")]);
        let script = format!(
            "import json, sys\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   op = message['op']\n\
             \x20   if op == 'init':\n\
             \x20       {READY}\n\
             \x20   elif op == 'collect_file':\n\
             \x20       path = message['path']\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': path,\n\
             \x20           'tests': [{{'id': 'test_a.py::test_one', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_one'}},\n\
             \x20                     {{'id': 'test_a.py::test_two', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_two'}}]}}) + chr(10))\n\
             \x20   elif op == 'execute_batch':\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': message['ids'][0], 'status': 'passed',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': message['ids'][1], 'status': 'passed',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': 'test_a.py::test_one', 'status': 'passed',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'batch_done',\n\
             \x20           'executed': 3, 'stopped': False}}) + chr(10))\n\
             \x20   else:\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n"
        );

        let err = run_default(tmp.path(), &[], &scripted(&script), 1, None, None)
            .expect_err("an over-answered batch is fatal");
        assert!(
            err.to_string().contains("3th result for a batch of 2"),
            "{err}"
        );
    }

    /// Results must arrive **in the order the ids were sent**.  The set of ids here is
    /// correct and complete — only the order is wrong — so nothing but the positional check
    /// can catch it, and without that check two tests would swap outcomes silently.
    #[test]
    fn a_batch_answering_out_of_order_is_protocol_fatal() {
        let tmp = tree(&[("test_a.py", "")]);
        let script = format!(
            "import json, sys\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   op = message['op']\n\
             \x20   if op == 'init':\n\
             \x20       {READY}\n\
             \x20   elif op == 'collect_file':\n\
             \x20       path = message['path']\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': path,\n\
             \x20           'tests': [{{'id': 'test_a.py::test_one', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_one'}},\n\
             \x20                     {{'id': 'test_a.py::test_two', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_two'}}]}}) + chr(10))\n\
             \x20   elif op == 'execute_batch':\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': message['ids'][1], 'status': 'passed',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': message['ids'][0], 'status': 'passed',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'batch_done',\n\
             \x20           'executed': 2, 'stopped': False}}) + chr(10))\n\
             \x20   else:\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n"
        );

        let err = run_default(tmp.path(), &[], &scripted(&script), 1, None, None)
            .expect_err("a reordered batch is fatal");
        assert!(
            err.to_string().contains("not the test at this position"),
            "{err}"
        );
    }

    /// A batch may not claim it stopped early when `-x` was never asked for.
    ///
    /// The mirror of `a_short_batch_that_did_not_stop_early_is_protocol_fatal`, and the more
    /// dangerous half: `stopped` is the one field a worker can set that legitimately shrinks
    /// the report, and the orchestrator **acts** on it — it sets the pool-wide stop flag, so
    /// every other worker stops dispatching too and the main thread then treats every missing
    /// slot as expected rather than as a lost result. An unsolicited `stopped` therefore
    /// truncates a whole green run into a shorter green run: the exit code stays 0, the
    /// summary just counts fewer tests, and nothing anywhere says why.
    ///
    /// This run passes no `-x`, so the request carries `stop_on_failure: false` and the flag
    /// is a lie whatever the worker's reason for setting it.
    #[test]
    fn a_batch_that_stops_early_without_being_asked_is_protocol_fatal() {
        let tmp = tree(&[("test_a.py", "")]);
        let script = format!(
            "import json, sys\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   op = message['op']\n\
             \x20   if op == 'init':\n\
             \x20       {READY}\n\
             \x20   elif op == 'collect_file':\n\
             \x20       path = message['path']\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': path,\n\
             \x20           'tests': [{{'id': 'test_a.py::test_one', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_one'}},\n\
             \x20                     {{'id': 'test_a.py::test_two', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_two'}}]}}) + chr(10))\n\
             \x20   elif op == 'execute_batch':\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': message['ids'][0], 'status': 'passed',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'batch_done',\n\
             \x20           'executed': 1, 'stopped': True}}) + chr(10))\n\
             \x20   else:\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n"
        );

        let err = run_default(tmp.path(), &[], &scripted(&script), 1, None, None)
            .expect_err("an unsolicited `stopped` is fatal");
        let message = err.to_string();
        assert!(
            message.contains("neither `-x` nor `--maxfail` was in effect"),
            "{message}"
        );
        assert!(message.contains("test_a.py::test_two"), "{message}");
    }

    /// A batch whose stream **ends** without a terminator is fatal, not silently short.
    ///
    /// The worker here answers every test and then exits, closing stdout. Without this check
    /// the run would report two passing tests and a green exit for a batch nobody ever
    /// confirmed — the terminator is what turns "the pipe closed" into a named error.
    ///
    /// **The neighbouring case blocks, and the reason is worth stating precisely** — the
    /// first draft of this test asserted it did not, and hung.
    ///
    /// A worker that answers the results and then simply *waits* — alive, idle, holding its
    /// pipe open — blocks the orchestrator's read, exactly as an unanswered `collect_file`
    /// always has. The two halves of that are not equally hopeless:
    ///
    /// * **mid-batch**, a silent worker is genuinely indistinguishable from a slow test.
    ///   Only a timeout could separate them, and any timeout value is wrong for somebody —
    ///   an integration test that takes four minutes is not a hung worker;
    /// * **after the last result**, it is not. The orchestrator knows exactly one line is
    ///   outstanding (`batch_done`) and that no test is running, so a short bounded wait
    ///   there would be sound and would cost nothing on a healthy run.
    ///
    /// The bounded wait is **not implemented**: it needs a second timeout mechanism on the
    /// read path, and the failure it catches (a worker that answers every test and then
    /// declines to say so) has never been observed and is not reachable through
    /// `_v2_worker.py`, whose batch arm writes the terminator unconditionally. It is recorded
    /// as the tractable half rather than described as impossible.
    #[test]
    fn a_batch_whose_stream_ends_without_a_terminator_is_fatal() {
        let tmp = tree(&[("test_a.py", "")]);
        let script = format!(
            "import json, sys\n\
             while True:\n\
             \x20   line = sys.stdin.readline()\n\
             \x20   if not line:\n\
             \x20       break\n\
             \x20   message = json.loads(line)\n\
             \x20   op = message['op']\n\
             \x20   if op == 'init':\n\
             \x20       {READY}\n\
             \x20   elif op == 'collect_file':\n\
             \x20       path = message['path']\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'collected', 'path': path,\n\
             \x20           'tests': [{{'id': 'test_a.py::test_one', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_one'}},\n\
             \x20                     {{'id': 'test_a.py::test_two', 'path': 'test_a.py',\n\
             \x20                      'qualname': 'test_two'}}]}}) + chr(10))\n\
             \x20   elif op == 'execute_batch':\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': message['ids'][0], 'status': 'passed',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20       sys.stdout.write(json.dumps({{'op': 'test_result',\n\
             \x20           'id': message['ids'][1], 'status': 'passed',\n\
             \x20           'duration_s': 0.0}}) + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       sys.exit(0)\n\
             \x20   else:\n\
             \x20       sys.stdout.write('{{\"op\":\"bye\"}}' + chr(10))\n\
             \x20       sys.stdout.flush()\n\
             \x20       break\n\
             \x20   sys.stdout.flush()\n"
        );

        let err = run_default(tmp.path(), &[], &scripted(&script), 1, None, None)
            .expect_err("a batch whose stream ends without a terminator is fatal");
        let message = err.to_string();
        assert!(message.contains("the worker exited mid-batch"), "{message}");
        assert!(message.contains("test_a.py"), "{message}");
    }

    /// Version skew is caught by the handshake, **before** any file is collected — the whole
    /// point of declaring a version.  An old worker declaring protocol 1 must fail the run
    /// loudly rather than half-speak the new ops.
    #[test]
    fn an_old_worker_fails_the_run_at_the_handshake() {
        let tmp = tree(&[("test_a.py", "def test_a():\n    pass\n")]);
        let script = "import json, sys\n\
             sys.stdin.readline()\n\
             sys.stdout.write('{\"op\":\"ready\",\"protocol_version\":1}\\n')\n\
             sys.stdout.flush()\n\
             sys.stdin.readline()\n";

        let err = run_default(tmp.path(), &[], &scripted(script), 1, None, None)
            .expect_err("version skew is fatal");
        let message = err.to_string();
        assert!(message.contains("speaks protocol 1"), "{message}");
        assert!(
            message.contains(&format!("requires {PROTOCOL_VERSION}")),
            "{message}"
        );
    }
}
