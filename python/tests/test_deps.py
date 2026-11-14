"""``rustest._deps``: the dependency recorder, one mutation test per kind, one pin per hole.

The module decides whether a stale cached pass can ever happen, so every claim in its docstring
is an executable fact here:

* **detected** -- record a run, mutate that dependency, assert the test is reported stale (and,
  where there is a path, affected by that path);
* **uncacheable** -- the audit event marks the test, with a reason, and an event that must *not*
  mark it (``asyncio``'s socket pair, a plain ``socket()``) does not;
* **hole** -- the dependency exists, the recorder cannot see it, and the test asserts exactly
  that.  When a hole is closed one day, its pin fails and says so.

The files under test are aged (mtime an hour back) before the recorder starts, as a checked-out
project's are; a file newer than the worker is its own, tested, uncacheable case.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from rustest import _deps
from rustest._deps import Recorder as _Recorder
from rustest._deps import (
    ABSENT,
    Record,
    Unavailable,
    affected_by_paths,
    merge,
    session_prefix,
    stale,
)

_HOUR_NS = 3_600 * 1_000_000_000


class Recorder(_Recorder):
    """A recorder that does not track this very file: it is edited while the tests are written, and
    a code file younger than the racy margin would make every test here uncacheable."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("ignore", [str(Path(__file__).resolve().parent)])
        super().__init__(**kwargs)


def _n(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(path))


