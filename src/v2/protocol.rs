//! The worker protocol: the JSON-lines contract between the Rust orchestrator and the
//! Python collection workers.
//!
//! One JSON object per line, newline-terminated: requests travel down a worker's stdin,
//! responses come back up its stdout.  A worker's stdout is therefore reserved for
//! protocol traffic — anything a collected module prints must be redirected elsewhere.
//!
//! Both enums are **internally tagged on `op`** with `snake_case` variant names, and both
//! carry `deny_unknown_fields`.  Two classes of malformed line are therefore hard decode
//! errors rather than silently dropped or silently empty messages:
//!
//! - an unrecognised **op** — a peer speaking a protocol this build does not know;
//! - an unrecognised **field** — e.g. a `"tsets"` typo, which without that attribute would
//!   decode as a perfectly clean, perfectly empty collection.
//!
//! Strictness is the right default here because the protocol is internal: orchestrator and
//! worker ship in the same wheel, so cross-release skew does not exist in production. A
//! mismatch therefore means a bug, and a bug must fail loudly rather than half-work.
//!
//! **What serde does not catch.**  [`WorkerResponse::Collected`] is a product type, so a
//! line carrying *both* `tests` and `error` decodes cleanly — rejecting it would need a
//! hand-written `Deserialize`, which this contract deliberately avoids.  The orchestrator
//! must therefore validate on receipt and treat a hybrid `collected` as **protocol-fatal,
//! exactly as it treats a decode error**.  The same division of labour applies to
//! [`WorkerResponse::TestResult`]'s `status`, which is an unvalidated `String` here (see
//! the field docs).  The test module below documents both tolerances explicitly so neither
//! is ever mistaken for coverage.
//!
//! Like [`crate::v2::manifest`], the JSON encoding here is a **frozen wire contract** —
//! field names, the tag key, and the omit-when-empty rules on `Collected` are pinned by
//! golden-string tests below, and any incompatible change must bump [`PROTOCOL_VERSION`].

use crate::v2::manifest::{CollectedTest, CollectionErrorEntry};
use serde::{Deserialize, Serialize};

/// Version of the worker wire protocol.  Bump on any incompatible change.
///
/// The orchestrator sends the version it requires in [`WorkerRequest::Init`], and the
/// worker declares the version it actually speaks in [`WorkerResponse::Ready`] — never an
/// echo of `Init`, or the handshake could not detect skew at all.  A mismatch is fatal.
///
/// **v2** (Phase 1b.2) adds the execute ops — [`WorkerRequest::ExecuteTest`] and
/// [`WorkerResponse::TestResult`] — plus `invocation_dir` on `Init`.  Incompatible in both
/// directions: a v1 worker fatals on the unknown `execute_test` op (exit 2), and a v1
/// orchestrator sends an `init` line missing a field v2 declares.  Neither failure is ever
/// *reached*, because the handshake compares versions before any work is dispatched — which
/// is the whole point of declaring one.
///
/// **v3** (Phase 2 Task 3) adds two things, both additive on the wire and neither optional
/// in effect:
///
/// * `assert_key` on [`WorkerRequest::CollectFile`] — the Tier S manifest cache key for a
///   file the static tier certified, which tells the worker to import that module through
///   the assertion-rewriting hook and where to cache the compiled result.  Absent means
///   "import it normally", so a Tier D file keeps plain asserts;
/// * [`WorkerRequest::ExecuteBatch`] / [`WorkerResponse::BatchDone`] — a whole file's tests
///   in one request, results streamed back and terminated by `batch_done`.
///
/// **v4** (Phase 3 Task 1) adds the three asyncio ini values to [`WorkerRequest::Init`]:
/// `asyncio_mode`, `asyncio_default_fixture_loop_scope` and `asyncio_default_test_loop_scope`.
/// They belong on `init` rather than on each `execute_batch` because they are whole-run facts
/// resolved once by `src/v2/config.rs`, and because the worker needs them at **collection**
/// time as well as at execution time — `asyncio_mode` decides whether an `async def` + `yield`
/// test acquires a synthesised `xfail` mark. The change is incompatible in the direction that
/// matters: a v3 worker would silently apply its own defaults to a suite that configured
/// something else, which is the failure mode a declared version exists to make impossible.
///
/// **v5** (Phase 3 Task 3) adds `coverage` to [`WorkerRequest::Init`] — the `--cov` surface's
/// only wire footprint. It is an `Option<`[`CoverageWire`]`>` rather than a pair of optional
/// scalars because "measure these trees, into this directory" is one instruction with one
/// presence signal: two independent `Option`s would have a fourth state (`data_dir` without
/// `sources`, or the reverse) that means nothing, and the codebase's own rule for that shape
/// is `parse_last_failed`'s — one value, not a pair with a dead corner.
///
/// Omitted entirely when `--cov` is absent, so a plain run's `init` line is byte-identical to
/// v4's apart from the version number, and the worker registers **no `sys.monitoring` tool at
/// all** (`python/rustest/_v2_coverage.py`). The bump is nonetheless real and not cosmetic in
/// the direction that matters: a v4 worker handed a v5 `init` would reject the unknown field
/// (`deny_unknown_fields`) — and, worse, a v4 worker that *tolerated* it would run the whole
/// suite and write no coverage data at all, reporting 0 % for a run the user asked to measure.
///
/// `python/rustest/_v2_worker.py` mirrors this constant and **must be bumped in the same
/// commit**; a worker still declaring the old number turns every run into a handshake
/// error.
/// **v6** adds `pythonpath`: the `pythonpath` ini, resolved to absolute posix directories,
/// which the worker prepends to `sys.path` in `handle_init` — pytest does the same in
/// `Config._configure_python_path` (`_pytest/config/__init__.py` l. 1316-1319), from
/// `pytest_load_initial_conftests`, i.e. before anything is imported. Like `coverage` the
/// field is omitted when empty, so an ordinary project's `init` line is byte-identical to
/// v5's apart from the number; the bump is still real, because a v5 worker handed the field
/// rejects it (`deny_unknown_fields`) and a v5 worker that *tolerated* it would fail to
/// import every module under a `src/` layout while claiming to honour the ini.
/// **v7** adds `execute_batch.max_fail`: `--maxfail=N`'s remaining budget, so a worker can
/// cut a batch on the Nth failure rather than only on the first. Omitted when there is no
/// limit, which is every run without the flag, so an ordinary batch line is byte-identical
/// to v6's.
pub const PROTOCOL_VERSION: u32 = 7;

/// The `--cov` instruction carried on [`WorkerRequest::Init`].
///
/// Both fields are **required** when the object is present, and the object is absent when
/// coverage is off. There is no "empty sources means everything" reading: the orchestrator
/// resolves a bare `--cov` to the rootdir *before* the wire (`python/rustest/core.py`), so a
/// worker never has to guess what "everything" means, and an empty list reaching a worker is a
/// bug the worker reports rather than a mode it invents.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoverageWire {
    /// Absolute posix directories whose files are measured — coverage.py's `source` setting
    /// (`coverage/inorout.py::check_include_omit_etc`, which answers "falls outside the
    /// --source spec" for anything else).
    pub sources: Vec<String>,
    /// Absolute posix directory each worker writes its own `.coverage.<suffix>` file into.
    ///
    /// A **directory**, never a file: the pool is N processes and coverage.py's parallel-mode
    /// naming (`coverage/data.py::filename_suffix`) is what keeps their writes from colliding,
    /// exactly as `coverage run -p` does across processes. The orchestrator combines them
    /// afterwards with `Coverage.combine`.
    pub data_dir: String,
}

