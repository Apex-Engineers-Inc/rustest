//! The **last-failed cache**: what `--lf` and `--ff` read and every run writes.
//!
//! Port of `_pytest/cacheprovider.py::LFPlugin` reduced to the two things a runner without
//! a plugin system needs: a set of node ids that failed last time, and the rules for
//! updating it.
//!
//! # Why the file moved
//!
//! The pre-rewrite engine wrote `.rustest_cache/lastfailed` as `{"failed": [...]}`, keyed on
//! its **display names** — native separators on Windows and a different parametrized-id
//! spelling.  This engine's ids are rootdir-relative posix (`src/engine/nodeid.rs`), so
//! sharing one file would silently have meant "no test matched" on every Windows machine.
//! The two kept independent caches while both engines existed; only one exists now, and the
//! cache sits at the layout below.
//!
//! # The layout inside that directory is pytest's, and that is now load-bearing
//!
//! The *content* shape always was: a JSON object mapping node id to `true`
//! (`_pytest/cacheprovider.py` stores `self.lastfailed: dict[str, bool]` under the cache key
//! `cache/lastfailed`).  Probed against pytest 8.4.2, whose
//! `.pytest_cache/v/cache/lastfailed` after two failing tests is exactly
//! `{"test_x.py::test_b": true, "test_x.py::test_c": true}`.
//!
//! The *path* is pytest's too — `.rustest_cache/v/cache/lastfailed`,
//! where `v/` is `Cache::_CACHE_PREFIX_VALUES` and `cache/lastfailed` is the key.  The reason
//! is the `cache` **fixture**, which landed in the same task: pytest's last-failed set is not
//! private, it is an ordinary cache value that any plugin can read through `config.cache`, and
//! `python/rustest/_builtins.py::Cache` is that API here.  Keeping `--lf`'s file outside
//! the value store would have meant `cache.get("cache/lastfailed", {})` answering `{}` on a
//! run that had just written twelve failures — a fixture that looks like pytest's and lies.
//! The move orphans caches written by earlier builds; they are caches, and the next run
//! rebuilds them.
//!
//! # What is deliberately *not* here
//!
//! `cache/nodeids` (pytest's `--nf`/new-first ordering) and the `--cache-clear` /
//! `--cache-show` surfaces.  Neither is on rustest's CLI, and writing a file nothing reads
//! is how caches rot.

use std::collections::BTreeMap;
use std::collections::HashSet;
use std::path::{Path, PathBuf};

/// Directory under the rootdir that holds every rustest cache: one directory to add to
/// `.gitignore`, one directory to delete.
///
/// The layout inside it is pytest's own — `v/<key>` for values, `d/<name>` for directories —
/// so a suite (or a person) that knows `.pytest_cache` knows this one.  It briefly carried a
/// `v2/` engine-version segment while two engines could both write here; there is one engine
/// now, and the segment named nothing.
pub(crate) const CACHE_DIR: &str = ".rustest_cache";

/// pytest's own sub-directory for cached **values** (`_pytest/cacheprovider.py::Cache::
/// _CACHE_PREFIX_VALUES`), and `cache/lastfailed` is the key it stores the last-failed set
/// under.  Reproduced exactly, because the `cache` fixture is the public API over this store:
/// `python/rustest/_builtins.py::Cache` resolves `cache.get("cache/lastfailed", {})` to
/// this very file, so a suite (or a plugin author, or a person) that knows pytest's cache
/// knows rustest's.  Splitting the two — a private `--lf` file beside a public value store —
/// would have made that fixture a plausible-looking dead end.
const CACHE_VALUES_DIR: &str = "v";

const LAST_FAILED_FILE: &str = "cache/lastfailed";

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

/// Where the last-failed file lives in a cache directory (`ResolvedConfig::cache_dir`).
pub fn last_failed_path(cache_dir: &Path) -> PathBuf {
    cache_dir.join(CACHE_VALUES_DIR).join(LAST_FAILED_FILE)
}

