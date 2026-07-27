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
import os
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
    NotInitializedError,
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


def test_import_mismatch_check_honours_py_ignore_importmismatch(tmp_path: Path) -> None:
    """`PY_IGNORE_IMPORTMISMATCH=1` skips the check, exactly as in
    `_pytest/pathlib.py::import_path` (`ignore = os.environ.get(...)`)."""
    first = write(tmp_path / "a" / "test_dup2.py", "def test_a(): pass\n")
    second = write(tmp_path / "b" / "test_dup2.py", "def test_b(): pass\n")

    previous = os.environ.get("PY_IGNORE_IMPORTMISMATCH")
    os.environ["PY_IGNORE_IMPORTMISMATCH"] = "1"
    try:
        with isolated_import_state():
            first_module = import_test_module(first, tmp_path)
            # No refusal: the cached first module comes back for the second path.
            assert import_test_module(second, tmp_path) is first_module
    finally:
        if previous is None:
            del os.environ["PY_IGNORE_IMPORTMISMATCH"]
        else:
            os.environ["PY_IGNORE_IMPORTMISMATCH"] = previous


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


def test_pytestmark_applies_to_every_test_in_pytest_order(tmp_path: Path) -> None:
    """Module- and class-level `pytestmark`, in the order pytest reports.

    This is the probe, reproduced: running pytest 8.4.2 over this exact shape and
    printing `[m.name for m in item.iter_markers()]` from
    `pytest_collection_modifyitems` gives

        test_top                    ['func', 'modA', 'modB']
        TestBase::test_inherited    ['basemeth', 'base', 'modA', 'modB']
        TestChild::test_inherited   ['basemeth', 'base', 'derived', 'modA', 'modB']
        TestChild::test_own         ['own', 'base', 'derived', 'modA', 'modB']

    i.e. **closest first**: function marks, then the class chain in reversed-MRO
    (base-first) order, then module marks.  `_pytest/nodes.py::iter_markers_with_node`
    iterates `iter_parents()`, and `get_closest_marker` takes the first match — so
    emitting the reverse order would invert "closest wins" for every consumer.
    Class marks come from each class `__dict__` per reversed MRO
    (`_pytest/mark/structures.py::get_unpacked_marks`), never plain `getattr`.
    """
    entries = collect_source(
        tmp_path,
        "test_pytestmark.py",
        """
        from rustest import mark

        pytestmark = [mark.modA, mark.modB]


        @mark.func
        def test_top():
            pass


        class TestBase:
            pytestmark = [mark.base]

            @mark.basemeth
            def test_inherited(self):
                pass


        class TestChild(TestBase):
            pytestmark = [mark.derived]

            @mark.own
            def test_own(self):
                pass
        """,
    )

    names = {
        str(entry["qualname"]): [mark["name"] for mark in entry.get("marks", [])]
        for entry in entries
    }
    assert names == {
        "test_top": ["func", "modA", "modB"],
        "TestBase.test_inherited": ["basemeth", "base", "modA", "modB"],
        "TestChild.test_inherited": ["basemeth", "base", "derived", "modA", "modB"],
        "TestChild.test_own": ["own", "base", "derived", "modA", "modB"],
    }


def test_single_pytestmark_is_accepted_unwrapped(tmp_path: Path) -> None:
    """`pytestmark = mark.solo` (not a list) — `get_unpacked_marks`'s non-list branch."""
    entries = collect_source(
        tmp_path,
        "test_solomark.py",
        """
        from rustest import mark

        pytestmark = mark.solo

        def test_x():
            pass
        """,
    )

    assert entries[0]["marks"] == [{"name": "solo"}]


def test_pytestmark_carries_args_and_kwargs(tmp_path: Path) -> None:
    entries = collect_source(
        tmp_path,
        "test_markargs.py",
        """
        from rustest import mark

        pytestmark = mark.skipif(True, reason="nope")

        def test_x():
            pass
        """,
    )

    assert entries[0]["marks"] == [{"name": "skipif", "args": [True], "kwargs": {"reason": "nope"}}]