/// A message from the orchestrator to a worker, one per stdin line.
///
/// `Init` is much larger than the other variants and is deliberately left unboxed:
/// **exactly one is built per worker**, immediately serialized to a line of JSON and
/// dropped, so the enum is never held in a collection and the "wasted" stack bytes are paid
/// once per process. Boxing would put an allocation and a level of indirection between the
/// golden-string tests and the struct they pin, for no measurable gain.
#[allow(clippy::large_enum_variant)]
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum WorkerRequest {
    /// Sent once as the first line of a worker's stdin.
    Init {
        protocol_version: u32,
        /// Absolute posix rootdir (nodeids are relative to it).
        rootdir: String,
        /// Absolute posix directory the run was invoked from — pytest's
        /// `Config.invocation_params.dir`.
        ///
        /// **Not a synonym for `rootdir`.**  pytest derives rootdir by walking *up* from
        /// the arguments looking for a config file (`_pytest/config/findpaths.py`), so any
        /// run started in a subdirectory has the two disagree, and a worker that
        /// substituted one for the other would be wrong precisely in that case.
        ///
        /// It is on the wire because a worker cannot infer it: worker processes are spawned
        /// by the orchestrator, so their cwd is an implementation detail of the spawn, not
        /// the user's directory.  Execution needs it wherever pytest's behaviour is
        /// invocation-relative — resolving relative paths a test body opens, and rooting
        /// the path-producing fixture factories identically in every worker of the pool so
        /// a test's result does not depend on which process it landed in.
        invocation_dir: String,
        /// Naming rules from ResolvedConfig, passed through verbatim.
        python_files: Vec<String>,
        python_classes: Vec<String>,
        python_functions: Vec<String>,
        /// `auto` or `strict`, already validated by `config::resolve_config`.
        ///
        /// The worker does not re-validate: a second implementation of the rule is a second
        /// thing that can disagree, and the orchestrator has already exited 4 for a bad value
        /// before any worker was spawned.
        asyncio_mode: String,
        /// The `asyncio_default_fixture_loop_scope` ini, **omitted from the wire when unset**.
        ///
        /// Absence is a distinct third answer, not a synonym for `"function"`:
        /// `pytest_asyncio/plugin.py::pytest_fixture_setup` (l. 736-741) resolves an async
        /// fixture's loop scope as `mark ?? this_option ?? fixturedef.scope`, so with the
        /// option unset a `scope="module"` async fixture gets a *module*-scoped loop. Sending
        /// `"function"` in its place would move every wider async fixture onto a loop that
        /// dies under it.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        asyncio_default_fixture_loop_scope: Option<String>,
        /// The `asyncio_default_test_loop_scope` ini — always present, since the oracle gives
        /// it a real default (`"function"`, plugin.py l. 123-128).
        asyncio_default_test_loop_scope: String,
        /// The `pythonpath` ini as absolute posix directories, **omitted when empty**.
        ///
        /// Absolute already: `type="paths"` resolves each entry against the config file's
        /// directory inside `config::resolve_config`, so the worker neither knows nor needs
        /// to know where the ini lived. Order is the ini's own; the worker inserts in
        /// reverse so entry 0 ends up first on `sys.path`, which is what
        /// `for path in reversed(...): sys.path.insert(0, ...)` produces in pytest.
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        pythonpath: Vec<String>,
        /// `--cov`: what to measure and where to write it, or **absent** for a run with no
        /// coverage at all.  See [`CoverageWire`] and [`PROTOCOL_VERSION`]'s v5 note.
        ///
        /// It belongs on `init` rather than on `execute_batch` for the same reason the asyncio
        /// options do, plus one that is specific to coverage: measurement has to be running
        /// **before the first `collect_file`**, because a module's import-time lines are lines
        /// coverage.py counts (it starts before pytest's collection), and a worker told to
        /// start measuring at execution time would miss every one of them.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        coverage: Option<CoverageWire>,
    },
    /// Collect one file. path: absolute posix.
    CollectFile {
        path: String,
        /// The Tier S manifest cache key (64 lowercase hex) for this file, when the static
        /// tier certified it as statically analysable — and **omitted entirely** otherwise.
        ///
        /// Presence is the whole signal: it means "rewrite this module's assertions, and
        /// cache the compiled result under this key" (`python/rustest/_assertion_rewrite.py`).
        /// Absence means "import it the ordinary way", which is the plan's "Tier D files keep
        /// plain asserts" — enforced by the orchestrator not sending a key rather than by a
        /// second policy check in the worker.
        ///
        /// It is a *key*, not a boolean, because the worker cannot compute one: the key
        /// composes the resolved config, the conftest chain and the stdlib shadow set
        /// (`src/v2/manifest_cache.rs`), all of which are whole-run facts the orchestrator
        /// holds and a single worker does not. Sending a boolean and re-deriving the key
        /// worker-side would be a second implementation of the key rules, free to diverge —
        /// the same argument `execute_test` makes for sending an id instead of a path.
        ///
        /// `skip_serializing_if` keeps every Tier D `collect_file` line byte-identical to
        /// v2's, which is what lets the golden below assert the omission rather than describe
        /// it.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        assert_key: Option<String>,
    },
    /// Execute one collected test by manifest id.
    ///
    /// **No orchestrator sends this any more** — [`Self::ExecuteBatch`] replaced it on the
    /// run path at Phase 2 Task 3, including for `-x` (which travels as `stop_on_failure`).
    /// It stays in the contract, and the worker keeps implementing it, because it is the
    /// unit a batch is *defined in terms of*: `_v2_worker.py::execute_batch` is a loop over
    /// `execute_test`, and the worker's own test suite drives the single op directly, which
    /// is how one test's execution can be exercised without a batch's framing in the way.
    ///
    /// The worker must have collected the test's file already in this session, so imports
    /// are warm and the test object is the one enumeration saw.  Executing an unknown id is
    /// a **protocol error response, not a silent skip**: a test that vanishes between
    /// collection and execution is the failure mode this whole contract exists to prevent.
    ///
    /// The id is the manifest id (`src/v2/nodeid.rs`) and nothing else — no path, no
    /// qualname.  The orchestrator already holds ids from collection; re-deriving one
    /// worker-side would be a second implementation of the nodeid rules, free to diverge.
    ExecuteTest { id: String },
    /// Execute a **whole file's** collected tests in one request.
    ///
    /// The worker answers one [`WorkerResponse::TestResult`] per executed id, in the order
    /// they were sent, and terminates the stream with [`WorkerResponse::BatchDone`]. The
    /// terminator is what makes the batch self-delimiting: without it the orchestrator would
    /// have to count results, and a worker that stopped early (below) would look exactly like
    /// a worker that hung.
    ///
    /// **Why this op exists.** Measured on the 5 000-test benchmark suite, the per-test
    /// `execute_test` round trip cost ~160 µs of orchestrator time against ~20 µs of actual
    /// in-worker execution: two pipe writes and a blocking line read per test, on a platform
    /// where each of those is a syscall through whatever filter driver is installed. Batching
    /// a file's tests collapses that to one write and one drain per *file*.
    ///
    /// **The batch is one file, never more.** `_v2_worker.py` detects module and class
    /// boundaries by comparing consecutive tests' files, so a batch spanning two files would
    /// tear down and rebuild module-scoped fixtures inside a single request — the ordering
    /// contract `src/v2/execute.rs` documents under "Dispatch order: grouped by file".
    ///
    /// `stop_on_failure` carries `-x` **into** the batch. Without it, `-x` would degrade from
    /// "nothing runs after the first failure" to "nothing runs after the failing file", which
    /// is a silent behaviour change on a flag whose entire purpose is stopping early; the
    /// orchestrator's own between-batch check cannot see inside a request in flight.
    ExecuteBatch {
        ids: Vec<String>,
        stop_on_failure: bool,
        /// `--maxfail`'s **remaining budget** for this batch, or absent for no limit.
        ///
        /// The count has to travel, not just the flag: `stop_on_failure` cuts a batch at the
        /// *first* failure, which is `--maxfail=1`, and a worker cannot know how many
        /// failures the rest of the pool has already recorded. The orchestrator subtracts
        /// what it has seen and sends what is left, so a single-worker run stops on exactly
        /// the Nth failure — pytest's own granularity — and a parallel run stops at the next
        /// batch boundary, which is `pytest-xdist`'s.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        max_fail: Option<usize>,
    },
    /// Graceful shutdown; worker replies Bye then exits 0.
    Shutdown,
}