/// Read the previously failed node ids, or an empty set.
///
/// **Never an error.**  A cache is an optimisation: a missing file is the first run, and a
/// corrupt or unreadable one is a cache to be rebuilt, not a reason to refuse to run tests.
/// pytest takes the same position — `Cache.get` returns the default "if the value is not
/// found or is invalid" (`_pytest/cacheprovider.py` l. 129-152).
pub fn read_last_failed(cache_dir: &Path) -> HashSet<String> {
    let Ok(content) = std::fs::read_to_string(last_failed_path(cache_dir)) else {
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

/// Write `entries` to the cache file, creating the directory.
///
/// Failures are swallowed for the same reason [`read_last_failed`] swallows them: a
/// read-only checkout must still be able to run its tests.  Returns whether the write
/// happened, which is what the tests assert on.
pub fn write_last_failed(cache_dir: &Path, entries: &BTreeMap<String, bool>) -> bool {
    let path = last_failed_path(cache_dir);
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

/// The previous runs' per-test durations, in pytest's value layout beside `cache/lastfailed`:
/// test id -> seconds. What the scheduler weighs a file by (`collect.rs::file_weights`).
const DURATIONS_FILE: &str = "rustest/durations";

fn durations_path(cache_dir: &Path) -> PathBuf {
    cache_dir.join(CACHE_VALUES_DIR).join(DURATIONS_FILE)
}

/// The recorded durations, test id -> whole microseconds (a file of thousands of
/// sub-millisecond tests must not sum to nothing), or none: a missing, unreadable or corrupt
/// record is no record. Never an error, like [`read_last_failed`].
pub fn read_durations(cache_dir: &Path) -> BTreeMap<String, u64> {
    std::fs::read_to_string(durations_path(cache_dir))
        .ok()
        .and_then(|content| serde_json::from_str(&content).ok())
        .unwrap_or_default()
}

/// Fold this run's durations (seconds) into the record. A run that selected everything it
/// collected (`complete`) replaces the entries of the files it ran, so deleted or renamed
/// tests leave; a partial run (`-k`, `-m`, node ids, `--lf`, a stop) only overwrites the tests
/// it ran. Written to a temporary file and renamed into place, and a failure is swallowed:
/// the record is a heuristic, never a reason for a run to fail.
pub fn record_durations(
    cache_dir: &Path,
    ran: impl IntoIterator<Item = (String, f64)>,
    complete: bool,
) {
    let ran: Vec<(String, u64)> = ran
        .into_iter()
        .map(|(id, seconds)| (id, (seconds.max(0.0) * 1e6) as u64))
        .collect();
    let mut record = read_durations(cache_dir);
    if complete {
        let files: HashSet<&str> = ran.iter().map(|(id, _)| file_of(id)).collect();
        record.retain(|id, _| !files.contains(file_of(id)));
    }
    record.extend(ran);
    let path = durations_path(cache_dir);
    let temporary = path.with_extension("tmp");
    let written = path
        .parent()
        .is_some_and(|parent| std::fs::create_dir_all(parent).is_ok())
        && serde_json::to_string(&record)
            .is_ok_and(|content| std::fs::write(&temporary, content).is_ok());
    if written && std::fs::rename(&temporary, &path).is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
}

/// The file part of a node id (`tests/test_a.py::test_x` -> `tests/test_a.py`).
pub(crate) fn file_of(id: &str) -> &str {
    id.split("::").next().unwrap_or(id)
}

/// `-p no:cacheprovider`, as the CLI records it in `hook_context.blocked_plugins`: pytest then
/// keeps no cache at all, so rustest reads and writes none.
pub(crate) fn cache_provider_off(hook_context: Option<&serde_json::Value>) -> bool {
    hook_context
        .and_then(|context| context.get("blocked_plugins"))
        .and_then(serde_json::Value::as_array)
        .is_some_and(|names| names.iter().any(|name| name == "cacheprovider"))
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

/// Which files `--lf` collects: pytest's file skipping, from `_pytest/cacheprovider.py`'s
/// `LFPluginCollWrapper` and `LFPluginCollSkipfiles` (pytest 8.4.2, l. 249-315).
///
/// pytest collects with every last-failed path (a remembered id's file, and that file's
/// directories) sorted first in each directory. Skipping **switches on** at the first
/// last-failed file whose *direct* items -- module-level functions and their parametrized
/// cases, not a class's methods -- include a remembered failure. From then on, every file off
/// the last-failed paths is skipped without being imported, and inside each last-failed file
/// a module-level test that did not fail is dropped without being counted as deselected
/// (unless the file was named as an argument). Files collected before the switch are left
/// alone, and if no file switches it on, nothing is skipped at all.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LastFailedSkip {
    /// Per target: collected, or skipped unimported.
    pub collect: Vec<bool>,
    /// Per target: collected once skipping was on, so pytest's filter applies to its
    /// module-level tests.
    pub filtered: Vec<bool>,
}

/// pytest's `--lf` file skipping over `targets` (rootdir-relative posix paths, walk order),
/// or `None` when it does not switch on.
///
/// `named` are the targets of node-id arguments, in argument order: pytest's `Session.collect`
/// collects those files while resolving the arguments, before any last-failed sorting, so they
/// come first. `direct(index)` answers whether target `index` -- a last-failed file -- has a
/// remembered failure among its module-level tests. Before collection only the ids can say
/// (an id with one `::` level, see [`predicted_direct`]); after it, the collected tests can.
pub fn last_failed_skip(
    targets: &[String],
    named: &[usize],
    last_failed: &HashSet<String>,
    direct: impl Fn(usize) -> bool,
) -> Option<LastFailedSkip> {
    // `LFPlugin.get_last_failed_paths`: each failed id's file and every parent directory.
    let mut paths: HashSet<&str> = HashSet::new();
    for id in last_failed {
        let file = id.split("::").next().unwrap_or(id);
        paths.insert(file);
        paths.extend(file.match_indices('/').map(|(end, _)| &file[..end]));
    }
    let is_lf_file: Vec<bool> = targets.iter().map(|t| paths.contains(t.as_str())).collect();

    // The collection order: at every directory, last-failed entries first, otherwise walk
    // order (the sort is stable). Two targets are ordered at their first differing
    // component; walk order already groups each directory's contents together.
    let mut order: Vec<usize> = (0..targets.len())
        .filter(|index| !named.contains(index))
        .collect();
    order.sort_by(|&a, &b| {
        let (left, right) = (targets[a].as_bytes(), targets[b].as_bytes());
        let common = left.iter().zip(right).take_while(|(x, y)| x == y).count();
        // On bytes: two paths can diverge inside a multi-byte character (`é`/`è`), where a
        // `str` slice would panic. `/` is ASCII, so `start` is always a char boundary.
        let start = left[..common]
            .iter()
            .rposition(|&byte| byte == b'/')
            .map_or(0, |slash| slash + 1);
        let component = |path: &str| -> bool {
            let end = path[start..]
                .find('/')
                .map_or(path.len(), |slash| start + slash);
            paths.contains(&path[..end])
        };
        component(&targets[b])
            .cmp(&component(&targets[a]))
            .then(a.cmp(&b))
    });
    let mut first: Vec<usize> = Vec::new();
    for &index in named {
        if !first.contains(&index) {
            first.push(index);
        }
    }
    let order: Vec<usize> = first.into_iter().chain(order).collect();

    let switch = order
        .iter()
        .position(|&index| is_lf_file[index] && direct(index))?;
    let mut collect = is_lf_file.clone();
    let mut filtered = vec![false; targets.len()];
    for (position, &index) in order.iter().enumerate() {
        if position < switch {
            collect[index] = true;
        } else {
            filtered[index] = is_lf_file[index];
        }
    }
    Some(LastFailedSkip { collect, filtered })
}

/// Before collection: the files a remembered id names a module-level test of -- one `::`
/// level before any parametrize id, `file::test_x` or `file::test_x[a::b]`, not
/// `file::Class::test_y`.
pub fn predicted_direct(last_failed: &HashSet<String>) -> HashSet<&str> {
    last_failed
        .iter()
        .filter_map(|id| {
            let (file, rest) = id.split_once("::")?;
            let name = rest.split_once('[').map_or(rest, |(name, _)| name);
            (!name.contains("::")).then_some(file)
        })
        .collect()
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

    /// The file path is versioned by *directory*, so a v1 build cannot see it, and inside
    /// that directory it is **pytest's cache layout**.  Pinned literally on both counts: it
    /// must differ from v1's `.rustest_cache/lastfailed` (`src/cache.rs`), and it must be the
    /// path `python/rustest/_builtins.py::Cache._getvaluepath("cache/lastfailed")` composes
    /// — which is what makes `cache.get("cache/lastfailed", {})` answer with the real set.
    #[test]
    fn the_cache_file_is_under_a_v2_directory_in_pytests_value_layout() {
        let path = last_failed_path(&Path::new("/repo").join(CACHE_DIR));
        assert!(path.ends_with("lastfailed"), "{path:?}");
        assert_eq!(
            path,
            Path::new("/repo")
                .join(".rustest_cache")
                .join("v")
                .join("cache")
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

    /// v1's document (`{"failed": [...]}`) is not the engine's, and must not decode into a set of
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

    // -- --lf file skipping ------------------------------------------------

    fn targets(paths: &[&str]) -> Vec<String> {
        paths.iter().map(|path| (*path).to_string()).collect()
    }

    /// The common case: the first last-failed file has a failing module-level test, so every
    /// other file is skipped and that file's own passing tests are filtered.
    #[test]
    fn a_module_level_failure_switches_skipping_on() {
        let all = targets(&["test_a.py", "test_b.py", "sub/test_c.py"]);
        let lf = set(&["test_a.py::test_x"]);
        let skip = last_failed_skip(&all, &[], &lf, |_| true).expect("skipping switches on");
        assert_eq!(skip.collect, vec![true, false, false]);
        assert_eq!(skip.filtered, vec![true, false, false]);
    }

    /// A failure inside a class does not switch it on: the file's direct item is the class.
    #[test]
    fn class_only_failures_never_switch_skipping_on() {
        let all = targets(&["test_a.py", "test_b.py"]);
        let lf = set(&["test_a.py::TestC::test_x"]);
        assert!(predicted_direct(&lf).is_empty());
        assert_eq!(last_failed_skip(&all, &[], &lf, |_| false), None);
    }

    /// Files collected before the switch are left alone, including a file off the
    /// last-failed paths that pytest reaches first because its directory is a last-failed
    /// path: `d/` sorts first, so `d/test_n.py` is collected before `test_z.py` switches it on.
    #[test]
    fn files_before_the_switch_are_collected_and_not_filtered() {
        let all = targets(&["d/test_a.py", "d/test_n.py", "test_m.py", "test_z.py"]);
        let lf = set(&["d/test_a.py::TestC::test_x", "test_z.py::test_y"]);
        let skip =
            last_failed_skip(&all, &[], &lf, |index| index == 3).expect("test_z switches it on");
        assert_eq!(skip.collect, vec![true, true, false, true]);
        assert_eq!(skip.filtered, vec![false, false, false, true]);
    }

    /// Last-failed entries sort first in every directory, so a later last-failed file is
    /// reached before an earlier sibling that is not one.
    #[test]
    fn last_failed_paths_are_collected_first_at_every_level() {
        let all = targets(&["a/test_1.py", "b/test_2.py", "test_3.py"]);
        let lf = set(&["b/test_2.py::test_x", "test_3.py::Test::test_y"]);
        // `b/` and `test_3.py` sort before `a/`; `b/test_2.py` switches it on first.
        let skip = last_failed_skip(&all, &[], &lf, |index| index == 1).expect("switches on");
        assert_eq!(skip.collect, vec![false, true, true]);
        assert_eq!(skip.filtered, vec![false, true, true]);
    }

    /// Node-id arguments' files are collected in argument order before anything is sorted, so
    /// a named file that is not a last-failed file is collected when it comes first -- and
    /// skipped when the switch came before it (pytest then reports the id `not found`).
    #[test]
    fn node_id_arguments_are_collected_first_in_argument_order() {
        let all = targets(&["sub/test_n.py", "test_m.py"]);
        let lf = set(&["test_m.py::test_b"]);
        let skip = last_failed_skip(&all, &[0, 1], &lf, |index| index == 1).expect("on");
        assert_eq!(skip.collect, vec![true, true]);
        let skip = last_failed_skip(&all, &[1, 0], &lf, |index| index == 1).expect("on");
        assert_eq!(skip.collect, vec![false, true]);
    }

    #[test]
    fn a_collection_error_entry_is_a_last_failed_file_but_not_a_direct_failure() {
        let lf = set(&["test_b.py", "test_a.py::test_x[1]"]);
        assert_eq!(predicted_direct(&lf), HashSet::from(["test_a.py"]));
        let all = targets(&["test_a.py", "test_b.py", "test_c.py"]);
        let skip = last_failed_skip(&all, &[], &lf, |index| index == 0).expect("switches on");
        assert_eq!(skip.collect, vec![true, true, false]);
    }

    /// A parametrize id may contain `::`; only what precedes its `[` counts as levels.
    #[test]
    fn a_parametrize_id_containing_colons_is_still_module_level() {
        let lf = set(&["test_p.py::test_p[a::b]", "test_q.py::Test::test_y[c]"]);
        assert_eq!(predicted_direct(&lf), HashSet::from(["test_p.py"]));
    }

    /// Paths that diverge inside a multi-byte character are ordered without slicing one.
    #[test]
    fn paths_diverging_inside_a_multibyte_character_sort_without_panicking() {
        let all = targets(&[
            "tests/test_\u{e8}.py",
            "tests/test_\u{e9}.py",
            "x/test_\u{8bd5}.py",
        ]);
        let lf = set(&["tests/test_\u{e9}.py::test_x", "x/test_\u{8bd5}.py::test_y"]);
        let skip = last_failed_skip(&all, &[], &lf, |index| index == 1).expect("switches on");
        assert_eq!(skip.collect, vec![false, true, true]);
    }
}
