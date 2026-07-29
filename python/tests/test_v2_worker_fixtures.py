"""Tests for the v2 worker's fixture engine (`rustest._v2_worker`).

Four things are under test, and the six `conformance/corpus/fixtures/*` cases are the
acceptance table for all of them:

1. **Registry** — builtins, then the conftest chain rootdir-down, then the module, so
   ``defs[-1]`` is the nearest definition (`fixtures/override-nearest`).
2. **Closure** — autouse first, then requested names, then transitive dependencies, then
   a stable scope sort (`fixtures/autouse`).  An unknown name is not a collection error;
   it errors at *setup* with pytest's `fixture '<name>' not found` wording.
3. **Scopes and teardown** — function fixtures are fresh per test and torn down right
   after it, module fixtures are cached across the file, and teardown runs in **reverse**
   setup order (`fixtures/scope-function`, `fixtures/scope-module`,
   `fixtures/yield-teardown`).
4. **Closure-driven param ids** — a `params=[...]` fixture in the closure multiplies the
   test, with pytest's id ordering (`fixtures/parametrized-fixture`).

The id half is **differential**: :func:`test_fixture_param_ids_match_pytest` runs real
pytest on the same tree in a subprocess and compares the ids byte for byte, so every id
row is anchored to the oracle rather than to a hand-written expectation.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import itertools
from pathlib import Path
import re
import subprocess
import sys
import textwrap
import types

import pytest

from rustest._v2_worker import (
    DEFAULT_NAMING,
    SCOPE_NAMES,
    ExecutionPlan,
    FixtureDef,
    FixtureLookupError,
    FixtureRegistry,
    FixtureRunner,
    ScopeMismatch,
    build_closure,
    build_registry,
    collect_module,
    conftest_chain,
    fixture_param_dimensions,
)
import rustest._v2_worker as worker


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@contextmanager
def isolated_import_state() -> Iterator[None]:
    """Run the worker's imports the way the worker does, then undo every mutation.

    Two things, both mandatory:

    * **The compat shim is installed for the duration.**  These tests run *under* real
      pytest, so a generated module's ``import pytest`` would otherwise bind real pytest,
      whose ``@pytest.fixture`` leaves none of the ``__rustest_*`` metadata the registry
      reads — every fixture would silently vanish.  :func:`install_pytest_shim` is what the
      worker calls in ``main()``; doing it here is running the same code path.
    * **`sys.path` / `sys.modules` / the conftest cache are restored.**  The worker mutates
      all three by design (module identity is the #130 fix, the conftest cache is pytest's
      ``_importconftest`` plugin cache), and a stale ``sys.modules["conftest"]`` would make
      a later test pass for the wrong reason.
    """
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    saved_conftests = dict(worker._conftest_modules)  # pyright: ignore[reportPrivateUsage]
    try:
        worker.install_pytest_shim()
        yield
    finally:
        sys.path[:] = saved_path
        for name in set(sys.modules) - set(saved_modules):
            del sys.modules[name]
        sys.modules.update(saved_modules)
        # `reset_registry_caches` also drops the per-worker chain registries and the
        # builtins' FixtureDefs, which are pure derivations of the conftest modules and
        # whose whole purpose is to share session values -- across two unrelated trees
        # that sharing is exactly what must not survive.
        worker.reset_registry_caches()
        worker._conftest_modules.update(saved_conftests)  # pyright: ignore[reportPrivateUsage]


def write(path: Path, source: str) -> Path:
    """Write dedented *source* to *path*, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def load(path: Path, rootdir: Path) -> tuple[types.ModuleType, FixtureRegistry]:
    """Import *path* and build its registry — literally what `collect_file` calls.

    Deliberately **not** a re-implementation: a helper that assembled the registry its own
    way could keep passing while production drifted, which is exactly the failure mode this
    indirection exists to prevent.
    """
    return build_registry(path, rootdir)


def collect(path: Path, rootdir: Path) -> tuple[list[str], list[ExecutionPlan]]:
    """Collect *path* and return ``(ids, plans)``."""
    module, registry = load(path, rootdir)
    entries, plans = collect_module(module, path, rootdir, DEFAULT_NAMING, registry)
    return [entry["id"] for entry in entries], plans


def run_all(plans: list[ExecutionPlan]) -> list[tuple[str, str]]:
    """Run every plan through a single :class:`FixtureRunner`, as a worker would.

    Returns ``(id, status)`` pairs where status is ``passed`` or ``<phase>:<message>``, so
    a test can assert on outcomes without importing the execute half (Task 3).

    A test method is called with ``runner.instance`` as ``self`` — the same object its
    class-body fixtures were bound to, which is the contract Task 3 inherits.
    """
    runner = FixtureRunner()
    results: list[tuple[str, str]] = []
    for plan in plans:
        status = "passed"
        try:
            kwargs = runner.setup(plan)
            assert plan.func is not None
            if runner.instance is not None:
                _ = plan.func(runner.instance, **kwargs)
            else:
                _ = plan.func(**kwargs)
        except BaseException as exc:  # noqa: BLE001 - the worker classifies, this records
            status = f"setup-or-call:{type(exc).__name__}:{exc}"
        try:
            runner.teardown("function")
        except BaseException as exc:  # noqa: BLE001
            status = f"teardown:{type(exc).__name__}:{exc}"
        results.append((plan.id, status))
    runner.teardown_all()
    return results


def pytest_ids(tree: Path) -> list[str]:
    """Real pytest's collected ids for *tree*, in emission order — the id oracle."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    ids: list[str] = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("=", "no tests ran")):
            break
        if "::" in stripped:
            ids.append(stripped.replace("\\", "/"))
    assert ids, f"pytest collected nothing:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return ids


def pytest_scope_mismatch(tree: Path) -> str:
    """Real pytest's ``ScopeMismatch`` block for *tree* — the message oracle."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = proc.stdout.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("ScopeMismatch:"):
            rest = list(itertools.takewhile(lambda row: not row.startswith("="), lines[index:]))
            return "\n".join(rest)
    raise AssertionError(f"pytest did not report a ScopeMismatch:\n{proc.stdout}")


def _basenames(message: str) -> str:
    """Strip directory prefixes so an absolute path compares equal to a relative one.

    The single documented divergence from pytest's wording: `_format_fixturedef_line` renders
    the frame path with ``bestrelpath`` against ``session.path``, and the worker has no
    session.  Everything else in the message must match byte for byte.
    """
    return re.sub(r"[^\s]*[/\\](?=[^/\\\s]+\.py:)", "", message)


# ---------------------------------------------------------------------------
# 1. registry — chain, shadowing, metadata
# ---------------------------------------------------------------------------


def test_conftest_chain_is_rootdir_down_and_stops_at_rootdir(tmp_path: Path) -> None:
    """Port of `_pytest/config/__init__.py::_loadconftestmodules` with default confcutdir.

    Outermost first (pytest's ``reversed((directory, *directory.parents))``), the conftest
    **at** rootdir included, anything above it excluded (``_is_in_confcutdir`` is
    ``path not in confcutdir.parents``, and confcutdir defaults to rootdir).
    """
    above = write(tmp_path / "conftest.py", "")
    root = tmp_path / "suite"
    top = write(root / "conftest.py", "")
    nested = write(root / "pkg" / "conftest.py", "")
    target = write(root / "pkg" / "test_a.py", "def test_x(): pass\n")

    assert conftest_chain(target, root) == [top, nested]
    assert above not in conftest_chain(target, root)


def test_module_fixture_shadows_the_conftest_one(tmp_path: Path) -> None:
    """Corpus `fixtures/override-nearest`: nearest definition wins.

    Registration order is furthest-to-nearest, so ``defs[-1]`` is the nearest —
    pytest's own convention (`_get_active_fixturedef`: "sorted from furthest to closest").
    """
    write(
        tmp_path / "conftest.py",
        """
        import pytest

        @pytest.fixture
        def value():
            return "conftest"
        """,
    )
    target = write(
        tmp_path / "test_override.py",
        """
        import pytest

        @pytest.fixture
        def value():
            return "module"

        def test_nearest_wins(value):
            assert value == "module"
        """,
    )

    with isolated_import_state():
        module, registry = load(target, tmp_path)
        defs = registry.getfixturedefs("value")
        assert defs is not None
        assert [d.baseid for d in defs] == ["", "test_override.py"]
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        assert run_all(plans) == [("test_override.py::test_nearest_wins", "passed")]