/// A message from a worker to the orchestrator, one per stdout line.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum WorkerResponse {
    /// Handshake reply. `protocol_version` declares the protocol the worker **speaks** —
    /// never an echo of what [`WorkerRequest::Init`] asked for.
    Ready { protocol_version: u32 },
    /// Per-file result. Either tests or an error entry (import/syntax failure).
    ///
    /// The two shapes are exclusive by contract but not by type: a line carrying both is
    /// decodable, and rejecting it is the orchestrator's job (see the module docs).
    Collected {
        path: String,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        tests: Vec<CollectedTest>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        error: Option<CollectionErrorEntry>,
    },
    /// Outcome of one [`WorkerRequest::ExecuteTest`], one line per executed test.
    ///
    /// pytest produces *up to three* reports per test — setup, call, teardown
    /// (`_pytest/runner.py::runtestprotocol`, which skips the call phase when setup failed,
    /// so a broken fixture yields two) — and its terminal output is the collapse of them
    /// into one category.  This wire carries the collapsed form directly, so the worker owns
    /// that reduction and the orchestrator never has to re-derive it from phases it cannot
    /// see — least of all guess how many there were.
    TestResult {
        /// The manifest id echoed from the request, so a response can never be attributed
        /// to the wrong test even if a worker ever answers out of order.
        id: String,
        /// One of the closed set `"passed"`, `"failed"`, `"skipped"`, `"xfailed"`,
        /// `"xpassed"`, `"error"` — pytest's **reporting categories** (its status letters
        /// `.`/`F`/`s`/`x`/`X`/`E`), not `TestReport.outcome`, which is only ever
        /// `"passed"`/`"failed"`/`"skipped"` (`_pytest/reports.py` l. 64, 301-302, pytest
        /// 8.4.2).  The mapping from outcome to category is pytest's own
        /// `pytest_report_teststatus` hook chain:
        ///
        /// * `"error"` is a **setup/teardown or internal failure**, never a failing assert
        ///   in the test body: `_pytest/runner.py::pytest_report_teststatus` returns
        ///   `("error", "E", "ERROR")` for `report.when in ("setup", "teardown")` with
        ///   `report.failed`, and `_pytest/terminal.py::pytest_report_teststatus` restates
        ///   it as `if report.when in ("collect", "setup", "teardown") and outcome ==
        ///   "failed": outcome = "error"`.  `_pytest/reports.py::TestReport.from_item_and_call`
        ///   is where the two diverge in the first place — the same `outcome = "failed"`
        ///   branches on `if call.when == "call"` for the *representation* only.  So
        ///   `"failed"` means "the test ran and disagreed", `"error"` means "the test never
        ///   properly ran": a fixture blew up, a teardown blew up, or the worker itself
        ///   did.  Reporting a broken fixture as `"failed"` would send a user reading the
        ///   E column to debug a body that never executed.
        /// * `"xfailed"`/`"xpassed"` come from `_pytest/skipping.py::pytest_report_teststatus`,
        ///   which promotes a report carrying `wasxfail` — skipped becomes `"xfailed"`,
        ///   passed becomes `"xpassed"`.  A *strict* xpass is not in this set: pytest turns
        ///   it into an ordinary failure (`rep.outcome = "failed"` with `[XPASS(strict)]`),
        ///   so the worker must send `"failed"` for it, and only the message says why.
        ///
        /// **Not validated at decode**, deliberately: this is a `String`, and an
        /// undocumented value decodes cleanly.  The reason is diagnostic quality, not
        /// laxity.  An enum would reject the line inside serde, producing an "unknown
        /// variant" error with no test id in hand — and there is nothing to forward-compat
        /// *to*, because the handshake already guarantees both peers speak this exact
        /// version, so an unexpected status is a worker bug rather than a newer peer.  The
        /// orchestrator holds the id, the request that caused it and the worker's stderr,
        /// so it validates one layer up and reports which test produced what.  Exactly the
        /// division of labour the hybrid `collected` shape uses.
        status: String,
        /// Wall-clock seconds for the test, as a JSON number.
        duration_s: f64,
        /// Failure detail: the formatted assertion/exception, or a skip/xfail reason.
        /// Omitted entirely for an unremarkable pass.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        message: Option<String>,
        /// Captured streams, omitted when nothing was captured — the common case, and the
        /// reason these are optional rather than empty strings: a passing test's line stays
        /// short.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        stdout: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        stderr: Option<String>,
    },
    /// Terminates the result stream of one [`WorkerRequest::ExecuteBatch`].
    ///
    /// `executed` is how many [`WorkerResponse::TestResult`] lines preceded it in this batch,
    /// and the orchestrator checks it against what it counted. That check is the entire
    /// reason the field is not simply `{"op":"batch_done"}`: a batch is the one place where a
    /// *lost* result looks like nothing at all — the stream still terminates, the ids still
    /// match one-to-one with the results that did arrive, and the missing test would simply
    /// be absent from the report. A counter the producer computes independently turns that
    /// into a loud protocol error.
    ///
    /// `stopped` says the worker cut the batch short because `stop_on_failure` was set and a
    /// test failed. It is distinct from `executed < ids.len()` being *inferred*, because the
    /// orchestrator must tell "`-x` fired here" apart from "this worker dropped results":
    /// the first truncates the report legitimately, the second is a bug.
    BatchDone { executed: usize, stopped: bool },
    /// Acknowledges [`WorkerRequest::Shutdown`]; the last line a worker writes before
    /// exiting 0.
    Bye,
}

#[cfg(test)]
mod tests {
    use super::*;

    // --- requests ---------------------------------------------------------

    fn sample_init() -> WorkerRequest {
        WorkerRequest::Init {
            protocol_version: PROTOCOL_VERSION,
            rootdir: "/repo".to_string(),
            // Deliberately *not* equal to `rootdir`: the two disagree whenever a run is
            // started below the rootdir, so a sample that made them equal could not tell a
            // correct producer from one that sent `rootdir` twice.
            invocation_dir: "/repo/tests".to_string(),
            python_files: vec!["test_*.py".to_string(), "*_test.py".to_string()],
            python_classes: vec!["Test*".to_string()],
            python_functions: vec!["test*".to_string()],
            asyncio_mode: "auto".to_string(),
            // The sample carries the *unset* fixture scope, because omission is the shape
            // that is easy to get wrong: `INIT_LINE` below asserts the key is absent rather
            // than `null`, exactly as the Tier D `collect_file` golden asserts for
            // `assert_key`.  `init_request_carries_a_set_fixture_loop_scope` covers the
            // present form.
            asyncio_default_fixture_loop_scope: None,
            asyncio_default_test_loop_scope: "session".to_string(),
            // The sample carries **no** coverage, because absence is the shape a plain run
            // sends and the one that has to stay byte-identical to v4's line.  The present
            // form is pinned separately by `init_request_carries_the_coverage_instruction`.
            coverage: None,
            // Likewise empty: `pythonpath` is unset in almost every project, and the empty
            // form is what has to stay byte-identical to v5's line bar the version number.
            // `init_request_carries_the_pythonpath_ini` pins the present form.
            pythonpath: Vec::new(),
        }
    }

    const INIT_LINE: &str = r#"{"op":"init","protocol_version":7,"rootdir":"/repo","invocation_dir":"/repo/tests","python_files":["test_*.py","*_test.py"],"python_classes":["Test*"],"python_functions":["test*"],"asyncio_mode":"auto","asyncio_default_test_loop_scope":"session"}"#;
    const INIT_LINE_WITH_FIXTURE_SCOPE: &str = r#"{"op":"init","protocol_version":7,"rootdir":"/repo","invocation_dir":"/repo/tests","python_files":["test_*.py","*_test.py"],"python_classes":["Test*"],"python_functions":["test*"],"asyncio_mode":"auto","asyncio_default_fixture_loop_scope":"session","asyncio_default_test_loop_scope":"session"}"#;
    const INIT_LINE_WITH_COVERAGE: &str = r#"{"op":"init","protocol_version":7,"rootdir":"/repo","invocation_dir":"/repo/tests","python_files":["test_*.py","*_test.py"],"python_classes":["Test*"],"python_functions":["test*"],"asyncio_mode":"auto","asyncio_default_test_loop_scope":"session","coverage":{"sources":["/repo/src"],"data_dir":"/tmp/rustest-cov-abc"}}"#;
    const COLLECT_FILE_LINE: &str = r#"{"op":"collect_file","path":"/repo/tests/test_math.py"}"#;
    const COLLECT_FILE_REWRITE_LINE: &str = r#"{"op":"collect_file","path":"/repo/tests/test_math.py","assert_key":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}"#;
    const EXECUTE_TEST_LINE: &str = r#"{"op":"execute_test","id":"tests/test_math.py::test_add"}"#;
    const EXECUTE_BATCH_LINE: &str = r#"{"op":"execute_batch","ids":["tests/test_math.py::test_add","tests/test_math.py::test_sub"],"stop_on_failure":false}"#;
    const SHUTDOWN_LINE: &str = r#"{"op":"shutdown"}"#;

