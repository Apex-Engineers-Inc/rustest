//! Nodeid construction and decomposition — the addressing contract of the v2 core.
//!
//! A nodeid is the byte string pytest prints, selects on, and writes into reports, so
//! **every rule here was extracted from the installed pytest source**
//! (`.venv/Lib/site-packages/_pytest/`, pytest 8.4.2) and cross-checked against nodeids
//! that pytest actually emitted.  Byte-compatibility is the point: the conformance
//! harness diffs our ids against pytest's, so a one-character difference is a failure.
//!
//! # How pytest composes a nodeid
//!
//! * The file segment is the rootdir-relative path with the OS separator rewritten to
//!   `/`.  Source: `_pytest/nodes.py::FSCollector.__init__` —
//!   `nodeid = str(self.path.relative_to(session.config.rootpath))` followed by
//!   `if nodeid and os.sep != SEP: nodeid = nodeid.replace(os.sep, SEP)`.
//!   That rewrite happens **upstream of this module**: [`build_nodeid`] takes a path that
//!   is already posix, matching the [`CollectedTest`](super::manifest::CollectedTest)
//!   doc contract, and only `debug_assert!`s the invariant to catch producer bugs.
//! * Every deeper collector appends `"::" + name`.  Source:
//!   `_pytest/nodes.py::Node.__init__` — `self._nodeid = self.parent.nodeid + "::" + self.name`.
//!   Classes and functions are ordinary nodes, so a class chain simply nests.
//! * A parametrized function's *name* already carries the bracket, which is why the
//!   bracket is always the tail of the nodeid.  Source:
//!   `_pytest/python.py::PyCollector._genfunctions` —
//!   `subname = f"{name}[{callspec.id}]" if callspec._idlist else name`.
//!   Note the `if` guards on `_idlist` — the list of id *components* — not on the joined
//!   `callspec.id`.  So an unparametrized function gets **no** brackets (`None` here),
//!   while a function parametrized with an empty-string id keeps its brackets around
//!   nothing: `@parametrize("s", ["", "a"])` really does emit `test_strings[]`, and pytest
//!   selects on that id.  `None` and `Some("")` are therefore **distinct, both reachable**
//!   states, and `split_nodeid` preserves the difference.
//!
//! No escaping happens in this module.  pytest sanitizes ids when it *generates* them
//! (`_pytest/python.py::IdMaker`), not when it assembles the nodeid; param ids therefore
//! arrive here pre-formed and are copied verbatim.
//!
//! # Why the split rule is what it is
//!
//! [`split_nodeid`] cuts the path at the **first** `::`, then treats the **first** `[` in
//! what remains as the param boundary.  Two nodeids that pytest 8.4.2 really produced
//! (captured with `pytest --collect-only -q`) rule out the obvious alternatives:
//!
//! * `tests/test_a.py::test_top[a::b]` — a param id may contain `::`, so "the first `[`
//!   after the **last** `::`" finds no bracket at all and mis-splits the id.
//! * `tests/[dir]/test_b.py::test_in_bracket_dir` — a path may contain `[`, so "the first
//!   `[` in the whole string" (what `_pytest/main.py::resolve_collection_argument` and
//!   `_pytest/junitxml.py::mangle_test_address` do, via `partition("[")`) swallows a
//!   directory name.  pytest gets away with it because those helpers parse
//!   *command-line arguments*, not collected ids; we need the inverse of [`build_nodeid`]
//!   to be exact.
//!
//! Anchoring the cut to the first `::` keeps both cases whole while agreeing byte for
//! byte with pytest on every id where the path holds no `[`.  Splitting the path at the
//! first `::` is pytest's own convention (`_pytest/main.py::resolve_collection_argument`,
//! `strpath, *parts = base.split("::")`; likewise `_pytest/reports.py::BaseReport.fspath`
//! and `_pytest/cacheprovider.py`, both `nodeid.split("::")[0]`).
//!
//! The param id is then everything between that first `[` and the **final** character of
//! the nodeid, which must be `]`.  Anchoring the close on the last byte rather than
//! bracket-matching is what makes unbalanced ids work, and pytest emits those: `ids=["p]q"]`
//! gives `test_x[p]q]` and `ids=["trail["]` gives `test_y[trail[]`.  Both round-trip here.

