//! The worker protocol: the JSON-lines contract between the Rust orchestrator and the
//! Python collection workers.
//!
//! One JSON object per line, newline-terminated: requests travel down a worker's stdin,
//! responses come back up its stdout.  A worker's stdout is therefore reserved for
//! protocol traffic — anything a collected module prints must be redirected elsewhere.
//!
//! Both enums are **internally tagged on `op`** with `snake_case` variant names, which
//! makes an unrecognised op a hard decode error rather than a silently dropped message.
//! That is deliberate: a version-skewed worker must fail loudly, not half-work.
//!
//! Like [`crate::v2::manifest`], the JSON encoding here is a **frozen wire contract** —
//! field names, the tag key, and the omit-when-empty rules on `Collected` are pinned by
//! golden-string tests below, and any incompatible change must bump [`PROTOCOL_VERSION`].

use crate::v2::manifest::{CollectedTest, CollectionErrorEntry};
use serde::{Deserialize, Serialize};

/// Version of the worker wire protocol.  Bump on any incompatible change.
///
/// The orchestrator sends it in [`WorkerRequest::Init`] and the worker echoes what it
/// speaks in [`WorkerResponse::Ready`]; a mismatch is a fatal handshake error.
pub const PROTOCOL_VERSION: u32 = 1;

/// A message from the orchestrator to a worker, one per stdin line.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
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
#[serde(tag = "op", rename_all = "snake_case")]
pub enum WorkerResponse {
    Ready {
        protocol_version: u32,
    },
    /// Per-file result. Either tests or an error entry (import/syntax failure).
    Collected {
        path: String,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        tests: Vec<CollectedTest>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        error: Option<CollectionErrorEntry>,
    },
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

    // --- framing ----------------------------------------------------------

    /// The transport is JSON-lines: every message must fit on one line, so no
    /// encoded form may contain a raw newline.
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

        let responses = [
            WorkerResponse::Ready {
                protocol_version: PROTOCOL_VERSION,
            },
            sample_collected_with_tests(),
            sample_collected_with_error(),
            WorkerResponse::Bye,
        ];
        for response in &responses {
            let encoded = serde_json::to_string(response).expect("response serializes");
            assert!(!encoded.contains('\n'), "multi-line response: {encoded}");
        }
    }
}