    #[test]
    fn init_request_matches_golden_contract() {
        assert_eq!(
            serde_json::to_string(&sample_init()).expect("init serializes"),
            INIT_LINE
        );

        let decoded: WorkerRequest = serde_json::from_str(INIT_LINE).expect("init deserializes");
        assert_eq!(decoded, sample_init());
    }

    /// The set form of `asyncio_default_fixture_loop_scope`, and the reason the omission
    /// above is asserted separately: `None` and `Some("function")` are different
    /// instructions to the worker (`plugin.py::pytest_fixture_setup` l. 736-741 falls back
    /// to the fixture's own scope only for the former), so a wire form that could not tell
    /// them apart would be a silent downgrade of every module- and session-scoped async
    /// fixture.
    #[test]
    fn init_request_carries_a_set_fixture_loop_scope() {
        let with_scope = init_with(|init| {
            if let WorkerRequest::Init {
                asyncio_default_fixture_loop_scope,
                ..
            } = init
            {
                *asyncio_default_fixture_loop_scope = Some("session".to_string());
            }
        });

        assert_eq!(
            serde_json::to_string(&with_scope).expect("init serializes"),
            INIT_LINE_WITH_FIXTURE_SCOPE
        );
        let decoded: WorkerRequest =
            serde_json::from_str(INIT_LINE_WITH_FIXTURE_SCOPE).expect("init deserializes");
        assert_eq!(decoded, with_scope);
    }

    /// [`sample_init`] with one field changed — so a new field on `Init` cannot silently
    /// escape the variant tests by being forgotten in a hand-written literal.
    fn init_with(edit: impl FnOnce(&mut WorkerRequest)) -> WorkerRequest {
        let mut init = sample_init();
        edit(&mut init);
        init
    }