def test_inherited_rustest_class_marks_are_counted_once(tmp_path: Path) -> None:
    """The `__rustest_marks__` class walk must not amplify issue #135.

    `decorators.py::MarkDecorator.__call__` seeds its list with
    `getattr(func, "__rustest_marks__", [])`, so decorating a subclass mutates the
    BASE class's list in place and leaves both `__dict__` entries pointing at the same
    object (probed: `A.__dict__[...] is B.__dict__[...]` is True). Reading per
    reversed-MRO `__dict__` while skipping an already-seen list *by identity* reports
    that shared list exactly once instead of once per level.
    """
    entries = collect_source(
        tmp_path,
        "test_sharedmarks.py",
        """
        from rustest import mark

        @mark.base
        class TestBase:
            def test_m(self):
                pass

        @mark.derived
        class TestChild(TestBase):
            pass
        """,
    )

    by_name = {str(entry["qualname"]): entry for entry in entries}
    # v1 has already merged both marks into the one shared list; the read path must
    # not double it up into ['base', 'derived', 'base', 'derived'].
    assert [mark["name"] for mark in by_name["TestChild.test_m"]["marks"]] == [
        "base",
        "derived",
    ]


def test_malformed_parametrization_is_a_collection_error(tmp_path: Path) -> None:
    """Malformed v1 metadata fails loud instead of silently collapsing to one entry."""
    path = write(
        tmp_path / "test_badparams.py",
        """
        def test_x(value):
            pass

        test_x.__rustest_parametrization__ = ["not-a-case-dict"]
        """,
    )

    with isolated_import_state():
        module = import_test_module(path, tmp_path)
        with pytest.raises(CollectionRefusal) as excinfo:
            _ = enumerate_module(module, path, tmp_path, DEFAULT_NAMING)

    assert "malformed rustest parametrization" in str(excinfo.value)


def test_malformed_marks_metadata_is_a_collection_error(tmp_path: Path) -> None:
    path = write(
        tmp_path / "test_badmarks.py",
        """
        def test_x():
            pass

        test_x.__rustest_marks__ = ["not-a-mark-dict"]
        """,
    )

    with isolated_import_state():
        module = import_test_module(path, tmp_path)
        with pytest.raises(CollectionRefusal) as excinfo:
            _ = enumerate_module(module, path, tmp_path, DEFAULT_NAMING)

    assert "malformed rustest mark metadata" in str(excinfo.value)


def test_malformed_pytestmark_is_a_collection_error(tmp_path: Path) -> None:
    path = write(
        tmp_path / "test_badpytestmark.py",
        """
        pytestmark = ["definitely-not-a-mark"]

        def test_x():
            pass
        """,
    )

    with isolated_import_state():
        module = import_test_module(path, tmp_path)
        with pytest.raises(CollectionRefusal) as excinfo:
            _ = enumerate_module(module, path, tmp_path, DEFAULT_NAMING)

    assert "malformed pytestmark entry" in str(excinfo.value)


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


def test_parameters_with_defaults_are_not_fixture_requests(tmp_path: Path) -> None:
    """Port of `_pytest/compat.py::getfuncargnames` — `p.default is Parameter.empty`.

    Verified against the installed pytest:
    `getfuncargnames(def test_defaults(tmp_path, flag=1, *, capsys, extra=2))` returns
    `('tmp_path', 'capsys')`.  pytest would never try to resolve a fixture named
    `flag`, so emitting one into the frozen manifest is wrong data.
    """
    entries = collect_source(
        tmp_path,
        "test_defaults.py",
        """
        def test_defaults(tmp_path, flag=1, *, capsys, extra=2):
            pass
        """,
    )

    assert entries[0]["fixtures"] == ["tmp_path", "capsys"]


def test_bound_first_argument_is_dropped_by_staticmethod_test_not_by_name(
    tmp_path: Path,
) -> None:
    """`getfuncargnames` decides with `inspect.getattr_static(cls, name)`.

    Two shapes a `self`/`cls` name check gets wrong: a method whose first parameter
    is called something else still loses it, and a `@staticmethod` keeps its first
    parameter (pytest's comment: "Not using `getattr` because we don't want to
    resolve the staticmethod").
    """
    entries = collect_source(
        tmp_path,
        "test_boundargs.py",
        """
        class TestOdd:
            def test_renamed_self(this, tmp_path):
                pass

            @staticmethod
            def test_static(tmp_path):
                pass

            @classmethod
            def test_classmethod(cls, tmp_path):
                pass
        """,
    )

    by_name = {str(entry["qualname"]): entry for entry in entries}
    assert by_name["TestOdd.test_renamed_self"]["fixtures"] == ["tmp_path"]
    assert by_name["TestOdd.test_static"]["fixtures"] == ["tmp_path"]
    assert by_name["TestOdd.test_classmethod"]["fixtures"] == ["tmp_path"]


