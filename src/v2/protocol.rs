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
//! exactly as it treats a decode error**.  The test module below documents that tolerance
//! explicitly so it is never mistaken for coverage.
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
pub const PROTOCOL_VERSION: u32 = 1;

/// A message from the orchestrator to a worker, one per stdin line.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum WorkerRequest {
    /// Sent once as the first line of a worker's stdin.
    Init {
        protocol_version: u32,
        /// Absolute posix rootdir (nodeids are relative to it).
        rootdir: String,
        /// Naming rules from ResolvedConfig, passed through verbatim.
        python_files: Vec<String>,
        python_classes: Vec<String>,
        python_functions: Vec<String>,
    },
    /// Collect one file. path: absolute posix.
    CollectFile { path: String },
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
            python_files: vec!["test_*.py".to_string(), "*_test.py".to_string()],
            python_classes: vec!["Test*".to_string()],
            python_functions: vec!["test*".to_string()],
        }
    }

    const INIT_LINE: &str = r#"{"op":"init","protocol_version":1,"rootdir":"/repo","python_files":["test_*.py","*_test.py"],"python_classes":["Test*"],"python_functions":["test*"]}"#;
    const COLLECT_FILE_LINE: &str = r#"{"op":"collect_file","path":"/repo/tests/test_math.py"}"#;
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

    const READY_LINE: &str = r#"{"op":"ready","protocol_version":1}"#;
    const COLLECTED_TESTS_LINE: &str = r#"{"op":"collected","path":"/repo/tests/test_math.py","tests":[{"id":"tests/test_math.py::test_add","path":"tests/test_math.py","qualname":"test_add"}]}"#;
    const COLLECTED_ERROR_LINE: &str = r#"{"op":"collected","path":"/repo/tests/test_broken.py","error":{"path":"tests/test_broken.py","message":"ImportError: No module named 'nope'"}}"#;
    const BYE_LINE: &str = r#"{"op":"bye"}"#;

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
    /// payload, and it is the only field on the wire that can plausibly contain a newline.
    /// Without it this test would pass vacuously, never exercising serde's escaping path.
    #[test]
    fn every_message_encodes_to_a_single_line() {
        let requests = [
            sample_init(),
            WorkerRequest::CollectFile {
                path: "/repo/tests/test_math.py".to_string(),
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

        let responses = [
            WorkerResponse::Ready {
                protocol_version: PROTOCOL_VERSION,
            },
            sample_collected_with_tests(),
            sample_collected_with_error(),
            traceback.clone(),
            WorkerResponse::Bye,
        ];
        for response in &responses {
            let encoded = serde_json::to_string(response).expect("response serializes");
            assert!(!encoded.contains('\n'), "multi-line response: {encoded}");
        }

        // ...and the escaped newlines survive the round trip, so a traceback reaches the
        // orchestrator intact rather than truncated at the first line break.
        let encoded = serde_json::to_string(&traceback).expect("traceback serializes");
        assert!(
            encoded.contains(r"\n"),
            "newlines must be escaped, not dropped"
        );
        let decoded: WorkerResponse =
            serde_json::from_str(&encoded).expect("traceback deserializes");
        assert_eq!(decoded, traceback);
    }
}
