//! The collection manifest: the serializable spine artifact of the v2 core.
//!
//! Collection produces a [`CollectionManifest`] — plain data, never live Python objects.
//! Workers receive it over a process boundary and resolve callables themselves, the
//! manifest cache stores it verbatim, and the v2 JSON report is derived from it.
//!
//! The JSON encoding below is a **frozen wire contract**: field names and the
//! omit-when-empty rules are consumed by the worker protocol (Phase 1b) and the JSON
//! report (Phase 1c).  This module's golden-string test pins the exact bytes;
//! any incompatible change must bump [`MANIFEST_SCHEMA_VERSION`].

use serde::{Deserialize, Serialize};

/// Version of the manifest wire format.  Bump on any incompatible change.
pub const MANIFEST_SCHEMA_VERSION: u32 = 2;

/// Which collection tier produced an entry.
///
/// `"s"` is the Rust static collector ([`crate::v2::static_collect`]), which parsed the file
/// and never imported it; `"d"` is a Python worker, which imported it.  The field exists
/// because the two tiers are *supposed* to be indistinguishable — the three-way differential
/// asserts `manifest(S+D) == manifest(D-only) == pytest` — and a claim that strong needs an
/// instrument that can tell which tier actually answered.  Without it a Tier S bug that
/// routed everything to D would look exactly like a pass.
///
/// [`Tier::Dynamic`] is the **default and the omitted form**: the Python worker
/// (`_v2_worker.py::_build_entry`) does not know tiers exist and never sends the key, so
/// every producer that predates this field keeps emitting bytes this type still decodes.
/// That is the same additive-compatibility argument
/// [`CollectionManifest::deselected`] makes, and it is why
/// [`MANIFEST_SCHEMA_VERSION`] does not move.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub enum Tier {
    /// Parsed, never imported.
    #[serde(rename = "s")]
    Static,
    /// Imported by a worker — the oracle tier.
    #[serde(rename = "d")]
    #[default]
    Dynamic,
}

impl Tier {
    /// The omission rule: `"d"` is the default, so it never reaches the wire.
    fn is_dynamic(&self) -> bool {
        matches!(self, Tier::Dynamic)
    }
}

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
    ///
    /// For a documentation code block, `qualname` carries a leading block segment
    /// (`codeblock_N_line_M`) that `class_name` deliberately does not — see the note on
    /// `class_name` below. Outside that case, `class_name` is `qualname` minus its last
    /// dotted segment whenever `qualname` has more than one.
    pub qualname: String,
    /// The enclosing class chain (`"TestBox.TestInner"`), or absent for a module-level test.
    ///
    /// This is **not** always derivable from `qualname` by trimming its last segment: a
    /// block segment reaches `qualname` and never reaches `class_name`, by design. Folding
    /// the two together would give every module-level test inside a documentation block a
    /// phantom class, and `class_name` is the class-scope teardown boundary
    /// (`_v2_worker.py::FixtureRunner.note_test_boundary`), so that phantom class would
    /// share a class-scoped fixture across tests that must each get their own instead. See
    /// `docs/superpowers/specs/2026-08-04-doc-block-execution-design.md` for the full
    /// argument; `_v2_worker.py::_build_entry` carries the matching note on the Python side.
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
    /// Which tier produced this entry.  Omitted when [`Tier::Dynamic`]; see [`Tier`].
    ///
    /// Last field on purpose: every byte before it is unchanged from the schema-v2 form the
    /// golden froze, so a `"d"` entry is byte-identical to what a pre-tier producer emitted.
    #[serde(default, skip_serializing_if = "Tier::is_dynamic")]
    pub tier: Tier,
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
    /// How many collected tests `-k`/`-m` removed — pytest's `N deselected`.
    ///
    /// Deselection belongs on the manifest rather than beside it because pytest performs it
    /// *inside* collection (`pytest_collection_modifyitems`, called from
    /// `Session.perform_collect`), and because `tests` alone cannot answer the question the
    /// exit code depends on: an empty `tests` is exit 5 either way, but "0 collected" and
    /// "7 collected, 7 deselected" are different sentences and users read both.
    ///
    /// Omitted when zero, so a run without selection is byte-identical to what the schema
    /// froze before this field existed — an additive, compatible change, which is why
    /// [`MANIFEST_SCHEMA_VERSION`] does not move.
    #[serde(default, skip_serializing_if = "is_zero")]
    pub deselected: usize,
    /// How many **modules** asked, at import time, not to be collected —
    /// `pytest.skip(..., allow_module_level=True)` and `pytest.importorskip` at module
    /// scope.
    ///
    /// A count with no ids, for the same reason `deselected` is one, and measured on pytest
    /// 8.4.2 rather than assumed: over a tree with two module-level-skipped files and one
    /// live one, `--collect-only -q` lists **only** the live file's tests, while the run
    /// summary reads `1 passed, 2 skipped`. So the skip is invisible to the id list and
    /// visible to the tally, which is exactly a field of this shape.
    ///
    /// Kept apart from `errors` deliberately: routing it there would abort the session
    /// (exit 2) for a file pytest collects past, which is what cost the Task 1b sweep both
    /// Pillow (4 036 tests) and FastAPI (3 289) — every one of them lost to six and eight
    /// module-level skips respectively.
    ///
    /// Omitted when zero, so every manifest that has none is byte-identical to what the
    /// schema froze before this field existed; additive and compatible, so
    /// [`MANIFEST_SCHEMA_VERSION`] does not move.
    #[serde(default, skip_serializing_if = "is_zero")]
    pub module_skipped: usize,
}

