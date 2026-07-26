//! The collection manifest: the serializable spine artifact of the v2 core.
//!
//! Collection produces a [`CollectionManifest`] — plain data, never live Python objects.
//! Workers receive it over a process boundary and resolve callables themselves, the
//! manifest cache stores it verbatim, and the v2 JSON report is derived from it.
//!
//! The JSON encoding below is a **frozen wire contract**: field names and the
//! omit-when-empty rules are consumed by the worker protocol (Phase 1b) and the JSON
//! report (Phase 1c).  `src/v2/manifest.rs`'s golden-string test pins the exact bytes;
//! any incompatible change must bump [`MANIFEST_SCHEMA_VERSION`].

use serde::{Deserialize, Serialize};

/// Version of the manifest wire format.  Bump on any incompatible change.
pub const MANIFEST_SCHEMA_VERSION: u32 = 2;

/// A mark applied to a collected test, captured as data.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MarkSpec {
    pub name: String,
    /// Positional args as JSON values (skipif conditions arrive pre-evaluated as bools in 1b).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub args: Vec<serde_json::Value>,
    #[serde(default, skip_serializing_if = "serde_json::Map::is_empty")]
    pub kwargs: serde_json::Map<String, serde_json::Value>,
}

/// A single collected test case, addressable without importing anything.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CollectedTest {
    /// Full pytest-byte-compatible nodeid, rootdir-relative, posix separators.
    pub id: String,
    /// Rootdir-relative posix file path (the nodeid's first segment).
    pub path: String,
    /// Dotted qualname within the module, e.g. "TestBox.test_method" or "test_top".
    pub qualname: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub class_name: Option<String>,
    /// Bracket content for parametrized cases, without brackets (e.g. "x-1").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub param_id: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub marks: Vec<MarkSpec>,
    /// Direct fixture parameter names in signature order.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub fixtures: Vec<String>,
}

/// A file that could not be collected, and why.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CollectionErrorEntry {
    pub path: String,
    pub message: String,
}

/// The complete output of a collection run.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CollectionManifest {
    pub schema_version: u32,
    /// Absolute rootdir, posix separators.
    pub rootdir: String,
    pub tests: Vec<CollectedTest>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub errors: Vec<CollectionErrorEntry>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Map, Value};

    /// Builds the fixed manifest used by both the round-trip and the golden-contract
    /// test: one plain test, one class + parametrized test carrying a mark with args and
    /// kwargs, and one collection error.
    fn sample_manifest() -> CollectionManifest {
        let mut kwargs = Map::new();
        kwargs.insert("reason".to_string(), json!("needs windows"));
        kwargs.insert("strict".to_string(), json!(true));

        CollectionManifest {
            schema_version: MANIFEST_SCHEMA_VERSION,
            rootdir: "/repo".to_string(),
            tests: vec![
                CollectedTest {
                    id: "tests/test_math.py::test_add".to_string(),
                    path: "tests/test_math.py".to_string(),
                    qualname: "test_add".to_string(),
                    class_name: None,
                    param_id: None,
                    marks: Vec::new(),
                    fixtures: Vec::new(),
                },
                CollectedTest {
                    id: "tests/test_math.py::TestBox::test_method[x-1]".to_string(),
                    path: "tests/test_math.py".to_string(),
                    qualname: "TestBox.test_method".to_string(),
                    class_name: Some("TestBox".to_string()),
                    param_id: Some("x-1".to_string()),
                    marks: vec![MarkSpec {
                        name: "skipif".to_string(),
                        args: vec![Value::Bool(true)],
                        kwargs,
                    }],
                    fixtures: vec!["tmp_path".to_string(), "capsys".to_string()],
                },
            ],
            errors: vec![CollectionErrorEntry {
                path: "tests/test_broken.py".to_string(),
                message: "ImportError: No module named 'nope'".to_string(),
            }],
        }
    }

    #[test]
    fn manifest_round_trips_through_json() {
        let manifest = sample_manifest();

        let encoded = serde_json::to_string(&manifest).expect("manifest serializes");
        let decoded: CollectionManifest =
            serde_json::from_str(&encoded).expect("manifest deserializes");

        assert_eq!(decoded, manifest);
    }

    /// The wire contract.  Field names and omission rules are consumed by the workers
    /// (Phase 1b) and the v2 JSON report (Phase 1c); changing this string is a breaking
    /// change that requires bumping [`MANIFEST_SCHEMA_VERSION`].
    #[test]
    fn manifest_json_matches_golden_contract() {
        let encoded = serde_json::to_string(&sample_manifest()).expect("manifest serializes");

        assert_eq!(
            encoded,
            r#"{"schema_version":2,"rootdir":"/repo","tests":[{"id":"tests/test_math.py::test_add","path":"tests/test_math.py","qualname":"test_add"},{"id":"tests/test_math.py::TestBox::test_method[x-1]","path":"tests/test_math.py","qualname":"TestBox.test_method","class_name":"TestBox","param_id":"x-1","marks":[{"name":"skipif","args":[true],"kwargs":{"reason":"needs windows","strict":true}}],"fixtures":["tmp_path","capsys"]}],"errors":[{"path":"tests/test_broken.py","message":"ImportError: No module named 'nope'"}]}"#
        );
    }

    /// An empty collection carries no optional noise: `errors` is omitted entirely and
    /// `tests` is an explicit empty array.
    #[test]
    fn empty_manifest_omits_empty_optional_fields() {
        let manifest = CollectionManifest {
            schema_version: MANIFEST_SCHEMA_VERSION,
            rootdir: "/repo".to_string(),
            tests: Vec::new(),
            errors: Vec::new(),
        };

        let encoded = serde_json::to_string(&manifest).expect("manifest serializes");

        assert_eq!(
            encoded,
            r#"{"schema_version":2,"rootdir":"/repo","tests":[]}"#
        );

        let decoded: CollectionManifest =
            serde_json::from_str(&encoded).expect("manifest deserializes without optional fields");
        assert_eq!(decoded, manifest);
    }
}
