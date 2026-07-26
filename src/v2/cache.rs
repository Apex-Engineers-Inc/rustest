//! The v2 **last-failed cache**: what `--lf` and `--ff` read and every run writes.
//!
//! Port of `_pytest/cacheprovider.py::LFPlugin` reduced to the two things a runner without
//! a plugin system needs: a set of node ids that failed last time, and the rules for
//! updating it.
//!
//! # Why this is a *v2* file and not v1's
//!
//! v1 writes `.rustest_cache/lastfailed` as `{"failed": [...]}`, keyed on v1's
//! **display names** — native separators on Windows (`tests\test_a.py::test_one`) and a
//! different parametrized-id spelling.  v2's ids are rootdir-relative posix
//! (`src/v2/nodeid.rs`).  Sharing one file would silently mean "no test matched" on every
//! Windows machine and, worse, would let a v1 run overwrite entries a v2 run needs.  So the
//! file is **versioned by directory** — `.rustest_cache/v2/lastfailed` — and the two engines
//! keep independent caches, which is also what makes `--v1` a true escape hatch rather than
//! a cache-corrupting one.
//!
//! The *content* shape is pytest's, not v1's: a JSON object mapping node id to `true`
//! (`_pytest/cacheprovider.py` stores `self.lastfailed: dict[str, bool]` under
//! `cache/lastfailed`).  Probed against pytest 8.4.2, whose
//! `.pytest_cache/v/cache/lastfailed` after two failing tests is exactly
//! `{"test_x.py::test_b": true, "test_x.py::test_c": true}`.  Keeping pytest's shape means a
//! reader (an editor plugin, a human) that already understands pytest's cache understands
//! this one.
//!
//! # What is deliberately *not* here
//!
//! `cache/nodeids` (pytest's `--nf`/new-first ordering) and the `--cache-clear` /
//! `--cache-show` surfaces.  Neither is on rustest's CLI, and writing a file nothing reads
//! is how caches rot.

use std::collections::BTreeMap;
use std::collections::HashSet;
use std::path::{Path, PathBuf};

/// Directory under the rootdir that holds every rustest cache.  Shared with v1 on purpose:
/// one directory to add to `.gitignore`, one directory to delete.
const CACHE_DIR: &str = ".rustest_cache";

/// The v2 sub-directory.  See the module docs for why the version is in the *path* rather
/// than in the file: a v1 build must not be able to read, or write, this file at all.
const CACHE_VERSION_DIR: &str = "v2";

const LAST_FAILED_FILE: &str = "lastfailed";

/// How `--lf` / `--ff` reorder the selected tests.
///
/// Port of the two branches of `_pytest/cacheprovider.py::LFPlugin::
/// pytest_collection_modifyitems` (pytest 8.4.2, l. 380-407).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum LastFailedMode {
    /// Neither flag; every selected test runs, in manifest order.
    #[default]
    None,
    /// `--lf` / `--last-failed`: run **only** the previously failed tests.
    Only,
    /// `--ff` / `--failed-first`: run the previously failed tests first, then the rest.
    First,
}

/// Where the v2 last-failed file lives for a given rootdir.
pub fn last_failed_path(rootdir: &Path) -> PathBuf {
    rootdir
        .join(CACHE_DIR)
        .join(CACHE_VERSION_DIR)
        .join(LAST_FAILED_FILE)
}

/// Read the previously failed node ids, or an empty set.
///
/// **Never an error.**  A cache is an optimisation: a missing file is the first run, and a
/// corrupt or unreadable one is a cache to be rebuilt, not a reason to refuse to run tests.
/// pytest takes the same position — `Cache.get` returns the default "if the value is not
/// found or is invalid" (`_pytest/cacheprovider.py` l. 129-152).
pub fn read_last_failed(rootdir: &Path) -> HashSet<String> {
    let Ok(content) = std::fs::read_to_string(last_failed_path(rootdir)) else {
        return HashSet::new();
    };
    let Ok(entries) = serde_json::from_str::<BTreeMap<String, bool>>(&content) else {
        return HashSet::new();
    };
    entries
        .into_iter()
        .filter_map(|(id, failed)| failed.then_some(id))
        .collect()
}