/// Build a pytest-byte-compatible nodeid.
///
/// `path` is a **rootdir-relative posix** path (normalization is the producer's job);
/// `parts` is the class chain followed by the function name; `param_id` is the bracket
/// content *without* the brackets.
///
/// ```ignore
/// build_nodeid("tests/test_a.py", &["TestBox", "test_m"], Some("x-1"))
///     == "tests/test_a.py::TestBox::test_m[x-1]"
/// ```
///
/// Nothing is escaped or validated: pytest's id sanitization happens at id-generation
/// time (`_pytest/python.py::IdMaker`), upstream of this call.
pub fn build_nodeid(path: &str, parts: &[&str], param_id: Option<&str>) -> String {
    debug_assert!(
        !path.contains('\\'),
        "nodeid paths must be posix-separated before reaching build_nodeid, got {path:?}"
    );
    // `split_nodeid` finds the path by cutting at the first `::`, so a path containing one
    // would be truncated and its tail mistaken for a class chain.
    debug_assert!(
        !path.contains("::"),
        "a nodeid path may not contain `::` — it delimits the path from the class chain, got {path:?}"
    );
    // pytest hangs the bracket off the *function* name (`_genfunctions`), so a param id
    // with no name to attach to is not a shape pytest can produce.  It is also not
    // invertible: the bracket would land inside the path segment, where `split_nodeid`
    // correctly refuses to read it as a param (paths may legitimately contain brackets).
    debug_assert!(
        param_id.is_none() || !parts.is_empty(),
        "a param id needs a function name to attach to, got path {path:?} with {param_id:?}"
    );

    let mut nodeid = String::with_capacity(path.len() + 16);
    nodeid.push_str(path);
    for part in parts {
        nodeid.push_str("::");
        nodeid.push_str(part);
    }
    if let Some(param_id) = param_id {
        nodeid.push('[');
        nodeid.push_str(param_id);
        nodeid.push(']');
    }
    nodeid
}