class Project:
    """A throwaway first-party tree: write files, age them, import modules from it."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.loaded: list[str] = []
        monkeypatch.syspath_prepend(str(root))

    def write(self, name: str, text: str = "", *, age: bool = True) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if age:
            self.age(path)
        return path

    def age(self, path: Path) -> None:
        old = time.time_ns() - _HOUR_NS
        os.utime(path, ns=(old, old))

    def load(self, name: str) -> ModuleType:
        importlib.invalidate_caches()
        module = importlib.import_module(name)
        self.loaded.append(name)
        return module

    def p(self, name: str) -> str:
        return _n(self.root / name)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Project]:
    proj = Project(tmp_path / "proj", monkeypatch)
    proj.root.mkdir()
    yield proj
    for name in proj.loaded:
        for loaded in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
            del sys.modules[loaded]
    importlib.invalidate_caches()


def record(
    body: Callable[[], object],
    *,
    root: Path | None = None,
    static: tuple[str | os.PathLike[str], ...] = (),
    started_ns: int | None = None,
) -> Record:
    recorder = Recorder(started_ns=started_ns, root=root)
    recorder.start()
    try:
        recorder.begin("t", static)
        try:
            body()
        finally:
            result = recorder.end("t")
    finally:
        recorder.stop()
    return result


def import_deps(rec: Record) -> set[tuple[str, str]]:
    """``(module name, fingerprint)`` of every ``import`` dependency."""
    return {(key.split(chr(0))[0], fp) for kind, key, fp in rec.deps if kind == "import"}


def import_names(rec: Record) -> set[str]:
    return {name for name, _ in import_deps(rec)}


def keys(rec: Record, kind: str) -> set[str]:
    return {key for k, key, _ in rec.deps if k == kind}


def stale_after(rec: Record, mutate: Callable[[], object]) -> dict[str, list[Any]]:
    """What ``stale`` says about ``rec`` after ``mutate()`` -- and nothing before it."""
    assert stale({"t": rec}) == {}, "a fresh record must not be stale"
    mutate()
    return stale({"t": rec})


# --------------------------------------------------------------------------------------
# detected: code
# --------------------------------------------------------------------------------------


def test_a_first_party_function_that_ran_is_a_code_dependency(project: Project) -> None:
    lib = project.write("lib_code.py", "def f():\n    return 1\n")
    module = project.load("lib_code")

    rec = record(module.f)

    assert rec.cacheable and project.p("lib_code.py") in keys(rec, "code")
    changed = stale_after(rec, lambda: lib.write_text("def f():\n    return 2\n"))
    assert [(k, key) for k, key, _ in changed["t"]] == [("code", project.p("lib_code.py"))]
    assert affected_by_paths({"t": rec}, [lib]) == {"t": [project.p("lib_code.py")]}


def test_only_the_files_that_ran_are_dependencies(project: Project) -> None:
    project.write("used_mod.py", "def f():\n    return 1\n")
    other = project.write("unused_mod.py", "def g():\n    return 1\n")
    used = project.load("used_mod")
    _ = project.load("unused_mod")

    rec = record(used.f)

    assert project.p("unused_mod.py") not in keys(rec, "code")
    assert affected_by_paths({"t": rec}, [other]) == {}
    assert stale_after(rec, lambda: other.write_text("def g():\n    return 2\n")) == {}


def test_the_stdlib_and_site_packages_and_rustest_are_not_recorded(project: Project) -> None:
    import json as stdlib_json

    rec = record(lambda: stdlib_json.dumps({"a": 1}))
    codes = keys(rec, "code")
    assert not any("json" + os.sep + "encoder" in c or "site-packages" in c for c in codes)
    package = _n(Path(_deps.__file__).parent) + os.sep
    assert not any(c.startswith(package) for c in codes)


def test_a_module_constant_read_without_running_its_code_is_a_dependency_by_import_closure(
    project: Project,
) -> None:
    """``assert lib.CONST == 1`` runs no function of ``lib``; only the test module's static
    imports (and a conftest's, passed as ``static``) can know."""
    lib = project.write("lib_const.py", "CONST = 1\n")
    test_module = project.write(
        "test_const_mod.py", "import lib_const\n\ndef test_it():\n    assert lib_const.CONST == 1\n"
    )
    module = project.load("test_const_mod")

    rec = record(module.test_it, static=(test_module,))

    assert project.p("lib_const.py") in keys(rec, "code")  # not executed: closure
    assert project.p("test_const_mod.py") in keys(rec, "code")
    assert stale_after(rec, lambda: lib.write_text("CONST = 2\n"))["t"][0][1] == project.p(
        "lib_const.py"
    )


def test_the_closure_follows_relative_and_from_imports_through_packages(project: Project) -> None:
    project.write("pkg_a/__init__.py", "")
    leaf = project.write("pkg_a/leaf.py", "VALUE = 1\n")
    project.write("pkg_a/mid.py", "from . import leaf\nfrom .leaf import VALUE\n")
    entry = project.write("entry_mod.py", "from pkg_a import mid\n")
    _ = project.load("entry_mod")
    _ = project.load("pkg_a.mid")

    rec = record(lambda: None, static=(entry,))

    assert {
        project.p("entry_mod.py"),
        project.p("pkg_a/mid.py"),
        project.p("pkg_a/leaf.py"),
    } <= keys(rec, "code")
    assert project.p("pkg_a/__init__.py") in keys(rec, "code")  # importing a.b imports a
    assert affected_by_paths({"t": rec}, [leaf]) == {"t": [project.p("pkg_a/leaf.py")]}


# --------------------------------------------------------------------------------------
# detected: files, listings
# --------------------------------------------------------------------------------------

_READERS = {
    "open": lambda p: open(p, encoding="utf-8").read(),
    "pathlib": lambda p: Path(p).read_text(encoding="utf-8"),
    "os.open": lambda p: os.close(os.open(p, os.O_RDONLY)),
    "binary": lambda p: Path(p).read_bytes(),
}


@pytest.mark.parametrize("how", sorted(_READERS))
def test_a_file_read_through_python_is_a_dependency(project: Project, how: str) -> None:
    data = project.write("data.txt", "one")

    rec = record(lambda: _READERS[how](str(data)))

    assert rec.cacheable and project.p("data.txt") in keys(rec, "file")
    assert stale_after(rec, lambda: data.write_text("two"))["t"][0][:2] == (
        "file",
        project.p("data.txt"),
    )
    assert affected_by_paths({"t": rec}, [data]) == {"t": [project.p("data.txt")]}


def test_a_missing_file_is_a_dependency_on_its_absence(project: Project) -> None:
    missing = project.root / "later.txt"

    def body() -> None:
        try:
            _ = missing.read_text()
        except FileNotFoundError:
            pass

    rec = record(body)

    assert (("file", project.p("later.txt"), ABSENT)) in rec.deps
    changed = stale_after(rec, lambda: missing.write_text("now it exists"))
    assert changed["t"][0][:2] == ("file", project.p("later.txt"))


def test_package_data_read_through_importlib_resources_is_a_file_dependency(
    project: Project,
) -> None:
    project.write("respkg/__init__.py", "")
    data = project.write("respkg/payload.txt", "v1")
    _ = project.load("respkg")

    def body() -> None:
        from importlib import resources

        _ = resources.files("respkg").joinpath("payload.txt").read_text(encoding="utf-8")

    rec = record(body)

    assert project.p("respkg/payload.txt") in keys(rec, "file")
    assert stale_after(rec, lambda: data.write_text("v2"))["t"][0][1] == project.p(
        "respkg/payload.txt"
    )


def test_a_resource_inside_a_zip_is_seen_as_the_archive(project: Project) -> None:
    """Not a hole after all: the zip is read through Python's ``open``."""
    archive = project.root / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("zippkg/__init__.py", "")
        handle.writestr("zippkg/payload.txt", "v1")
    project.age(archive)
    sys.path.insert(0, str(archive))
    try:
        _ = project.load("zippkg")

        def body() -> None:
            from importlib import resources

            _ = resources.files("zippkg").joinpath("payload.txt").read_text(encoding="utf-8")

        rec = record(body)
        assert project.p("bundle.zip") in keys(rec, "file")

        def mutate() -> None:
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("zippkg/__init__.py", "")
                handle.writestr("zippkg/payload.txt", "v2")

        assert stale_after(rec, mutate)["t"][0][1] == project.p("bundle.zip")
    finally:
        sys.path.remove(str(archive))


_LISTERS = {
    "os.listdir": lambda d: os.listdir(d),
    "os.scandir": lambda d: [e.name for e in os.scandir(d)],
    "pathlib.iterdir": lambda d: [p.name for p in Path(d).iterdir()],
    "glob": lambda d: __import__("glob").glob(os.path.join(d, "*.txt")),
    "pathlib.glob": lambda d: list(Path(d).glob("*.txt")),
    "os.walk": lambda d: list(os.walk(d)),
}


@pytest.mark.parametrize("how", sorted(_LISTERS))
def test_a_directory_listed_is_a_dependency_on_its_names(project: Project, how: str) -> None:
    project.write("listed/a.txt", "a")
    folder = project.root / "listed"

    rec = record(lambda: _LISTERS[how](str(folder)))

    assert project.p("listed") in keys(rec, "listing")
    changed = stale_after(rec, lambda: (folder / "b.txt").write_text("b"))
    assert changed["t"][0][:2] == ("listing", project.p("listed"))
    # a file appearing in the directory is the change `--related` is asked about
    assert affected_by_paths({"t": rec}, [folder / "b.txt"]) == {"t": [_n(folder / "b.txt")]}


def test_removing_a_listed_name_changes_the_listing(project: Project) -> None:
    gone = project.write("shrink/a.txt", "a")
    project.write("shrink/b.txt", "b")

    rec = record(lambda: os.listdir(project.root / "shrink"))

    assert stale_after(rec, gone.unlink)["t"][0][0] == "listing"


# --------------------------------------------------------------------------------------
# detected: environment
# --------------------------------------------------------------------------------------

_ENV_READERS = {
    "environ[]": lambda: os.environ["RUSTEST_DEPS_PROBE"],
    "environ.get": lambda: os.environ.get("RUSTEST_DEPS_PROBE"),
    "in": lambda: "RUSTEST_DEPS_PROBE" in os.environ,
    "getenv": lambda: os.getenv("RUSTEST_DEPS_PROBE"),
}


@pytest.mark.parametrize("how", sorted(_ENV_READERS))
def test_an_environment_variable_read_is_a_dependency(
    monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    monkeypatch.setenv("RUSTEST_DEPS_PROBE", "one")

    def read() -> None:
        try:
            _ = _ENV_READERS[how]()
        except KeyError:
            pass

    rec = record(read)

    assert "RUSTEST_DEPS_PROBE" in keys(rec, "env")
    assert stale_after(rec, lambda: monkeypatch.setenv("RUSTEST_DEPS_PROBE", "two"))["t"][0][
        :2
    ] == (
        "env",
        "RUSTEST_DEPS_PROBE",
    )


def test_an_unset_variable_is_a_dependency_on_being_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUSTEST_DEPS_PROBE", raising=False)

    rec = record(lambda: os.environ.get("RUSTEST_DEPS_PROBE"))

    assert ("env", "RUSTEST_DEPS_PROBE", "unset") in rec.deps
    assert stale_after(rec, lambda: monkeypatch.setenv("RUSTEST_DEPS_PROBE", "x"))


def test_only_the_variables_read_are_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUSTEST_DEPS_READ", "a")
    monkeypatch.setenv("RUSTEST_DEPS_OTHER", "a")

    rec = record(lambda: os.environ.get("RUSTEST_DEPS_READ"))

    assert keys(rec, "env") == {"RUSTEST_DEPS_READ"}
    assert stale_after(rec, lambda: monkeypatch.setenv("RUSTEST_DEPS_OTHER", "b")) == {}


@pytest.mark.parametrize(
    "how", [lambda: dict(os.environ), lambda: len(os.environ), lambda: list(os.environ)]
)
def test_reading_the_whole_environment_depends_on_all_of_it(
    monkeypatch: pytest.MonkeyPatch, how: Callable[[], object]
) -> None:
    rec = record(how)

    assert "*" in keys(rec, "env")
    changed = stale_after(rec, lambda: monkeypatch.setenv("RUSTEST_DEPS_ANY", "1"))
    assert ("env", "*") in [(k, key) for k, key, _ in changed["t"]]


def test_a_variable_the_test_set_itself_is_not_an_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUSTEST_DEPS_SELF", raising=False)

    def body() -> None:
        os.environ["RUSTEST_DEPS_SELF"] = "mine"
        _ = os.environ["RUSTEST_DEPS_SELF"]
        del os.environ["RUSTEST_DEPS_SELF"]

    rec = record(body)

    assert "RUSTEST_DEPS_SELF" not in keys(rec, "env")


def test_the_volatile_variable_pytest_sets_per_test_does_not_make_the_whole_env_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "a::b (call)")
    rec = record(lambda: len(os.environ))
    assert stale_after(rec, lambda: monkeypatch.setenv("PYTEST_CURRENT_TEST", "c::d (call)")) == {}


def test_reading_the_environment_does_not_change_what_a_test_observes() -> None:
    """No ``os.environ`` is replaced or wrapped: the object, its class and its methods are the
    same during a recording."""
    before = (os.environ, type(os.environ), type(os.environ).__getitem__)
    seen: list[object] = []
    _ = record(lambda: seen.extend((os.environ, type(os.environ), type(os.environ).__getitem__)))
    assert tuple(seen) == before


# --------------------------------------------------------------------------------------
# detected: imports during the test
# --------------------------------------------------------------------------------------


def test_a_first_party_module_imported_during_the_test_is_an_import_dependency(
    project: Project,
) -> None:
    lib = project.write("late_import_mod.py", "CONST = 1\n")

    def body() -> None:
        _ = __import__("late_import_mod")
        project.loaded.append("late_import_mod")

    rec = record(body)

    assert "late_import_mod" in import_names(rec)
    changed = stale_after(rec, lambda: lib.write_text("CONST = 2\n"))
    assert {k for k, _, _ in changed["t"]} >= {"code"}  # its content is the code dependency's
    assert affected_by_paths({"t": rec}, [lib])["t"] == [project.p("late_import_mod.py")]


def test_an_import_that_failed_is_a_dependency_on_its_absence(project: Project) -> None:
    def body() -> None:
        try:
            _ = __import__("optional_dep_zz")
        except ImportError:
            pass

    rec = record(body)

    assert ("optional_dep_zz", ABSENT) in import_deps(rec)

    def appear() -> None:
        project.write("optional_dep_zz.py", "X = 1\n")
        project.loaded.append("optional_dep_zz")

    changed = stale_after(rec, appear)
    assert any(d[0] == "import" and d[1].startswith("optional_dep_zz") for d in changed["t"])
    assert affected_by_paths({"t": rec}, [project.root / "optional_dep_zz.py"]) == {
        "t": [project.p("optional_dep_zz.py")]
    }


def test_importlib_import_module_of_a_present_module_is_seen_as_its_file(
    project: Project,
) -> None:
    lib = project.write("viaimportlib_mod.py", "CONST = 1\n")

    def body() -> None:
        _ = importlib.import_module("viaimportlib_mod")
        project.loaded.append("viaimportlib_mod")

    rec = record(body)

    assert project.p("viaimportlib_mod.py") in keys(rec, "file") | keys(rec, "code")
    assert stale_after(rec, lambda: lib.write_text("CONST = 2\n"))


def test_hole_importlib_import_module_of_an_absent_module_is_invisible(project: Project) -> None:
    """No ``import`` event from ``importlib.import_module``: a module that was not there, and
    then is, is not noticed."""

    project.write("primes_the_finder_cache.py", "")
    _ = project.load("primes_the_finder_cache")  # the directory is listed once, here

    def body() -> None:
        try:
            _ = importlib.import_module("absent_via_importlib_zz")
        except ImportError:
            pass

    rec = record(body)

    def appear() -> None:
        project.write("absent_via_importlib_zz.py", "X = 1\n")

    assert "absent_via_importlib_zz" not in import_names(rec)
    # (the import system's own directory listing may still catch it, incidentally; the
    # `import` dependency is what is missing)
    changed = stale_after(rec, appear)
    assert not [d for d in changed.get("t", []) if d[0] == "import"]


def test_standard_library_imports_are_not_dependencies() -> None:
    def body() -> None:
        sys.modules.pop("colorsys", None)
        _ = importlib.import_module("colorsys")

    rec = record(body)
    assert not any(k == "import" and key == "colorsys" for k, key, _ in rec.deps)


# --------------------------------------------------------------------------------------
# detected: sqlite
# --------------------------------------------------------------------------------------


def _make_db(path: Path, value: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute("create table if not exists t (v integer)")
    conn.execute("delete from t")
    conn.execute("insert into t values (?)", (value,))
    conn.commit()
    conn.close()


def test_a_sqlite_database_the_test_opens_is_a_file_dependency(project: Project) -> None:
    db = project.root / "app.db"
    _make_db(db, 1)
    project.age(db)

    def body() -> None:
        conn = sqlite3.connect(db)
        _ = conn.execute("select v from t").fetchall()
        conn.close()

    rec = record(body)

    assert rec.cacheable and project.p("app.db") in keys(rec, "file")

    def mutate() -> None:
        _make_db(db, 2)

    assert stale_after(rec, mutate)["t"][0][1] == project.p("app.db")


def test_a_database_the_test_creates_or_keeps_in_memory_is_not_a_dependency(
    project: Project,
) -> None:
    def body() -> None:
        sqlite3.connect(":memory:").close()
        sqlite3.connect(project.root / "made_here.db").close()

    rec = record(body)

    assert rec.cacheable and not keys(rec, "file") & {project.p("made_here.db")}


def test_a_sqlite_uri_is_uncacheable(project: Project) -> None:
    rec = record(lambda: sqlite3.connect("file::memory:?cache=shared", uri=True).close())
    assert not rec.cacheable and any("sqlite3 URI" in r for r in rec.reasons)


# --------------------------------------------------------------------------------------
# what the test itself wrote is output, not input
# --------------------------------------------------------------------------------------


def test_files_and_directories_the_test_created_are_not_dependencies(project: Project) -> None:
    out = project.root / "out"

    def body() -> None:
        out.mkdir()
        (out / "a.txt").write_text("1")
        _ = (out / "a.txt").read_text()
        _ = os.listdir(out)
        shutil.copyfile(out / "a.txt", out / "b.txt")
        _ = (out / "b.txt").read_text()
        made = tempfile.mkdtemp(dir=project.root)
        (Path(made) / "c.txt").write_text("c")
        _ = (Path(made) / "c.txt").read_text()
        os.rename(out / "b.txt", out / "moved.txt")
        _ = (out / "moved.txt").read_text()

    rec = record(body)

    assert rec.cacheable, rec.reasons
    assert not any(
        str(project.root) in key.replace("\\", "/") or "proj" in key
        for _, key, _ in rec.deps
        if "out" in key
    )
    assert not [d for d in rec.deps if d[0] in ("file", "listing")]


def test_reading_a_file_and_then_rewriting_it_is_uncacheable(project: Project) -> None:
    data = project.write("counter.txt", "1")

    def body() -> None:
        value = int(data.read_text())
        data.write_text(str(value + 1))

    rec = record(body)

    assert not rec.cacheable and any("modifies a pre-existing file" in r for r in rec.reasons)


def test_deleting_a_file_it_read_is_uncacheable(project: Project) -> None:
    data = project.write("ephemeral.txt", "1")

    def body() -> None:
        _ = data.read_text()
        data.unlink()

    rec = record(body)

    assert not rec.cacheable and any("modifies a pre-existing file" in r for r in rec.reasons)


_CHANGERS: dict[str, Callable[[str], object]] = {
    "r+": lambda p: open(p, "r+", encoding="utf-8").close(),
    "a": lambda p: open(p, "a", encoding="utf-8").close(),
    "w": lambda p: open(p, "w", encoding="utf-8").close(),
    "wb+": lambda p: open(p, "wb+").close(),
    "Path.write_text": lambda p: Path(p).write_text("new", encoding="utf-8"),
    "os.remove": os.remove,
    "Path.unlink": lambda p: Path(p).unlink(),
    "os.rename away": lambda p: os.rename(p, p + ".moved"),
    "os.replace": lambda p: os.replace(p, p + ".moved"),
    "shutil.move": lambda p: shutil.move(p, p + ".moved"),
    "os.utime": lambda p: os.utime(p, None),
    "os.chmod": lambda p: os.chmod(p, 0o644),
    "os.open O_WRONLY": lambda p: os.close(os.open(p, os.O_WRONLY)),
}


@pytest.mark.parametrize("how", sorted(_CHANGERS))
@pytest.mark.parametrize("read_first", [False, True])
def test_changing_a_pre_existing_file_makes_the_test_uncacheable(
    project: Project, how: str, read_first: bool
) -> None:
    """The file existed, so it is an input the test changes: the next run would see a different
    world.  Reading it first or not makes no difference (it used to count as the test's own)."""
    victim = project.write("victim.txt", "old")

    def body() -> None:
        if read_first:
            _ = victim.read_text()
        _ = _CHANGERS[how](str(victim))

    rec = record(body)

    assert not rec.cacheable, how
    assert any("modifies a pre-existing file" in r for r in rec.reasons), rec.reasons


def test_renaming_onto_an_existing_file_is_uncacheable_and_renaming_what_it_made_is_not(
    project: Project,
) -> None:
    existing = project.write("target.txt", "old")

    def over() -> None:
        made = project.root / "made.txt"
        made.write_text("new")
        os.replace(made, existing)

    def own() -> None:
        made = project.root / "made2.txt"
        made.write_text("new")
        os.replace(made, project.root / "renamed2.txt")
        _ = (project.root / "renamed2.txt").read_text()

    assert not record(over).cacheable
    assert record(own).cacheable


@pytest.mark.parametrize("how", ["copyfile", "copy", "copy2", "copy via audit event"])
def test_a_copy_reads_its_source_and_creates_its_destination(project: Project, how: str) -> None:
    """The fixture pattern ``shutil.copy2(DATA / "in.csv", tmp_path)``.  On Windows ``copy2`` raises
    only ``_winapi.CopyFile2`` (no ``open``, no ``shutil.copyfile``)."""
    source = project.write("in.csv", "a,b")
    destination = project.root / "copied.csv"

    def body() -> None:
        if how == "copyfile":
            _ = shutil.copyfile(source, destination)
        elif how == "copy":
            _ = shutil.copy(source, destination)
        elif how == "copy2":
            _ = shutil.copy2(source, destination)
        else:
            sys.audit("_winapi.CopyFile2", str(source), str(destination), 0)
            destination.write_text("a,b")
        _ = destination.read_text()

    rec = record(body)

    assert rec.cacheable, rec.reasons
    assert project.p("in.csv") in keys(rec, "file")
    assert project.p("copied.csv") not in keys(rec, "file")
    assert stale_after(rec, lambda: source.write_text("a,b,c"))["t"][0][1] == project.p("in.csv")


def test_copying_over_an_existing_destination_is_uncacheable(project: Project) -> None:
    source = project.write("src.txt", "a")
    destination = project.write("dst.txt", "b")
    rec = record(lambda: shutil.copy2(source, destination))
    assert not rec.cacheable and any("modifies a pre-existing file" in r for r in rec.reasons)


# --------------------------------------------------------------------------------------
# uncacheable
# --------------------------------------------------------------------------------------


def _connect_refused() -> None:
    try:
        socket.create_connection(("127.0.0.1", 9), timeout=0.2).close()
    except OSError:
        pass


def _bind_loopback() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))