def test_positional_only_self_does_not_eat_the_first_fixture(tmp_path: Path) -> None:
    """`getfuncargnames` skips the bound-arg drop when any parameter is positional-only.

    A positional-only `self` never enters the name list in the first place (only
    POSITIONAL_OR_KEYWORD/KEYWORD_ONLY do), so dropping the first name would delete a
    real fixture.  Verified against the installed pytest:
    `getfuncargnames(def test_m(self, /, tmp_path), cls=..., name=...)` returns
    `('tmp_path',)`.
    """
    entries = collect_source(
        tmp_path,
        "test_posonly.py",
        """
        class TestPosOnly:
            def test_m(self, /, tmp_path):
                pass
        """,
    )

    assert entries[0]["fixtures"] == ["tmp_path"]


def test_fully_populated_entry_matches_the_manifest_golden(tmp_path: Path) -> None:
    """A REAL collected entry, byte-compared to the `src/v2/manifest.rs` golden.

    The golden fragment is the second test in that module's
    `manifest_json_matches_golden_contract`.  Producing it from an actual module —
    rather than hand-building a dict — is what proves the worker's field order,
    nodeid composition, class chain, param id, mark shape and fixture list all agree
    with the frozen wire contract at once.
    """
    source = """
        from rustest import parametrize
        from rustest.decorators import MarkDecorator

        skipif = MarkDecorator("skipif", (True,), {"reason": "needs windows", "strict": True})
        slow = MarkDecorator("slow", (), {})


        class TestBox:
            @slow
            @skipif
            @parametrize("x,y", [("x", 1)])
            def test_method(self, x, y, tmp_path, capsys):
                pass
        """
    path = write(tmp_path / "tests" / "test_math.py", source)

    with isolated_import_state():
        module = import_test_module(path, tmp_path)
        entries = enumerate_module(module, path, tmp_path, DEFAULT_NAMING)

    assert len(entries) == 1
    assert encode_response(entries[0]) == (
        '{"id":"tests/test_math.py::TestBox::test_method[x-1]",'
        '"path":"tests/test_math.py","qualname":"TestBox.test_method",'
        '"class_name":"TestBox","param_id":"x-1",'
        '"marks":[{"name":"skipif","args":[true],'
        '"kwargs":{"reason":"needs windows","strict":true}},{"name":"slow"}],'
        '"fixtures":["tmp_path","capsys"]}'
    )


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
    # Pinned as a literal, not read from the constant: this and `PROTOCOL_VERSION` in
    # `src/v2/protocol.rs` must move together, so bumping one alone has to fail here.
    assert PROTOCOL_VERSION == 3