def test_conftest_is_imported_once_and_shared_with_the_test_module(tmp_path: Path) -> None:
    """Corpus `fixtures/autouse`: the conftest the worker parses IS the one the test imports.

    v1's bug (#, ledger entry "conftest loaded twice as two module objects") made the
    autouse fixture append to a different list than the test read.  The plugin-style cache
    in :func:`import_conftest` plus real-identity import is what closes it.
    """
    write(
        tmp_path / "conftest.py",
        """
        import pytest

        applied = []

        @pytest.fixture(autouse=True)
        def always():
            applied.append(1)
        """,
    )
    target = write(
        tmp_path / "test_autouse.py",
        """
        from conftest import applied

        def test_autouse_applied():
            assert len(applied) == 1
        """,
    )

    with isolated_import_state():
        _ids, plans = collect(target, tmp_path)
        assert run_all(plans) == [("test_autouse.py::test_autouse_applied", "passed")]


def test_a_conftest_is_imported_once_per_worker_however_many_files_use_it(
    tmp_path: Path,
) -> None:
    """Port of `_importconftest`'s ``get_plugin(str(conftestpath))`` cache (l. 695-698).

    Two test files in one directory share one conftest *object*.  Re-importing it per file
    would reset its module-level state — the exact shape of v1's double-import bug — and
    would go unnoticed in any suite whose conftest happens to be stateless.
    """
    write(
        tmp_path / "conftest.py",
        """
        import pytest

        loads = []
        loads.append(1)

        @pytest.fixture
        def load_count():
            return len(loads)
        """,
    )
    first = write(tmp_path / "test_one.py", "def test_a(load_count):\n    assert load_count == 1\n")
    second = write(
        tmp_path / "test_two.py", "def test_b(load_count):\n    assert load_count == 1\n"
    )

    with isolated_import_state():
        modules: list[object] = []
        for target in (first, second):
            module, registry = load(target, tmp_path)
            del module
            defs = registry.getfixturedefs("load_count")
            assert defs is not None
            modules.append(defs[-1].func)
            _ids, plans = collect(target, tmp_path)
            assert [status for _id, status in run_all(plans)] == ["passed"]
        assert modules[0] is modules[1]


def test_collect_file_uses_the_same_registry_assembly_as_build_registry(tmp_path: Path) -> None:
    """The protocol entry point must go through :func:`build_registry`, not its own copy.

    Everything else in this file drives `build_registry` directly; if `collect_file` assembled
    the registry a second way, all of it could stay green while the shipped path lost its
    conftest chain.  So this drives ``collect_file`` — the function ``main()`` calls — and
    asserts the conftest's parametrized fixture still expands the ids.
    """
    write(
        tmp_path / "conftest.py",
        """
        import pytest

        @pytest.fixture(params=["c1", "c2"])
        def shared(request):
            return request.param
        """,
    )
    target = write(tmp_path / "test_via_protocol.py", "def test_uses(shared):\n    pass\n")

    with isolated_import_state():
        _ = worker.handle_init({"rootdir": str(tmp_path)})
        try:
            response = worker.collect_file(target.as_posix())
        finally:
            worker._state = None  # pyright: ignore[reportPrivateUsage]
            worker._execution_plans.clear()  # pyright: ignore[reportPrivateUsage]
    assert "error" not in response, response.get("error")
    assert [entry["id"] for entry in response.get("tests", [])] == [
        "test_via_protocol.py::test_uses[c1]",
        "test_via_protocol.py::test_uses[c2]",
    ]


def test_sibling_conftests_do_not_collide(tmp_path: Path) -> None:
    """Port of `_importconftest`'s ``del sys.modules[conftestpath.stem]``.

    Probed against pytest 8.4.2: two non-package directories each with a ``conftest.py``
    collect and pass.  Without the eviction the second one is an import-file mismatch.
    """
    for label in ("a", "b"):
        write(
            tmp_path / label / "conftest.py",
            f"""
            import pytest

            @pytest.fixture
            def v():
                return {label!r}
            """,
        )
        write(
            tmp_path / label / f"test_{label}.py",
            f"""
            def test_{label}(v):
                assert v == {label!r}
            """,
        )

    with isolated_import_state():
        for label in ("a", "b"):
            _ids, plans = collect(tmp_path / label / f"test_{label}.py", tmp_path)
            assert run_all(plans) == [(f"{label}/test_{label}.py::test_{label}", "passed")]


def test_fixture_metadata_is_read_from_the_decorator(tmp_path: Path) -> None:
    """scope / autouse / name / params come off ``decorators.py::fixture``'s attributes."""
    target = write(
        tmp_path / "test_meta.py",
        """
        import pytest

        @pytest.fixture(scope="module", autouse=True, name="renamed")
        def _hidden():
            return 1

        @pytest.fixture(params=[1, 2], ids=["one", "two"])
        def choice(request):
            return request.param

        def test_x(renamed, choice):
            pass
        """,
    )
    with isolated_import_state():
        _module, registry = load(target, tmp_path)
        renamed = registry.getfixturedefs("renamed")
        assert renamed is not None and renamed[-1].scope == "module"
        assert renamed[-1].autouse is True
        assert registry.getfixturedefs("_hidden") is None
        choice = registry.getfixturedefs("choice")
        assert choice is not None
        # `(id, value, marks)` since Phase 4 Task 1: a fixture's `params=` takes
        # `pytest.param(..., marks=...)`, which pytest carries because
        # `FixtureManager.pytest_generate_tests` hands `fixturedef.params` to
        # `metafunc.parametrize` as ordinary parameter sets.
        assert choice[-1].params == (("one", 1, ()), ("two", 2, ()))
        assert registry.autouse_names == ("renamed",)


def test_runtest_is_collected_when_every_test_method_is_hidden(tmp_path: Path) -> None:
    """Port of `_pytest/unittest.py::UnitTestCase.collect`'s ``foundsomething`` flag.

    pytest sets ``foundsomething`` only for methods that survive the ``__test__`` filter, and
    the ``runTest`` fallback is never itself filtered.  So a ``TestCase`` whose only
    ``test_*`` method is hidden still collects ``runTest`` — probed against pytest 8.4.2,
    which emits ``test_runtest.py::Legacy::runTest``.  Computing the method list up front
    (fallback before the filter, then filtering the fallback too) gets this backwards in
    both directions and collects nothing.
    """
    target = write(
        tmp_path / "test_runtest.py",
        """
        import unittest

        class Legacy(unittest.TestCase):
            def test_hidden(self):
                pass
            test_hidden.__test__ = False

            def runTest(self):
                pass
        """,
    )
    with isolated_import_state():
        ids, plans = collect(target, tmp_path)
    assert ids == ["test_runtest.py::Legacy::runTest"]
    assert [plan.unittest_method for plan in plans] == ["runTest"]


def test_runtest_is_not_collected_when_a_real_test_method_survives(tmp_path: Path) -> None:
    """The other side of ``if not foundsomething`` — one visible method suppresses runTest."""
    target = write(
        tmp_path / "test_runtest_skipped.py",
        """
        import unittest

        class Legacy(unittest.TestCase):
            def test_visible(self):
                pass

            def runTest(self):
                pass
        """,
    )
    with isolated_import_state():
        ids, _plans = collect(target, tmp_path)
    assert ids == ["test_runtest_skipped.py::Legacy::test_visible"]


def test_class_fixtures_are_invisible_outside_the_class(tmp_path: Path) -> None:
    """Port of `Class.collect`'s ``parsefactories(self.newinstance(), self.nodeid)``."""
    target = write(
        tmp_path / "test_cls.py",
        """
        import pytest

        class TestBox:
            @pytest.fixture
            def boxed(self):
                return "in-class"

            def test_inside(self, boxed):
                assert boxed == "in-class"

        def test_outside(boxed):
            pass
        """,
    )
    with isolated_import_state():
        _ids, plans = collect(target, tmp_path)
        results = dict(run_all(plans))
    assert results["test_cls.py::TestBox::test_inside"] == "passed"
    assert results["test_cls.py::test_outside"].startswith(
        "setup-or-call:FixtureLookupError:fixture 'boxed' not found"
    )


# ---------------------------------------------------------------------------
# 2. closure — transitive deps, autouse, ordering, unknown names
# ---------------------------------------------------------------------------


def _registry(*defs: FixtureDef) -> FixtureRegistry:
    registry = FixtureRegistry()
    for fixturedef in defs:
        registry.register(fixturedef)
    return registry


def _def(
    name: str,
    argnames: tuple[str, ...] = (),
    scope: str = "function",
    autouse: bool = False,
    params: tuple[tuple[str, object], ...] | None = None,
    baseid: str = "",
) -> FixtureDef:
    return FixtureDef(
        name=name,
        func=lambda: None,
        scope=scope,
        params=params,
        autouse=autouse,
        baseid=baseid,
        argnames=argnames,
    )