/// Merge this run's outcomes into `previous` and return the new cache contents.
///
/// Port of `LFPlugin::pytest_runtest_logreport` (l. 355-363) and
/// `LFPlugin::pytest_collectreport` (l. 365-370):
///
/// ```text
/// if (report.when == "call" and report.passed) or report.skipped:
///     self.lastfailed.pop(report.nodeid, None)
/// elif report.failed:
///     self.lastfailed[report.nodeid] = True
/// ```
///
/// Three consequences worth stating, because each is a decision a naive
/// "write this run's failures" implementation gets wrong:
///
/// * **Entries for tests that did not run are kept.**  That is what makes a `--lf` loop
///   converge: run the 3 failures, fix 1, and the next `--lf` still knows about the other 2.
///   It is also why `-x` and `-k` do not amputate the cache.
/// * **A skip clears an entry**, because `report.skipped` is the first branch.  A test that
///   was failing and is now skipped is no longer a known failure.
/// * **A collection error is an entry**, keyed on the *collector's* id — the file path —
///   because `pytest_collectreport` is the same function.  A run whose file stopped
///   importing therefore still has something to re-run.
///
/// `xfailed`/`xpassed` are neither `failed` nor `skipped` at the wire level here, and pytest
/// agrees: an xfail report carries `outcome == "skipped"` with `wasxfail`, so it hits the
/// *pop* branch.  Both are passed in as non-failures by the caller for that reason.
pub fn merge_last_failed(
    previous: &HashSet<String>,
    outcomes: impl IntoIterator<Item = (String, bool)>,
    collection_errors: impl IntoIterator<Item = String>,
) -> BTreeMap<String, bool> {
    let mut merged: BTreeMap<String, bool> = previous.iter().map(|id| (id.clone(), true)).collect();
    for (id, failed) in outcomes {
        if failed {
            let _ = merged.insert(id, true);
        } else {
            let _ = merged.remove(&id);
        }
    }
    for path in collection_errors {
        let _ = merged.insert(path, true);
    }
    merged
}

/// Write `entries` to the v2 cache file, creating the directory.
///
/// Failures are swallowed for the same reason [`read_last_failed`] swallows them: a
/// read-only checkout must still be able to run its tests.  Returns whether the write
/// happened, which is what the tests assert on.
pub fn write_last_failed(rootdir: &Path, entries: &BTreeMap<String, bool>) -> bool {
    let path = last_failed_path(rootdir);
    let Some(parent) = path.parent() else {
        return false;
    };
    if std::fs::create_dir_all(parent).is_err() {
        return false;
    }
    let Ok(content) = serde_json::to_string_pretty(entries) else {
        return false;
    };
    std::fs::write(&path, content + "\n").is_ok()
}

/// Split `ids` into "failed last time" and "did not", preserving order in both halves.
///
/// The caller applies [`LastFailedMode`] to the result.  Returned as index vectors rather
/// than ids because the caller has to reorder several parallel vectors (the tests, their
/// origins, their report slots) by the same permutation.
pub fn partition_by_last_failed(
    ids: impl IntoIterator<Item = String>,
    last_failed: &HashSet<String>,
) -> (Vec<usize>, Vec<usize>) {
    let mut failed = Vec::new();
    let mut rest = Vec::new();
    for (index, id) in ids.into_iter().enumerate() {
        if last_failed.contains(&id) {
            failed.push(index);
        } else {
            rest.push(index);
        }
    }
    (failed, rest)
}