def test_ready_line_matches_the_rust_golden() -> None:
    assert (
        encode_response({"op": "ready", "protocol_version": PROTOCOL_VERSION})
        == '{"op":"ready","protocol_version":3}'
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


# ---------------------------------------------------------------------------
# v3: the batch execute op and the assertion-rewrite key
# ---------------------------------------------------------------------------


def _init(rootdir: Path) -> dict[str, Any]:
    return {
        "op": "init",
        "protocol_version": PROTOCOL_VERSION,
        "rootdir": rootdir.as_posix(),
        "invocation_dir": rootdir.as_posix(),
        "python_files": ["test_*.py", "*_test.py"],
        "python_classes": ["Test"],
        "python_functions": ["test"],
    }


BATCH_MODULE = """
    def test_one():
        assert 1 == 1


    def test_two():
        assert 1 == 2


    def test_three():
        assert 1 == 1
    """


def test_execute_batch_answers_every_test_then_batch_done(tmp_path: Path) -> None:
    """The op's whole contract in one exchange: N results in order, then the terminator.

    The terminator's ``executed`` is the orchestrator's only defence against a lost result
    (``src/v2/protocol.rs``), so it is asserted here against the number of results that
    actually arrived rather than against the number requested — the two differ precisely when
    the bug this field exists for has happened.
    """
    write(tmp_path / "test_batch.py", BATCH_MODULE)
    path = (tmp_path / "test_batch.py").as_posix()
    ids = [f"test_batch.py::test_{name}" for name in ("one", "two", "three")]

    proc = _run_worker(
        [
            _init(tmp_path),
            {"op": "collect_file", "path": path},
            {"op": "execute_batch", "ids": ids, "stop_on_failure": False},
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    results = [r for r in responses if r["op"] == "test_result"]
    assert [r["id"] for r in results] == ids, "results must arrive in the order sent"
    assert [r["status"] for r in results] == ["passed", "failed", "passed"]

    done = [r for r in responses if r["op"] == "batch_done"]
    assert done == [{"op": "batch_done", "executed": len(results), "stopped": False}]
    assert responses[-1] == {"op": "bye"}


def test_execute_batch_stops_at_the_first_failure_when_asked(tmp_path: Path) -> None:
    """``-x`` reaching inside a batch.

    The failing test is still **reported** — pytest reports it and then stops — and only what
    follows is cancelled. Without ``stopped`` on the terminator the orchestrator could not
    tell this from a worker that dropped the tail.
    """
    write(tmp_path / "test_batch.py", BATCH_MODULE)
    path = (tmp_path / "test_batch.py").as_posix()
    ids = [f"test_batch.py::test_{name}" for name in ("one", "two", "three")]

    proc = _run_worker(
        [
            _init(tmp_path),
            {"op": "collect_file", "path": path},
            {"op": "execute_batch", "ids": ids, "stop_on_failure": True},
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    results = [r for r in responses if r["op"] == "test_result"]
    assert [r["id"] for r in results] == ids[:2], "the failing test is reported, the next is not"
    assert [r for r in responses if r["op"] == "batch_done"] == [
        {"op": "batch_done", "executed": 2, "stopped": True}
    ]


def test_execute_batch_without_a_stop_flag_is_protocol_drift(tmp_path: Path) -> None:
    """The flag is required, not defaulted.

    A default of ``False`` would turn a truncated or older line into a run that silently
    ignores ``-x`` — the one behaviour that flag exists to guarantee.
    """
    write(tmp_path / "test_batch.py", BATCH_MODULE)
    proc = _run_worker(
        [
            _init(tmp_path),
            {"op": "collect_file", "path": (tmp_path / "test_batch.py").as_posix()},
            {"op": "execute_batch", "ids": ["test_batch.py::test_one"]},
        ]
    )
    assert proc.returncode == 2
    assert "stop_on_failure" in proc.stderr


def test_execute_batch_with_an_unknown_id_is_protocol_drift(tmp_path: Path) -> None:
    """An id this worker never collected is a routing bug, and the results already produced
    are flushed before the worker dies so the orchestrator keeps what it earned."""
    write(tmp_path / "test_batch.py", BATCH_MODULE)
    proc = _run_worker(
        [
            _init(tmp_path),
            {"op": "collect_file", "path": (tmp_path / "test_batch.py").as_posix()},
            {
                "op": "execute_batch",
                "ids": ["test_batch.py::test_one", "test_batch.py::test_nowhere"],
                "stop_on_failure": False,
            },
        ]
    )
    assert proc.returncode == 2
    assert "test_batch.py::test_nowhere" in proc.stderr
    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert [r["id"] for r in responses if r["op"] == "test_result"] == [
        "test_batch.py::test_one"
    ], "the result produced before the drift must still reach the wire"


def test_an_assert_key_on_collect_file_rewrites_the_module(tmp_path: Path) -> None:
    """The Tier S half of the split, through the real protocol loop.

    The key doubles as the bytecode cache file name, so the artefact's presence under
    ``.rustest_cache/v2-assert`` is asserted too — that directory is the payoff Task 2's
    manifest cache key was kept for.
    """
    write(tmp_path / "test_r.py", "\n        def test_one():\n            assert 1 == 2\n        ")
    key = "a" * 64
    proc = _run_worker(
        [
            _init(tmp_path),
            {
                "op": "collect_file",
                "path": (tmp_path / "test_r.py").as_posix(),
                "assert_key": key,
            },
            {"op": "execute_batch", "ids": ["test_r.py::test_one"], "stop_on_failure": False},
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    result = next(r for r in responses if r["op"] == "test_result")
    assert result["status"] == "failed"
    # The discriminator is the exception's **message**, not the traceback text: the traceback
    # echoes the source line either way, so `"assert 1 == 2" in message` would pass for an
    # unrewritten module too. A rewritten one gives `AssertionError` an argument.
    assert result["message"].rstrip().endswith("AssertionError: assert 1 == 2"), result["message"]
    # One artefact, named `<path tag>-<key>.pyc` (see `_assertion_rewrite._cache_path`); the
    # key half is asserted rather than the whole name, because the tag is a path digest.
    artefacts = list((tmp_path / ".rustest_cache" / "v2-assert").glob(f"*-{key}.pyc"))
    assert len(artefacts) == 1, artefacts


def test_no_assert_key_leaves_the_module_with_plain_asserts(tmp_path: Path) -> None:
    """The Tier D half: same file, no key, and the message is a bare ``AssertionError``.

    Paired with the test above on purpose — either one alone would pass for a rewriter that
    was always on, or always off.
    """
    write(tmp_path / "test_r.py", "\n        def test_one():\n            assert 1 == 2\n        ")
    proc = _run_worker(
        [
            _init(tmp_path),
            {"op": "collect_file", "path": (tmp_path / "test_r.py").as_posix()},
            {"op": "execute_batch", "ids": ["test_r.py::test_one"], "stop_on_failure": False},
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    result = next(r for r in responses if r["op"] == "test_result")
    assert result["status"] == "failed"
    # A bare `AssertionError` — no argument, so nothing after the class name. See the
    # comment in the paired test above for why the traceback text cannot be the signal.
    assert result["message"].rstrip().endswith("AssertionError"), result["message"]
    assert not (tmp_path / ".rustest_cache" / "v2-assert").exists()


def test_a_non_string_assert_key_is_protocol_drift(tmp_path: Path) -> None:
    """A key that is not a string is drift, not "no rewriting".

    Silently treating it as absent would turn a producer bug into a quality regression
    nobody can see: the run stays green and the messages quietly get worse.
    """
    write(tmp_path / "test_r.py", "\n        def test_one():\n            assert 1 == 2\n        ")
    proc = _run_worker(
        [
            _init(tmp_path),
            {
                "op": "collect_file",
                "path": (tmp_path / "test_r.py").as_posix(),
                "assert_key": 17,
            },
        ]
    )
    assert proc.returncode == 2
    assert "assert_key" in proc.stderr


def test_worker_subprocess_rejects_an_unknown_op() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "rustest._v2_worker"],
        input=json.dumps({"op": "collect_dir", "path": "/repo"}) + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 2
    assert "collect_dir" in proc.stderr


def _run_worker(lines: list[dict[str, Any]]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rustest._v2_worker"],
        input="".join(json.dumps(line) + "\n" for line in lines),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_collect_file_before_init_is_protocol_fatal(tmp_path: Path) -> None:
    """Not a bad file — a protocol violation, so exit 2 like every other drift,
    never an uncaught traceback (exit 1, no framing)."""
    proc = _run_worker([{"op": "collect_file", "path": (tmp_path / "t.py").as_posix()}])

    assert proc.returncode == 2
    assert "before init" in proc.stderr
    assert proc.stdout.strip() == ""


def test_collect_file_before_init_raises_not_initialized(tmp_path: Path) -> None:
    """The in-process half of the same rule (the module-level state is global, so this
    also documents that `collect_file` never silently invents a rootdir)."""
    import rustest._v2_worker as worker

    saved = worker._state  # pyright: ignore[reportPrivateUsage]
    worker._state = None  # pyright: ignore[reportPrivateUsage]
    try:
        with pytest.raises(NotInitializedError):
            _ = collect_file((tmp_path / "t.py").as_posix())
    finally:
        worker._state = saved  # pyright: ignore[reportPrivateUsage]


def test_collect_file_without_a_path_is_protocol_fatal(tmp_path: Path) -> None:
    proc = _run_worker(
        [
            {
                "op": "init",
                "protocol_version": PROTOCOL_VERSION,
                "rootdir": tmp_path.as_posix(),
                "python_files": ["test_*.py"],
                "python_classes": ["Test"],
                "python_functions": ["test"],
            },
            {"op": "collect_file"},
        ]
    )

    assert proc.returncode == 2
    assert "without a path" in proc.stderr


def test_execute_test_for_a_file_this_worker_never_collected_is_protocol_fatal(
    tmp_path: Path,
) -> None:
    """The execute op exists now; what must still fail loudly is an id nobody collected.

    This is the successor to the placeholder that pinned "the op is not implemented yet".
    The failure it guards is the one that survives implementation: the orchestrator routes an
    execute back to the worker that collected the file, so an id this worker has never seen
    is **routing drift**.  It must fail on the path a real orchestrator takes — after
    `init`/`ready` has agreed on protocol 2, so exit 2 cannot be mistaken for a handshake
    rejection — and leave **nothing on stdout beyond `ready`**, never a swallowed request
    that blocks the orchestrator on a `test_result` line that is never coming.

    The behavioural table for the op lives in `test_v2_worker_execute.py`.
    """
    proc = _run_worker(
        [
            {
                "op": "init",
                "protocol_version": PROTOCOL_VERSION,
                "rootdir": tmp_path.as_posix(),
                "invocation_dir": tmp_path.as_posix(),
                "python_files": ["test_*.py"],
                "python_classes": ["Test"],
                "python_functions": ["test"],
            },
            {"op": "execute_test", "id": "tests/test_math.py::test_add"},
        ]
    )

    assert proc.returncode == 2
    assert "tests/test_math.py::test_add" in proc.stderr
    assert proc.stdout.splitlines() == [f'{{"op":"ready","protocol_version":{PROTOCOL_VERSION}}}']


def test_worker_subprocess_rejects_an_undecodable_line() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "rustest._v2_worker"],
        input="{not json\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 2
    assert "undecodable" in proc.stderr


def test_install_pytest_shim_registers_the_underscore_pytest_stubs() -> None:
    """`install_pytest_shim` must also install the `_pytest.*` stubs.

    Mirrors `core.py`'s pytest-compat path (`install_pytest_stubs()`).  Checked in a
    **fresh interpreter** because the assertion is otherwise vacuous twice over: this
    test process has real pytest loaded (so `install_pytest_stubs` deliberately
    no-ops), and this venv has real pytest installed (so `import _pytest.outcomes`
    would succeed with or without the stubs — verified: it resolves to
    `.venv/Lib/site-packages/_pytest/outcomes.py`).  Only a subprocess that has
    imported neither can show which module actually ends up in `sys.modules`.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys;"
            "from rustest._v2_worker import install_pytest_shim;"
            "install_pytest_shim();"
            "print(sys.modules['pytest'].__name__, sys.modules['_pytest'].__name__,"
            " sys.modules['_pytest.outcomes'].__name__)",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == [
        "rustest.compat.pytest",
        "rustest._pytest_stub",
        "rustest._pytest_stub.outcomes",
    ]


def test_worker_subprocess_collects_a_module_using_pytest_internal_api(
    tmp_path: Path,
) -> None:
    """End-to-end: a module importing `_pytest.*` collects rather than erroring.

    Weaker than it looks in this venv (real `_pytest` is importable, so the import
    would succeed even with no stubs installed) — the decisive check is
    `test_install_pytest_shim_registers_the_underscore_pytest_stubs`.  Kept because it
    pins the *outcome* that matters to the corpus: such a file must not come back as a
    collection error.
    """
    write(
        tmp_path / "test_internal_import.py",
        """
        from _pytest.outcomes import Failed
        from _pytest.monkeypatch import MonkeyPatch

        def test_uses_internal_api():
            assert Failed is not None
        """,
    )
    proc = _run_worker(
        [
            {
                "op": "init",
                "protocol_version": PROTOCOL_VERSION,
                "rootdir": tmp_path.as_posix(),
                "python_files": ["test_*.py"],
                "python_classes": ["Test"],
                "python_functions": ["test"],
            },
            {
                "op": "collect_file",
                "path": (tmp_path / "test_internal_import.py").as_posix(),
            },
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode == 0, proc.stderr
    collected: dict[str, Any] = json.loads(proc.stdout.splitlines()[1])
    assert "error" not in collected, collected
    assert [test["id"] for test in collected["tests"]] == [
        "test_internal_import.py::test_uses_internal_api"
    ]