def test_closure_pulls_transitive_dependencies() -> None:
    """`getfixtureclosure`'s fixpoint loop: deps of deps land in the closure."""
    registry = _registry(_def("a", ("b",)), _def("b", ("c",)), _def("c"))
    closure = build_closure(registry, ["a"])
    assert closure.names == ("a", "b", "c")
    assert set(closure.arg2defs) == {"a", "b", "c"}


def test_closure_puts_autouse_names_first() -> None:
    """``deduplicate_names(autousenames, usefixturesnames, argnames)`` — autouse leads.

    Load-bearing for ids: an autouse parametrized fixture contributes the leftmost id
    component even for a test that never names it (probed: ``test_auto_only[1]``).
    """
    registry = _registry(_def("auto", autouse=True), _def("asked"))
    assert build_closure(registry, ["asked"]).names == ("auto", "asked")


def test_closure_sorts_wider_scopes_first_and_is_stable() -> None:
    """``fixturenames_closure.sort(key=sort_by_scope, reverse=True)`` — a STABLE sort.

    Probed: ``test_scope_order[m1-1-f1]`` for a module fixture requested after a function
    one, so the module id component leads.  Two same-scope names keep discovery order.
    """
    registry = _registry(
        _def("f1"), _def("m1", scope="module"), _def("f2"), _def("s1", scope="session")
    )
    assert build_closure(registry, ["f1", "m1", "f2", "s1"]).names == ("s1", "m1", "f1", "f2")


def test_closure_keeps_but_never_resolves_directly_parametrized_names() -> None:
    """``ignore_args`` from `_get_direct_parametrize_args`: the name is shadowed, not resolved."""
    registry = _registry(_def("direct", ("dep",)), _def("dep"))
    closure = build_closure(registry, ["direct"], ignore_args=frozenset({"direct"}))
    assert closure.names == ("direct",)
    assert closure.arg2defs == {}


def test_closure_takes_transitive_deps_from_the_NEAREST_definition() -> None:
    """``for arg in fixturedefs[-1].argnames`` — the nearest def decides what else is needed.

    A conftest ``thing()`` overridden by a module ``thing(helper)``: only the nearest def
    knows about ``helper``, so reading the furthest one would leave it out of the closure
    and the test would die with "fixture 'helper' not found" at setup.
    """
    registry = FixtureRegistry()
    registry.register(_def("thing", baseid=""))
    registry.register(_def("thing", ("helper",), baseid="test_x.py"))
    registry.register(_def("helper"))
    assert build_closure(registry, ["thing"]).names == ("thing", "helper")


def test_closure_needs_more_than_one_pass_over_a_snapshot() -> None:
    """The fixpoint loop and the live-list iteration are one mechanism, not two.

    pytest iterates ``fixturenames_closure`` *while appending to it*, so a dependency found
    mid-pass is resolved in the same pass; the ``while lastlen != len(...)`` wrapper is what
    makes the algorithm correct if that ever stops being true.  A four-deep chain needs
    either property, so this pins the pair.
    """
    registry = _registry(_def("a", ("b",)), _def("b", ("c",)), _def("c", ("d",)), _def("d"))
    assert build_closure(registry, ["a"]).names == ("a", "b", "c", "d")


def test_request_is_never_a_registered_fixture(tmp_path: Path) -> None:
    """pytest special-cases ``request`` in `_get_active_fixturedef` (l. 566-570).

    It lands in the closure with no fixturedef and must not raise "not found".
    """
    target = write(
        tmp_path / "test_req.py",
        """
        import pytest

        @pytest.fixture(params=["only"])
        def flavour(request):
            return request.param

        def test_uses(flavour):
            assert flavour == "only"
        """,
    )
    with isolated_import_state():
        _module, registry = load(target, tmp_path)
        closure = build_closure(registry, ["flavour"])
        assert "request" in closure.names
        assert "request" not in closure.arg2defs
        _ids, plans = collect(target, tmp_path)
        assert run_all(plans) == [("test_req.py::test_uses[only]", "passed")]


def test_unknown_fixture_errors_at_setup_with_pytests_wording(tmp_path: Path) -> None:
    """`FixtureLookupError.formatrepr` l. 875-877, verbatim — and NOT a collection error.

    pytest collects the test fine and reports an **error** at setup; so does this.
    """
    target = write(
        tmp_path / "test_missing.py",
        """
        import pytest

        @pytest.fixture
        def known():
            return 1

        def test_needs_ghost(ghost):
            pass
        """,
    )
    with isolated_import_state():
        ids, plans = collect(target, tmp_path)
        assert ids == ["test_missing.py::test_needs_ghost"]
        runner = FixtureRunner()
        with pytest.raises(FixtureLookupError) as excinfo:
            _ = runner.setup(plans[0])
    message = str(excinfo.value)
    assert message.splitlines()[0] == "fixture 'ghost' not found"
    assert " available fixtures: " in message
    assert "known" in message
    assert message.endswith(" use 'pytest --fixtures [testpath]' for help on them.")


def test_unsupported_builtin_says_so_instead_of_not_found(tmp_path: Path) -> None:
    """``pytester`` is a gap in this worker, not a fixture the user forgot to write.

    The subject has now moved twice: it was ``caplog`` until Phase 3 Task 2 implemented it,
    then ``recwarn`` until Phase 4 Task 1c did (MECHANISM M5 — the recorded claim that it
    "needs a warnings channel the v2 wire does not have" turned out to be wrong, since it
    records **in-process** and tells the orchestrator nothing). The *wording* rule is what
    this tests and it outlives any particular gap. ``pytester`` is pytest's own in-process
    test harness — genuinely different machinery, not a fixture waiting to be written.
    """
    target = write(
        tmp_path / "test_pytester.py",
        "def test_harness(pytester):\n    pass\n",
    )
    with isolated_import_state():
        _ids, plans = collect(target, tmp_path)
        with pytest.raises(FixtureLookupError) as excinfo:
            _ = FixtureRunner().setup(plans[0])
    assert "not supported by the rustest v2 worker yet" in str(excinfo.value)
    assert "not found" not in str(excinfo.value)


def test_the_supported_and_unsupported_builtin_sets_are_disjoint() -> None:
    """A name in both lists would print itself as its own alternative.

    ``_fixture_not_found_message`` consults ``UNSUPPORTED_BUILTIN_FIXTURES`` first and then
    lists ``BUILTIN_FIXTURES`` as what *is* available, so an overlap produces "capfd is not
    supported (supported builtins: ..., capfd, ...)". Phase 3 Task 2 moved seven names from
    one list to the other, which is exactly the edit that leaves a straggler behind.
    """
    assert not set(worker.BUILTIN_FIXTURES) & worker.UNSUPPORTED_BUILTIN_FIXTURES


def test_a_wider_fixture_may_not_request_a_narrower_one(tmp_path: Path) -> None:
    """Port of `SubRequest._check_scope` (l. 780-801) — message and all.

    Silently caching the narrow value for the wide lifetime is the alternative, which is
    wrong-answer-shaped rather than error-shaped.  The wording is pytest's verbatim; only the
    frame paths are absolute, because the runner has no session to relativise against.
    """
    _ = write(tmp_path / "pytest.ini", "[pytest]\n")
    target = write(
        tmp_path / "test_mismatch.py",
        """
        import pytest

        @pytest.fixture
        def narrow():
            return 1

        @pytest.fixture(scope="module")
        def wide(narrow):
            return narrow

        def test_x(wide):
            pass
        """,
    )
    with isolated_import_state():
        _ids, plans = collect(target, tmp_path)
        with pytest.raises(ScopeMismatch) as excinfo:
            _ = FixtureRunner().setup(plans[0])
    assert _basenames(str(excinfo.value)) == _basenames(pytest_scope_mismatch(tmp_path))


def test_the_scope_check_uses_the_selected_definition_not_the_nearest(tmp_path: Path) -> None:
    """`_get_active_fixturedef` checks the def it is *about to execute* (l. 633).

    An override chain can hold definitions of different scopes.  Here the nearest ``value``
    is module-scoped and legal for a module-scoped consumer, but the one actually selected —
    the super fixture, reached because the override requests its own name — is function
    scoped and must be rejected.  Reading ``defs[-1]`` would wave it through and cache a
    function-scoped value for the module's lifetime.
    """
    write(
        tmp_path / "conftest.py",
        """
        import pytest

        @pytest.fixture
        def value():
            return "narrow"
        """,
    )
    target = write(
        tmp_path / "test_selected.py",
        """
        import pytest

        @pytest.fixture(scope="module")
        def value(value):
            return value

        def test_x(value):
            pass
        """,
    )
    with isolated_import_state():
        _ids, plans = collect(target, tmp_path)
        with pytest.raises(ScopeMismatch) as excinfo:
            _ = FixtureRunner().setup(plans[0])
    assert "the function scoped fixture value with a module scoped request object" in str(
        excinfo.value
    )