/// The order (and membership) the selected tests take under `mode`.
///
/// Port of `LFPlugin::pytest_collection_modifyitems` l. 380-407.  The branch that is easy to
/// get wrong is the **empty** one, and it is pytest's own comment: "Running a subset of all
/// tests with recorded failures outside of it" — when nothing in this selection failed last
/// time, `--lf` runs **everything** rather than nothing.  Probed: `pytest --lf -k neverfails`
/// in a tree with recorded failures runs the whole `-k` selection and exits 0, it does not
/// exit 5.
///
/// Returns `None` when the mode changes nothing, so the caller can skip the reorder entirely
/// and keep manifest order byte-identical.
pub fn last_failed_order(
    ids: impl IntoIterator<Item = String>,
    last_failed: &HashSet<String>,
    mode: LastFailedMode,
) -> Option<Vec<usize>> {
    if mode == LastFailedMode::None || last_failed.is_empty() {
        return None;
    }
    let (failed, rest) = partition_by_last_failed(ids, last_failed);
    if failed.is_empty() {
        return None;
    }
    match mode {
        LastFailedMode::None => None,
        LastFailedMode::Only => Some(failed),
        LastFailedMode::First => Some(failed.into_iter().chain(rest).collect()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn set(ids: &[&str]) -> HashSet<String> {
        ids.iter().map(|id| (*id).to_string()).collect()
    }

    fn ids(items: &[&str]) -> Vec<String> {
        items.iter().map(|id| (*id).to_string()).collect()
    }

    /// The file path is versioned by *directory*, so a v1 build cannot see it.  Pinned
    /// literally: the whole point is that this string differs from v1's
    /// `.rustest_cache/lastfailed` (`src/cache.rs`).
    #[test]
    fn the_cache_file_is_under_a_v2_directory() {
        let path = last_failed_path(Path::new("/repo"));
        assert!(path.ends_with("lastfailed"), "{path:?}");
        assert_eq!(
            path,
            Path::new("/repo")
                .join(".rustest_cache")
                .join("v2")
                .join("lastfailed")
        );
    }

    /// The on-disk shape is **pytest's**, probed from a real
    /// `.pytest_cache/v/cache/lastfailed`: an object of id -> `true`.
    #[test]
    fn the_written_document_is_pytests_id_to_true_map() {
        let tmp = TempDir::new().unwrap();
        let entries = merge_last_failed(
            &HashSet::new(),
            [
                ("test_x.py::test_b".to_string(), true),
                ("test_x.py::test_a".to_string(), false),
            ],
            [],
        );
        assert!(write_last_failed(tmp.path(), &entries));

        let raw = std::fs::read_to_string(last_failed_path(tmp.path())).unwrap();
        assert_eq!(raw, "{\n  \"test_x.py::test_b\": true\n}\n");
        assert_eq!(read_last_failed(tmp.path()), set(&["test_x.py::test_b"]));
    }

    /// A missing cache is the first run, not an error.
    #[test]
    fn a_missing_cache_reads_as_empty() {
        let tmp = TempDir::new().unwrap();
        assert!(read_last_failed(tmp.path()).is_empty());
    }

    /// ...and so is a corrupt one: a half-written file must never stop a test run.
    #[test]
    fn a_corrupt_cache_reads_as_empty_rather_than_failing() {
        let tmp = TempDir::new().unwrap();
        let path = last_failed_path(tmp.path());
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, "{not json").unwrap();
        assert!(read_last_failed(tmp.path()).is_empty());
    }

    /// v1's document (`{"failed": [...]}`) is not v2's, and must not decode into a set of
    /// one entry named `failed`.  This is the concrete corruption the directory split
    /// prevents, asserted rather than assumed.
    #[test]
    fn v1s_document_shape_does_not_decode() {
        let tmp = TempDir::new().unwrap();
        let path = last_failed_path(tmp.path());
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, r#"{"failed":["tests\\test_a.py::test_one"]}"#).unwrap();
        assert!(read_last_failed(tmp.path()).is_empty());
    }

    /// The convergence property: entries for tests that did not run this time survive.
    #[test]
    fn entries_for_tests_that_did_not_run_are_kept() {
        let previous = set(&["a::one", "b::two", "c::three"]);
        let merged = merge_last_failed(&previous, [("a::one".to_string(), false)], []);
        assert_eq!(
            merged.keys().cloned().collect::<Vec<_>>(),
            vec!["b::two", "c::three"]
        );
    }

    /// A skip clears an entry — `report.skipped` is pytest's *first* branch, ahead of
    /// `report.failed`.
    #[test]
    fn a_non_failure_clears_its_entry() {
        let merged = merge_last_failed(&set(&["a::one"]), [("a::one".to_string(), false)], []);
        assert!(merged.is_empty());
    }

    /// A collection error is keyed on the collector's id (the file), because pytest routes
    /// `pytest_collectreport` into the same function.
    #[test]
    fn a_collection_error_becomes_an_entry() {
        let merged = merge_last_failed(&HashSet::new(), [], ["tests/test_broken.py".to_string()]);
        assert_eq!(
            merged.keys().cloned().collect::<Vec<_>>(),
            vec!["tests/test_broken.py"]
        );
    }

    /// `--lf` keeps only the previously failed, in manifest order.
    #[test]
    fn only_mode_keeps_the_previously_failed_in_order() {
        let order = last_failed_order(
            ids(&["a", "b", "c", "d"]),
            &set(&["c", "a"]),
            LastFailedMode::Only,
        );
        assert_eq!(order, Some(vec![0, 2]));
    }

    /// `--ff` keeps everything, failures first, each half in manifest order.
    #[test]
    fn first_mode_moves_the_previously_failed_to_the_front() {
        let order = last_failed_order(
            ids(&["a", "b", "c", "d"]),
            &set(&["c", "a"]),
            LastFailedMode::First,
        );
        assert_eq!(order, Some(vec![0, 2, 1, 3]));
    }

    /// pytest's own carve-out: when the current selection contains **none** of the recorded
    /// failures, `--lf` runs everything rather than nothing.  A "filter to the intersection"
    /// implementation would exit 5 here.
    #[test]
    fn a_selection_with_no_recorded_failures_is_left_alone() {
        assert_eq!(
            last_failed_order(ids(&["a", "b"]), &set(&["z"]), LastFailedMode::Only),
            None
        );
        assert_eq!(
            last_failed_order(ids(&["a", "b"]), &set(&["z"]), LastFailedMode::First),
            None
        );
    }

    /// An empty cache (the first run) changes nothing under either flag.
    #[test]
    fn an_empty_cache_changes_nothing() {
        assert_eq!(
            last_failed_order(ids(&["a"]), &HashSet::new(), LastFailedMode::Only),
            None
        );
    }

    /// ...and neither does the default mode, however full the cache is — the reorder must be
    /// skipped entirely so an ordinary run keeps byte-identical manifest order.
    #[test]
    fn the_default_mode_never_reorders() {
        assert_eq!(
            last_failed_order(ids(&["a"]), &set(&["a"]), LastFailedMode::None),
            None
        );
    }
}