fn is_zero(count: &usize) -> bool {
    *count == 0
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Map, Value};

    /// Builds the fixed manifest used by both the round-trip and the golden-contract
    /// test: one plain test, one class + parametrized test carrying a mark with args and
    /// kwargs plus a bare mark with neither, one **Tier S** test, and one collection error.
    ///
    /// The bare `slow` mark is what exercises the omit-when-empty rules on `MarkSpec`:
    /// without it, `args`/`kwargs` are never empty in any test and the
    /// `skip_serializing_if`/`default` attributes on both fields would be unpinned.
    ///
    /// The Tier S entry does the same job for [`CollectedTest::tier`]: the other two are
    /// `Dynamic`, so without it the `"tier":"s"` spelling and its position after `fixtures`
    /// would be pinned nowhere.
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
                    tier: Tier::Dynamic,
                },
                CollectedTest {
                    id: "tests/test_math.py::TestBox::test_method[x-1]".to_string(),
                    path: "tests/test_math.py".to_string(),
                    qualname: "TestBox.test_method".to_string(),
                    class_name: Some("TestBox".to_string()),
                    param_id: Some("x-1".to_string()),
                    marks: vec![
                        MarkSpec {
                            name: "skipif".to_string(),
                            args: vec![Value::Bool(true)],
                            kwargs,
                        },
                        MarkSpec {
                            name: "slow".to_string(),
                            args: Vec::new(),
                            kwargs: Map::new(),
                        },
                    ],
                    fixtures: vec!["tmp_path".to_string(), "capsys".to_string()],
                    tier: Tier::Dynamic,
                },
                CollectedTest {
                    id: "tests/test_static.py::test_parsed".to_string(),
                    path: "tests/test_static.py".to_string(),
                    qualname: "test_parsed".to_string(),
                    class_name: None,
                    param_id: None,
                    marks: Vec::new(),
                    fixtures: Vec::new(),
                    tier: Tier::Static,
                },
            ],
            errors: vec![CollectionErrorEntry {
                path: "tests/test_broken.py".to_string(),
                message: "ImportError: No module named 'nope'".to_string(),
            }],
            deselected: 0,
            module_skipped: 0,
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
            r#"{"schema_version":2,"rootdir":"/repo","tests":[{"id":"tests/test_math.py::test_add","path":"tests/test_math.py","qualname":"test_add"},{"id":"tests/test_math.py::TestBox::test_method[x-1]","path":"tests/test_math.py","qualname":"TestBox.test_method","class_name":"TestBox","param_id":"x-1","marks":[{"name":"skipif","args":[true],"kwargs":{"reason":"needs windows","strict":true}},{"name":"slow"}],"fixtures":["tmp_path","capsys"]},{"id":"tests/test_static.py::test_parsed","path":"tests/test_static.py","qualname":"test_parsed","tier":"s"}],"errors":[{"path":"tests/test_broken.py","message":"ImportError: No module named 'nope'"}]}"#
        );
    }

    /// A `CollectedTest` with no explicit `tier` decodes as [`Tier::Dynamic`] — the rule the
    /// Python worker depends on, since `_v2_worker.py::_build_entry` never writes the key.
    ///
    /// Mutation row for the omission: flipping the default to `Static` fails here, and
    /// flipping the `skip_serializing_if` off fails the golden above.
    #[test]
    fn a_tierless_entry_decodes_as_dynamic() {
        let decoded: CollectedTest = serde_json::from_str(
            r#"{"id":"tests/test_a.py::test_one","path":"tests/test_a.py","qualname":"test_one"}"#,
        )
        .expect("a worker entry deserializes");

        assert_eq!(decoded.tier, Tier::Dynamic);
    }

    /// The other half: `Tier::Static` **is** on the wire, spelled `"s"`, and round-trips.
    #[test]
    fn tier_static_is_on_the_wire_and_round_trips() {
        let test = CollectedTest {
            id: "tests/test_a.py::test_one".to_string(),
            path: "tests/test_a.py".to_string(),
            qualname: "test_one".to_string(),
            class_name: None,
            param_id: None,
            marks: Vec::new(),
            fixtures: Vec::new(),
            tier: Tier::Static,
        };

        let encoded = serde_json::to_string(&test).expect("test serializes");
        assert_eq!(
            encoded,
            r#"{"id":"tests/test_a.py::test_one","path":"tests/test_a.py","qualname":"test_one","tier":"s"}"#
        );
        let decoded: CollectedTest = serde_json::from_str(&encoded).expect("test deserializes");
        assert_eq!(decoded, test);
    }

    /// `"d"` is a legal *explicit* spelling even though nothing emits it, so a manifest
    /// round-tripped through a producer that writes tiers verbatim still decodes.
    #[test]
    fn an_explicit_dynamic_tier_decodes_but_is_not_re_emitted() {
        let decoded: CollectedTest =
            serde_json::from_str(r#"{"id":"a.py::t","path":"a.py","qualname":"t","tier":"d"}"#)
                .expect("explicit dynamic decodes");
        assert_eq!(decoded.tier, Tier::Dynamic);
        assert_eq!(
            serde_json::to_string(&decoded).unwrap(),
            r#"{"id":"a.py::t","path":"a.py","qualname":"t"}"#
        );
    }

    /// A mark with no args and no kwargs is `{"name":"slow"}` on the wire — nothing else —
    /// and decodes back from exactly that, so producers in 1b may omit both keys.
    #[test]
    fn bare_mark_wire_form_is_name_only() {
        let bare = MarkSpec {
            name: "slow".to_string(),
            args: Vec::new(),
            kwargs: Map::new(),
        };

        let encoded = serde_json::to_string(&bare).expect("mark serializes");
        assert_eq!(encoded, r#"{"name":"slow"}"#);

        let decoded: MarkSpec =
            serde_json::from_str(r#"{"name":"slow"}"#).expect("bare mark deserializes");
        assert_eq!(decoded, bare);
    }

    /// An empty collection carries no optional noise: `errors` and `deselected` are
    /// omitted entirely and `tests` is an explicit empty array.
    #[test]
    fn empty_manifest_omits_empty_optional_fields() {
        let manifest = CollectionManifest {
            schema_version: MANIFEST_SCHEMA_VERSION,
            rootdir: "/repo".to_string(),
            tests: Vec::new(),
            errors: Vec::new(),
            deselected: 0,
            module_skipped: 0,
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

    /// `deselected` appears **only** when selection removed something, and it round-trips.
    /// The zero case is pinned above; this is the other half, and together they are what
    /// let the field be added without moving [`MANIFEST_SCHEMA_VERSION`].
    #[test]
    fn deselected_is_on_the_wire_only_when_non_zero() {
        let manifest = CollectionManifest {
            schema_version: MANIFEST_SCHEMA_VERSION,
            rootdir: "/repo".to_string(),
            tests: Vec::new(),
            errors: Vec::new(),
            deselected: 3,
            module_skipped: 0,
        };

        let encoded = serde_json::to_string(&manifest).expect("manifest serializes");
        assert_eq!(
            encoded,
            r#"{"schema_version":2,"rootdir":"/repo","tests":[],"deselected":3}"#
        );

        let decoded: CollectionManifest =
            serde_json::from_str(&encoded).expect("manifest deserializes");
        assert_eq!(decoded, manifest);
    }
}
