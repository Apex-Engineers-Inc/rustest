//! Nodeid construction and decomposition — the addressing contract of the engine core.
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
//!   states.
//!
//! No escaping happens in this module.  pytest sanitizes ids when it *generates* them
//! (`_pytest/python.py::IdMaker`), not when it assembles the nodeid; param ids therefore
//! arrive here pre-formed and are copied verbatim.
//!

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
    // `::` delimits the path from the class chain, so a path containing one would be read
    // back with its tail mistaken for a class chain.
    debug_assert!(
        !path.contains("::"),
        "a nodeid path may not contain `::` — it delimits the path from the class chain, got {path:?}"
    );
    // pytest hangs the bracket off the *function* name (`_genfunctions`), so a param id
    // with no name to attach to is not a shape pytest can produce.  It is also not
    // invertible: the bracket would land inside the path segment, where it cannot be read
    // as a param (paths may legitimately contain brackets).
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

/// Split a command-line argument into its path and its `::` selection parts.
///
/// Port of `_pytest/main.py::resolve_collection_argument`'s first two lines:
/// `base, squacket, rest = arg.partition("[")`, `strpath, *parts = base.split("::")`, and the
/// bracket tail glued back onto the last part.  So `a.py::t[x::y]` selects `t[x::y]`, and —
/// pytest's own quirk, ported rather than fixed — a path holding a `[` before any `::` is cut
/// at the bracket (`tests/[d]/t.py` resolves to `tests/`).
pub(crate) fn split_collection_argument(arg: &str) -> (&str, Vec<String>) {
    let base_end = arg.find('[').unwrap_or(arg.len());
    match arg[..base_end].find("::") {
        None => (&arg[..base_end], Vec::new()),
        Some(split) => (&arg[..split], name_parts(&arg[split + 2..])),
    }
}

/// The `::` parts of whatever follows a path: an argument's selection, or a node id's names
/// below its file.  Same partition-on-`[` rule as [`split_collection_argument`], so an
/// argument and the id it names decompose identically.
pub(crate) fn name_parts(rest: &str) -> Vec<String> {
    let base_end = rest.find('[').unwrap_or(rest.len());
    let mut parts: Vec<String> = rest[..base_end].split("::").map(str::to_string).collect();
    if let Some(last) = parts.last_mut() {
        last.push_str(&rest[base_end..]);
    }
    parts
}

/// What one argument's selection parts matched inside one module.
#[derive(Debug, Default, PartialEq, Eq)]
pub(crate) struct NameWalk {
    /// The matched nodes, in collection order, each as its name path below the module.
    pub(crate) matched: Vec<Vec<String>>,
    /// Every collector the walk went *through* — collected, so pytest's collection cache holds
    /// it and `genitems` will not expand it again.  `[]` is the module itself.
    pub(crate) collected: Vec<Vec<String>>,
    /// Collectors none of whose children matched: pytest's `notfound_collectors`.
    pub(crate) unmatched: Vec<Vec<String>>,
}

