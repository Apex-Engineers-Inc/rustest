"""Tests for the v2 collection worker (`rustest._v2_worker`).

Two correctness cores are under test:

1. **Module identity** — a test file is imported under its REAL dotted name, so
   `sys.modules` holds the same object a test's own `import conftest` reaches
   (issue #130).  The regression test is the corpus `collection/conftest-visibility`
   shape with shared state added: a conftest fixture appends to a module-level list
   and the test module reads that list back through `import conftest`.
2. **Enumeration** — pytest's collection rules, ported and cited.  The table tests
   mirror the `conformance/corpus/collection/*` cases exactly, plus the shapes probed
   directly from pytest 8.4.2 (inheritance ordering, unittest sorting, `__new__`
   refusal, staticmethod/classmethod).

Wire-format tests pin the emitted JSON against the **byte-identical golden strings**
in `src/v2/protocol.rs` / `src/v2/manifest.rs`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Any

import pytest

from rustest._v2_worker import (
    DEFAULT_NAMING,
    PROTOCOL_VERSION,
    CollectionRefusal,
    Naming,
    collect_file,
    encode_response,
    enumerate_module,
    handle_init,
    handle_shutdown,
    import_test_module,
    install_pytest_shim,
    resolve_module_identity,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@contextmanager
def isolated_import_state() -> Iterator[None]:
    """Undo every `sys.path` / `sys.modules` mutation an import makes.

    The worker mutates both by design (that IS the module-identity fix), so a test
    that imports must not leak the mutation into the next one — or into the pytest
    process running the suite.
    """
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for name in set(sys.modules) - set(saved_modules):
            del sys.modules[name]
        sys.modules.update(saved_modules)


def write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def collect_source(
    tmp_path: Path,
    name: str,
    source: str,
    naming: Naming = DEFAULT_NAMING,
) -> list[dict[str, Any]]:
    """Write *source* to *name* under *tmp_path*, import it, enumerate it."""
    path = write(tmp_path / name, source)
    with isolated_import_state():
        module = import_test_module(path, tmp_path)
        return enumerate_module(module, path, tmp_path, naming)


def ids_of(entries: list[dict[str, Any]]) -> list[str]:
    return [str(entry["id"]) for entry in entries]


# ---------------------------------------------------------------------------
# module identity
# ---------------------------------------------------------------------------


def test_package_file_gets_its_real_dotted_name(tmp_path: Path) -> None:
    write(tmp_path / "pkg" / "__init__.py", "")
    write(tmp_path / "pkg" / "sub" / "__init__.py", "")
    path = write(tmp_path / "pkg" / "sub" / "test_deep.py", "def test_x(): pass\n")

    module_name, package_root = resolve_module_identity(path, tmp_path)

    assert module_name == "pkg.sub.test_deep"
    assert package_root == str(tmp_path)


def test_non_package_file_gets_its_bare_stem(tmp_path: Path) -> None:
    path = write(tmp_path / "loose" / "test_a.py", "def test_x(): pass\n")

    module_name, package_root = resolve_module_identity(path, tmp_path)

    assert module_name == "test_a"
    assert package_root is None


def test_conftest_in_a_non_package_dir_is_named_conftest(tmp_path: Path) -> None:
    path = write(tmp_path / "conftest.py", "value = 1\n")

    module_name, package_root = resolve_module_identity(path, tmp_path)

    assert module_name == "conftest"
    assert package_root is None


def test_package_walk_stops_at_a_non_identifier_directory(tmp_path: Path) -> None:
    """`resolve_package_path` breaks on a dir whose name is not an identifier.

    Source: `_pytest/pathlib.py::resolve_package_path` — `if not parent.name.isidentifier(): break`.
    """
    write(tmp_path / "my-tests" / "__init__.py", "")
    path = write(tmp_path / "my-tests" / "test_dashed.py", "def test_x(): pass\n")

    module_name, package_root = resolve_module_identity(path, tmp_path)

    assert module_name == "test_dashed"
    assert package_root is None


def test_package_init_file_names_the_package_itself(tmp_path: Path) -> None:
    write(tmp_path / "pkg" / "__init__.py", "")
    path = tmp_path / "pkg" / "__init__.py"

    module_name, package_root = resolve_module_identity(path, tmp_path)

    assert module_name == "pkg"
    assert package_root == str(tmp_path)


def test_module_identity_ignores_rootdir(tmp_path: Path) -> None:
    """Identity is anchored on the `__init__.py` chain, never on rootdir.

    pytest's default importmode (`prepend`) resolves the name with
    `_pytest/pathlib.py::resolve_pkg_root_and_module_name`, which never looks at
    `root`; only `ImportMode.importlib` anchors on rootdir
    (`_pytest/pathlib.py::import_path`, the `module_name_from_path(path, root)` branch).
    Probed: running pytest with rootdir=`pkg/` still reports `pkg.sub.test_deep`.
    """
    write(tmp_path / "pkg" / "__init__.py", "")
    write(tmp_path / "pkg" / "sub" / "__init__.py", "")
    path = write(tmp_path / "pkg" / "sub" / "test_deep.py", "def test_x(): pass\n")

    assert resolve_module_identity(path, tmp_path) == resolve_module_identity(
        path, tmp_path / "pkg" / "sub"
    )


def test_import_registers_the_real_name_in_sys_modules(tmp_path: Path) -> None:
    write(tmp_path / "pkg" / "__init__.py", "")
    path = write(tmp_path / "pkg" / "test_named.py", "VALUE = 7\n")

    with isolated_import_state():
        module = import_test_module(path, tmp_path)

        assert module.__name__ == "pkg.test_named"
        assert sys.modules["pkg.test_named"] is module
        assert str(tmp_path) in sys.path


def test_conftest_shared_state_is_the_same_object(tmp_path: Path) -> None:
    """The #130 regression, in the corpus `conftest-visibility` shape.

    A fixture in `conftest.py` appends to a module-level list; the test module does
    `import conftest` and reads it.  If the worker imported `conftest.py` under a
    synthetic name (or by exec'ing it into a fresh namespace), the test module's
    `import conftest` would create a SECOND, empty module object and the appended
    value would be invisible.
    """
    conftest = write(
        tmp_path / "conftest.py",
        """
        import rustest

        SEEN = []

        @rustest.fixture
        def shared_value():
            SEEN.append(42)
            return 42
        """,
    )
    test_path = write(
        tmp_path / "test_uses_fixture.py",
        """
        import conftest

        def test_conftest_fixture(shared_value):
            assert shared_value == 42
        """,
    )

    with isolated_import_state():
        conftest_module = import_test_module(conftest, tmp_path)
        # Stand in for the fixture actually running (execution is 1b.2).
        _ = conftest_module.shared_value()

        test_module = import_test_module(test_path, tmp_path)

        assert test_module.conftest is conftest_module
        assert test_module.conftest.SEEN == [42]
        assert test_module.conftest.SEEN is conftest_module.SEEN


def test_same_stem_in_two_non_package_dirs_is_first_wins_with_an_error(
    tmp_path: Path,
) -> None:
    """Probed against pytest 8.4.2: the second file is a collection ERROR.

    pytest prints `import file mismatch: ...` and exits 2 (`Interrupted: 1 error
    during collection`), keeping the first module.  Source:
    `_pytest/python.py::importtestmodule` (the `ImportPathMismatchError` branch).
    """
    first = write(tmp_path / "a" / "test_dup.py", "def test_a(): pass\n")
    second = write(tmp_path / "b" / "test_dup.py", "def test_b(): pass\n")

    with isolated_import_state():
        module = import_test_module(first, tmp_path)
        assert module.__name__ == "test_dup"

        with pytest.raises(CollectionRefusal) as excinfo:
            _ = import_test_module(second, tmp_path)

    message = str(excinfo.value)
    assert message.startswith("import file mismatch:")
    assert "imported module 'test_dup' has this __file__ attribute:" in message
    assert "HINT: remove __pycache__ / .pyc files" in message
    # First-wins: the already-imported module is untouched.
    assert str(first) in message
    assert str(second) in message


def test_reimporting_the_same_file_returns_the_same_module(tmp_path: Path) -> None:
    path = write(tmp_path / "test_stable.py", "VALUE = 1\n")

    with isolated_import_state():
        first = import_test_module(path, tmp_path)
        second = import_test_module(path, tmp_path)

        assert first is second


# ---------------------------------------------------------------------------
# enumeration — corpus collection cases
# ---------------------------------------------------------------------------


def test_corpus_naming_testfoo(tmp_path: Path) -> None:
    """`python_functions = ["test"]` is a PREFIX test, so `testfoo` collects.

    Corpus: `collection/naming-testfoo`.
    Source: `_pytest/python.py::PyCollector._matches_prefix_or_glob_option`.
    """
    entries = collect_source(
        tmp_path,
        "test_naming.py",
        """
        def test_proper():
            assert True

        def testfoo():
            assert True
        """,
    )

    assert ids_of(entries) == [
        "test_naming.py::test_proper",
        "test_naming.py::testfoo",
    ]


def test_corpus_naming_underscore(tmp_path: Path) -> None:
    """Corpus: `collection/naming-underscore` — `_test_hidden` is not a prefix match."""
    entries = collect_source(
        tmp_path,
        "test_underscore.py",
        """
        def _test_hidden():
            raise AssertionError("must not be collected")

        def test_visible():
            assert True
        """,
    )

    assert ids_of(entries) == ["test_underscore.py::test_visible"]


def test_corpus_nested_function_is_invisible(tmp_path: Path) -> None:
    """Corpus: `collection/nested-function` — only the module `__dict__` is walked.

    Source: `_pytest/python.py::PyCollector.collect` — `dicts = [getattr(self.obj, "__dict__", {})]`.
    """
    entries = collect_source(
        tmp_path,
        "test_nested.py",
        """
        def test_outer():
            def test_inner():
                raise AssertionError("must not run")

            assert True
        """,
    )

    assert ids_of(entries) == ["test_nested.py::test_outer"]


def test_corpus_empty_suite_collects_nothing(tmp_path: Path) -> None:
    """Corpus: `collection/empty-suite`."""
    entries = collect_source(tmp_path, "test_nothing_collected.py", "def helper(): pass\n")

    assert entries == []


def test_corpus_class_collection(tmp_path: Path) -> None:
    """Corpus: `collection/class-collection`.

    `Helper` fails `python_classes`; `TestWithInit` is refused because it has an
    `__init__` (`_pytest/python.py::Class.collect` -> PytestCollectionWarning).
    The refusal is a WARNING in pytest, not an error: pytest still exits 0, so the
    worker emits no error entry (see the module docstring's warning-channel note).
    """
    entries = collect_source(
        tmp_path,
        "test_classes.py",
        """
        class TestBox:
            def test_method(self):
                assert True


        class Helper:
            def test_ignored(self):
                raise AssertionError("must not run")


        class TestWithInit:
            def __init__(self):
                pass

            def test_ignored(self):
                raise AssertionError("must not run")
        """,
    )

    assert ids_of(entries) == ["test_classes.py::TestBox::test_method"]
    assert entries[0]["qualname"] == "TestBox.test_method"
    assert entries[0]["class_name"] == "TestBox"


def test_class_with_new_constructor_is_refused(tmp_path: Path) -> None:
    """Probed: pytest warns `cannot collect test class 'TestNew' because it has a
    __new__ constructor`.  Source: `_pytest/python.py::Class.collect` + `hasnew`."""
    entries = collect_source(
        tmp_path,
        "test_newctor.py",
        """
        class TestNew:
            def __new__(cls):
                return super().__new__(cls)

            def test_ignored(self):
                pass

        class TestOk:
            def test_ok(self):
                pass
        """,
    )

    assert ids_of(entries) == ["test_newctor.py::TestOk::test_ok"]


def test_corpus_unittest_basic(tmp_path: Path) -> None:
    """Corpus: `collection/unittest-basic` — names come from `TestLoader.getTestCaseNames`.

    Source: `_pytest/unittest.py::UnitTestCase.collect`.
    """
    entries = collect_source(
        tmp_path,
        "test_unittest.py",
        """
        import unittest


        class TestLegacy(unittest.TestCase):
            def test_addition(self):
                self.assertEqual(1 + 1, 2)

            def test_failure(self):
                self.assertEqual(1, 2)
        """,
    )

    assert ids_of(entries) == [
        "test_unittest.py::TestLegacy::test_addition",
        "test_unittest.py::TestLegacy::test_failure",
    ]
    # `UnitTestCase.nofuncargs = True`: unittest items take no fixtures, so the
    # optional field is omitted entirely.
    assert "fixtures" not in entries[0]


def test_unittest_case_ignores_python_classes_and_sorts_methods(tmp_path: Path) -> None:
    """Two probed unittest-only rules.

    * The unittest hook has **no name filter**: `_pytest/unittest.py::pytest_pycollect_makeitem`
      only checks `issubclass(obj, unittest.TestCase)`, so `Legacy` collects even
      though it fails `python_classes`.
    * `TestLoader.getTestCaseNames` **sorts** (`sortTestMethodsUsing`), so methods come
      out alphabetically — unlike a plain class, which keeps definition order.
    """
    entries = collect_source(
        tmp_path,
        "test_ut_order.py",
        """
        import unittest


        class Legacy(unittest.TestCase):
            def test_zeta(self):
                pass

            def test_alpha(self):
                pass

            def helper(self):
                pass


        class TestPlain:
            def test_zulu(self):
                pass

            def test_bravo(self):
                pass
        """,
    )

    assert ids_of(entries) == [
        "test_ut_order.py::Legacy::test_alpha",
        "test_ut_order.py::Legacy::test_zeta",
        "test_ut_order.py::TestPlain::test_zulu",
        "test_ut_order.py::TestPlain::test_bravo",
    ]


def test_inherited_methods_are_collected_base_first(tmp_path: Path) -> None:
    """Probed against pytest 8.4.2.

    Source: `_pytest/python.py::PyCollector.collect` — the MRO `__dict__` walk with
    `seen` de-duplication, emitted in **reverse** group order so base-class methods
    precede subclass ones.  An override is attributed to the derived class's dict
    (first seen), so `TestChild::test_shared` sorts with `test_own`, not with the base.
    """
    entries = collect_source(
        tmp_path,
        "test_inherit.py",
        """
        class TestA:
            def test_base(self):
                pass


        class TestB(TestA):
            def test_derived(self):
                pass


        class TestC(TestA):
            pass


        class TestOverride(TestA):
            def test_base(self):
                pass

            def test_own(self):
                pass
        """,
    )

    assert ids_of(entries) == [
        "test_inherit.py::TestA::test_base",
        "test_inherit.py::TestB::test_base",
        "test_inherit.py::TestB::test_derived",
        "test_inherit.py::TestC::test_base",
        "test_inherit.py::TestOverride::test_base",
        "test_inherit.py::TestOverride::test_own",
    ]


def test_static_and_class_methods_are_collected(tmp_path: Path) -> None:
    """Source: `_pytest/python.py::PyCollector.istestfunction` unwraps `__func__`."""
    entries = collect_source(
        tmp_path,
        "test_static.py",
        """
        class TestStatic:
            @staticmethod
            def test_static():
                pass

            @classmethod
            def test_class(cls):
                pass
        """,
    )

    assert ids_of(entries) == [
        "test_static.py::TestStatic::test_static",
        "test_static.py::TestStatic::test_class",
    ]


def test_nested_classes_nest_in_the_nodeid(tmp_path: Path) -> None:
    entries = collect_source(
        tmp_path,
        "test_nestcls.py",
        """
        class TestBox:
            class TestInner:
                def test_m(self):
                    pass

            def test_outer(self):
                pass
        """,
    )

    assert ids_of(entries) == [
        "test_nestcls.py::TestBox::TestInner::test_m",
        "test_nestcls.py::TestBox::test_outer",
    ]
    assert entries[0]["qualname"] == "TestBox.TestInner.test_m"
    assert entries[0]["class_name"] == "TestBox.TestInner"


def test_imported_test_functions_are_collected(tmp_path: Path) -> None:
    """pytest 8.4's `collect_imported_tests` defaults to **True**.

    Source: `_pytest/main.py::pytest_addoption` — `addini("collect_imported_tests",
    type="bool", default=True)`; the `__module__` filter in
    `_pytest/python.py::PyCollector.collect` only runs when that ini is False.
    Probed: `from helpers import test_shared` collects as `test_imports.py::test_shared`.
    """
    write(tmp_path / "helpers.py", "def test_shared(): pass\n")
    entries = collect_source(
        tmp_path,
        "test_imports.py",
        """
        from helpers import test_shared

        def test_local():
            pass
        """,
    )

    assert ids_of(entries) == [
        "test_imports.py::test_shared",
        "test_imports.py::test_local",
    ]


def test_fixtures_are_not_collected_as_tests(tmp_path: Path) -> None:
    """Source: `_pytest/python.py::PyCollector.istestfunction` — a function carrying a
    fixture marker is excluded even when its name matches."""
    entries = collect_source(
        tmp_path,
        "test_fixturenames.py",
        """
        import rustest

        @rustest.fixture
        def test_looking_fixture():
            return 1

        def test_real():
            pass
        """,
    )

    assert ids_of(entries) == ["test_fixturenames.py::test_real"]


def test_dunder_test_false_hides_module_class_and_function(tmp_path: Path) -> None:
    """Source: `_pytest/python.py::PyCollector.collect` (`self.obj.__test__`),
    `Class.collect`, and `pytest_pycollect_makeitem` (`getattr(obj, "__test__", True)`)."""
    module_off = collect_source(
        tmp_path, "test_moduleoff.py", "__test__ = False\n\ndef test_x(): pass\n"
    )
    assert module_off == []

    entries = collect_source(
        tmp_path,
        "test_partsoff.py",
        """
        class TestOff:
            __test__ = False

            def test_hidden(self):
                pass

        def test_hidden_func():
            pass

        test_hidden_func.__test__ = False

        def test_kept():
            pass
        """,
    )
    assert ids_of(entries) == ["test_partsoff.py::test_kept"]


def test_non_function_callable_is_not_collected(tmp_path: Path) -> None:
    """Source: `_pytest/python.py::pytest_pycollect_makeitem` — a matching name that is
    not a function warns and returns None."""
    entries = collect_source(
        tmp_path,
        "test_callable.py",
        """
        class _Callable:
            def __call__(self):
                pass

        test_instance = _Callable()

        test_value = 3

        def test_real():
            pass
        """,
    )

    assert ids_of(entries) == ["test_callable.py::test_real"]


def test_generator_test_function_refuses_the_file(tmp_path: Path) -> None:
    """Source: `_pytest/python.py::pytest_pycollect_makeitem` —
    `fail("'yield' keyword is allowed in fixtures, but not in tests (...)")`,
    which aborts collection of the whole module."""
    path = write(tmp_path / "test_gen.py", "def test_gen():\n    yield 1\n")

    with isolated_import_state():
        module = import_test_module(path, tmp_path)
        with pytest.raises(CollectionRefusal) as excinfo:
            _ = enumerate_module(module, path, tmp_path, DEFAULT_NAMING)

    assert "yield" in str(excinfo.value)


def test_naming_patterns_are_honoured(tmp_path: Path) -> None:
    """Glob patterns take the `fnmatch` branch; prefixes take `startswith`."""
    naming = Naming(
        python_files=("check_*.py",),
        python_classes=("Suite*",),
        python_functions=("check", "*_case"),
    )
    entries = collect_source(
        tmp_path,
        "check_things.py",
        """
        class SuiteOne:
            def check_method(self):
                pass

            def scenario_case(self):
                pass

        class TestOld:
            def check_ignored(self):
                pass

        def check_top():
            pass
        """,
        naming=naming,
    )

    assert ids_of(entries) == [
        "check_things.py::SuiteOne::check_method",
        "check_things.py::SuiteOne::scenario_case",
        "check_things.py::check_top",
    ]


def test_ids_are_rootdir_relative_posix(tmp_path: Path) -> None:
    path = write(tmp_path / "nested" / "dir" / "test_deep.py", "def test_x(): pass\n")

    with isolated_import_state():
        module = import_test_module(path, tmp_path)
        entries = enumerate_module(module, path, tmp_path, DEFAULT_NAMING)

    assert ids_of(entries) == ["nested/dir/test_deep.py::test_x"]
    assert entries[0]["path"] == "nested/dir/test_deep.py"


# ---------------------------------------------------------------------------
# enumeration — parametrize and marks
# ---------------------------------------------------------------------------


def test_parametrize_expands_with_pytest_ids(tmp_path: Path) -> None:
    """Corpus: `parametrize/basic-ids`, `explicit-ids`, `stacking`.

    ID formatting is v1's, consumed verbatim from `__rustest_parametrization__`
    (`python/rustest/decorators.py::parametrize` -> `_build_cases` -> `_resolve_case_id`).
    """
    entries = collect_source(
        tmp_path,
        "test_params.py",
        """
        from rustest import parametrize

        @parametrize("value", [1, 2, 3])
        def test_value(value):
            pass

        @parametrize("value", [1, 2], ids=["one", "two"])
        def test_named(value):
            pass

        @parametrize("a", [1, 2])
        @parametrize("b", ["x", "y"])
        def test_grid(a, b):
            pass
        """,
    )

    assert ids_of(entries) == [
        "test_params.py::test_value[1]",
        "test_params.py::test_value[2]",
        "test_params.py::test_value[3]",
        "test_params.py::test_named[one]",
        "test_params.py::test_named[two]",
        "test_params.py::test_grid[x-1]",
        "test_params.py::test_grid[x-2]",
        "test_params.py::test_grid[y-1]",
        "test_params.py::test_grid[y-2]",
    ]
    assert entries[0]["param_id"] == "1"
    assert entries[0]["qualname"] == "test_value"


def test_empty_string_param_id_keeps_its_brackets(tmp_path: Path) -> None:
    """`Some("")` is reachable and distinct from "no param" —
    see `src/v2/nodeid.rs` module docs (`_pytest/python.py::PyCollector._genfunctions`
    guards on `callspec._idlist`, not on the joined id)."""
    entries = collect_source(
        tmp_path,
        "test_emptyparam.py",
        """
        from rustest import parametrize

        @parametrize("s", ["", "a"])
        def test_strings(s):
            pass
        """,
    )

    assert ids_of(entries) == [
        "test_emptyparam.py::test_strings[]",
        "test_emptyparam.py::test_strings[a]",
    ]
    assert entries[0]["param_id"] == ""


def test_duplicate_param_ids_get_pytest_uniqueness_suffixes(tmp_path: Path) -> None:
    """Source: `_pytest/python.py::IdMaker.make_unique_parameterset_ids` — duplicates
    get `_0`/`_1` suffixes (an underscore only when the id already ends in a digit).
    Probed: `[1, 1, 2]` -> `1_0`, `1_1`, `2`; `["a", "a"]` -> `a0`, `a1`.

    Without this a file would emit two CollectedTests with the SAME nodeid, which
    breaks the manifest's addressability contract.
    """
    entries = collect_source(
        tmp_path,
        "test_dupes.py",
        """
        from rustest import parametrize

        @parametrize("v", [1, 1, 2])
        def test_digits(v):
            pass

        @parametrize("v", ["a", "a"])
        def test_letters(v):
            pass
        """,
    )

    assert ids_of(entries) == [
        "test_dupes.py::test_digits[1_0]",
        "test_dupes.py::test_digits[1_1]",
        "test_dupes.py::test_digits[2]",
        "test_dupes.py::test_letters[a0]",
        "test_dupes.py::test_letters[a1]",
    ]


def test_parametrized_methods_carry_the_class_chain(tmp_path: Path) -> None:
    entries = collect_source(
        tmp_path,
        "test_clsparam.py",
        """
        from rustest import parametrize

        class TestBox:
            @parametrize("x", [1])
            def test_method(self, x):
                pass
        """,
    )

    assert ids_of(entries) == ["test_clsparam.py::TestBox::test_method[1]"]
    assert entries[0]["qualname"] == "TestBox.test_method"
    assert entries[0]["param_id"] == "1"


def test_marks_are_carried_as_mark_specs(tmp_path: Path) -> None:
    """Marks are data, never evaluated here (evaluation is 1b.2)."""
    entries = collect_source(
        tmp_path,
        "test_marks.py",
        """
        from rustest import mark

        @mark.slow
        def test_bare():
            pass

        @mark.skipif(True, reason="needs windows")
        def test_conditional():
            pass

        @mark.skip(reason="later")
        def test_skipped():
            pass
        """,
    )

    by_name = {str(entry["qualname"]): entry for entry in entries}
    assert by_name["test_bare"]["marks"] == [{"name": "slow"}]
    assert by_name["test_conditional"]["marks"] == [
        {"name": "skipif", "args": [True], "kwargs": {"reason": "needs windows"}}
    ]
    assert by_name["test_skipped"]["marks"] == [{"name": "skip", "kwargs": {"reason": "later"}}]


def test_compat_mark_skip_is_carried_as_a_skip_mark(tmp_path: Path) -> None:
    """The compat `pytest.mark.skip` takes a different route than the native one.

    `compat/pytest.py::_PytestMarkCompat.skip` calls `decorators.py::skip_decorator`,
    which sets `__rustest_skip__` instead of appending to `__rustest_marks__`.  Corpus
    files reach this path (they `import pytest`), so the worker must read both.  The
    module is imported by its real dotted name here rather than through the shim, so
    the test process's own `pytest` is untouched.
    """
    entries = collect_source(
        tmp_path,
        "test_compatskip.py",
        """
        from rustest.compat import pytest as compat_pytest

        @compat_pytest.mark.skip(reason="later")
        def test_skipped():
            pass

        @compat_pytest.mark.slow
        def test_marked():
            pass
        """,
    )

    by_name = {str(entry["qualname"]): entry for entry in entries}
    assert by_name["test_skipped"]["marks"] == [{"name": "skip", "kwargs": {"reason": "later"}}]
    assert by_name["test_marked"]["marks"] == [{"name": "slow"}]


def test_non_json_serializable_mark_args_become_reprs(tmp_path: Path) -> None:
    entries = collect_source(
        tmp_path,
        "test_reprmarks.py",
        """
        from rustest import mark

        class Thing:
            def __repr__(self):
                return "<thing>"

        @mark.custom(Thing(), key={1, 2})
        def test_x():
            pass
        """,
    )

    assert entries[0]["marks"] == [
        {"name": "custom", "args": ["<thing>"], "kwargs": {"key": "{1, 2}"}}
    ]


def test_class_marks_are_appended_after_method_marks(tmp_path: Path) -> None:
    """Closest-first, per `_pytest/nodes.py::Node.iter_markers`."""
    entries = collect_source(
        tmp_path,
        "test_clsmarks.py",
        """
        from rustest import mark

        @mark.integration
        class TestBox:
            @mark.slow
            def test_method(self):
                pass
        """,
    )

    assert entries[0]["marks"] == [{"name": "slow"}, {"name": "integration"}]


def test_fixture_parameters_are_listed_in_signature_order(tmp_path: Path) -> None:
    entries = collect_source(
        tmp_path,
        "test_fixtureargs.py",
        """
        from rustest import parametrize

        def test_plain(tmp_path, capsys):
            pass

        @parametrize("value", [1])
        def test_mixed(value, tmp_path):
            pass

        class TestBox:
            def test_method(self, tmp_path):
                pass
        """,
    )

    by_name = {str(entry["qualname"]): entry for entry in entries}
    assert by_name["test_plain"]["fixtures"] == ["tmp_path", "capsys"]
    # Parametrized argnames are supplied by the parametrization, not by a fixture.
    assert by_name["test_mixed"]["fixtures"] == ["tmp_path"]
    # `self` is not a fixture.
    assert by_name["TestBox.test_method"]["fixtures"] == ["tmp_path"]


def test_entries_omit_every_empty_optional_field(tmp_path: Path) -> None:
    """The manifest's omit-when-empty rules (`src/v2/manifest.rs` golden test):
    a plain test carries only `id`, `path`, `qualname`."""
    entries = collect_source(tmp_path, "test_minimal.py", "def test_x(): pass\n")

    assert entries == [
        {
            "id": "test_minimal.py::test_x",
            "path": "test_minimal.py",
            "qualname": "test_x",
        }
    ]


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------


def test_ready_declares_the_version_the_worker_speaks(tmp_path: Path) -> None:
    """`Ready.protocol_version` is a constant, NEVER an echo of Init's value —
    otherwise the handshake could not detect skew (`src/v2/protocol.rs`)."""
    response = handle_init(
        {
            "op": "init",
            "protocol_version": 99,
            "rootdir": tmp_path.as_posix(),
            "python_files": ["test_*.py"],
            "python_classes": ["Test"],
            "python_functions": ["test"],
        }
    )

    assert response == {"op": "ready", "protocol_version": PROTOCOL_VERSION}
    assert PROTOCOL_VERSION == 1


def test_ready_line_matches_the_rust_golden() -> None:
    assert (
        encode_response({"op": "ready", "protocol_version": PROTOCOL_VERSION})
        == '{"op":"ready","protocol_version":1}'
    )


def test_bye_line_matches_the_rust_golden() -> None:
    assert handle_shutdown() == {"op": "bye"}
    assert encode_response(handle_shutdown()) == '{"op":"bye"}'


def test_collected_line_matches_the_rust_golden_and_omits_error() -> None:
    """Byte-for-byte with `COLLECTED_TESTS_LINE` in `src/v2/protocol.rs`."""
    line = encode_response(
        {
            "op": "collected",
            "path": "/repo/tests/test_math.py",
            "tests": [
                {
                    "id": "tests/test_math.py::test_add",
                    "path": "tests/test_math.py",
                    "qualname": "test_add",
                }
            ],
        }
    )

    assert line == (
        '{"op":"collected","path":"/repo/tests/test_math.py","tests":'
        '[{"id":"tests/test_math.py::test_add","path":"tests/test_math.py",'
        '"qualname":"test_add"}]}'
    )
    assert '"error":' not in line


def test_collected_error_line_matches_the_rust_golden_and_omits_tests() -> None:
    """Byte-for-byte with `COLLECTED_ERROR_LINE` in `src/v2/protocol.rs`."""
    line = encode_response(
        {
            "op": "collected",
            "path": "/repo/tests/test_broken.py",
            "error": {
                "path": "tests/test_broken.py",
                "message": "ImportError: No module named 'nope'",
            },
        }
    )

    assert line == (
        '{"op":"collected","path":"/repo/tests/test_broken.py","error":'
        '{"path":"tests/test_broken.py",'
        '"message":"ImportError: No module named \'nope\'"}}'
    )
    assert '"tests":' not in line


def test_encoded_messages_are_single_line() -> None:
    line = encode_response(
        {
            "op": "collected",
            "path": "/repo/t.py",
            "error": {"path": "t.py", "message": "Traceback:\n  File 'x'\nBoom"},
        }
    )

    assert "\n" not in line
    assert "\\n" in line
    decoded: dict[str, Any] = json.loads(line)
    assert "\n" in decoded["error"]["message"]


def test_collect_file_returns_a_collected_response(tmp_path: Path) -> None:
    path = write(tmp_path / "test_wire.py", "def test_x(): pass\n")
    _ = handle_init(
        {
            "op": "init",
            "protocol_version": PROTOCOL_VERSION,
            "rootdir": tmp_path.as_posix(),
            "python_files": ["test_*.py"],
            "python_classes": ["Test"],
            "python_functions": ["test"],
        }
    )

    with isolated_import_state():
        response = collect_file(path.as_posix())

    assert response == {
        "op": "collected",
        "path": path.as_posix(),
        "tests": [
            {
                "id": "test_wire.py::test_x",
                "path": "test_wire.py",
                "qualname": "test_x",
            }
        ],
    }
    assert "error" not in response


def test_collect_file_reports_an_import_error_without_a_tests_key(tmp_path: Path) -> None:
    path = write(tmp_path / "test_broken.py", "import definitely_not_a_module\n")
    _ = handle_init(
        {
            "op": "init",
            "protocol_version": PROTOCOL_VERSION,
            "rootdir": tmp_path.as_posix(),
            "python_files": ["test_*.py"],
            "python_classes": ["Test"],
            "python_functions": ["test"],
        }
    )

    with isolated_import_state():
        response = collect_file(path.as_posix())

    assert "tests" not in response
    error = response["error"]
    assert isinstance(error, dict)
    assert error["path"] == "test_broken.py"
    assert "definitely_not_a_module" in str(error["message"])


def test_collect_file_reports_a_syntax_error(tmp_path: Path) -> None:
    path = write(tmp_path / "test_syntax.py", "def broken(:\n")
    _ = handle_init(
        {
            "op": "init",
            "protocol_version": PROTOCOL_VERSION,
            "rootdir": tmp_path.as_posix(),
            "python_files": ["test_*.py"],
            "python_classes": ["Test"],
            "python_functions": ["test"],
        }
    )

    with isolated_import_state():
        response = collect_file(path.as_posix())

    assert "tests" not in response
    error = response["error"]
    assert isinstance(error, dict)
    assert "SyntaxError" in str(error["message"])


def test_collect_file_of_an_empty_module_omits_the_tests_key(tmp_path: Path) -> None:
    """No tests and no error: `tests` must be OMITTED, never `[]` — the wire contract
    pins omission even though the Rust decoder tolerates an explicit empty array."""
    path = write(tmp_path / "test_none.py", "def helper(): pass\n")
    _ = handle_init(
        {
            "op": "init",
            "protocol_version": PROTOCOL_VERSION,
            "rootdir": tmp_path.as_posix(),
            "python_files": ["test_*.py"],
            "python_classes": ["Test"],
            "python_functions": ["test"],
        }
    )

    with isolated_import_state():
        response = collect_file(path.as_posix())

    assert response == {"op": "collected", "path": path.as_posix()}
    assert encode_response(response) == ('{"op":"collected","path":"' + path.as_posix() + '"}')


def test_install_pytest_shim_redirects_import_pytest() -> None:
    """The worker must not import real pytest; test modules doing `import pytest`
    get rustest's compat layer (mirrors `src/discovery.rs::inject_pytest_compat_shim`)."""
    saved = {name: sys.modules.get(name) for name in ("pytest", "pytest_asyncio")}
    try:
        install_pytest_shim()
        assert sys.modules["pytest"].__name__ == "rustest.compat.pytest"
        assert sys.modules["pytest_asyncio"].__name__ == "rustest.compat.pytest_asyncio"
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


# ---------------------------------------------------------------------------
# subprocess smoke test — the real protocol loop
# ---------------------------------------------------------------------------


def test_worker_subprocess_speaks_the_protocol(tmp_path: Path) -> None:
    write(
        tmp_path / "test_smoke.py",
        """
        import pytest


        @pytest.mark.parametrize("value", [1, 2])
        def test_value(value):
            assert value

        print("noise on stdout at import time")
        """,
    )
    requests = "\n".join(
        [
            json.dumps(
                {
                    "op": "init",
                    "protocol_version": PROTOCOL_VERSION,
                    "rootdir": tmp_path.as_posix(),
                    "python_files": ["test_*.py", "*_test.py"],
                    "python_classes": ["Test"],
                    "python_functions": ["test"],
                }
            ),
            json.dumps({"op": "collect_file", "path": (tmp_path / "test_smoke.py").as_posix()}),
            json.dumps({"op": "shutdown"}),
        ]
    )

    proc = subprocess.run(
        [sys.executable, "-m", "rustest._v2_worker"],
        input=requests + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 3, proc.stdout
    responses: list[dict[str, Any]] = [json.loads(line) for line in lines]

    assert responses[0] == {"op": "ready", "protocol_version": PROTOCOL_VERSION}
    assert responses[2] == {"op": "bye"}

    collected = responses[1]
    assert collected["op"] == "collected"
    assert collected["path"] == (tmp_path / "test_smoke.py").as_posix()
    assert [test["id"] for test in collected["tests"]] == [
        "test_smoke.py::test_value[1]",
        "test_smoke.py::test_value[2]",
    ]
    # A module printing at import time must not corrupt the protocol stream.
    assert "noise on stdout" in proc.stderr


def test_worker_subprocess_rejects_an_unknown_op() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "rustest._v2_worker"],
        input=json.dumps({"op": "collect_dir", "path": "/repo"}) + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode != 0
    assert "collect_dir" in proc.stderr