/// Inverse of [`build_nodeid`], for tooling and for the conformance harness's diffing:
/// returns `(path, parts, param_id)`.
///
/// The path runs to the first `::`; the param id is the first `[` in the remainder
/// through the trailing `]`.  A nodeid with no `::` is a bare file id and yields no parts
/// and no param, even when the path itself contains brackets.  An unterminated `[` is
/// left in the final part rather than guessed at, so `split_nodeid` is total: it never
/// panics and `build_nodeid(split_nodeid(id)) == id` for every id `build_nodeid` can emit.
pub fn split_nodeid(nodeid: &str) -> (String, Vec<String>, Option<String>) {
    let Some(sep) = nodeid.find("::") else {
        return (nodeid.to_string(), Vec::new(), None);
    };
    let (path, rest) = (&nodeid[..sep], &nodeid[sep + 2..]);

    let (base, param_id) = match rest.find('[') {
        Some(open) if rest.ends_with(']') => (
            &rest[..open],
            Some(rest[open + 1..rest.len() - 1].to_string()),
        ),
        _ => (rest, None),
    };

    let parts = base.split("::").map(str::to_string).collect();
    (path.to_string(), parts, param_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The full round-trip table.  Each row is `(path, parts, param_id, nodeid)`.
    fn table() -> Vec<(
        &'static str,
        Vec<&'static str>,
        Option<&'static str>,
        &'static str,
    )> {
        vec![
            (
                "tests/test_a.py",
                vec!["TestBox", "test_m"],
                Some("x-1"),
                "tests/test_a.py::TestBox::test_m[x-1]",
            ),
            (
                "tests/test_a.py",
                vec!["TestA", "TestB", "test_x"],
                None,
                "tests/test_a.py::TestA::TestB::test_x",
            ),
            (
                "tests/test_a.py",
                vec!["test_top"],
                None,
                "tests/test_a.py::test_top",
            ),
            (
                "tests/test_a.py",
                vec!["test_top"],
                Some("data[0]"),
                "tests/test_a.py::test_top[data[0]]",
            ),
            (
                "tests/test_a.py",
                vec!["test_top"],
                Some("a::b"),
                "tests/test_a.py::test_top[a::b]",
            ),
            (
                "tests/test_a.py",
                vec!["TestBox", "TestInner", "test_m"],
                Some("data[0]"),
                "tests/test_a.py::TestBox::TestInner::test_m[data[0]]",
            ),
            ("tests/test_a.py", vec![], None, "tests/test_a.py"),
            ("test_a.py", vec![], None, "test_a.py"),
            (
                "tests/[dir]/test_b.py",
                vec!["test_in_bracket_dir"],
                None,
                "tests/[dir]/test_b.py::test_in_bracket_dir",
            ),
            (
                "tests/[dir]/test_b.py",
                vec![],
                None,
                "tests/[dir]/test_b.py",
            ),
            // An empty-string param id keeps its brackets and is distinct from "no param":
            // `@parametrize("s", ["", "a"])` emits `test_strings[]` (see module docs).
            (
                "t.py",
                vec!["test_strings"],
                Some(""),
                "t.py::test_strings[]",
            ),
            // Unbalanced brackets inside a param id.  pytest emits both of these
            // (`ids=["p]q"]`, `ids=["trail["]`); they pin the `ends_with(']')` anchor.
            ("t.py", vec!["test_x"], Some("p]q"), "t.py::test_x[p]q]"),
            (
                "t.py",
                vec!["test_y"],
                Some("trail["),
                "t.py::test_y[trail[]",
            ),
        ]
    }

    /// Normalization is the producer's job (`CollectedTest.path` is documented posix), so
    /// a Windows-separated path reaching this far is a bug we want loud in debug builds.
    #[test]
    #[cfg(debug_assertions)]
    #[should_panic(expected = "posix-separated")]
    fn build_rejects_windows_separators_in_debug_builds() {
        build_nodeid("tests\\test_a.py", &["test_x"], None);
    }

    /// pytest attaches the bracket to the function name, never to a bare file
    /// (`_pytest/python.py::PyCollector._genfunctions`), and the shape is not invertible.
    #[test]
    #[cfg(debug_assertions)]
    #[should_panic(expected = "needs a function name")]
    fn build_rejects_a_param_id_with_no_function_name_in_debug_builds() {
        build_nodeid("tests/test_a.py", &[], Some("x-1"));
    }

    /// `::` delimits the path from the class chain, so a path carrying one would be
    /// truncated by [`split_nodeid`] — the symmetric producer-bug guard to the `\` one.
    #[test]
    #[cfg(debug_assertions)]
    #[should_panic(expected = "may not contain")]
    fn build_rejects_double_colons_in_the_path_in_debug_builds() {
        build_nodeid("tests/od::d.py", &["test_x"], None);
    }

    #[test]
    fn build_matches_the_brief_contract() {
        assert_eq!(
            build_nodeid("tests/test_a.py", &["TestBox", "test_m"], Some("x-1")),
            "tests/test_a.py::TestBox::test_m[x-1]"
        );
    }

    #[test]
    fn build_produces_every_table_nodeid() {
        for (path, parts, param, expected) in table() {
            assert_eq!(
                build_nodeid(path, &parts, param),
                expected,
                "build({path:?}, {parts:?}, {param:?})"
            );
        }
    }

    #[test]
    fn split_decomposes_every_table_nodeid() {
        for (path, parts, param, nodeid) in table() {
            let (got_path, got_parts, got_param) = split_nodeid(nodeid);
            assert_eq!(got_path, path, "path of {nodeid:?}");
            assert_eq!(got_parts, parts, "parts of {nodeid:?}");
            assert_eq!(got_param.as_deref(), param, "param of {nodeid:?}");
        }
    }

    #[test]
    fn split_round_trips_build_for_every_table_row() {
        for (path, parts, param, _) in table() {
            let nodeid = build_nodeid(path, &parts, param);
            let (got_path, got_parts, got_param) = split_nodeid(&nodeid);
            let refs: Vec<&str> = got_parts.iter().map(String::as_str).collect();
            assert_eq!(
                build_nodeid(&got_path, &refs, got_param.as_deref()),
                nodeid,
                "round trip of {nodeid:?}"
            );
        }
    }

    #[test]
    fn split_treats_bare_file_nodeid_as_path_only() {
        let (path, parts, param) = split_nodeid("tests/test_a.py");
        assert_eq!(path, "tests/test_a.py");
        assert!(parts.is_empty());
        assert_eq!(param, None);
    }

    #[test]
    fn split_keeps_brackets_that_belong_to_the_path() {
        let (path, parts, param) = split_nodeid("tests/[dir]/test_b.py::test_in_bracket_dir");
        assert_eq!(path, "tests/[dir]/test_b.py");
        assert_eq!(parts, vec!["test_in_bracket_dir"]);
        assert_eq!(param, None);
    }

    #[test]
    fn split_takes_the_first_bracket_after_the_path_so_nested_brackets_survive() {
        let (path, parts, param) = split_nodeid("tests/test_a.py::test_top[data[0]]");
        assert_eq!(path, "tests/test_a.py");
        assert_eq!(parts, vec!["test_top"]);
        assert_eq!(param.as_deref(), Some("data[0]"));
    }

    #[test]
    fn split_keeps_double_colons_that_belong_to_the_param_id() {
        let (path, parts, param) = split_nodeid("tests/test_a.py::test_top[a::b]");
        assert_eq!(path, "tests/test_a.py");
        assert_eq!(parts, vec!["test_top"]);
        assert_eq!(param.as_deref(), Some("a::b"));
    }

    #[test]
    fn split_ignores_an_unterminated_bracket() {
        let (path, parts, param) = split_nodeid("tests/test_a.py::test_top[oops");
        assert_eq!(path, "tests/test_a.py");
        assert_eq!(parts, vec!["test_top[oops"]);
        assert_eq!(param, None);
    }

    #[test]
    fn build_does_not_escape_param_ids() {
        assert_eq!(
            build_nodeid("t.py", &["test_x"], Some("a b/c\\d::e[f]")),
            "t.py::test_x[a b/c\\d::e[f]]"
        );
    }

    /// Nodeids observed from pytest 8.4.2 itself (`pytest --collect-only -q`).
    #[test]
    fn split_handles_nodeids_captured_from_pytest() {
        let observed = [
            "tests/[dir]/test_b.py::test_in_bracket_dir",
            "tests/test_a.py::test_top[data[0]]",
            "tests/test_a.py::test_top[x-1]",
            "tests/test_a.py::test_top[a::b]",
            "tests/test_a.py::TestBox::TestInner::test_m[data[0]]",
            "tests/test_a.py::test_plain",
            "test_p.py::test_strings[]",
            "test_p.py::test_strings[a]",
            "test_p.py::test_x[p]q]",
            "test_p.py::test_y[trail[]",
        ];
        for nodeid in observed {
            let (path, parts, param) = split_nodeid(nodeid);
            let refs: Vec<&str> = parts.iter().map(String::as_str).collect();
            assert_eq!(
                build_nodeid(&path, &refs, param.as_deref()),
                nodeid,
                "round trip of pytest-observed {nodeid:?}"
            );
        }
    }
}