def _urlopen() -> None:
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen("http://127.0.0.1:9/", timeout=0.2)  # noqa: S310
    except (urllib.error.URLError, OSError):
        pass


def _mmap() -> None:
    import mmap

    mmap.mmap(-1, 16).close()


def _ctypes() -> None:
    import ctypes
    import ctypes.util

    library = ctypes.util.find_library("c") or ctypes.util.find_library("msvcrt")
    _ = ctypes.CDLL(library)


_UNCACHEABLE_CASES = {
    "subprocess.run": (
        lambda: subprocess.run([sys.executable, "-c", "pass"], check=False),
        "subprocess",
    ),
    "os.system": (lambda: os.system("exit 0"), "subprocess"),  # noqa: S605
    "socket.connect": (_connect_refused, "network"),
    "socket.bind": (_bind_loopback, "network"),
    "socket.getaddrinfo": (lambda: socket.getaddrinfo("localhost", 80), "network"),
    "urllib": (_urlopen, "network"),
    "mmap": (_mmap, "native code (mmap)"),
    "ctypes": (_ctypes, "native code (ctypes)"),
}


@pytest.mark.parametrize("name", sorted(_UNCACHEABLE_CASES))
def test_an_event_that_cannot_be_an_input_makes_the_test_uncacheable(name: str) -> None:
    body, reason = _UNCACHEABLE_CASES[name]

    rec = record(body)

    assert not rec.cacheable, name
    assert any(reason in r for r in rec.reasons), rec.reasons


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows registry")
def test_the_windows_registry_is_uncacheable() -> None:
    import winreg

    rec = record(lambda: winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")))
    assert not rec.cacheable and any("registry" in r for r in rec.reasons)


def test_asyncio_and_a_bare_socket_do_not_make_a_test_uncacheable() -> None:
    """``asyncio.run`` makes a socket pair (on Windows a loopback bind and connect) per loop."""
    import asyncio

    def body() -> None:
        asyncio.run(asyncio.sleep(0))
        socket.socket().close()
        left, right = socket.socketpair()
        left.close()
        right.close()

    rec = record(body)

    assert rec.cacheable, rec.reasons


def test_a_failing_recorder_never_raises_into_the_test_and_makes_it_uncacheable(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = project.write("fragile.txt", "x")

    def boom(self: Recorder, args: Any) -> None:
        raise RuntimeError("bug in the recorder")

    monkeypatch.setattr(Recorder, "_on_open", boom)
    seen: list[str] = []

    rec = record(lambda: seen.append(data.read_text()))

    assert seen == ["x"]  # the test still ran, and read its file
    assert not rec.cacheable and any("recorder failed on open" in r for r in rec.reasons)


def test_a_file_newer_than_the_worker_is_uncacheable(project: Project) -> None:
    """The process may hold older code than the file now has."""
    started = time.time_ns()
    fresh = project.write("fresh_mod.py", "def f():\n    return 1\n", age=False)
    module = project.load("fresh_mod")

    rec = record(module.f, started_ns=started)

    assert not rec.cacheable
    assert any("fresh_mod.py was modified after the worker started" in r for r in rec.reasons)
    assert fresh.exists()


def test_a_data_file_is_fingerprinted_as_it_was_opened_however_young_it_is(
    project: Project,
) -> None:
    """The hash is taken when the file is opened, so what is recorded is what the test could read:
    a file newer than the worker is fine, and an edit after the read makes the record stale."""
    started = time.time_ns()
    fresh = project.write("fresh.txt", "x", age=False)

    rec = record(fresh.read_text, started_ns=started)

    assert rec.cacheable and project.p("fresh.txt") in keys(rec, "file")
    assert stale_after(rec, lambda: fresh.write_text("y"))


def test_a_code_file_is_fingerprinted_when_it_is_imported_not_at_the_end(
    project: Project,
) -> None:
    """The recorder starts before the import (as the host must).  The file is then edited and its
    old mtime put back (a coarse timestamp): the fingerprint is still of what was imported."""
    path = project.write("imported_mod.py", "def f():\n    return 1\n")
    recorder = Recorder()
    recorder.start()
    try:
        module = project.load("imported_mod")  # fingerprinted inside this import
        old = os.stat(path)
        _deps._tls.busy = True  # pyright: ignore[reportPrivateUsage]  # an editor, not the code
        try:
            path.write_text("def f():\n    return 2\n")
            os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))  # same mtime, same size
        finally:
            _deps._tls.busy = False  # pyright: ignore[reportPrivateUsage]
        recorder.begin("t")
        assert module.f() == 1  # the code in memory is the old code
        rec = recorder.end("t")
    finally:
        recorder.stop()

    assert rec.cacheable, rec.reasons
    assert stale({"t": rec})["t"][0][:2] == ("code", project.p("imported_mod.py"))