/// The name half of `_pytest/main.py::Session.collect`: match `names` level by level against
/// a module whose tests are `tests` (each a [`name_parts`] path), depth first.
///
/// A level matches on `name == part or name.split("[")[0] == part`, which is what makes
/// `test_p` select every `test_p[...]`.  An item with parts left over is discarded without
/// being recorded as unmatched — the source of `found no collectors` for one part too many.
/// The module is always a collector, even with no tests; a deeper node is one when some
/// test lies strictly below it.
pub(crate) fn walk_names(tests: &[Vec<String>], names: &[String]) -> NameWalk {
    let mut walk = NameWalk::default();
    let mut work: Vec<(Vec<String>, usize)> = vec![(Vec::new(), 0)];
    while let Some((node, depth)) = work.pop() {
        if depth == names.len() {
            walk.matched.push(node);
            continue;
        }
        let below = |test: &&Vec<String>| test.len() > node.len() && test.starts_with(&node);
        if !node.is_empty() && !tests.iter().any(|test| below(&test)) {
            continue;
        }
        let mut children: Vec<&String> = Vec::new();
        for test in tests.iter().filter(below) {
            if !children.contains(&&test[node.len()]) {
                children.push(&test[node.len()]);
            }
        }
        let part = &names[depth];
        let mut any = false;
        // Pushed in reverse so they pop in collection order, as pytest's `reversed(subnodes)`.
        for child in children.into_iter().rev() {
            if child == part || child.split('[').next() == Some(part.as_str()) {
                let mut next = node.clone();
                next.push(child.clone());
                work.push((next, depth + 1));
                any = true;
            }
        }
        if !any {
            walk.unmatched.push(node.clone());
        }
        walk.collected.push(node);
    }
    walk
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parts(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| (*item).to_string()).collect()
    }

    #[test]
    fn an_argument_splits_like_resolve_collection_argument() {
        let cases: &[(&str, &str, &[&str])] = &[
            ("test_m.py", "test_m.py", &[]),
            ("test_m.py::test_a", "test_m.py", &["test_a"]),
            (
                "test_m.py::TestC::test_q[a]",
                "test_m.py",
                &["TestC", "test_q[a]"],
            ),
            ("test_m.py::test_p[a::b]", "test_m.py", &["test_p[a::b]"]),
            ("test_m.py::", "test_m.py", &[""]),
            ("test_m.py::test_a::", "test_m.py", &["test_a", ""]),
            (r"sub\test_n.py::test_a", r"sub\test_n.py", &["test_a"]),
            // pytest's quirk: the path is cut at the first `[`.
            ("test_x[1].py", "test_x", &[]),
            ("tests/[d]/t.py::test_a", "tests/", &[]),
        ];
        for (arg, path, names) in cases {
            assert_eq!(
                split_collection_argument(arg),
                (*path, parts(names)),
                "{arg}"
            );
        }
    }

    #[test]
    fn a_node_ids_names_decompose_like_an_argument() {
        assert_eq!(
            name_parts("TestC::test_q[a]"),
            parts(&["TestC", "test_q[a]"])
        );
        assert_eq!(name_parts("test_p[x::y]"), parts(&["test_p[x::y]"]));
        assert_eq!(name_parts("test_a"), parts(&["test_a"]));
    }

    fn module() -> Vec<Vec<String>> {
        [
            "test_a",
            "test_p[1]",
            "test_p[2]",
            "TestC::test_one",
            "TestC::test_q[a]",
            "TestC::test_q[b]",
        ]
        .iter()
        .map(|id| name_parts(id))
        .collect()
    }

    fn walk(names: &[&str]) -> NameWalk {
        walk_names(&module(), &parts(names))
    }

    fn paths(items: &[&[&str]]) -> Vec<Vec<String>> {
        items.iter().map(|item| parts(item)).collect()
    }

    #[test]
    fn a_function_name_matches_every_parametrized_case() {
        assert_eq!(
            walk(&["test_p"]).matched,
            paths(&[&["test_p[1]"], &["test_p[2]"]])
        );
        assert_eq!(walk(&["test_p[2]"]).matched, paths(&[&["test_p[2]"]]));
    }

    #[test]
    fn a_class_matches_as_a_collector_and_is_not_collected_by_its_own_walk() {
        let walked = walk(&["TestC"]);
        assert_eq!(walked.matched, paths(&[&["TestC"]]));
        assert_eq!(walked.collected, paths(&[&[]]));
        let deeper = walk(&["TestC", "test_q"]);
        assert_eq!(
            deeper.matched,
            paths(&[&["TestC", "test_q[a]"], &["TestC", "test_q[b]"]])
        );
        assert_eq!(deeper.collected, paths(&[&[], &["TestC"]]));
    }

    #[test]
    fn a_miss_names_the_collector_it_happened_in() {
        assert_eq!(walk(&["nope"]).unmatched, paths(&[&[]]));
        assert_eq!(walk(&["TestC", "nope"]).unmatched, paths(&[&["TestC"]]));
        assert_eq!(walk(&["test_p[9]"]).unmatched, paths(&[&[]]));
        assert_eq!(walk(&[""]).unmatched, paths(&[&[]]));
    }

    /// An item with parts left over is dropped silently — no matched node *and* no
    /// unmatched collector, which is pytest's `found no collectors for` shape.
    #[test]
    fn parts_past_an_item_match_nothing_and_blame_nothing() {
        let walked = walk(&["TestC", "test_one", "extra"]);
        assert!(walked.matched.is_empty());
        assert!(walked.unmatched.is_empty());
    }

    #[test]
    fn an_empty_module_is_still_a_collector() {
        let walked = walk_names(&[], &parts(&["test_a"]));
        assert_eq!(walked.unmatched, paths(&[&[]]));
    }

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
    /// read back wrongly — the symmetric producer-bug guard to the `\` one.
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
    fn build_does_not_escape_param_ids() {
        assert_eq!(
            build_nodeid("t.py", &["test_x"], Some("a b/c\\d::e[f]")),
            "t.py::test_x[a b/c\\d::e[f]]"
        );
    }
}