def test_getfixturevalue_is_scope_checked_like_a_declared_dependency(tmp_path: Path) -> None:
    """`getfixturevalue` is a thin wrapper over `_get_active_fixturedef`, so it checks too."""
    target = write(
        tmp_path / "test_dynamic.py",
        """
        import pytest

        @pytest.fixture
        def narrow():
            return 1

        @pytest.fixture(scope="module")
        def wide(request):
            return request.getfixturevalue("narrow")

        def test_x(wide):
            pass
        """,
    )
    with isolated_import_state():
        _ids, plans = collect(target, tmp_path)
        with pytest.raises(ScopeMismatch):
            _ = FixtureRunner().setup(plans[0])


def test_request_scope_reports_the_declared_scope_not_the_cache_bucket(tmp_path: Path) -> None:
    """``session`` must read back as ``session``, even though it caches with ``package``."""
    target = write(
        tmp_path / "test_req_scope.py",
        """
        import pytest

        seen = {}

        @pytest.fixture(scope="session")
        def wide(request):
            seen["wide"] = request.scope
            return 1

        @pytest.fixture(scope="class")
        def middling(request):
            seen["middling"] = request.scope
            return 2

        @pytest.fixture(scope="package")
        def boxed(request):
            seen["boxed"] = request.scope
            return 3

        def test_x(wide, middling, boxed):
            pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        assert [status for _id, status in run_all(plans)] == ["passed"]
        seen = getattr(module, "seen")
    # ``package`` is the one scope whose bucket differs from its name (`_SCOPE_BUCKET` maps it
    # onto the worker-lifetime bucket), so it is the only row that can catch a runner
    # reporting its internal bucket instead of what the author wrote.
    assert seen == {"wide": "session", "middling": "class", "boxed": "package"}


def test_an_override_can_request_the_fixture_it_overrides(tmp_path: Path) -> None:
    """`_get_active_fixturedef` l. 596-608: one level up per matching request in the chain."""
    write(
        tmp_path / "conftest.py",
        """
        import pytest

        @pytest.fixture
        def value():
            return "base"
        """,
    )
    target = write(
        tmp_path / "test_super.py",
        """
        import pytest

        @pytest.fixture
        def value(value):
            return value + "+module"

        def test_chained(value):
            assert value == "base+module"
        """,
    )
    with isolated_import_state():
        _ids, plans = collect(target, tmp_path)
        assert run_all(plans) == [("test_super.py::test_chained", "passed")]


# ---------------------------------------------------------------------------
# 3. scopes and teardown order
# ---------------------------------------------------------------------------


def test_function_scope_is_fresh_per_test(tmp_path: Path) -> None:
    """Corpus `fixtures/scope-function`, verbatim."""
    target = write(
        tmp_path / "test_scope_function.py",
        """
        import itertools

        import pytest

        counter = itertools.count()

        @pytest.fixture
        def fresh():
            return next(counter)

        def test_first(fresh):
            assert fresh == 0

        def test_second(fresh):
            assert fresh == 1
        """,
    )
    with isolated_import_state():
        _ids, plans = collect(target, tmp_path)
        assert [status for _id, status in run_all(plans)] == ["passed", "passed"]


def test_module_scope_is_cached_across_the_file(tmp_path: Path) -> None:
    """Corpus `fixtures/scope-module`, verbatim — and the cache survives function teardown."""
    target = write(
        tmp_path / "test_scope_module.py",
        """
        import itertools

        import pytest

        counter = itertools.count()

        @pytest.fixture(scope="module")
        def shared():
            return next(counter)

        def test_first(shared):
            assert shared == 0

        def test_second(shared):
            assert shared == 0
        """,
    )
    with isolated_import_state():
        _ids, plans = collect(target, tmp_path)
        assert [status for _id, status in run_all(plans)] == ["passed", "passed"]


def test_yield_teardown_runs_after_the_test_that_used_it(tmp_path: Path) -> None:
    """Corpus `fixtures/yield-teardown`, verbatim — the case that pins *when* teardown runs.

    The second test asserts the first test's fixture was already finalised, so a teardown
    deferred to shutdown fails it.
    """
    target = write(
        tmp_path / "test_teardown.py",
        """
        import pytest

        events = []

        @pytest.fixture
        def resource():
            events.append("setup")
            yield "value"
            events.append("teardown")

        def test_uses_resource(resource):
            assert resource == "value"
            assert events == ["setup"]

        def test_teardown_ran_after_previous_test():
            assert events == ["setup", "teardown"]
        """,
    )
    with isolated_import_state():
        _ids, plans = collect(target, tmp_path)
        assert [status for _id, status in run_all(plans)] == ["passed", "passed"]


def test_teardown_is_reverse_setup_order(tmp_path: Path) -> None:
    """A **dependent** fixture is torn down before the fixture it was built from.

    ``outer`` depends on ``inner``, so setup is inner-then-outer and teardown must be
    outer-then-inner.

    What this row actually pins is the **#4871 dependency cascade**, not the scope bucket's
    LIFO: since `FixtureDef.execute` l. 1115-1121 was ported, every dependency carries a
    finalizer that finishes its dependents first, and that alone produces this order even
    from a bucket drained FIFO.  The docstring used to claim a FIFO drain would fail here;
    it would not, and a claim a mutation cannot make true is worse than no claim.
    ``test_independent_fixtures_tear_down_in_reverse_setup_order`` is the row that pins the
    bucket order, because two *independent* fixtures leave nothing else to produce it.
    """
    target = write(
        tmp_path / "test_order.py",
        """
        import pytest

        events = []

        @pytest.fixture
        def inner():
            events.append("setup:inner")
            yield 1
            events.append("teardown:inner")

        @pytest.fixture
        def outer(inner):
            events.append("setup:outer")
            yield 2
            events.append("teardown:outer")

        def test_uses(outer):
            pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        assert run_all(plans) == [("test_order.py::test_uses", "passed")]
        events = getattr(module, "events")
    assert events == ["setup:inner", "setup:outer", "teardown:outer", "teardown:inner"]


def test_independent_fixtures_tear_down_in_reverse_setup_order(tmp_path: Path) -> None:
    """The scope bucket's own LIFO, with the dependency cascade taken out of the picture.

    ``test_teardown_is_reverse_setup_order`` uses a dependency chain, and since #4871 landed
    the cascade alone would produce the right answer there — the bucket order is no longer
    load-bearing in that shape.  Two **independent** fixtures leave nothing but the bucket:
    probed, pytest emits ``teardown:beta, teardown:alpha`` for ``test(alpha, beta)``.
    """
    target = write(
        tmp_path / "test_indep.py",
        """
        import pytest

        events = []

        @pytest.fixture
        def alpha():
            yield 1
            events.append("teardown:alpha")

        @pytest.fixture
        def beta():
            yield 2
            events.append("teardown:beta")

        def test_uses(alpha, beta):
            pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        assert [status for _id, status in run_all(plans)] == ["passed"]
        events = getattr(module, "events")
    assert events == ["teardown:beta", "teardown:alpha"]


def test_independent_scopes_unwind_narrowest_first(tmp_path: Path) -> None:
    """Same reasoning across scopes: no dependency edge, so only the bucket order can save it.

    A function fixture that does **not** request the module one still has to be finalised
    first — pytest unwinds the deeper node before the shallower one.
    """
    target = write(
        tmp_path / "test_indep_scopes.py",
        """
        import pytest

        events = []

        @pytest.fixture(scope="module")
        def wide():
            yield 1
            events.append("teardown:module")

        @pytest.fixture
        def narrow():
            yield 2
            events.append("teardown:function")

        def test_uses(wide, narrow):
            pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        runner = FixtureRunner()
        _ = runner.setup(plans[0])
        runner.teardown("module")
        events = getattr(module, "events")
    assert events == ["teardown:function", "teardown:module"]


def test_wider_scopes_tear_down_after_narrower_ones(tmp_path: Path) -> None:
    """`SetupState.teardown_exact` unwinds narrowest-first; :meth:`teardown` mirrors it."""
    target = write(
        tmp_path / "test_scoped_order.py",
        """
        import pytest

        events = []

        @pytest.fixture(scope="module")
        def modfix():
            events.append("setup:module")
            yield 1
            events.append("teardown:module")

        @pytest.fixture
        def funcfix(modfix):
            events.append("setup:function")
            yield 2
            events.append("teardown:function")

        def test_uses(funcfix):
            pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        assert run_all(plans) == [("test_scoped_order.py::test_uses", "passed")]
        events = getattr(module, "events")
    assert events == [
        "setup:module",
        "setup:function",
        "teardown:function",
        "teardown:module",
    ]


def test_a_teardown_exception_surfaces_and_still_finalises_the_rest(tmp_path: Path) -> None:
    """`FixtureDef.finish` re-raises but always clears the cache ("Even if finalization fails").

    pytest reports a teardown failure as an **error** even when the body passed, which is
    why this must not be swallowed.
    """
    target = write(
        tmp_path / "test_bad_teardown.py",
        """
        import pytest

        events = []

        @pytest.fixture
        def good():
            yield 1
            events.append("good-torn-down")

        @pytest.fixture
        def bad(good):
            yield 2
            raise RuntimeError("boom")

        def test_body_passes(bad):
            pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        runner = FixtureRunner()
        _ = runner.setup(plans[0])
        with pytest.raises(RuntimeError, match="boom"):
            runner.teardown("function")
        events = getattr(module, "events")
    assert events == ["good-torn-down"]


def test_several_teardown_exceptions_become_a_group(tmp_path: Path) -> None:
    """``BaseExceptionGroup`` for >1, mirroring `FixtureDef.finish` l. 1069-1072."""
    target = write(
        tmp_path / "test_two_bad.py",
        """
        import pytest

        @pytest.fixture
        def first():
            yield 1
            raise RuntimeError("first")

        @pytest.fixture
        def second(first):
            yield 2
            raise RuntimeError("second")

        def test_x(second):
            pass
        """,
    )
    with isolated_import_state():
        _ids, plans = collect(target, tmp_path)
        runner = FixtureRunner()
        _ = runner.setup(plans[0])
        with pytest.raises(BaseExceptionGroup) as excinfo:
            runner.teardown("function")
    assert [str(exc) for exc in excinfo.value.exceptions] == ["first", "second"]


def test_session_scope_is_worker_lifetime(tmp_path: Path) -> None:
    """The documented 1b.2 limitation: session == per-worker, torn down at Shutdown."""
    target = write(
        tmp_path / "test_session.py",
        """
        import pytest

        events = []

        @pytest.fixture(scope="session")
        def wide():
            events.append("setup")
            yield 1
            events.append("teardown")

        def test_a(wide):
            pass

        def test_b(wide):
            pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        runner = FixtureRunner()
        for plan in plans:
            _ = runner.setup(plan)
            runner.teardown("function")
        events = list(getattr(module, "events"))
        assert events == ["setup"]
        runner.teardown_all()
        events = getattr(module, "events")
    assert events == ["setup", "teardown"]


def test_a_dependent_fixture_is_rebuilt_when_its_parametrized_dependency_changes(
    tmp_path: Path,
) -> None:
    """pytest #4871: the dependency cascade, in full.

    Two module-scoped fixtures where the *dependency* is parametrized. Without the cascade a
    worker hands the second test a ``derived`` built from ``base == "a"`` — a **silently
    wrong value**, not a crash — and tears ``derived`` down after the thing it was built
    from.

    Both halves of pytest's mechanism are pinned by the event list: dependencies resolved
    before the cache check (`FixtureDef.execute` l. 1077-1088, so ``derived`` notices it was
    invalidated), and each dependency carrying a finalizer for its dependents (l. 1115-1121,
    so ``derived`` is torn down *before* ``base``). The sequence below is real pytest 8.4.2
    output, copied from the probe.
    """
    target = write(
        tmp_path / "test_cascade.py",
        """
        import pytest

        events = []

        @pytest.fixture(scope="module", params=["a", "b"])
        def base(request):
            events.append(f"setup:base:{request.param}")
            yield request.param
            events.append(f"teardown:base:{request.param}")

        @pytest.fixture(scope="module")
        def derived(base):
            events.append(f"setup:derived({base})")
            yield f"derived-of-{base}"
            events.append(f"teardown:derived({base})")

        def test_pair(base, derived):
            events.append("test")
            assert derived == f"derived-of-{base}"
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        results = run_all(plans)
        events = list(getattr(module, "events"))
    assert [status for _id, status in results] == ["passed", "passed"]
    # The first eight entries are what the reviewer's probe printed from inside the run; the
    # last two are the module teardown, which `pytest_sessionfinish` confirms pytest emits
    # too (the in-run probe simply could not see them yet).  `run_all` ends with
    # `teardown_all`, so both halves are visible here.
    assert events == [
        "setup:base:a",
        "setup:derived(a)",
        "test",
        "teardown:derived(a)",
        "teardown:base:a",
        "setup:base:b",
        "setup:derived(b)",
        "test",
        "teardown:derived(b)",
        "teardown:base:b",
    ]


def test_a_dependent_notices_it_was_invalidated_while_resolving_its_own_dependency(
    tmp_path: Path,
) -> None:
    """pytest #4871 **half 1**: the cache is read only after the dependencies are resolved.

    The test above requests ``base`` *and* ``derived``, so the closure resolves ``base``
    first and ``derived``'s frame starts after the cascade has already run — which makes it
    blind to the ordering inside ``_resolve_active``.  A mutation pass proved exactly that:
    hoisting the cache read above the dependency loop left that test green.

    Requesting **only the dependent** is the shape #4871 was filed about.  ``derived``'s
    frame reads its own cache *while* the ``base`` underneath it is being re-parametrized;
    reading it too early returns the stale ``derived-of-a`` for the ``b`` round and never
    re-runs the fixture body.  Probed sequence below is real pytest 8.4.2 (2 passed).
    """
    target = write(
        tmp_path / "test_only_dependent.py",
        """
        import pytest

        events = []

        @pytest.fixture(scope="module", params=["a", "b"])
        def base(request):
            events.append(f"setup:base:{request.param}")
            yield request.param
            events.append(f"teardown:base:{request.param}")

        @pytest.fixture(scope="module")
        def derived(base):
            events.append(f"setup:derived({base})")
            yield f"derived-of-{base}"
            events.append(f"teardown:derived({base})")

        def test_only_derived(derived):
            events.append(f"test({derived})")
            assert derived in ("derived-of-a", "derived-of-b")
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        assert [status for _id, status in run_all(plans)] == ["passed", "passed"]
        events = getattr(module, "events")
    assert events == [
        "setup:base:a",
        "setup:derived(a)",
        "test(derived-of-a)",
        "teardown:derived(a)",
        "teardown:base:a",
        "setup:base:b",
        "setup:derived(b)",
        "test(derived-of-b)",
        "teardown:derived(b)",
        "teardown:base:b",
    ]


def test_a_dependent_is_torn_down_before_its_dependency_at_scope_end(tmp_path: Path) -> None:
    """The cascade's other half, on the ordinary path: dependents finish first.

    `FixtureDef.execute` l. 1115-1121 registers this fixture's ``finish`` on **every**
    fixture it requested, "ensuring that if a requested fixture gets torn down we get torn
    down first". With no parametrization in sight, draining the module bucket must still
    finalise ``derived`` before ``base``.
    """
    target = write(
        tmp_path / "test_dep_order.py",
        """
        import pytest

        events = []

        @pytest.fixture(scope="module")
        def base():
            yield 1
            events.append("teardown:base")

        @pytest.fixture(scope="module")
        def derived(base):
            yield 2
            events.append("teardown:derived")

        def test_uses(derived):
            pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        assert [status for _id, status in run_all(plans)] == ["passed"]
        events = getattr(module, "events")
    assert events == ["teardown:derived", "teardown:base"]


def test_teardown_of_a_wide_scope_unwinds_the_narrow_ones_first(tmp_path: Path) -> None:
    """One `teardown("module")` call must unwind function scope before module scope.

    Port of `SetupState.teardown_exact`, which pops the node stack from the deepest node
    upwards.  Driven directly (no intervening ``teardown("function")``) because that is the
    only shape in which the *within-call* bucket order is observable — Task 3's normal
    sequence tears function scope down after every test, which hides it.
    """
    target = write(
        tmp_path / "test_unwind.py",
        """
        import pytest

        events = []

        @pytest.fixture(scope="module")
        def modfix():
            yield 1
            events.append("teardown:module")

        @pytest.fixture
        def funcfix(modfix):
            yield 2
            events.append("teardown:function")

        def test_uses(funcfix):
            pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        runner = FixtureRunner()
        _ = runner.setup(plans[0])
        runner.teardown("module")
        events = getattr(module, "events")
    assert events == ["teardown:function", "teardown:module"]


def test_teardown_of_module_scope_leaves_session_scope_alone(tmp_path: Path) -> None:
    """``session`` is a strictly wider bucket: moving to the next module must not drain it."""
    target = write(
        tmp_path / "test_keep_session.py",
        """
        import pytest

        events = []

        @pytest.fixture(scope="session")
        def wide():
            yield 1
            events.append("teardown:session")

        @pytest.fixture(scope="module")
        def narrow(wide):
            yield 2
            events.append("teardown:module")

        def test_uses(narrow):
            pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        runner = FixtureRunner()
        _ = runner.setup(plans[0])
        runner.teardown("module")
        assert getattr(module, "events") == ["teardown:module"]
        runner.teardown_all()
        events = getattr(module, "events")
    assert events == ["teardown:module", "teardown:session"]


def test_request_addfinalizer_belongs_to_its_fixture_and_runs_once(tmp_path: Path) -> None:
    """Port of `SubRequest.addfinalizer` + `FixtureDef.finish`'s LIFO drain.

    A module-scoped parametrized fixture is finalised *early* when its parameter changes, so
    this pins three things at once: the finalizer travels with the fixture (not the scope),
    it runs after the post-yield teardown (LIFO), and it runs exactly **once** per instance.
    """
    target = write(
        tmp_path / "test_addfinalizer.py",
        """
        import pytest

        events = []

        @pytest.fixture(scope="module", params=["a", "b"])
        def flavour(request):
            request.addfinalizer(lambda: events.append(f"final:{request.param}"))
            yield request.param
            events.append(f"yield:{request.param}")

        def test_uses(flavour):
            pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        runner = FixtureRunner()
        for plan in plans:
            _ = runner.setup(plan)
            runner.teardown("function")
        runner.teardown_all()
        events = getattr(module, "events")
    assert events == ["yield:a", "final:a", "yield:b", "final:b"]


CLASS_SCOPE_MODULE = """
    import pytest

    events = []

    @pytest.fixture(scope="class")
    def per_class():
        events.append("setup")
        yield object()
        events.append("teardown")

    class TestA:
        def test_one(self, per_class):
            events.append("A.one")

        def test_two(self, per_class):
            events.append("A.two")

    class TestB:
        def test_three(self, per_class):
            events.append("B.three")
"""


def test_class_scope_is_torn_down_at_the_class_boundary(tmp_path: Path) -> None:
    """A class-scoped fixture must not survive into the next class.

    pytest gets the boundary from the collection tree; a worker gets it from the change in
    ``CollectedTest.class_name``, which :meth:`FixtureRunner.setup` notices via
    ``note_test_boundary``. Probed against pytest 8.4.2, whose event sequence for exactly
    this module is reproduced below — one setup per class, torn down before the next class
    starts.
    """
    target = write(tmp_path / "test_cls_scope.py", CLASS_SCOPE_MODULE)
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        assert [status for _id, status in run_all(plans)] == ["passed"] * 3
        events = getattr(module, "events")
    assert events == [
        "setup",
        "A.one",
        "A.two",
        "teardown",
        "setup",
        "B.three",
        "teardown",
    ]


def test_the_class_boundary_is_what_separates_the_instances(tmp_path: Path) -> None:
    """Without the boundary one class-scoped value silently leaks into every later class.

    Asserts the *object identity* the reviewer's probe caught: `TestA`'s two methods share
    an instance, and `TestB` gets a different one.
    """
    target = write(tmp_path / "test_cls_identity.py", CLASS_SCOPE_MODULE)
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        runner = FixtureRunner()
        seen: list[object] = []
        for plan in plans:
            kwargs = runner.setup(plan)
            seen.append(kwargs["per_class"])
            runner.teardown("function")
        runner.teardown_all()
    assert seen[0] is seen[1]
    assert seen[2] is not seen[0]


def test_the_class_boundary_compares_the_whole_class_chain(tmp_path: Path) -> None:
    """Two nested classes with the same leaf name are still two different classes.

    ``CollectedTest.class_name`` is the whole chain (``TestOuter.TestInner``); comparing only
    the innermost part would see one unbroken ``TestInner`` and share a class-scoped fixture
    across two unrelated classes — a silent leak, not a crash.
    """
    target = write(
        tmp_path / "test_nested_cls.py",
        """
        import pytest

        events = []

        @pytest.fixture(scope="class")
        def per_class():
            events.append("setup")
            yield object()
            events.append("teardown")

        class TestOuterA:
            class TestInner:
                def test_a(self, per_class):
                    pass

        class TestOuterB:
            class TestInner:
                def test_b(self, per_class):
                    pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        ids = [entry["id"] for entry in entries]
        assert [plan.class_name for plan in plans] == [
            "TestOuterA.TestInner",
            "TestOuterB.TestInner",
        ]
        assert [status for _id, status in run_all(plans)] == ["passed", "passed"]
        events = getattr(module, "events")
    assert ids == [
        "test_nested_cls.py::TestOuterA::TestInner::test_a",
        "test_nested_cls.py::TestOuterB::TestInner::test_b",
    ]
    assert events == ["setup", "teardown", "setup", "teardown"]


def test_note_test_boundary_is_a_no_op_within_one_class(tmp_path: Path) -> None:
    """Only a *change* of class tears class scope down — repeating a name must not."""
    target = write(tmp_path / "test_cls_stable.py", CLASS_SCOPE_MODULE)
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        runner = FixtureRunner()
        _ = runner.setup(plans[0])
        runner.teardown("function")
        runner.note_test_boundary("TestA")
        runner.note_test_boundary("TestA")
        assert getattr(module, "events") == ["setup"]
        runner.teardown_all()
        assert getattr(module, "events") == ["setup", "teardown"]


#: ``teardown(scope)`` must drain that scope AND every narrower one, and nothing wider.
#: A test that only asserted "does not raise" would pass with every branch deleted, which
#: is what the previous version of this table did.
SCOPE_TEARDOWN_TABLE: dict[str, list[str]] = {
    "function": ["function"],
    "class": ["function", "class"],
    "module": ["function", "class", "module"],
    # package and session share the worker-lifetime bucket -- see `_SCOPE_BUCKET`.
    "package": ["function", "class", "module", "package", "session"],
    "session": ["function", "class", "module", "package", "session"],
}


@pytest.mark.parametrize("scope", list(SCOPE_NAMES))
def test_teardown_drains_exactly_its_scope_and_the_narrower_ones(
    scope: str, tmp_path: Path
) -> None:
    """One fixture per declared scope; assert precisely which ones `teardown(scope)` ends."""
    assert set(SCOPE_TEARDOWN_TABLE) == set(SCOPE_NAMES)
    target = write(
        tmp_path / f"test_scopes_{scope}.py",
        """
        import pytest

        events = []

        def _make(scope_name):
            @pytest.fixture(scope=scope_name, name=scope_name)
            def _fixture():
                yield scope_name
                events.append(scope_name)
            return _fixture

        session = _make("session")
        package = _make("package")
        module = _make("module")
        klass = _make("class")
        function = _make("function")

        def test_all(session, package, module):
            pass
        """,
    )
    with isolated_import_state():
        module_obj, registry = load(target, tmp_path)
        closure = build_closure(registry, ["session", "package", "module", "class", "function"])
        plan = ExecutionPlan(
            id="probe",
            path=target,
            module=module_obj,
            parts=("test_all",),
            func=lambda **_kwargs: None,
            owner=None,
            closure=closure,
            fixture_params={},
            direct_params={},
            argnames=(),
            marks=(),
        )
        runner = FixtureRunner()
        _ = runner.setup(plan)
        assert getattr(module_obj, "events") == []
        runner.teardown(scope)
        drained = sorted(getattr(module_obj, "events"))
        runner.teardown_all()
    assert drained == sorted(SCOPE_TEARDOWN_TABLE[scope])


# ---------------------------------------------------------------------------
# 4. builtin fixtures
# ---------------------------------------------------------------------------


def test_supported_builtins_resolve(tmp_path: Path) -> None:
    """tmp_path / monkeypatch / capsys come from v1's `builtin_fixtures`, wrapped not forked."""
    target = write(
        tmp_path / "test_builtins.py",
        """
        import os
        from pathlib import Path

        def test_builtins(tmp_path, monkeypatch, capsys):
            assert isinstance(tmp_path, Path) and tmp_path.is_dir()
            monkeypatch.setenv("RUSTEST_V2_PROBE", "1")
            assert os.environ["RUSTEST_V2_PROBE"] == "1"
            print("hello")
            assert capsys.readouterr().out == "hello\\n"
        """,
    )
    with isolated_import_state():
        _ids, plans = collect(target, tmp_path)
        assert run_all(plans) == [("test_builtins.py::test_builtins", "passed")]
    assert "RUSTEST_V2_PROBE" not in __import__("os").environ


# ---------------------------------------------------------------------------
# 5. closure-driven param ids — the fixtures/parametrized-fixture un-waiver
# ---------------------------------------------------------------------------


#: Each row is a whole test module; ids are compared against real pytest, never asserted
#: by hand.  Together they cover every ordering rule `fixture_param_dimensions` encodes.
ID_CASES: dict[str, str] = {
    "corpus_parametrized_fixture": """
        import pytest

        @pytest.fixture(params=[1, 2])
        def number(request):
            return request.param

        def test_number(number):
            assert number in (1, 2)
    """,
    "two_fixtures_signature_order_irrelevant": """
        import pytest

        @pytest.fixture(params=[1, 2])
        def number(request):
            return request.param

        @pytest.fixture(params=["a", "b"])
        def letter(request):
            return request.param

        def test_forward(number, letter):
            pass

        def test_reverse(letter, number):
            pass
    """,
    "explicit_ids": """
        import pytest

        @pytest.fixture(params=[10, 20], ids=["ten", "twenty"])
        def named(request):
            return request.param

        def test_named(named):
            pass
    """,
    "fixture_components_precede_direct_ones": """
        import pytest

        @pytest.fixture(params=[1, 2])
        def number(request):
            return request.param

        @pytest.mark.parametrize("direct", [7, 8])
        def test_mixed(direct, number):
            pass

        @pytest.mark.parametrize("direct", [7, 8])
        def test_mixed_rev(number, direct):
            pass
    """,
    "transitive_parametrized_dependency": """
        import pytest

        @pytest.fixture(params=[1, 2])
        def number(request):
            return request.param

        @pytest.fixture(params=["x", "y"])
        def outer(request, number):
            return request.param

        def test_chain(outer):
            pass
    """,
    "autouse_parametrized_fixture_leads": """
        import pytest

        @pytest.fixture(params=["p", "q"], autouse=True)
        def auto(request):
            return request.param

        def test_never_asks():
            pass
    """,
    "direct_parametrize_shadows_the_fixture": """
        import pytest

        @pytest.fixture(params=[1, 2])
        def value(request):
            return request.param

        @pytest.mark.parametrize("value", ["shadow"])
        def test_shadowed(value):
            assert value == "shadow"
    """,
    "conftest_parametrized_fixture": """
        def test_from_conftest(shared):
            pass
    """,
    "duplicate_param_values_get_unique_ids": """
        import pytest

        @pytest.fixture(params=[1, 1])
        def twin(request):
            return request.param

        def test_twin(twin):
            pass
    """,
    "class_method_with_parametrized_fixture": """
        import pytest

        @pytest.fixture(params=["u", "v"])
        def flavour(request):
            return request.param

        class TestBox:
            def test_method(self, flavour):
                pass
    """,
}


@pytest.mark.parametrize("case", sorted(ID_CASES))
def test_fixture_param_ids_match_pytest(case: str, tmp_path: Path) -> None:
    """Differential: the worker's ids vs real pytest's, byte for byte, in order.

    This is the acceptance oracle for corpus `fixtures/parametrized-fixture` and for every
    ordering rule in :func:`fixture_param_dimensions` — component order (closure order,
    scope-sorted, autouse first), fixture-before-direct, and per-call id de-duplication.
    """
    root = tmp_path / case
    _ = write(root / "pytest.ini", "[pytest]\n")
    write(
        root / "conftest.py",
        """
        import pytest

        @pytest.fixture(params=["c1", "c2"])
        def shared(request):
            return request.param
        """,
    )
    target = write(root / f"test_{case}.py", ID_CASES[case])

    expected = pytest_ids(root)
    with isolated_import_state():
        ids, _execution_plans = collect(target, root)
    assert ids == expected


def test_parametrized_fixture_values_reach_the_test(tmp_path: Path) -> None:
    """Ids alone are not enough — each expanded test must receive ITS parameter."""
    target = write(
        tmp_path / "test_values.py",
        """
        import pytest

        seen = []

        @pytest.fixture(params=[1, 2])
        def number(request):
            return request.param

        def test_number(number):
            seen.append(number)
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        assert [status for _id, status in run_all(plans)] == ["passed", "passed"]
        seen = getattr(module, "seen")
    assert seen == [1, 2]


def test_a_module_scoped_parametrized_fixture_is_rebuilt_per_parameter(tmp_path: Path) -> None:
    """`FixtureDef.execute`'s cache-key branch: a different ``request.param`` finalises first."""
    target = write(
        tmp_path / "test_reparam.py",
        """
        import pytest

        events = []

        @pytest.fixture(scope="module", params=["a", "b"])
        def flavour(request):
            events.append(f"setup:{request.param}")
            yield request.param
            events.append(f"teardown:{request.param}")

        def test_x(flavour):
            pass
        """,
    )
    with isolated_import_state():
        module, registry = load(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        runner = FixtureRunner()
        for plan in plans:
            _ = runner.setup(plan)
            runner.teardown("function")
        runner.teardown_all()
        events = getattr(module, "events")
    assert events == ["setup:a", "teardown:a", "setup:b", "teardown:b"]


def test_fixture_param_dimensions_skips_names_the_test_parametrizes() -> None:
    """Unit view of the "give the test precedence" branch of `pytest_generate_tests`.

    The closure is deliberately built **without** ``ignore_args`` so the fixturedef IS
    present: `_collect_function` always ignores the same names it passes here, so the
    branch is otherwise shadowed by the ``not defs`` short-circuit and would be untestable
    (and unkillable) through the collection path.
    """
    registry = _registry(_def("value", params=(("1", 1), ("2", 2))))
    closure = build_closure(registry, ["value"])
    assert fixture_param_dimensions(closure, frozenset()) == [("value", (("1", 1), ("2", 2)))]
    assert fixture_param_dimensions(closure, frozenset({"value"})) == []


def test_fixture_param_dimensions_walks_overrides_nearest_first() -> None:
    """pytest #1953: an override requesting its super fixture keeps looking for params.

    Two rows in one: the override's OWN params win when it has them (``reversed`` starts at
    the nearest), and the super fixture's params are reached when it does not.
    """
    both = FixtureRegistry()
    both.register(_def("value", params=(("far", "far"),), baseid=""))
    both.register(_def("value", ("value",), params=(("near", "near"),), baseid="test_x.py"))
    assert fixture_param_dimensions(build_closure(both, ["value"]), frozenset()) == [
        ("value", (("near", "near"),))
    ]

    registry = FixtureRegistry()
    registry.register(_def("value", params=(("p", "p"),), baseid=""))
    registry.register(_def("value", argnames=("value",), baseid="test_x.py"))
    closure = build_closure(registry, ["value"])
    assert fixture_param_dimensions(closure, frozenset()) == [("value", (("p", "p"),))]


def test_an_override_that_ignores_its_super_fixture_stops_the_walk() -> None:
    """The ``if argname not in fixturedef.argnames: break`` branch — no params inherited."""
    registry = FixtureRegistry()
    registry.register(_def("value", params=(("p", "p"),), baseid=""))
    registry.register(_def("value", baseid="test_x.py"))
    closure = build_closure(registry, ["value"])
    assert fixture_param_dimensions(closure, frozenset()) == []


def test_build_registry_orders_builtins_conftests_then_module(tmp_path: Path) -> None:
    """The single ordering rule the whole shadowing story rests on."""
    write(
        tmp_path / "conftest.py",
        """
        import pytest

        @pytest.fixture
        def tmp_path():
            return "conftest-wins-over-builtin"
        """,
    )
    target = write(tmp_path / "test_shadow.py", "def test_x(tmp_path):\n    pass\n")
    with isolated_import_state():
        _module, registry = build_registry(target, tmp_path)
        defs = registry.getfixturedefs("tmp_path")
        assert defs is not None
        # Builtin first (baseid "" = total visibility), rootdir conftest second and
        # therefore nearest -- `defs[-1]` is what the closure resolves.  Both carry
        # baseid "" because pytest normalises a rootdir conftest's "." to "" itself
        # (`pytest_plugin_registered` l. 1597-1598).
        assert len(defs) == 2
        assert [d.baseid for d in defs] == ["", ""]
        assert defs[-1].func() == "conftest-wins-over-builtin"
        assert defs[0].func is not defs[-1].func


# ---------------------------------------------------------------------------
# the per-worker conftest cache (Phase 4c) -- what it shares and what it must not
# ---------------------------------------------------------------------------
#
# `conftest_fixturedefs` hands the *same* `FixtureDef` objects to every file under a given
# conftest, which is what gives session scope a worker lifetime rather than a file one
# (`FixtureRunner._cache` keys on identity).  Sharing the wrong objects would be a silent
# visibility bug -- a subdirectory's fixture leaking upward, or a parent's registry served to
# a chain that is not its own -- so each direction gets its own pin.


def _nested_tree(root: Path) -> tuple[Path, Path]:
    """A rootdir conftest plus a subdirectory one, and a test file beside each."""
    write(
        root / "conftest.py",
        """
        import pytest

        @pytest.fixture(scope="session")
        def shared():
            return "from-root"
        """,
    )
    write(
        root / "sub" / "conftest.py",
        """
        import pytest

        @pytest.fixture(scope="session")
        def extra():
            return "from-sub"

        @pytest.fixture(scope="session")
        def shared():
            return "sub-override"
        """,
    )
    top = write(root / "test_top.py", "def test_top(shared):\n    pass\n")
    deep = write(root / "sub" / "test_deep.py", "def test_deep(shared, extra):\n    pass\n")
    return top, deep


def test_a_deeper_conftest_chain_does_not_reuse_the_shallower_registry(tmp_path: Path) -> None:
    """Chain-mismatch safety, **parent first**: the subdirectory still gets its own conftest."""
    top, deep = _nested_tree(tmp_path)
    with isolated_import_state():
        _module, shallow = build_registry(top, tmp_path)
        _module, deeper = build_registry(deep, tmp_path)

        assert shallow.getfixturedefs("extra") is None
        deep_extra = deeper.getfixturedefs("extra")
        assert deep_extra is not None and deep_extra[-1].func() == "from-sub"
        # Two definitions of `shared` below `sub/`, nearest last -- the override, not the
        # parent's value the shallower registry was built from.
        deep_shared = deeper.getfixturedefs("shared")
        assert deep_shared is not None
        assert [d.func() for d in deep_shared] == ["from-root", "sub-override"]


def test_a_shallower_conftest_chain_does_not_inherit_the_deeper_registry(tmp_path: Path) -> None:
    """Chain-mismatch safety, **subdirectory first** -- the direction a cache gets wrong.

    Building the deep chain first populates the cache with ``sub/conftest.py``'s block.  The
    parent's chain must compose only the rootdir block, or a fixture defined in a
    subdirectory would be visible to a file above it -- which pytest's ``baseid`` filter
    forbids and which would make a suite pass under rustest and fail under pytest.
    """
    top, deep = _nested_tree(tmp_path)
    with isolated_import_state():
        _module, deeper = build_registry(deep, tmp_path)
        _module, shallow = build_registry(top, tmp_path)

        assert deeper.getfixturedefs("extra") is not None
        assert shallow.getfixturedefs("extra") is None
        top_shared = shallow.getfixturedefs("shared")
        assert top_shared is not None
        assert [d.func() for d in top_shared] == ["from-root"]


def test_a_rootdir_conftest_fixture_is_one_object_for_every_chain_below_it(
    tmp_path: Path,
) -> None:
    """The reason the cache is keyed per conftest and not per chain.

    A session fixture in the rootdir conftest is **one** instance for the whole session under
    pytest, whichever directory the test that asks for it lives in.  Because the value cache
    keys on the ``FixtureDef`` object, reproducing that means the rootdir block being the same
    objects in both chains -- which per-chain keying would not have given.
    """
    top, deep = _nested_tree(tmp_path)
    with isolated_import_state():
        _module, shallow = build_registry(top, tmp_path)
        _module, deeper = build_registry(deep, tmp_path)

        shallow_shared = shallow.getfixturedefs("shared")
        deep_shared = deeper.getfixturedefs("shared")
        assert shallow_shared is not None and deep_shared is not None
        assert shallow_shared[0] is deep_shared[0]
        # ...and the subdirectory's override is emphatically *not* the parent's object.
        assert deep_shared[-1] is not deep_shared[0]


def test_two_files_in_one_directory_share_their_conftest_fixturedefs(tmp_path: Path) -> None:
    """The fix itself, at the level it is implemented: same object, hence same cache key."""
    write(
        tmp_path / "conftest.py",
        """
        import pytest

        @pytest.fixture(scope="session")
        def resource():
            return object()
        """,
    )
    first = write(tmp_path / "test_a.py", "def test_a(resource):\n    pass\n")
    second = write(tmp_path / "test_b.py", "def test_b(resource):\n    pass\n")
    with isolated_import_state():
        _module, one = build_registry(first, tmp_path)
        _module, two = build_registry(second, tmp_path)
        first_defs = one.getfixturedefs("resource")
        second_defs = two.getfixturedefs("resource")
        assert first_defs is not None and second_defs is not None
        assert first_defs[-1] is second_defs[-1]


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Two spellings of one path are two files on a case-sensitive filesystem.",
)
def test_a_conftest_reached_under_two_path_spellings_is_still_one_block(tmp_path: Path) -> None:
    """The two caches must agree about identity, and on Windows they nearly did not.

    ``_conftest_modules`` keys on ``Path``, whose hash is case-**in**sensitive on Windows
    (``PurePath._str_normcase``); ``_content_key`` keyed on ``as_posix()``, which preserves
    case.  One conftest reached under two spellings therefore produced **one module** and
    **two** ``FixtureDef`` blocks -- and since the value cache keys on ``FixtureDef``
    identity, that is the per-file duplication the Phase 4c cache exists to remove, quietly
    resurrected: a session fixture ran its setup twice where pytest runs it once.

    The realistic vector is not a typo, it is **drive-letter case**: ``c:\\repo`` from one
    tool and ``C:\\repo`` from another are both ordinary, and nothing normalises between
    them.  Hence swapping exactly that here, and hence ``os.path.normcase`` in
    ``_content_key``.
    """
    write(
        tmp_path / "conftest.py",
        """
        import pytest

        @pytest.fixture(scope="session")
        def resource():
            return object()
        """,
    )
    first = write(tmp_path / "test_a.py", "def test_a(resource):\n    pass\n")

    # Same file, other drive-letter case. `Path` compares these equal; `as_posix()` does not.
    native = str(tmp_path)
    swapped_root = Path(native[0].swapcase() + native[1:])
    assert str(swapped_root) != native, "drive letter did not change; the probe proves nothing"
    second = Path(str(first)[0].swapcase() + str(first)[1:])

    with isolated_import_state():
        _module, one = build_registry(first, tmp_path)
        _module, two = build_registry(second, swapped_root)
        first_defs = one.getfixturedefs("resource")
        second_defs = two.getfixturedefs("resource")
        assert first_defs is not None and second_defs is not None
        assert first_defs[-1] is second_defs[-1]


def test_a_files_own_fixtures_do_not_reach_a_sibling_sharing_the_conftest(
    tmp_path: Path,
) -> None:
    """The container is per file even though its contents are shared.

    ``conftest_registry`` returns a fresh :class:`FixtureRegistry` every call, so the module
    fixtures ``build_registry`` registers on top of it cannot leak sideways.  Without that a
    second file would see the first file's module-level fixtures and a name-not-found error
    would turn into a silently wrong value.
    """
    write(
        tmp_path / "conftest.py",
        """
        import pytest

        @pytest.fixture(scope="session")
        def resource():
            return "shared"
        """,
    )
    first = write(
        tmp_path / "test_a.py",
        """
        import pytest

        @pytest.fixture
        def local_to_a():
            return 1

        def test_a(resource, local_to_a):
            pass
        """,
    )
    second = write(tmp_path / "test_b.py", "def test_b(resource):\n    pass\n")
    with isolated_import_state():
        _module, one = build_registry(first, tmp_path)
        _module, two = build_registry(second, tmp_path)
        assert one.getfixturedefs("local_to_a") is not None
        assert two.getfixturedefs("local_to_a") is None


def test_an_edited_conftest_is_not_served_from_the_cache(tmp_path: Path) -> None:
    """Byte-identical or rebuild -- the conservative half of the cache key.

    The key carries the conftest's ``sha256``, so rewriting the file between two
    ``build_registry`` calls produces a *different* key and therefore fresh ``FixtureDef``
    objects.  Nothing in a run edits a conftest, which is the point: the cache must not be the
    thing that decides what happens if something does.
    """
    conftest = tmp_path / "conftest.py"
    write(
        conftest,
        """
        import pytest

        @pytest.fixture(scope="session")
        def resource():
            return "before"
        """,
    )
    first = write(tmp_path / "test_a.py", "def test_a(resource):\n    pass\n")
    second = write(tmp_path / "test_b.py", "def test_b(resource):\n    pass\n")
    with isolated_import_state():
        _module, one = build_registry(first, tmp_path)
        write(
            conftest,
            """
            import pytest

            @pytest.fixture(scope="session")
            def resource():
                return "after"
            """,
        )
        _module, two = build_registry(second, tmp_path)
        first_defs = one.getfixturedefs("resource")
        second_defs = two.getfixturedefs("resource")
        assert first_defs is not None and second_defs is not None
        assert first_defs[-1] is not second_defs[-1]
        # The *module* is still the one already imported -- `import_conftest` caches by path
        # and pytest's `_importconftest` does too -- so only the objects are new, not the
        # code.  Stated so the next reader does not mistake this for hot reloading.
        assert first_defs[-1].func() == "before"
        assert second_defs[-1].func() == "before"