    /// The `--cov` shape, asserted as a **key that is present** the same way the Tier D
    /// `collect_file` golden asserts one that is absent.
    ///
    /// The omission is the load-bearing half and it is asserted in
    /// `init_request_matches_golden_contract` above: a run without `--cov` must send no
    /// `coverage` key, because the worker keys "register a `sys.monitoring` tool at all" on
    /// that absence, and an explicit `{"sources":[],"data_dir":""}` would turn every plain run
    /// into a measured one.
    #[test]
    fn init_request_carries_the_coverage_instruction() {
        let with_coverage = init_with(|init| {
            if let WorkerRequest::Init { coverage, .. } = init {
                *coverage = Some(CoverageWire {
                    sources: vec!["/repo/src".to_string()],
                    data_dir: "/tmp/rustest-cov-abc".to_string(),
                });
            }
        });

        assert_eq!(
            serde_json::to_string(&with_coverage).expect("init serializes"),
            INIT_LINE_WITH_COVERAGE
        );
        let decoded: WorkerRequest =
            serde_json::from_str(INIT_LINE_WITH_COVERAGE).expect("init deserializes");
        assert_eq!(decoded, with_coverage);

        assert!(
            !INIT_LINE.contains(r#""coverage":"#),
            "a run without --cov must not carry the key at all"
        );
    }

    /// `pythonpath` travels only when the ini is set, and it travels **in ini order**.
    ///
    /// Order is the whole content of the field: the worker inserts in reverse so that entry
    /// 0 ends up first on `sys.path`, which is what
    /// `for path in reversed(getini("pythonpath")): sys.path.insert(0, ...)` produces
    /// (`_pytest/config/__init__.py` l. 1316-1319). A serializer that sorted or deduplicated
    /// would change which of two same-named packages a suite imports.
    #[test]
    fn init_request_carries_the_pythonpath_ini() {
        let with_paths = init_with(|init| {
            if let WorkerRequest::Init { pythonpath, .. } = init {
                *pythonpath = vec!["/repo/src".to_string(), "/repo/vendor".to_string()];
            }
        });

        let line = r#"{"op":"init","protocol_version":7,"rootdir":"/repo","invocation_dir":"/repo/tests","python_files":["test_*.py","*_test.py"],"python_classes":["Test*"],"python_functions":["test*"],"asyncio_mode":"auto","asyncio_default_test_loop_scope":"session","pythonpath":["/repo/src","/repo/vendor"]}"#;
        assert_eq!(
            serde_json::to_string(&with_paths).expect("init serializes"),
            line
        );
        let decoded: WorkerRequest = serde_json::from_str(line).expect("init deserializes");
        assert_eq!(decoded, with_paths);

        assert!(
            !INIT_LINE.contains(r#""pythonpath":"#),
            "a project without the ini must not carry the key at all"
        );
    }

    /// Both `CoverageWire` fields are required, and the struct denies unknown fields.
    ///
    /// Pinned apart from `every_non_optional_field_is_required` because `deny_unknown_fields`
    /// is an attribute on *this* struct, not something the enclosing enum's attribute reaches:
    /// a nested type added without it accepts `{"sources":[],"data_dir":"/x","branch":true}`
    /// and silently drops the field, which on this particular struct would mean accepting a
    /// `--cov-branch` request and measuring lines.
    #[test]
    fn the_coverage_object_is_strict_about_its_own_fields() {
        for (field, object) in [
            ("sources", r#"{"data_dir":"/tmp/cov"}"#),
            ("data_dir", r#"{"sources":["/repo/src"]}"#),
        ] {
            let line = INIT_LINE.replace(
                r#""asyncio_default_test_loop_scope":"session""#,
                &format!(r#""asyncio_default_test_loop_scope":"session","coverage":{object}"#),
            );
            let err = serde_json::from_str::<WorkerRequest>(&line)
                .unwrap_err_or_panic(field, "request", &line);
            assert!(
                err.contains(field),
                "error should name the missing `{field}`, got: {err}"
            );
        }

        let line = INIT_LINE.replace(
            r#""asyncio_default_test_loop_scope":"session""#,
            r#""asyncio_default_test_loop_scope":"session","coverage":{"sources":[],"data_dir":"/tmp/cov","branch":true}"#,
        );
        let err = serde_json::from_str::<WorkerRequest>(&line)
            .expect_err("an unknown coverage field must not decode");
        assert!(
            err.to_string().contains("branch"),
            "error should name the unknown field, got: {err}"
        );
    }

    /// The Tier D shape: **no `assert_key` key at all** (not `null`), so every line a
    /// pre-v3 orchestrator would have written is still byte-for-byte what v3 writes.
    #[test]
    fn collect_file_request_matches_golden_contract() {
        let request = WorkerRequest::CollectFile {
            path: "/repo/tests/test_math.py".to_string(),
            assert_key: None,
        };

        assert_eq!(
            serde_json::to_string(&request).expect("collect_file serializes"),
            COLLECT_FILE_LINE
        );
        assert!(!COLLECT_FILE_LINE.contains(r#""assert_key":"#));

        // Decoding the golden line — which carries no `assert_key` — exercises the `default`,
        // so dropping it is a test failure rather than a silent change.
        let decoded: WorkerRequest =
            serde_json::from_str(COLLECT_FILE_LINE).expect("collect_file deserializes");
        assert_eq!(decoded, request);
    }

    /// The Tier S shape: the key present, and the *omission rule inverted* — a worker that
    /// ignored this field would import the module unrewritten and silently give the user
    /// pytest's old bare `AssertionError`, which is a quality regression no test of the
    /// manifest would ever notice.
    #[test]
    fn collect_file_request_carries_the_assert_key_when_the_file_is_rewritable() {
        let request = WorkerRequest::CollectFile {
            path: "/repo/tests/test_math.py".to_string(),
            assert_key: Some(
                "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef".to_string(),
            ),
        };

        assert_eq!(
            serde_json::to_string(&request).expect("collect_file serializes"),
            COLLECT_FILE_REWRITE_LINE
        );

        let decoded: WorkerRequest = serde_json::from_str(COLLECT_FILE_REWRITE_LINE)
            .expect("collect_file with a key deserializes");
        assert_eq!(decoded, request);

        // An explicit `null` decodes as the absent key — the same documented tolerance the
        // `collected` and `test_result` omissions carry, stated so it is not discovered by
        // accident later.
        let explicit_null: WorkerRequest = serde_json::from_str(
            r#"{"op":"collect_file","path":"/repo/tests/test_math.py","assert_key":null}"#,
        )
        .expect("an explicit null assert_key deserializes");
        assert_eq!(
            explicit_null,
            WorkerRequest::CollectFile {
                path: "/repo/tests/test_math.py".to_string(),
                assert_key: None,
            }
        );
    }

    #[test]
    fn execute_test_request_matches_golden_contract() {
        let request = WorkerRequest::ExecuteTest {
            id: "tests/test_math.py::test_add".to_string(),
        };

        assert_eq!(
            serde_json::to_string(&request).expect("execute_test serializes"),
            EXECUTE_TEST_LINE
        );

        let decoded: WorkerRequest =
            serde_json::from_str(EXECUTE_TEST_LINE).expect("execute_test deserializes");
        assert_eq!(decoded, request);

        // The addressing unit is the **manifest id**, not a path plus a qualname: the
        // orchestrator already holds ids from collection, and re-deriving one worker-side
        // would be a second, divergent implementation of `nodeid.rs`.
        assert!(!EXECUTE_TEST_LINE.contains(r#""path":"#));
        assert!(!EXECUTE_TEST_LINE.contains(r#""qualname":"#));
    }

    /// `ids` is an **array**, `stop_on_failure` a **bool**, and both are always present —
    /// there is no "empty means all" and no defaulted flag, because either would let a
    /// truncated line decode as a well-formed request that runs the wrong set of tests.
    #[test]
    fn execute_batch_request_matches_golden_contract() {
        let request = WorkerRequest::ExecuteBatch {
            ids: vec![
                "tests/test_math.py::test_add".to_string(),
                "tests/test_math.py::test_sub".to_string(),
            ],
            stop_on_failure: false,
            max_fail: None,
        };

        assert_eq!(
            serde_json::to_string(&request).expect("execute_batch serializes"),
            EXECUTE_BATCH_LINE
        );

        let decoded: WorkerRequest =
            serde_json::from_str(EXECUTE_BATCH_LINE).expect("execute_batch deserializes");
        assert_eq!(decoded, request);

        // Same addressing rule as `execute_test`: manifest ids, never paths or qualnames.
        assert!(!EXECUTE_BATCH_LINE.contains(r#""path":"#));
        assert!(!EXECUTE_BATCH_LINE.contains(r#""qualname":"#));

        // `stop_on_failure` is serialised even when false. A `skip_serializing_if` here
        // would make "the flag is off" and "the peer forgot the flag" the same bytes, on the
        // one field whose absence silently disables `-x` inside a batch.
        assert!(EXECUTE_BATCH_LINE.contains(r#""stop_on_failure":false"#));
    }

    #[test]
    fn batch_done_response_matches_golden_contract() {
        let response = WorkerResponse::BatchDone {
            executed: 2,
            stopped: false,
        };

        assert_eq!(
            serde_json::to_string(&response).expect("batch_done serializes"),
            BATCH_DONE_LINE
        );

        let decoded: WorkerResponse =
            serde_json::from_str(BATCH_DONE_LINE).expect("batch_done deserializes");
        assert_eq!(decoded, response);

        // Both fields always on the wire, for the reason the variant's docs give: `executed`
        // is the independent count that turns a lost result into an error, and `stopped`
        // separates "`-x` fired" from "results went missing".
        assert!(BATCH_DONE_LINE.contains(r#""executed":2"#));
        assert!(BATCH_DONE_LINE.contains(r#""stopped":false"#));

        let stopped = WorkerResponse::BatchDone {
            executed: 1,
            stopped: true,
        };
        assert_eq!(
            serde_json::to_string(&stopped).expect("batch_done serializes"),
            r#"{"op":"batch_done","executed":1,"stopped":true}"#
        );
    }

    #[test]
    fn shutdown_request_matches_golden_contract() {
        assert_eq!(
            serde_json::to_string(&WorkerRequest::Shutdown).expect("shutdown serializes"),
            SHUTDOWN_LINE
        );

        let decoded: WorkerRequest =
            serde_json::from_str(SHUTDOWN_LINE).expect("shutdown deserializes");
        assert_eq!(decoded, WorkerRequest::Shutdown);
    }

    // --- responses --------------------------------------------------------

    const READY_LINE: &str = r#"{"op":"ready","protocol_version":7}"#;
    const COLLECTED_TESTS_LINE: &str = r#"{"op":"collected","path":"/repo/tests/test_math.py","tests":[{"id":"tests/test_math.py::test_add","path":"tests/test_math.py","qualname":"test_add"}]}"#;
    const COLLECTED_ERROR_LINE: &str = r#"{"op":"collected","path":"/repo/tests/test_broken.py","error":{"path":"tests/test_broken.py","message":"ImportError: No module named 'nope'"}}"#;
    const TEST_RESULT_PASSED_LINE: &str = r#"{"op":"test_result","id":"tests/test_math.py::test_add","status":"passed","duration_s":0.125}"#;
    const TEST_RESULT_FAILED_LINE: &str = r#"{"op":"test_result","id":"tests/test_math.py::test_add","status":"failed","duration_s":1.5,"message":"assert 1 == 2","stdout":"computing\n","stderr":"deprecated\n"}"#;
    const BATCH_DONE_LINE: &str = r#"{"op":"batch_done","executed":2,"stopped":false}"#;
    const BYE_LINE: &str = r#"{"op":"bye"}"#;

    /// The closed set the `status` field documents.  Mirrors pytest's *reporting
    /// categories* (`_pytest/runner.py`, `_pytest/skipping.py`, `_pytest/terminal.py`
    /// `pytest_report_teststatus`), not `TestReport.outcome`, which has only three values.
    const DOCUMENTED_STATUSES: [&str; 6] =
        ["passed", "failed", "skipped", "xfailed", "xpassed", "error"];

    fn sample_collected_with_tests() -> WorkerResponse {
        WorkerResponse::Collected {
            path: "/repo/tests/test_math.py".to_string(),
            tests: vec![CollectedTest {
                id: "tests/test_math.py::test_add".to_string(),
                path: "tests/test_math.py".to_string(),
                qualname: "test_add".to_string(),
                class_name: None,
                param_id: None,
                marks: Vec::new(),
                fixtures: Vec::new(),
                tier: crate::v2::manifest::Tier::Dynamic,
            }],
            error: None,
        }
    }

    fn sample_collected_with_error() -> WorkerResponse {
        WorkerResponse::Collected {
            path: "/repo/tests/test_broken.py".to_string(),
            tests: Vec::new(),
            error: Some(CollectionErrorEntry {
                path: "tests/test_broken.py".to_string(),
                message: "ImportError: No module named 'nope'".to_string(),
            }),
        }
    }

    fn sample_test_result_passed() -> WorkerResponse {
        WorkerResponse::TestResult {
            id: "tests/test_math.py::test_add".to_string(),
            status: "passed".to_string(),
            duration_s: 0.125,
            message: None,
            stdout: None,
            stderr: None,
        }
    }

    fn sample_test_result_failed() -> WorkerResponse {
        WorkerResponse::TestResult {
            id: "tests/test_math.py::test_add".to_string(),
            status: "failed".to_string(),
            duration_s: 1.5,
            message: Some("assert 1 == 2".to_string()),
            stdout: Some("computing\n".to_string()),
            stderr: Some("deprecated\n".to_string()),
        }
    }

    #[test]
    fn ready_response_matches_golden_contract() {
        let response = WorkerResponse::Ready {
            protocol_version: PROTOCOL_VERSION,
        };

        assert_eq!(
            serde_json::to_string(&response).expect("ready serializes"),
            READY_LINE
        );

        let decoded: WorkerResponse = serde_json::from_str(READY_LINE).expect("ready deserializes");
        assert_eq!(decoded, response);
    }

    /// Success shape: `tests` present, and **no `error` key at all** (not `null`).
    #[test]
    fn collected_with_tests_omits_the_error_key() {
        assert_eq!(
            serde_json::to_string(&sample_collected_with_tests()).expect("collected serializes"),
            COLLECTED_TESTS_LINE
        );
        assert!(!COLLECTED_TESTS_LINE.contains(r#""error":"#));

        // Decoding the golden line — which carries no `error` key — exercises the
        // `default` on `error`, so dropping it is a test failure, not a silent change.
        let decoded: WorkerResponse =
            serde_json::from_str(COLLECTED_TESTS_LINE).expect("collected deserializes");
        assert_eq!(decoded, sample_collected_with_tests());

        // Tolerance, documented so Task 2 does not discover it by accident: an explicit
        // `"error":null` decodes identically to the omitted key.  A producer that always
        // emits both keys is therefore *accepted* here while still violating the pinned
        // wire form above — which is why the golden string, not the decoder, is authority.
        let with_explicit_null: WorkerResponse = serde_json::from_str(
            r#"{"op":"collected","path":"/repo/tests/test_math.py","tests":[{"id":"tests/test_math.py::test_add","path":"tests/test_math.py","qualname":"test_add"}],"error":null}"#,
        )
        .expect("explicit null error deserializes");
        assert_eq!(with_explicit_null, sample_collected_with_tests());
    }

    /// Failure shape: `error` present, and **no `tests` key at all** (not `[]`).
    #[test]
    fn collected_with_error_omits_the_tests_key() {
        assert_eq!(
            serde_json::to_string(&sample_collected_with_error()).expect("collected serializes"),
            COLLECTED_ERROR_LINE
        );
        // Quoted-key form: the sample path legitimately contains the substring `tests`.
        assert!(!COLLECTED_ERROR_LINE.contains(r#""tests":"#));

        // Decoding the golden line — which carries no `tests` key — exercises the
        // `default` on `tests`.
        let decoded: WorkerResponse =
            serde_json::from_str(COLLECTED_ERROR_LINE).expect("collected deserializes");
        assert_eq!(decoded, sample_collected_with_error());

        // Mirror tolerance: an explicit `"tests":[]` decodes identically to the omitted
        // key.  Same caveat as above — accepted by the decoder, still off-contract.
        let with_explicit_empty: WorkerResponse = serde_json::from_str(
            r#"{"op":"collected","path":"/repo/tests/test_broken.py","tests":[],"error":{"path":"tests/test_broken.py","message":"ImportError: No module named 'nope'"}}"#,
        )
        .expect("explicit empty tests deserializes");
        assert_eq!(with_explicit_empty, sample_collected_with_error());
    }

    /// Populated shape: every optional field carried, in declaration order.
    #[test]
    fn test_result_with_every_field_matches_golden_contract() {
        assert_eq!(
            serde_json::to_string(&sample_test_result_failed()).expect("test_result serializes"),
            TEST_RESULT_FAILED_LINE
        );

        let decoded: WorkerResponse =
            serde_json::from_str(TEST_RESULT_FAILED_LINE).expect("test_result deserializes");
        assert_eq!(decoded, sample_test_result_failed());

        // `duration_s` is a JSON **number**, never a string: the orchestrator sums these.
        assert!(TEST_RESULT_FAILED_LINE.contains(r#""duration_s":1.5"#));
    }

    /// Omission shape: a clean pass carries **no `message`/`stdout`/`stderr` keys at all**
    /// (not `null`, not `""`), so the common case is the short line.
    #[test]
    fn test_result_omits_the_optional_fields_it_does_not_carry() {
        assert_eq!(
            serde_json::to_string(&sample_test_result_passed()).expect("test_result serializes"),
            TEST_RESULT_PASSED_LINE
        );
        for key in [r#""message":"#, r#""stdout":"#, r#""stderr":"#] {
            assert!(
                !TEST_RESULT_PASSED_LINE.contains(key),
                "the passing shape must not carry {key}"
            );
        }

        // Decoding the golden line — which carries none of the three keys — exercises the
        // `default` on each, so dropping one is a test failure, not a silent change.
        let decoded: WorkerResponse =
            serde_json::from_str(TEST_RESULT_PASSED_LINE).expect("test_result deserializes");
        assert_eq!(decoded, sample_test_result_passed());

        // Same documented tolerance as `collected`: explicit nulls decode identically to
        // the omitted keys, so a noisy producer is *accepted* by the decoder while still
        // violating the pinned wire form above.  The golden string is the authority.
        let with_explicit_nulls: WorkerResponse = serde_json::from_str(
            r#"{"op":"test_result","id":"tests/test_math.py::test_add","status":"passed","duration_s":0.125,"message":null,"stdout":null,"stderr":null}"#,
        )
        .expect("explicit nulls deserialize");
        assert_eq!(with_explicit_nulls, sample_test_result_passed());
    }

    /// All six documented statuses are ordinary strings on the wire — no aliasing, no
    /// renaming, and `duration_s` keeps its `.0` for a whole number (a JSON `0` would be a
    /// different token, and this pins that the encoder never emits one).
    #[test]
    fn every_documented_status_round_trips() {
        for status in DOCUMENTED_STATUSES {
            let response = WorkerResponse::TestResult {
                id: "t.py::test_a".to_string(),
                status: status.to_string(),
                duration_s: 0.0,
                message: None,
                stdout: None,
                stderr: None,
            };

            let encoded = serde_json::to_string(&response).expect("test_result serializes");
            assert_eq!(
                encoded,
                format!(
                    r#"{{"op":"test_result","id":"t.py::test_a","status":"{status}","duration_s":0.0}}"#
                )
            );

            let decoded: WorkerResponse =
                serde_json::from_str(&encoded).expect("test_result deserializes");
            assert_eq!(decoded, response);
        }
    }

    /// **Deliberate non-validation, pinned so it is never mistaken for an oversight.**
    /// `status` is a `String`, so a value outside the documented six decodes cleanly.
    ///
    /// Rejecting it here would produce serde's "unknown variant" error against a raw line,
    /// with no test id in hand and no way to keep running; the orchestrator holds both, so
    /// it validates on receipt and reports the offending id (the same division of labour as
    /// the hybrid `collected` shape).  This test is the one to invert if `status` ever
    /// becomes an enum.
    #[test]
    fn an_unknown_status_decodes_and_must_be_rejected_by_the_orchestrator() {
        let decoded: WorkerResponse = serde_json::from_str(
            r#"{"op":"test_result","id":"t.py::test_a","status":"exploded","duration_s":0.0}"#,
        )
        .expect("an undocumented status decodes — validation lives one layer up");

        let WorkerResponse::TestResult { status, .. } = decoded else {
            panic!("expected a TestResult response");
        };
        assert_eq!(
            status, "exploded",
            "the raw status reaches the orchestrator"
        );
    }

    /// A JSON integer decodes into `duration_s` — serde widens it.  Pinned because a
    /// producer that computes an exact-zero duration and emits `0` is *accepted*, so no
    /// worker is ever refused over a token that carries the same value.
    #[test]
    fn an_integer_duration_decodes_as_a_float() {
        let decoded: WorkerResponse = serde_json::from_str(
            r#"{"op":"test_result","id":"t.py::test_a","status":"passed","duration_s":0}"#,
        )
        .expect("an integer duration deserializes");

        let WorkerResponse::TestResult { duration_s, .. } = decoded else {
            panic!("expected a TestResult response");
        };
        assert_eq!(duration_s, 0.0);
    }

    #[test]
    fn bye_response_matches_golden_contract() {
        assert_eq!(
            serde_json::to_string(&WorkerResponse::Bye).expect("bye serializes"),
            BYE_LINE
        );

        let decoded: WorkerResponse = serde_json::from_str(BYE_LINE).expect("bye deserializes");
        assert_eq!(decoded, WorkerResponse::Bye);
    }

    // --- version skew -----------------------------------------------------

    /// A worker one version ahead may send an op this build has never heard of.
    /// That must be a hard decode error, never a silently dropped message.
    #[test]
    fn unknown_response_op_is_a_hard_error() {
        let err = serde_json::from_str::<WorkerResponse>(r#"{"op":"progress","done":3}"#)
            .expect_err("unknown response op must not decode");
        assert!(
            err.to_string().contains("progress"),
            "error should name the unknown op, got: {err}"
        );
    }

    /// Same in the other direction: an orchestrator one version ahead.
    #[test]
    fn unknown_request_op_is_a_hard_error() {
        let err = serde_json::from_str::<WorkerRequest>(r#"{"op":"collect_dir","path":"/repo"}"#)
            .expect_err("unknown request op must not decode");
        assert!(
            err.to_string().contains("collect_dir"),
            "error should name the unknown op, got: {err}"
        );
    }

    /// A response whose `op` is absent is not a `Collected` with defaults — it is a
    /// decode error, so a truncated or malformed line can never masquerade as data.
    #[test]
    fn missing_op_is_a_hard_error() {
        assert!(serde_json::from_str::<WorkerResponse>(r#"{"path":"/repo/t.py"}"#).is_err());
        assert!(serde_json::from_str::<WorkerRequest>(r#"{"path":"/repo/t.py"}"#).is_err());
    }

    /// The exact `init` line a **v1 orchestrator** sends: right op, right version field,
    /// no `invocation_dir`.  It must not decode.
    ///
    /// This is the single most likely wrong line to arrive on a v2 worker's stdin, and the
    /// one shape where a missing field would be silently *plausible* — a decoder that
    /// defaulted `invocation_dir` to `""` would hand every worker an empty invocation
    /// directory and only misbehave later, in whatever fixture resolved a path against it.
    #[test]
    fn a_v1_shaped_init_line_is_a_hard_error() {
        let err = serde_json::from_str::<WorkerRequest>(
            r#"{"op":"init","protocol_version":1,"rootdir":"/repo","python_files":["test_*.py"],"python_classes":["Test*"],"python_functions":["test*"]}"#,
        )
        .expect_err("a v1 init line must not decode");
        assert!(
            err.to_string().contains("invocation_dir"),
            "error should name the missing field, got: {err}"
        );
    }

    /// **Every non-optional field is required**, in both directions.  Serde gives that for
    /// free — right up until someone adds `#[serde(default)]` to quiet a producer, at which
    /// point a truncated line decodes as a well-formed message carrying `""` or `0.0`.  The
    /// `default`s that *are* deliberate (`tests`, `error`, `message`, `stdout`, `stderr`)
    /// are pinned by the omission goldens above; this pins the complement, so the two
    /// together say exactly which fields may be absent.
    ///
    /// Each line below is a real golden with **one key removed** — nothing else — so a
    /// failure here names precisely the field that stopped being required.
    #[test]
    fn every_non_optional_field_is_required() {
        let requests = [
            (
                "protocol_version",
                r#"{"op":"init","rootdir":"/repo","invocation_dir":"/repo/tests","python_files":["test_*.py"],"python_classes":["Test*"],"python_functions":["test*"]}"#,
            ),
            (
                "rootdir",
                r#"{"op":"init","protocol_version":2,"invocation_dir":"/repo/tests","python_files":["test_*.py"],"python_classes":["Test*"],"python_functions":["test*"],"asyncio_mode":"auto","asyncio_default_test_loop_scope":"function"}"#,
            ),
            (
                "invocation_dir",
                r#"{"op":"init","protocol_version":2,"rootdir":"/repo","python_files":["test_*.py"],"python_classes":["Test*"],"python_functions":["test*"],"asyncio_mode":"auto","asyncio_default_test_loop_scope":"function"}"#,
            ),
            (
                "python_files",
                r#"{"op":"init","protocol_version":2,"rootdir":"/repo","invocation_dir":"/repo/tests","python_classes":["Test*"],"python_functions":["test*"],"asyncio_mode":"auto","asyncio_default_test_loop_scope":"function"}"#,
            ),
            // The two asyncio fields that are NOT optional.  A worker that defaulted them
            // would apply `auto`/`function` to a suite whose ini said otherwise, and the
            // resulting wrong-loop failures would point at the test, not at the handshake.
            (
                "asyncio_mode",
                r#"{"op":"init","protocol_version":4,"rootdir":"/repo","invocation_dir":"/repo/tests","python_files":["test_*.py"],"python_classes":["Test*"],"python_functions":["test*"],"asyncio_default_test_loop_scope":"function"}"#,
            ),
            (
                "asyncio_default_test_loop_scope",
                r#"{"op":"init","protocol_version":4,"rootdir":"/repo","invocation_dir":"/repo/tests","python_files":["test_*.py"],"python_classes":["Test*"],"python_functions":["test*"],"asyncio_mode":"auto"}"#,
            ),
            ("path", r#"{"op":"collect_file"}"#),
            ("id", r#"{"op":"execute_test"}"#),
            ("ids", r#"{"op":"execute_batch","stop_on_failure":false}"#),
            (
                "stop_on_failure",
                r#"{"op":"execute_batch","ids":["t.py::test_a"]}"#,
            ),
        ];
        for (field, line) in requests {
            let err = serde_json::from_str::<WorkerRequest>(line)
                .unwrap_err_or_panic(field, "request", line);
            assert!(
                err.contains(field),
                "error should name the missing `{field}`, got: {err}"
            );
        }

        let responses = [
            ("protocol_version", r#"{"op":"ready"}"#),
            ("path", r#"{"op":"collected","tests":[]}"#),
            (
                "id",
                r#"{"op":"test_result","status":"passed","duration_s":0.125}"#,
            ),
            (
                "status",
                r#"{"op":"test_result","id":"t.py::test_a","duration_s":0.125}"#,
            ),
            (
                "duration_s",
                r#"{"op":"test_result","id":"t.py::test_a","status":"passed"}"#,
            ),
            ("executed", r#"{"op":"batch_done","stopped":false}"#),
            ("stopped", r#"{"op":"batch_done","executed":0}"#),
        ];
        for (field, line) in responses {
            let err = serde_json::from_str::<WorkerResponse>(line)
                .unwrap_err_or_panic(field, "response", line);
            assert!(
                err.contains(field),
                "error should name the missing `{field}`, got: {err}"
            );
        }
    }

    /// Turns "this line decoded when it should not have" into a failure that says *which*
    /// line and *which* field, instead of serde's `called `Result::unwrap_err()` on an `Ok`.
    trait RequiredFieldExt {
        fn unwrap_err_or_panic(self, field: &str, direction: &str, line: &str) -> String;
    }

    impl<T: std::fmt::Debug> RequiredFieldExt for Result<T, serde_json::Error> {
        fn unwrap_err_or_panic(self, field: &str, direction: &str, line: &str) -> String {
            match self {
                Err(err) => err.to_string(),
                Ok(decoded) => {
                    panic!("a {direction} missing `{field}` decoded: {decoded:?}\n  line: {line}")
                }
            }
        }
    }

    /// `deny_unknown_fields`, the reason it is worth having: a `"tsets"` typo in a worker
    /// would otherwise decode as a flawless, empty collection and silently lose every test
    /// in the file.  Field drift must be as loud as op drift.  Asserted for both enums —
    /// the attribute has to be on each one.
    #[test]
    fn unknown_field_is_a_hard_error() {
        let err = serde_json::from_str::<WorkerResponse>(
            r#"{"op":"collected","path":"/a/t.py","tsets":[]}"#,
        )
        .expect_err("unknown response field must not decode");
        assert!(
            err.to_string().contains("tsets"),
            "error should name the unknown field, got: {err}"
        );

        let err = serde_json::from_str::<WorkerRequest>(
            r#"{"op":"collect_file","path":"/a/t.py","recurse":true}"#,
        )
        .expect_err("unknown request field must not decode");
        assert!(
            err.to_string().contains("recurse"),
            "error should name the unknown field, got: {err}"
        );
    }

    /// The same rule on the v2 variants, asserted rather than assumed: `deny_unknown_fields`
    /// is an *enum*-level attribute, so it covers a newly added variant automatically — and
    /// the way to keep that true is to have a test that fails if a variant is ever split out
    /// with its own `Deserialize`.  The plausible drift is a field a future revision adds
    /// (`timeout_s`, `phase`) reaching a build that predates it.
    #[test]
    fn unknown_field_on_the_execute_ops_is_a_hard_error() {
        let err = serde_json::from_str::<WorkerRequest>(
            r#"{"op":"execute_test","id":"t.py::test_a","timeout_s":30}"#,
        )
        .expect_err("unknown execute_test field must not decode");
        assert!(
            err.to_string().contains("timeout_s"),
            "error should name the unknown field, got: {err}"
        );

        let err = serde_json::from_str::<WorkerResponse>(
            r#"{"op":"test_result","id":"t.py::test_a","status":"passed","duration_s":0.0,"phase":"call"}"#,
        )
        .expect_err("unknown test_result field must not decode");
        assert!(
            err.to_string().contains("phase"),
            "error should name the unknown field, got: {err}"
        );
    }

    /// The same rule on the **v3** ops.  The plausible drift here is real rather than
    /// hypothetical: `assert_key` and `stop_on_failure` were both added by one commit, and a
    /// half-applied upgrade — new orchestrator, old worker, or the reverse — is exactly how a
    /// field arrives at a build that predates it.
    #[test]
    fn unknown_field_on_the_v3_ops_is_a_hard_error() {
        let err = serde_json::from_str::<WorkerRequest>(
            r#"{"op":"collect_file","path":"/a/t.py","assert_keys":"deadbeef"}"#,
        )
        .expect_err("unknown collect_file field must not decode");
        assert!(
            err.to_string().contains("assert_keys"),
            "error should name the unknown field, got: {err}"
        );

        let err = serde_json::from_str::<WorkerRequest>(
            r#"{"op":"execute_batch","ids":["t.py::test_a"],"stop_on_failure":false,"chunk":4}"#,
        )
        .expect_err("unknown execute_batch field must not decode");
        assert!(
            err.to_string().contains("chunk"),
            "error should name the unknown field, got: {err}"
        );

        let err = serde_json::from_str::<WorkerResponse>(
            r#"{"op":"batch_done","executed":1,"stopped":false,"elapsed_s":0.5}"#,
        )
        .expect_err("unknown batch_done field must not decode");
        assert!(
            err.to_string().contains("elapsed_s"),
            "error should name the unknown field, got: {err}"
        );
    }

    /// A **v2 worker** answering a v3 orchestrator would never send `batch_done`, and a v2
    /// orchestrator would never send `execute_batch`.  Neither line may decode on the other
    /// side of the version boundary — asserted here so the handshake is not the only thing
    /// standing between the two, since a handshake only runs once and a bug can reintroduce
    /// an old peer at any point in the stream.
    #[test]
    fn the_v2_ops_and_the_v3_ops_are_not_interchangeable() {
        // A v2 orchestrator's `collect_file` still decodes — the field is additive, and that
        // is the point of `default` — but it decodes as "no rewriting", never as a key.
        let v2_collect: WorkerRequest =
            serde_json::from_str(r#"{"op":"collect_file","path":"/a/t.py"}"#)
                .expect("a v2 collect_file line still decodes");
        assert_eq!(
            v2_collect,
            WorkerRequest::CollectFile {
                path: "/a/t.py".to_string(),
                assert_key: None,
            }
        );

        // ...whereas the ops themselves are not forward-compatible in the other direction.
        assert!(serde_json::from_str::<WorkerResponse>(r#"{"op":"batch_finished"}"#).is_err());
        assert!(serde_json::from_str::<WorkerRequest>(r#"{"op":"execute_tests"}"#).is_err());
    }

    /// **Documented tolerance, not coverage.**  `Collected` is a product type, so a line
    /// carrying both `tests` and `error` — the one malformed shape serde cannot reject
    /// without a hand-written `Deserialize` — decodes cleanly with both fields populated.
    ///
    /// Task 3's orchestrator therefore MUST validate `collected` on receipt and treat the
    /// hybrid as protocol-fatal, exactly as it treats a decode error.  If that check is
    /// ever added here as a custom `Deserialize`, this test is the one to invert.
    #[test]
    fn hybrid_collected_decodes_and_must_be_rejected_by_the_orchestrator() {
        let hybrid: WorkerResponse = serde_json::from_str(
            r#"{"op":"collected","path":"/a/t.py","tests":[{"id":"t.py::test_a","path":"t.py","qualname":"test_a"}],"error":{"path":"t.py","message":"boom"}}"#,
        )
        .expect("the hybrid shape decodes — serde cannot reject it here");

        let WorkerResponse::Collected { tests, error, .. } = hybrid else {
            panic!("expected a Collected response");
        };
        assert_eq!(tests.len(), 1, "hybrid keeps the tests it carried");
        assert!(error.is_some(), "hybrid keeps the error it carried");
    }

    // --- framing ----------------------------------------------------------

    /// The transport is JSON-lines: every message must fit on one line, so no encoded form
    /// may contain a raw newline.
    ///
    /// The sample error carries a **multi-line message** — a traceback is the realistic
    /// payload, and `collected` carries no other field that can plausibly contain a
    /// newline.  Without it this test would pass vacuously, never exercising serde's
    /// escaping path.  `test_result` widens that exposure by three: a failure message, and
    /// whole captured `stdout`/`stderr` streams, are multi-line by nature.
    #[test]
    fn every_message_encodes_to_a_single_line() {
        let requests = [
            sample_init(),
            WorkerRequest::CollectFile {
                path: "/repo/tests/test_math.py".to_string(),
                assert_key: None,
            },
            WorkerRequest::ExecuteTest {
                id: "tests/test_math.py::test_add".to_string(),
            },
            WorkerRequest::ExecuteBatch {
                ids: vec![
                    "tests/test_math.py::test_add".to_string(),
                    "tests/test_math.py::test_sub".to_string(),
                ],
                stop_on_failure: true,
                max_fail: None,
            },
            WorkerRequest::Shutdown,
        ];
        for request in &requests {
            let encoded = serde_json::to_string(request).expect("request serializes");
            assert!(!encoded.contains('\n'), "multi-line request: {encoded}");
        }

        let traceback = WorkerResponse::Collected {
            path: "/repo/tests/test_broken.py".to_string(),
            tests: Vec::new(),
            error: Some(CollectionErrorEntry {
                path: "tests/test_broken.py".to_string(),
                message: "Traceback (most recent call last):\n  File \"t.py\", line 1\n    import nope\nModuleNotFoundError: No module named 'nope'".to_string(),
            }),
        };

        let multi_line_result = WorkerResponse::TestResult {
            id: "tests/test_math.py::test_add".to_string(),
            status: "failed".to_string(),
            duration_s: 0.5,
            message: Some(
                "Traceback (most recent call last):\n  File \"t.py\", line 2\nAssertionError"
                    .to_string(),
            ),
            stdout: Some("first\nsecond\n".to_string()),
            stderr: Some("warning: one\nwarning: two\n".to_string()),
        };

        let responses = [
            WorkerResponse::Ready {
                protocol_version: PROTOCOL_VERSION,
            },
            sample_collected_with_tests(),
            sample_collected_with_error(),
            traceback.clone(),
            sample_test_result_passed(),
            multi_line_result.clone(),
            WorkerResponse::BatchDone {
                executed: 2,
                stopped: false,
            },
            WorkerResponse::Bye,
        ];
        for response in &responses {
            let encoded = serde_json::to_string(response).expect("response serializes");
            assert!(!encoded.contains('\n'), "multi-line response: {encoded}");
        }

        // ...and the escaped newlines survive the round trip, so a traceback — or a whole
        // captured stream — reaches the orchestrator intact rather than truncated at the
        // first line break.
        for response in [&traceback, &multi_line_result] {
            let encoded = serde_json::to_string(response).expect("response serializes");
            assert!(
                encoded.contains(r"\n"),
                "newlines must be escaped, not dropped: {encoded}"
            );
            let decoded: WorkerResponse =
                serde_json::from_str(&encoded).expect("response deserializes");
            assert_eq!(&decoded, response);
        }
    }
}