def test_a_code_file_first_seen_executing_after_its_import_needs_two_seconds_of_age(
    project: Project,
) -> None:
    """Imported before the recorder started, so its load time is unknown: a margin that covers
    FAT's two-second timestamps."""
    young = project.write("lazy_young.py", "def f():\n    return 1\n", age=False)
    old = time.time_ns() - 1_000_000_000
    os.utime(young, ns=(old, old))  # a second old: fine at import, not for an unknown load time
    _ = project.write("lazy_older.py", "def f():\n    return 1\n")
    module_young = project.load("lazy_young")
    module_older = project.load("lazy_older")

    assert not record(module_young.f).cacheable
    assert record(module_older.f).cacheable


def test_a_file_that_changes_while_the_test_runs_is_uncacheable(project: Project) -> None:
    data = project.write("moving.txt", "x")

    def body() -> None:
        _ = data.read_text()
        os.utime(data, None)  # an edit by someone else, with no audit event

    rec = record(body)

    assert not rec.cacheable and any("changed while the test ran" in r for r in rec.reasons)


def test_a_file_the_test_reads_while_missing_and_then_creates_is_uncacheable(
    project: Project,
) -> None:
    late = project.root / "created_by_the_test.txt"

    def body() -> None:
        try:
            _ = late.read_text()
        except FileNotFoundError:
            late.write_text("now")

    rec = record(body)

    assert not rec.cacheable and any("creates a file it found missing" in r for r in rec.reasons)


def test_a_file_that_appears_while_the_test_runs_without_an_event_is_uncacheable(
    project: Project,
) -> None:
    """Another process creates it: nothing is audited, the end-of-test stat notices."""
    late = project.root / "appears.txt"

    def body() -> None:
        try:
            _ = late.read_text()
        except FileNotFoundError:
            pass
        _deps._tls.busy = True  # pyright: ignore[reportPrivateUsage]  # the recorder looks away
        try:
            late.write_text("from elsewhere")
        finally:
            _deps._tls.busy = False  # pyright: ignore[reportPrivateUsage]

    rec = record(body)

    assert not rec.cacheable and any("appeared while the test ran" in r for r in rec.reasons)


def test_a_code_file_edited_mid_run_keeps_the_fingerprint_of_what_is_in_memory(
    project: Project,
) -> None:
    """The in-memory code cannot change, so a later edit may only make a *later* comparison
    differ -- the safe direction: the second test is recorded with the first fingerprint and is
    stale as soon as the file differs."""
    lib = project.write("midrun_mod.py", "def f():\n    return 1\n")
    module = project.load("midrun_mod")
    recorder = Recorder()
    recorder.start()
    try:
        recorder.begin("t1")
        module.f()
        first = recorder.end("t1")
        lib.write_text("def f():\n    return 2\n")  # an edit; `module` still runs the old code
        recorder.begin("t2")
        module.f()
        second = recorder.end("t2")
    finally:
        recorder.stop()

    path = project.p("midrun_mod.py")
    fingerprint = {d[2] for d in first.deps if d[1] == path}
    assert fingerprint == {d[2] for d in second.deps if d[1] == path}
    assert list(stale({"t1": first, "t2": second})) == ["t1", "t2"]


def test_every_test_on_one_recorder_sees_the_code_it_runs(project: Project) -> None:
    """Locations disabled after their first report are re-enabled at each ``begin``."""
    project.write("again_mod.py", "def f():\n    return 1\n")
    module = project.load("again_mod")
    recorder = Recorder()
    recorder.start()
    try:
        records = []
        for name in ("a", "b", "c"):
            recorder.begin(name)
            module.f()
            records.append(recorder.end(name))
    finally:
        recorder.stop()
    assert all(project.p("again_mod.py") in keys(r, "code") for r in records)


def test_code_run_in_other_threads_is_attributed_to_the_test(project: Project) -> None:
    from concurrent.futures import ThreadPoolExecutor

    data = project.write("pool_data.txt", "x")
    project.write("pool_mod.py", "def work(n):\n    return n + 1\n")
    module = project.load("pool_mod")

    def body() -> None:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(module.work, range(8))) == list(range(1, 9))
            assert set(pool.map(lambda _: data.read_text(), range(4))) == {"x"}

    rec = record(body)

    assert project.p("pool_mod.py") in keys(rec, "code")
    assert project.p("pool_data.txt") in keys(rec, "file")


def test_one_threads_recorder_work_never_hides_another_threads_events(project: Project) -> None:
    """The guard is per thread: while *this* thread is inside the recorder, another thread's reads
    and calls are still recorded, and a code object whose event this thread dropped is not
    disabled (it reports again the next time)."""
    data = project.write("busy_data.txt", "x")
    project.write("busy_other.py", "def other():\n    return 1\n")
    project.write("busy_dropped.py", "def dropped():\n    return 1\n")
    other = project.load("busy_other")
    dropped = project.load("busy_dropped")

    def body() -> None:
        _deps._tls.busy = True  # pyright: ignore[reportPrivateUsage]  # this thread is "inside"
        try:
            worker = threading.Thread(target=lambda: (data.read_text(), other.other()))
            worker.start()
            worker.join()
            dropped.dropped()  # this thread's own event: dropped
        finally:
            _deps._tls.busy = False  # pyright: ignore[reportPrivateUsage]
        dropped.dropped()  # and recorded now: it was not disabled

    rec = record(body)

    assert project.p("busy_data.txt") in keys(rec, "file")
    assert project.p("busy_other.py") in keys(rec, "code")
    assert project.p("busy_dropped.py") in keys(rec, "code")


_RESUMED = """
class Suspend:
    def __await__(self):
        yield

def gen():
    yield 1
    total = 2 * 3
    yield total

async def agen():
    yield 1
    value = 4 * 5
    yield value

async def co():
    await Suspend()
    return 7 * 8

def thrower():
    try:
        yield 1
    except ValueError:
        result = 9 * 9
        yield result
"""


@pytest.mark.parametrize("kind", ["generator", "async generator", "coroutine", "throw"])
def test_a_generator_or_coroutine_started_earlier_and_resumed_in_the_test_is_recorded(
    project: Project, kind: str
) -> None:
    """``PY_START`` fires once, when the body starts; the code that runs after a ``yield`` or an
    ``await`` is a ``PY_RESUME`` (or ``PY_THROW``), and its file may be nowhere else."""
    project.write("resumed_mod.py", _RESUMED)
    module = project.load("resumed_mod")
    if kind == "generator":
        g = module.gen()
        _ = next(g)
        resume = lambda: next(g)  # noqa: E731
    elif kind == "async generator":
        ag = module.agen()
        step = ag.__anext__()
        try:
            step.send(None)
        except StopIteration:
            pass
        resume = lambda: _drive(ag.__anext__())  # noqa: E731
    elif kind == "coroutine":
        c = module.co()
        _ = c.send(None)
        resume = lambda: _finish(c)  # noqa: E731
    else:
        th = module.thrower()
        _ = next(th)
        resume = lambda: th.throw(ValueError("go"))  # noqa: E731

    rec = record(resume)

    assert project.p("resumed_mod.py") in keys(rec, "code")


def _drive(awaitable: Any) -> None:
    try:
        awaitable.send(None)
    except StopIteration:
        pass


def _finish(coroutine: Any) -> None:
    try:
        coroutine.send(None)
    except StopIteration:
        pass


# --------------------------------------------------------------------------------------
# import time: what modules did while the session was collecting
# --------------------------------------------------------------------------------------

_SETTINGS = """
import json
import os
import pathlib

try:
    import fastimpl_zz as impl
except ImportError:
    impl = None

MODE = os.environ.get("APP_MODE")
CONF = json.loads((pathlib.Path(__file__).parent / "conf.json").read_text())

def mode():
    return MODE, CONF, impl
"""


def record_after_collection(
    project: Project,
    modules: list[str],
    body: Callable[[], object],
    *,
    static: tuple[str | os.PathLike[str], ...] = (),
    between: Callable[[Recorder], object] | None = None,
) -> Record:
    """The recorder starts first (as the host must), the modules are imported while it collects,
    and only then does a test run."""
    recorder = Recorder()
    recorder.start()
    try:
        for name in modules:
            _ = project.load(name)
        if between is not None:
            _ = between(recorder)
        recorder.begin("t", static)
        try:
            body()
        finally:
            result = recorder.end("t")
    finally:
        recorder.stop()
    return result


def _settings_project(project: Project) -> tuple[Path, Path]:
    _ = project.write("conf.json", '{"debug": false}')
    _ = project.write("settings_mod.py", _SETTINGS)
    test_module = project.write(
        "test_settings_mod.py",
        "import settings_mod\n\ndef test_it():\n    assert settings_mod.mode()\n",
    )
    return test_module, project.root / "conf.json"


