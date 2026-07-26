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
/// `python/rustest/_v2_worker.py` mirrors this constant and **must be bumped in the same
/// commit**; a worker still declaring the old number turns every run into a handshake
/// error.
pub const PROTOCOL_VERSION: u32 = 2;

/// A message from the orchestrator to a worker, one per stdin line.
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
    },
    /// Collect one file. path: absolute posix.
    CollectFile { path: String },
    /// Execute one collected test by manifest id.
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
        }
    }

    const INIT_LINE: &str = r#"{"op":"init","protocol_version":2,"rootdir":"/repo","invocation_dir":"/repo/tests","python_files":["test_*.py","*_test.py"],"python_classes":["Test*"],"python_functions":["test*"]}"#;
    const COLLECT_FILE_LINE: &str = r#"{"op":"collect_file","path":"/repo/tests/test_math.py"}"#;
    const EXECUTE_TEST_LINE: &str = r#"{"op":"execute_test","id":"tests/test_math.py::test_add"}"#;
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

    #[test]
    fn collect_file_request_matches_golden_contract() {
        let request = WorkerRequest::CollectFile {
            path: "/repo/tests/test_math.py".to_string(),
        };

        assert_eq!(
            serde_json::to_string(&request).expect("collect_file serializes"),
            COLLECT_FILE_LINE
        );

        let decoded: WorkerRequest =
            serde_json::from_str(COLLECT_FILE_LINE).expect("collect_file deserializes");
        assert_eq!(decoded, request);
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

    const READY_LINE: &str = r#"{"op":"ready","protocol_version":2}"#;
    const COLLECTED_TESTS_LINE: &str = r#"{"op":"collected","path":"/repo/tests/test_math.py","tests":[{"id":"tests/test_math.py::test_add","path":"tests/test_math.py","qualname":"test_add"}]}"#;
    const COLLECTED_ERROR_LINE: &str = r#"{"op":"collected","path":"/repo/tests/test_broken.py","error":{"path":"tests/test_broken.py","message":"ImportError: No module named 'nope'"}}"#;
    const TEST_RESULT_PASSED_LINE: &str = r#"{"op":"test_result","id":"tests/test_math.py::test_add","status":"passed","duration_s":0.125}"#;
    const TEST_RESULT_FAILED_LINE: &str = r#"{"op":"test_result","id":"tests/test_math.py::test_add","status":"failed","duration_s":1.5,"message":"assert 1 == 2","stdout":"computing\n","stderr":"deprecated\n"}"#;
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
                r#"{"op":"init","protocol_version":2,"invocation_dir":"/repo/tests","python_files":["test_*.py"],"python_classes":["Test*"],"python_functions":["test*"]}"#,
            ),
            (
                "invocation_dir",
                r#"{"op":"init","protocol_version":2,"rootdir":"/repo","python_files":["test_*.py"],"python_classes":["Test*"],"python_functions":["test*"]}"#,
            ),
            (
                "python_files",
                r#"{"op":"init","protocol_version":2,"rootdir":"/repo","invocation_dir":"/repo/tests","python_classes":["Test*"],"python_functions":["test*"]}"#,
            ),
            ("path", r#"{"op":"collect_file"}"#),
            ("id", r#"{"op":"execute_test"}"#),
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
            },
            WorkerRequest::ExecuteTest {
                id: "tests/test_math.py::test_add".to_string(),
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