def test_what_a_module_read_at_import_time_is_a_dependency_of_the_tests_that_import_it(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_MODE", "dev")
    test_module, conf = _settings_project(project)

    rec = record_after_collection(
        project,
        ["settings_mod", "test_settings_mod"],
        lambda: sys.modules["test_settings_mod"].test_it(),
        static=(test_module,),
    )

    assert rec.cacheable, rec.reasons
    assert ("env", "APP_MODE") in {(k, key) for k, key, _ in rec.deps}
    assert project.p("conf.json") in keys(rec, "file")
    assert "fastimpl_zz" in import_names(rec)
    assert stale_after(rec, lambda: monkeypatch.setenv("APP_MODE", "prod"))["t"][0][:2] == (
        "env",
        "APP_MODE",
    )
    monkeypatch.setenv("APP_MODE", "dev")
    assert stale_after(rec, lambda: conf.write_text('{"debug": true}'))["t"][0][1] == project.p(
        "conf.json"
    )
    conf.write_text('{"debug": false}')
    changed = stale_after(rec, lambda: project.write("fastimpl_zz.py", "X = 1\n"))
    assert any(d[0] == "import" for d in changed["t"])


def test_import_time_inputs_belong_to_the_module_that_read_them_not_to_every_test(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The measure of 'everything is affected': a test whose closure does not include the settings
    module is untouched by what the settings module read."""
    monkeypatch.setenv("APP_MODE", "dev")
    _ = _settings_project(project)
    _ = project.write("plain_mod.py", "def f():\n    return 1\n")
    plain_test = project.write(
        "test_plain_mod.py", "import plain_mod\n\ndef test_it():\n    assert plain_mod.f()\n"
    )

    rec = record_after_collection(
        project,
        ["settings_mod", "plain_mod", "test_plain_mod"],
        lambda: sys.modules["test_plain_mod"].test_it(),
        static=(plain_test,),
    )

    assert rec.cacheable and "APP_MODE" not in keys(rec, "env")
    assert stale_after(rec, lambda: monkeypatch.setenv("APP_MODE", "prod")) == {}


def test_a_directory_listed_at_import_time_is_a_dependency(project: Project) -> None:
    """A plugin registry filled by ``pkgutil.iter_modules`` at import: a new plugin file is a change."""
    _ = project.write("plugins_dir/__init__.py", "")
    _ = project.write("plugins_dir/one.py", "")
    _ = project.write(
        "registry_mod.py",
        "import os, pathlib\nNAMES = sorted(os.listdir(pathlib.Path(__file__).parent / 'plugins_dir'))\n"
        "def names():\n    return NAMES\n",
    )
    registry = project.root / "registry_mod.py"

    rec = record_after_collection(
        project, ["registry_mod"], lambda: sys.modules["registry_mod"].names(), static=(registry,)
    )

    assert project.p("plugins_dir") in keys(rec, "listing")
    assert stale_after(rec, lambda: (project.root / "plugins_dir" / "two.py").write_text(""))


def test_an_event_with_no_first_party_frame_is_the_sessions_and_reaches_every_test(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOBAL_PROBE", "a")
    _ = project.write("quiet_mod.py", "def f():\n    return 1\n")
    quiet = project.load("quiet_mod")

    def collect(recorder: Recorder) -> None:
        # a thread whose target is a builtin: no first-party frame anywhere on its stack
        code = compile("import os\nos.environ.get('GLOBAL_PROBE')", "<string>", "exec")
        worker = threading.Thread(target=exec, args=(code, {}))  # noqa: S102
        worker.start()
        worker.join()

    rec = record_after_collection(project, [], quiet.f, between=collect)

    assert "GLOBAL_PROBE" in keys(rec, "env")


def test_the_workers_own_activity_is_not_blamed_on_the_session(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKER_PROBE", "a")
    package = Path(_deps.__file__).resolve().parent
    machinery = compile(
        "import os\nos.environ.get('WORKER_PROBE')",
        str(package / "fake_worker_machinery.py"),
        "exec",
    )

    rec = record_after_collection(
        project,
        [],
        lambda: None,
        between=lambda recorder: exec(machinery, {}),  # noqa: S102
    )

    assert "WORKER_PROBE" not in keys(rec, "env")


def test_a_later_import_phase_inside_session_scope_is_import_time_not_a_tests(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_MODE", "dev")
    _ = project.write("conf.json", "{}")
    path = project.write("late_settings_mod.py", _SETTINGS)
    recorder = Recorder()
    recorder.start()
    try:
        recorder.begin("first")
        _ = recorder.end("first")  # tests have begun; collection of this module comes later
        with recorder.session_scope():
            module = project.load("late_settings_mod")
        recorder.begin("t", (path,))
        _ = module.mode()
        rec = recorder.end("t")
    finally:
        recorder.stop()

    assert "APP_MODE" in keys(rec, "env") and project.p("conf.json") in keys(rec, "file")


def test_events_between_tests_belong_to_nobody(project: Project) -> None:
    data = project.write("between.txt", "x")
    recorder = Recorder()
    recorder.start()
    try:
        recorder.begin("a")
        _ = recorder.end("a")
        _ = data.read_text()  # worker bookkeeping between two tests
        recorder.begin("b")
        rec = recorder.end("b")
    finally:
        recorder.stop()
    assert project.p("between.txt") not in keys(rec, "file")


# --------------------------------------------------------------------------------------
# import resolution: which file a name means, not just which file was loaded
# --------------------------------------------------------------------------------------


def _static_importer(project: Project, imported: str) -> Path:
    return project.write(
        f"importer_of_{imported}.py",
        f"import {imported}\n\ndef value():\n    return {imported}.VALUE\n",
    )


def test_a_package_beside_a_statically_imported_module_changes_what_the_import_means(
    project: Project,
) -> None:
    """``helpers.py`` and a new ``helpers/__init__.py`` in one directory: the package wins."""
    _ = project.write("helpers_a.py", "VALUE = 1\n")
    importer = _static_importer(project, "helpers_a")
    module = project.load("importer_of_helpers_a")

    rec = record(module.value, static=(importer,))

    assert "helpers_a" in import_names(rec)

    def shadow() -> None:
        _ = project.write("helpers_a/__init__.py", "VALUE = 2\n")

    changed = stale_after(rec, shadow)
    assert any(d[0] == "import" and d[1].startswith("helpers_a") for d in changed["t"])
    assert affected_by_paths({"t": rec}, [project.root / "helpers_a" / "__init__.py"])


def test_a_new_file_earlier_on_the_search_path_shadows_a_module(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    front = tmp_path / "front"
    front.mkdir()
    monkeypatch.syspath_prepend(str(front))  # now ahead of the project's directory
    _ = project.write("shadowed_mod.py", "VALUE = 1\n")
    importer = _static_importer(project, "shadowed_mod")
    module = project.load("importer_of_shadowed_mod")

    rec = record(module.value, static=(importer,))

    changed = stale_after(rec, lambda: (front / "shadowed_mod.py").write_text("VALUE = 2\n"))
    assert any(d[0] == "import" and d[1].startswith("shadowed_mod") for d in changed["t"])
    assert affected_by_paths({"t": rec}, [front / "shadowed_mod.py"]) == {
        "t": [_n(front / "shadowed_mod.py")]
    }


def test_a_statically_imported_module_that_was_missing_is_a_dependency_on_its_absence(
    project: Project,
) -> None:
    """``try: import local_overrides except ImportError`` in a module imported long before; the
    user then creates the file in a directory only the worker's search path covers."""
    importer = project.write(
        "tries_overrides.py",
        "try:\n    import local_overrides_zz\nexcept ImportError:\n    local_overrides_zz = None\n",
    )
    _ = project.load("tries_overrides")

    rec = record(lambda: None, static=(importer,))

    assert ("local_overrides_zz", ABSENT) in import_deps(rec)
    changed = stale_after(rec, lambda: project.write("local_overrides_zz.py", "X = 1\n"))
    assert any(d[0] == "import" and d[1].startswith("local_overrides_zz") for d in changed["t"])


def test_resolution_uses_the_search_path_it_was_recorded_with_not_the_checkers(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker added a directory to ``sys.path``; the process that checks never did."""
    extra = tmp_path / "worker_only"
    extra.mkdir()
    monkeypatch.syspath_prepend(str(extra))
    importer = project.write(
        "tries_worker_only.py",
        "try:\n    import worker_only_zz\nexcept ImportError:\n    worker_only_zz = None\n",
    )
    _ = project.load("tries_worker_only")
    rec = record(lambda: None, static=(importer,))
    assert ("worker_only_zz", ABSENT) in import_deps(rec)

    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(extra)])  # the checker
    changed = stale_after(rec, lambda: (extra / "worker_only_zz.py").write_text("X = 1\n"))

    assert any(d[0] == "import" and d[1].startswith("worker_only_zz") for d in changed["t"])


class _FinderFixture:
    def __init__(self, project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
        self.project = project
        self.target = project.write("elsewhere/opaque_target.py", "VALUE = 1\n")
        self.finder_file = project.write(
            "opaque_finder_mod.py",
            "import importlib.util\n\n"
            "MAP = {}\n\n"
            "class Finder:\n"
            "    @classmethod\n"
            "    def find_spec(cls, name, path=None, target=None):\n"
            "        if name in MAP:\n"
            "            return importlib.util.spec_from_file_location(name, MAP[name])\n"
            "        return None\n",
        )
        module = project.load("opaque_finder_mod")
        module.MAP["opaque_target"] = str(self.target)
        monkeypatch.setattr(sys, "meta_path", [module.Finder, *sys.meta_path])
        self.importer = _static_importer(project, "opaque_target")
        project.loaded.append("opaque_target")


def test_a_module_resolved_by_a_non_standard_finder_is_fingerprinted_by_the_finder_too(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An editable-install finder or a ``meta_path`` hook: the file and the finder's own source."""
    fixture = _FinderFixture(project, monkeypatch)
    module = project.load("importer_of_opaque_target")

    rec = record(module.value, static=(fixture.importer,))

    assert rec.cacheable, rec.reasons
    opaque = [
        key for kind, key, _ in rec.deps if kind == "import" and "opaque" in key.split(chr(0))
    ]
    assert len(opaque) == 1
    changed = stale_after(rec, lambda: fixture.finder_file.write_text("MAP = {}  # edited\n"))
    assert any(d[0] == "import" and "opaque" in d[1].split(chr(0)) for d in changed["t"])


def test_a_module_resolved_by_a_finder_with_no_source_file_is_uncacheable(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = project.write("elsewhere/anon_target.py", "VALUE = 1\n")
    namespace: dict[str, Any] = {"target": str(target)}
    exec(  # noqa: S102
        "import importlib.util\n"
        "class AnonFinder:\n"
        "    @classmethod\n"
        "    def find_spec(cls, name, path=None, target_=None):\n"
        "        if name == 'anon_target':\n"
        "            return importlib.util.spec_from_file_location(name, target)\n",
        namespace,
    )
    namespace["AnonFinder"].__module__ = "no_such_module_zz"
    monkeypatch.setattr(sys, "meta_path", [namespace["AnonFinder"], *sys.meta_path])
    importer = _static_importer(project, "anon_target")
    project.loaded.append("anon_target")
    module = project.load("importer_of_anon_target")

    rec = record(module.value, static=(importer,))

    assert not rec.cacheable and any("whose source file is unknown" in r for r in rec.reasons)


def test_hole_a_new_local_file_named_like_a_standard_library_module_is_invisible(
    project: Project,
) -> None:
    """Standard-library names are not re-resolved (the records would be enormous): a new
    ``random.py`` beside the code shadows ``random`` for the next run, and nothing says so."""
    importer = project.write("uses_stdlib_random.py", "import colorsys\n\ndef f():\n    return 1\n")
    module = project.load("uses_stdlib_random")
    rec = record(module.f, static=(importer,))
    assert "colorsys" not in import_names(rec)
    assert stale_after(rec, lambda: project.write("colorsys.py", "X = 1\n")) == {}


# --------------------------------------------------------------------------------------
# the holes: real dependencies the recorder cannot see, pinned
# --------------------------------------------------------------------------------------


def test_hole_an_existence_check_is_invisible(project: Project) -> None:
    """``os.stat`` / ``exists`` / ``is_file`` raise no audit event in CPython."""
    flag = project.root / "feature.flag"

    def body() -> bool:
        return flag.exists() or os.path.isfile(flag) or os.stat(project.root).st_size >= 0

    rec = record(body)

    assert rec.cacheable and not keys(rec, "file") and not keys(rec, "listing")
    # creating the file would change the outcome; nothing says so
    assert stale_after(rec, lambda: flag.write_text("on")) == {}


def test_hole_the_clock_is_invisible() -> None:
    rec = record(lambda: (time.time(), time.monotonic(), __import__("datetime").datetime.now()))
    assert rec.cacheable and not [d for d in rec.deps if d[0] not in ("code", "import")]


def test_hole_randomness_is_invisible() -> None:
    import random
    import uuid

    rec = record(lambda: (random.random(), os.urandom(4), uuid.uuid4()))
    assert rec.cacheable and not [d for d in rec.deps if d[0] not in ("code", "import")]


def test_hole_a_file_a_c_library_opens_itself_is_invisible(project: Project) -> None:
    """OpenSSL reads ``cafile`` itself: no ``open`` event.  (libxml2, HDF5, Arrow ... likewise.)"""
    import ssl

    cafile = project.write("ca.pem", "not a certificate")

    def body() -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try:
            context.load_verify_locations(cafile=str(cafile))
        except (ssl.SSLError, FileNotFoundError):
            pass

    rec = record(body)

    assert rec.cacheable and project.p("ca.pem") not in keys(rec, "file")
    # deleting it would turn the (swallowed) SSLError into FileNotFoundError; nothing says so
    assert stale_after(rec, cafile.unlink) == {}


def test_hole_state_left_by_an_earlier_test_is_invisible(project: Project) -> None:
    """Order dependence: B passes only because A ran first.  B's record has no trace of A."""
    project.write(
        "shared_state_mod.py",
        "STATE = []\ndef put():\n    STATE.append('set by A')\ndef need():\n    return STATE.index('set by A')\n",
    )
    state = project.load("shared_state_mod")

    first = record(state.put)
    second = record(state.need)
    state.STATE.clear()
    alone = record(lambda: None)

    assert first.cacheable and second.cacheable
    # the same recorder facts, nothing about A: B's deps are exactly its files
    assert {d[:2] for d in second.deps} >= {("code", project.p("shared_state_mod.py"))}
    assert not [r for r in second.reasons]
    assert alone.cacheable


def test_hole_a_cached_dynamic_import_read_for_data_is_invisible(project: Project) -> None:
    """``import_module`` of a module already in ``sys.modules`` raises no ``import`` event, and
    reading a constant runs none of its code."""
    lib = project.write("dyn_mod.py", "CONST = 1\n")
    _ = project.load("dyn_mod")
    caller = project.write(
        "dyn_caller.py",
        "import importlib\n\ndef read():\n    return importlib.import_module('dyn_mod').CONST\n",
    )
    module = project.load("dyn_caller")

    rec = record(module.read, static=(caller,))

    assert project.p("dyn_mod.py") not in keys(rec, "code")
    assert stale_after(rec, lambda: lib.write_text("CONST = 2\n")) == {}


def test_hole_the_environment_read_by_c_is_invisible(monkeypatch: pytest.MonkeyPatch) -> None:
    """``locale.setlocale(LC_ALL, "")`` makes the C runtime read ``LANG`` / ``LC_ALL``: no
    Python-level ``os.environ`` access.  The common ones are in the *prefix* instead."""
    import locale

    def body() -> None:
        saved = locale.setlocale(locale.LC_CTYPE)
        try:
            locale.setlocale(locale.LC_CTYPE, "")
        except locale.Error:
            pass
        finally:
            locale.setlocale(locale.LC_CTYPE, saved)

    rec = record(body)

    assert not keys(rec, "env")
    before = session_prefix()
    monkeypatch.setenv("LC_ALL", "C")
    assert before.changed(session_prefix()) == ["c_env"]


_AUDIT_TABLE_3_14 = [
    "_thread.start_new_thread",
    "array.__new__",
    "builtins.breakpoint",
    "builtins.id",
    "builtins.input",
    "builtins.input/result",
    "code.__new__",
    "compile",
    "cpython.PyConfig_Set",
    "cpython.PyInterpreterState_Clear",
    "cpython.PyInterpreterState_New",
    "cpython._PySys_ClearAuditHooks",
    "cpython.remote_debugger_script",
    "cpython.run_command",
    "cpython.run_file",
    "cpython.run_interactivehook",
    "cpython.run_module",
    "cpython.run_startup",
    "cpython.run_stdin",
    "ctypes.addressof",
    "ctypes.call_function",
    "ctypes.cdata",
    "ctypes.cdata/buffer",
    "ctypes.create_string_buffer",
    "ctypes.create_unicode_buffer",
    "ctypes.dlopen",
    "ctypes.dlsym",
    "ctypes.dlsym/handle",
    "ctypes.get_errno",
    "ctypes.get_last_error",
    "ctypes.memoryview_at",
    "ctypes.set_errno",
    "ctypes.set_exception",
    "ctypes.set_last_error",
    "ctypes.string_at",
    "ctypes.wstring_at",
    "ensurepip.bootstrap",
    "exec",
    "fcntl.fcntl",
    "fcntl.flock",
    "fcntl.ioctl",
    "fcntl.lockf",
    "ftplib.connect",
    "ftplib.sendcmd",
    "function.__new__",
    "gc.get_objects",
    "gc.get_referents",
    "gc.get_referrers",
    "glob.glob",
    "glob.glob/2",
    "http.client.connect",
    "http.client.send",
    "imaplib.open",
    "imaplib.send",
    "import",
    "marshal.dumps",
    "marshal.load",
    "marshal.loads",
    "mmap.__new__",
    "msvcrt.get_osfhandle",
    "msvcrt.locking",
    "msvcrt.open_osfhandle",
    "object.__delattr__",
    "object.__getattr__",
    "object.__setattr__",
    "open",
    "os.add_dll_directory",
    "os.chdir",
    "os.chflags",
    "os.chmod",
    "os.chown",
    "os.exec",
    "os.fork",
    "os.forkpty",
    "os.fwalk",
    "os.getxattr",
    "os.kill",
    "os.killpg",
    "os.link",
    "os.listdir",
    "os.listdrives",
    "os.listmounts",
    "os.listvolumes",
    "os.listxattr",
    "os.lockf",
    "os.mkdir",
    "os.posix_spawn",
    "os.putenv",
    "os.remove",
    "os.removexattr",
    "os.rename",
    "os.rmdir",
    "os.scandir",
    "os.setxattr",
    "os.spawn",
    "os.startfile",
    "os.startfile/2",
    "os.symlink",
    "os.system",
    "os.truncate",
    "os.unsetenv",
    "os.utime",
    "os.walk",
    "pathlib.Path.glob",
    "pathlib.Path.rglob",
    "pdb.Pdb",
    "pickle.find_class",
    "poplib.connect",
    "poplib.putline",
    "pty.spawn",
    "resource.prlimit",
    "resource.setrlimit",
    "setopencodehook",
    "shutil.chown",
    "shutil.copyfile",
    "shutil.copymode",
    "shutil.copystat",
    "shutil.copytree",
    "shutil.make_archive",
    "shutil.move",
    "shutil.rmtree",
    "shutil.unpack_archive",
    "signal.pthread_kill",
    "smtplib.connect",
    "smtplib.send",
    "socket.__new__",
    "socket.bind",
    "socket.connect",
    "socket.getaddrinfo",
    "socket.gethostbyaddr",
    "socket.gethostbyname",
    "socket.gethostname",
    "socket.getnameinfo",
    "socket.getservbyname",
    "socket.getservbyport",
    "socket.sendmsg",
    "socket.sendto",
    "socket.sethostname",
    "sqlite3.connect",
    "sqlite3.connect/handle",
    "sqlite3.enable_load_extension",
    "sqlite3.load_extension",
    "subprocess.Popen",
    "sys._current_exceptions",
    "sys._current_frames",
    "sys._getframe",
    "sys._getframemodulename",
    "sys.addaudithook",
    "sys.excepthook",
    "sys.monitoring.register_callback",
    "sys.remote_exec",
    "sys.set_asyncgen_hooks_finalizer",
    "sys.set_asyncgen_hooks_firstiter",
    "sys.setprofile",
    "sys.settrace",
    "sys.unraisablehook",
    "syslog.closelog",
    "syslog.openlog",
    "syslog.setlogmask",
    "syslog.syslog",
    "tempfile.mkdtemp",
    "tempfile.mkstemp",
    "time.sleep",
    "urllib.Request",
    "webbrowser.open",
    "winreg.ConnectRegistry",
    "winreg.CreateKey",
    "winreg.DeleteKey",
    "winreg.DeleteValue",
    "winreg.DisableReflectionKey",
    "winreg.EnableReflectionKey",
    "winreg.EnumKey",
    "winreg.EnumValue",
    "winreg.ExpandEnvironmentStrings",
    "winreg.LoadKey",
    "winreg.OpenKey",
    "winreg.OpenKey/result",
    "winreg.PyHKEY.Detach",
    "winreg.QueryInfoKey",
    "winreg.QueryReflectionKey",
    "winreg.QueryValue",
    "winreg.SaveKey",
    "winreg.SetValue",
    "_winapi.CreateFile",
    "_winapi.CreateJunction",
    "_winapi.CreateNamedPipe",
    "_winapi.CreatePipe",
    "_winapi.CreateProcess",
    "_winapi.OpenProcess",
    "_winapi.TerminateProcess",
    "_posixsubprocess.fork_exec",
    "ctypes.PyObj_FromPtr",
]

_AUDIT_EVENTS_IGNORED = [
    "_thread.start_new_thread",
    "_winapi.CreateNamedPipe",
    "_winapi.CreatePipe",
    "array.__new__",
    "builtins.id",
    "code.__new__",
    "compile",
    "cpython.PyConfig_Set",
    "cpython.PyInterpreterState_Clear",
    "cpython.PyInterpreterState_New",
    "cpython._PySys_ClearAuditHooks",
    "cpython.remote_debugger_script",
    "cpython.run_command",
    "cpython.run_file",
    "cpython.run_interactivehook",
    "cpython.run_module",
    "cpython.run_startup",
    "cpython.run_stdin",
    "ctypes.PyObj_FromPtr",
    "ctypes.addressof",
    "ctypes.call_function",
    "ctypes.cdata",
    "ctypes.cdata/buffer",
    "ctypes.create_string_buffer",
    "ctypes.create_unicode_buffer",
    "ctypes.get_errno",
    "ctypes.get_last_error",
    "ctypes.memoryview_at",
    "ctypes.set_errno",
    "ctypes.set_exception",
    "ctypes.set_last_error",
    "ctypes.string_at",
    "ctypes.wstring_at",
    "exec",
    "fcntl.fcntl",
    "fcntl.ioctl",
    "function.__new__",
    "gc.get_objects",
    "gc.get_referents",
    "gc.get_referrers",
    "glob.glob",
    "glob.glob/2",
    "marshal.dumps",
    "marshal.load",
    "marshal.loads",
    "msvcrt.get_osfhandle",
    "msvcrt.open_osfhandle",
    "object.__delattr__",
    "object.__getattr__",
    "object.__setattr__",
    "os.add_dll_directory",
    "os.chdir",
    "os.truncate",
    "os.walk",
    "pathlib.Path.glob",
    "pathlib.Path.rglob",
    "pickle.find_class",
    "resource.prlimit",
    "resource.setrlimit",
    "setopencodehook",
    "shutil.make_archive",
    "socket.__new__",
    "sqlite3.connect/handle",
    "sys._current_exceptions",
    "sys._current_frames",
    "sys._getframe",
    "sys._getframemodulename",
    "sys.addaudithook",
    "sys.excepthook",
    "sys.set_asyncgen_hooks_finalizer",
    "sys.set_asyncgen_hooks_firstiter",
    "sys.unraisablehook",
    "syslog.closelog",
    "syslog.openlog",
    "syslog.setlogmask",
    "syslog.syslog",
]


# --------------------------------------------------------------------------------------
# every audit event CPython documents is classified (handled, or ignored on purpose)
# --------------------------------------------------------------------------------------


def test_every_documented_audit_event_is_handled_or_ignored_on_purpose() -> None:
    """The table is CPython 3.14's (earlier versions raise a subset).  A new event fails this test
    until someone decides what it means for a cached pass."""
    handled = set(_deps._EVENTS)  # pyright: ignore[reportPrivateUsage]
    table = set(_AUDIT_TABLE_3_14)
    assert len(_AUDIT_TABLE_3_14) == len(table) == 192
    assert table - handled == set(_AUDIT_EVENTS_IGNORED)
    assert not handled & set(_AUDIT_EVENTS_IGNORED)
    assert handled - table == {
        "_winapi.CopyFile2"
    }  # raised by shutil.copy2 on Windows; undocumented


@pytest.mark.parametrize("event", sorted(_deps._UNCACHEABLE))  # pyright: ignore[reportPrivateUsage]
def test_every_event_that_cannot_be_an_input_makes_the_test_uncacheable(event: str) -> None:
    """Driven with ``sys.audit``, which raises the event without doing the thing."""
    if event == "socket.bind" or event == "socket.connect":
        args: tuple[object, ...] = (object(), ("127.0.0.1", 1))
    else:
        args = (object(),)

    rec = record(lambda: sys.audit(event, *args))

    assert not rec.cacheable and any(event in r for r in rec.reasons), rec.reasons


#: The first CPython version that raises each audit event this module handles and that an earlier
#: version lacks (docs.python.org "Audit events table", 3.12 / 3.13 / 3.14).  The 3.14 table in
#: ``_AUDIT_TABLE_3_14`` is the superset; this is what the earlier versions are missing.
_EVENT_SINCE: dict[str, tuple[int, int]] = {
    "time.sleep": (3, 13),
    "sys.monitoring.register_callback": (3, 13),
    "sys.remote_exec": (3, 14),
    "_posixsubprocess.fork_exec": (3, 14),  # undocumented before; subprocess.Popen covers it
}


def test_the_events_older_pythons_lack_are_the_ones_the_contract_names() -> None:
    handled = set(_deps._EVENTS)  # pyright: ignore[reportPrivateUsage]
    assert set(_EVENT_SINCE) <= handled
    # the contract names the 3.12 holes, so a version table change cannot go unnoticed
    doc = _deps.__doc__ or ""
    assert "on 3.12 a sleep and a second tracer" in doc


@pytest.mark.skipif(sys.version_info < (3, 13), reason="time.sleep raises its event from 3.13")
def test_time_sleep_is_the_clock_but_a_zero_sleep_is_a_yield() -> None:
    assert not record(lambda: time.sleep(0.001)).cacheable
    assert record(lambda: time.sleep(0)).cacheable


@pytest.mark.skipif(sys.version_info >= (3, 13), reason="3.13 raises the time.sleep event")
def test_hole_time_sleep_on_python_3_12_is_invisible() -> None:
    assert record(lambda: time.sleep(0.001)).cacheable


def test_a_sleep_event_is_the_clock_on_every_version() -> None:
    """The handler, driven with ``sys.audit`` (which raises the event on any version)."""
    assert not record(lambda: sys.audit("time.sleep", 0.001)).cacheable
    assert record(lambda: sys.audit("time.sleep", 0)).cacheable


def test_machine_identity_routes() -> None:
    import platform

    for route in (socket.gethostname, platform.node, platform.system, platform.uname):
        rec = record(route)
        assert not rec.cacheable, route  # an audit event, or `platform.uname` being started
        assert any("machine's identity" in r for r in rec.reasons), (route, rec.reasons)


@pytest.mark.skipif(os.name == "nt", reason="os.uname is POSIX only")
def test_a_hostname_file_read_is_a_file_dependency() -> None:
    hostname = Path("/etc/hostname")
    if not hostname.is_file():
        pytest.skip("no /etc/hostname here")
    rec = record(hostname.read_text)
    assert rec.cacheable and "/etc/hostname" in keys(rec, "file")


@pytest.mark.skipif(os.name == "nt", reason="os.uname is POSIX only")
def test_hole_os_uname_is_invisible_on_posix() -> None:
    """A C function with no audit event: the one route to the node name that is not caught."""
    assert record(os.uname).cacheable


def test_the_machines_identity_is_uncacheable() -> None:
    rec = record(socket.gethostname)
    assert not rec.cacheable and any("machine's identity" in r for r in rec.reasons)


# --------------------------------------------------------------------------------------
# what the environment, the machine and the install can hide
# --------------------------------------------------------------------------------------


def test_hole_repr_of_the_environment_is_invisible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUSTEST_DEPS_REPR", "a")
    rec = record(lambda: repr(os.environ))
    assert "*" not in keys(rec, "env") and not keys(rec, "env")


def test_hole_the_raw_environment_dict_is_invisible(monkeypatch: pytest.MonkeyPatch) -> None:
    """``posix.environ`` / ``nt.environ`` are plain dicts; ``os.environ`` is built from them."""
    raw = getattr(os, "environ")  # noqa: B009
    monkeypatch.setenv("RUSTEST_DEPS_RAW", "a")
    rec = record(lambda: dict(raw._data))  # the dict inside os.environ, not a `_Environ` method
    assert not keys(rec, "env")


@pytest.mark.skipif(sys.platform == "win32", reason="os.environb is POSIX only")
def test_os_environb_reads_are_recorded_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUSTEST_DEPS_BYTES", "a")
    rec = record(lambda: os.environb.get(b"RUSTEST_DEPS_BYTES"))  # pyright: ignore[reportAttributeAccessIssue]
    assert "RUSTEST_DEPS_BYTES" in keys(rec, "env")


def test_hole_the_cpu_count_is_invisible_but_the_node_name_is_caught() -> None:
    import platform

    rec = record(lambda: (os.cpu_count(), os.getpid()))
    assert rec.cacheable and not [d for d in rec.deps if d[0] not in ("code", "import")]
    assert not record(platform.node).cacheable  # it asks `socket.gethostname`


def test_hole_an_edit_inside_an_untracked_install_is_invisible(
    project: Project, tmp_path: Path
) -> None:
    """The prefix has installed *versions*; a file edited inside site-packages keeps the version.
    (Here an ignored directory stands in for it.)"""
    installed = tmp_path / "site_packages_like"
    installed.mkdir()
    data = installed / "vendored.dat"
    data.write_text("v1")
    recorder = Recorder(ignore=[str(installed)])
    recorder.start()
    try:
        recorder.begin("t")
        _ = data.read_text()
        rec = recorder.end("t")
    finally:
        recorder.stop()
    assert not keys(rec, "file")
    assert stale_after(rec, lambda: data.write_text("v2")) == {}


def test_hole_sitecustomize_and_pth_files_are_in_no_prefix_part() -> None:
    assert set(session_prefix().parts) == {
        "rustest",
        "rustest_build",
        "python",
        "flags",
        "dists",
        "cwd",
        "c_env",
        "options",
    }


def test_python_warning_options_are_part_of_the_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    before = session_prefix()
    monkeypatch.setattr(sys, "warnoptions", ["error"])
    assert before.changed(session_prefix()) == ["flags"]


def test_a_listing_notices_a_file_replaced_by_a_directory_of_the_same_name(
    project: Project,
) -> None:
    _ = project.write("typed/x", "file")
    folder = project.root / "typed"

    rec = record(lambda: os.listdir(folder))

    def swap() -> None:
        (folder / "x").unlink()
        (folder / "x").mkdir()

    assert stale_after(rec, swap)["t"][0][0] == "listing"


def test_a_sourceless_first_party_module_is_uncacheable(project: Project) -> None:
    """Only a ``.pyc`` is left, so there is no source to fingerprint."""
    import py_compile

    source = project.write("sourceless_mod.py", "def f():\n    return 1\n")
    compiled = project.root / "sourceless_mod.pyc"
    _ = py_compile.compile(str(source), cfile=str(compiled), doraise=True)
    source.unlink()
    module = project.load("sourceless_mod")

    rec = record(module.f)

    assert not rec.cacheable and any("no source file" in r for r in rec.reasons)


def test_a_markdown_doc_block_is_a_dependency_only_when_the_host_passes_the_file(
    project: Project,
) -> None:
    """A ``.md`` code block has a non-``.py`` ``co_filename``: ``PY_START`` cannot see it, so the
    host must name the file in ``static_files``."""
    page = project.write("page.md", "```python\nassert True\n```\n")
    assert project.p("page.md") not in keys(record(lambda: None), "code")
    rec = record(lambda: None, static=(page,))
    assert project.p("page.md") in keys(rec, "code")
    assert stale_after(rec, lambda: page.write_text("```python\nassert 1\n```\n"))


def test_a_changed_symlinked_file_is_matched_by_its_real_path(project: Project) -> None:
    """A watcher reports the real path of an edited file; the dependency was read through a link."""
    real = project.write("real_data.txt", "x")
    link = project.root / "link_data.txt"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks are not available")
    rec = record(link.read_text)
    assert affected_by_paths({"t": rec}, [real]) == {"t": [project.p("link_data.txt")]}
    assert affected_by_paths({"t": rec}, [link]) == {"t": [project.p("link_data.txt")]}


def test_a_retargeted_symlink_marks_what_read_through_it_as_affected(project: Project) -> None:
    first = project.write("first_target.txt", "1")
    second = project.write("second_target.txt", "2")
    link = project.root / "moving_link.txt"
    try:
        link.symlink_to(first)
    except OSError:
        pytest.skip("symlinks are not available")
    rec = record(link.read_text)
    link.unlink()
    link.symlink_to(second)
    assert affected_by_paths({"t": rec}, [link]) == {"t": [project.p("moving_link.txt")]}
    assert affected_by_paths({"t": rec}, [first]) == {}  # the old target no longer feeds it


def test_a_module_swapped_in_sys_modules_without_changing_its_size_is_not_a_stale_closure(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = project.write("swap_a.py", "VALUE = 1\n")
    _ = project.write("swap_b.py", "VALUE = 2\n")
    importer = project.write("swap_importer.py", "import swap_a\n")
    _ = project.load("swap_a")
    _ = project.load("swap_b")
    _ = project.load("swap_importer")
    recorder = Recorder()
    recorder.start()
    try:
        recorder.begin("one", (importer,))
        first = recorder.end("one")
        monkeypatch.setitem(sys.modules, "swap_a", sys.modules["swap_b"])  # same count
        recorder.begin("two", (importer,))
        second = recorder.end("two")
    finally:
        recorder.stop()
    assert project.p("swap_a.py") in keys(first, "code")
    assert project.p("swap_a.py") in keys(second, "code")  # still the importer's static import


# --------------------------------------------------------------------------------------
# the prefix
# --------------------------------------------------------------------------------------


def test_the_prefix_is_stable_and_names_what_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    base = session_prefix({"markers": "a"})
    assert base.digest == session_prefix({"markers": "a"}).digest
    assert set(base.parts) >= {
        "rustest",
        "rustest_build",
        "python",
        "flags",
        "dists",
        "cwd",
        "c_env",
        "options",
    }
    assert len(base.parts["rustest_build"]) == 32


def test_a_changed_option_changes_the_prefix() -> None:
    first, second = session_prefix({"x": 1}), session_prefix({"x": 2})
    assert first.digest != second.digest and first.changed(second) == ["options"]


def test_a_changed_extra_part_changes_the_prefix() -> None:
    first = session_prefix(extra={"plugins": "a"})
    assert first.changed(session_prefix(extra={"plugins": "b"})) == ["plugins"]


def test_a_changed_c_visible_variable_changes_the_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TZ", raising=False)
    before = session_prefix()
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    assert before.changed(session_prefix()) == ["c_env"]


def test_a_changed_working_directory_changes_the_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = session_prefix()
    monkeypatch.chdir(tmp_path)
    assert before.changed(session_prefix()) == ["cwd"]


def test_a_changed_python_changes_the_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    before = session_prefix()
    monkeypatch.setattr(sys, "version", sys.version + " (other build)")
    assert before.changed(session_prefix()) == ["python"]


def test_a_changed_installed_distribution_set_changes_the_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Fake:
        metadata = {"Name": "rustest-fake-dist"}
        version = "1.0"

    real = importlib.metadata.distributions
    before = session_prefix()
    monkeypatch.setattr(importlib.metadata, "distributions", lambda: [*real(), Fake()])
    assert before.changed(session_prefix()) == ["dists"]


# --------------------------------------------------------------------------------------
# mechanics
# --------------------------------------------------------------------------------------


def test_a_wide_scope_fixture_record_merges_into_the_tests_that_use_it(project: Project) -> None:
    config = project.write("fixture_cfg.txt", "v1")
    data = project.write("test_data.txt", "d1")
    recorder = Recorder()
    recorder.start()
    try:
        recorder.begin("fixture")
        _ = config.read_text()
        fixture = recorder.end("fixture")
        recorder.begin("t")
        _ = data.read_text()
        test = recorder.end("t")
    finally:
        recorder.stop()

    assert project.p("fixture_cfg.txt") not in keys(test, "file")
    both = merge(test, fixture)
    assert {project.p("fixture_cfg.txt"), project.p("test_data.txt")} <= keys(both, "file")
    config.write_text("v2")
    assert list(stale({"t": both})) == ["t"] and stale({"t": test}) == {}


def test_nested_begin_records_into_both_scopes(project: Project) -> None:
    data = project.write("nested.txt", "x")
    recorder = Recorder()
    recorder.start()
    try:
        recorder.begin("outer")
        recorder.begin("inner")
        _ = data.read_text()
        inner = recorder.end("inner")
        outer = recorder.end("outer")
    finally:
        recorder.stop()
    assert project.p("nested.txt") in keys(inner, "file") | keys(outer, "file")
    assert keys(inner, "file") == keys(outer, "file")


def test_end_must_match_the_innermost_begin() -> None:
    recorder = Recorder()
    recorder.start()
    try:
        recorder.begin("a")
        with pytest.raises(RuntimeError):
            _ = recorder.end("b")
    finally:
        recorder.stop()


def test_a_second_recorder_is_unavailable_and_so_is_a_taken_tool_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = Recorder()
    first.start()
    try:
        with pytest.raises(Unavailable):
            Recorder().start()
    finally:
        first.stop()

    def taken(tool: int, name: str) -> None:
        raise ValueError("in use")

    monkeypatch.setattr(sys.monitoring, "use_tool_id", taken)
    with pytest.raises(Unavailable):
        Recorder().start()


def test_after_stop_nothing_is_recorded_and_nothing_breaks(project: Project) -> None:
    data = project.write("after.txt", "x")
    recorder = Recorder()
    recorder.start()
    recorder.stop()
    assert data.read_text() == "x"  # the audit hook is still installed, and inert
    assert sys.monitoring.get_tool(5) is None


def test_records_round_trip_through_json(project: Project) -> None:
    data = project.write("roundtrip.txt", "x")
    rec = record(data.read_text)
    again = Record.load(json.loads(json.dumps(rec.dump())))
    assert again == rec


def test_stale_lists_an_uncacheable_record_with_no_changed_dependency() -> None:
    rec = Record(frozenset(), False, ("uses network (socket.connect)",))
    assert stale({"t": rec}) == {"t": []}


def test_affected_by_paths_does_not_list_unrelated_or_uncacheable_records(project: Project) -> None:
    data = project.write("rel.txt", "x")
    other = project.write("other_rel.txt", "x")
    rec = record(data.read_text)
    uncacheable = Record(frozenset(), False, ("subprocess",))
    assert affected_by_paths({"a": rec, "b": uncacheable}, [other]) == {}
    assert affected_by_paths({"a": rec, "b": uncacheable}, [data]) == {"a": [project.p("rel.txt")]}


def test_a_stat_gate_cannot_hide_an_edit_in_the_same_timestamp_tick(project: Project) -> None:
    """A file edited with the same size right after being hashed is rehashed, not trusted:
    the memo only trusts a file that was already two seconds old when it was hashed."""
    recorder = Recorder()
    path = project.write("young.txt", "aaaa", age=False)
    now = _deps._stat(str(path))  # pyright: ignore[reportPrivateUsage]
    first = recorder._digest(str(path), now)  # pyright: ignore[reportPrivateUsage]
    stat = os.stat(path)
    path.write_text("bbbb")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # same size, same mtime
    second = recorder._digest(str(path), _deps._stat(str(path)))  # pyright: ignore[reportPrivateUsage]
    assert first != second
