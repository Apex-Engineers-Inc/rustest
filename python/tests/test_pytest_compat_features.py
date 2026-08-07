"""Tests for pytest-compat mode features.

This module tests the pytest compatibility features including:
- pytest.warns() context manager
- pytest.deprecated_call()
- capsys and capfd fixtures
- pytest.param() for parametrize
- List parametrize (lists treated as tuples)
- pytest.importorskip()
"""

from __future__ import annotations

import warnings

import pytest

from rustest.compat.pytest import (
    warns,
    deprecated_call,
    param,
    importorskip,
    FixtureRequest,
)
from rustest.decorators import parametrize, ParameterSet, Failed, _build_cases
from rustest.builtin_fixtures import CaptureFixture
from rustest.fixture_registry import register_fixtures, clear_registry


# =============================================================================
# Tests for pytest.warns()
# =============================================================================


class TestWarns:
    """Tests for the warns() context manager."""

    def test_warns_captures_warning(self):
        """Test that warns captures a warning of the expected type."""
        with warns(UserWarning) as record:
            warnings.warn("test warning", UserWarning)

        assert len(record) == 1
        assert "test warning" in str(record[0].message)

    def test_warns_captures_multiple_warnings(self):
        """Test that warns captures multiple warnings."""
        with warns(UserWarning) as record:
            warnings.warn("first", UserWarning)
            warnings.warn("second", UserWarning)

        assert len(record) == 2

    def test_warns_with_match_pattern(self):
        """Test that warns can filter by message pattern."""
        with warns(UserWarning, match="specific"):
            warnings.warn("this is a specific warning", UserWarning)

    def test_warns_match_pattern_fails_when_no_match(self):
        """The *category matched, regex did not* message -- pytest's second wording.

        The old implementation had one message for both failures. pytest distinguishes them
        because they have different causes and different fixes, and the regex miss is the
        more common of the two (`_pytest/recwarn.py` l. 316-321).
        """
        with pytest.raises(Failed, match="matching the regex were emitted"):
            with warns(UserWarning, match="nonexistent"):
                warnings.warn("different message", UserWarning)

    def test_warns_raises_when_no_warning(self):
        """The DID-NOT-WARN message shape, `_pytest/recwarn.py` l. 311-315."""
        with pytest.raises(Failed, match="DID NOT WARN. No warnings of type"):
            with warns(UserWarning):
                pass  # No warning emitted

    def test_warns_raises_when_wrong_type(self):
        """Test that warns raises when wrong warning type is emitted."""
        with pytest.raises(Failed, match="DID NOT WARN"):
            with warns(DeprecationWarning):
                warnings.warn("wrong type", UserWarning)

    def test_warns_with_tuple_of_types(self):
        """Test warns with multiple warning types."""
        with warns((UserWarning, DeprecationWarning)) as record:
            warnings.warn("user warning", UserWarning)

        assert len(record) == 1

    def test_warns_without_expected_type_captures_all(self):
        """Test warns without type captures all warnings."""
        with warns() as record:
            warnings.warn("first", UserWarning)
            warnings.warn("second", DeprecationWarning)

        assert len(record) == 2

    def test_warns_subclass_matching(self):
        """Test that warns matches subclasses of expected warning."""
        # DeprecationWarning is a subclass of Warning
        with warns(Warning):
            warnings.warn("deprecated", DeprecationWarning)

    # -- the Phase 4 final polish wave (review findings I3 and I7) -----------------

    def test_warns_with_no_category_still_requires_a_warning(self):
        """FINDING I3 -- the false green. ``pytest.warns()`` defaults to ``Warning``.

        ``expected_warning`` defaulted to ``None`` and ``None`` was read as "assert nothing",
        so a bare ``with pytest.warns():`` passed over a block that warned about nothing at
        all. pytest's default is ``Warning`` (`_pytest/recwarn.py` l. 224 and l. 259), which
        is why the spelling is common: a caller who does not care *which* warning still cares
        that one happened.
        """
        with pytest.raises(Failed, match="DID NOT WARN"):
            with warns():
                pass

    def test_the_default_is_the_class_warning_not_none(self):
        """Read off the checker itself, so the default cannot drift back to ``None``."""
        checker = warns()
        assert checker.expected_warning == (Warning,)
        assert checker.match_expr is None

    def test_a_non_warning_category_is_a_type_error(self):
        """pytest's own validation and message (`recwarn.py` l. 267-277)."""
        with pytest.raises(TypeError, match="exceptions must be derived from Warning"):
            warns(ValueError)  # pyright: ignore[reportArgumentType]
        with pytest.raises(TypeError, match="exceptions must be derived from Warning"):
            warns((UserWarning, ValueError))  # pyright: ignore[reportArgumentType]

    def test_the_callable_form_runs_the_function_and_returns_its_value(self):
        """pytest's second signature: ``warns(cat, func, *args, **kwargs)`` (l. 245-252)."""

        def warner(a, b, c=0):
            warnings.warn("called", UserWarning)
            return a + b + c

        assert warns(UserWarning, warner, 1, 2, c=3) == 6

    def test_the_callable_form_rejects_a_non_callable(self):
        with pytest.raises(TypeError, match="must be callable"):
            warns(UserWarning, "not callable")

    def test_keyword_arguments_without_a_callable_are_a_type_error(self):
        """pytest's "Use context-manager form instead?" hint (l. 240-244)."""
        with pytest.raises(TypeError, match="Use context-manager form instead"):
            warns(UserWarning, nonsense=1)

    def test_unmatched_warnings_are_re_emitted_on_exit(self):
        """pytest 8.0+ (`recwarn.py` l. 328-338): a warning the assertion did not claim
        must reach the enclosing filter stack rather than being eaten by the recorder.

        This is pytest's own doctest, transcribed: the inner block matches on its regex, and
        the *unmatched* UserWarning it also saw is re-raised into the outer ``warns``. Note
        the two regexes are deliberately non-overlapping -- ``matches()`` is ``re.search``, so
        "claimed"/"not claimed" would both match the inner pattern and nothing would be
        re-emitted at all (which is how this test first passed for the wrong reason).
        """
        with warns(UserWarning, match="beta") as outer:
            with warns(UserWarning, match="alpha"):
                warnings.warn("alpha, claimed by the inner block", UserWarning)
                warnings.warn("beta, not claimed by the inner block", UserWarning)
        assert [str(w.message) for w in outer] == ["beta, not claimed by the inner block"]

    def test_a_base_exception_passes_straight_through(self):
        """`recwarn.py` l. 297-305: ``skip``/``fail``/``exit``/Ctrl-C must not become
        "DID NOT WARN", because a control-flow exception is the answer, not a symptom.
        """
        with pytest.raises(KeyboardInterrupt):
            with warns(UserWarning):
                raise KeyboardInterrupt

    def test_a_plain_exception_does_NOT_suppress_the_check(self):
        """The other half, and the one the old early-return got wrong.

        The old code returned for *any* exception, so a block that raised a normal error
        without warning was silently accepted. pytest still fails it -- and both exceptions
        surface, the ``Failed`` chained onto the original.
        """
        with pytest.raises(Failed, match="DID NOT WARN"):
            with warns(UserWarning):
                raise ValueError("something else went wrong")


class TestDeprecatedCall:
    """Tests for the deprecated_call() context manager."""

    def test_deprecated_call_captures_deprecation(self):
        """Test that deprecated_call captures DeprecationWarning."""
        with deprecated_call():
            warnings.warn("old function", DeprecationWarning)

    def test_deprecated_call_captures_pending_deprecation(self):
        """Test that deprecated_call captures PendingDeprecationWarning."""
        with deprecated_call():
            warnings.warn("will be deprecated", PendingDeprecationWarning)

    def test_deprecated_call_with_match(self):
        """Test deprecated_call with match pattern."""
        with deprecated_call(match="old"):
            warnings.warn("old function", DeprecationWarning)

    def test_deprecated_call_raises_when_no_deprecation(self):
        """Test deprecated_call raises when no deprecation warning."""
        with pytest.raises(Failed, match="DID NOT WARN"):
            with deprecated_call():
                pass  # No warning

    def test_deprecated_call_captures_future_warning(self):
        """FINDING I7 -- ``FutureWarning`` is the **third** member and was missing.

        `_pytest/recwarn.py` l. 219: ``warns((DeprecationWarning, PendingDeprecationWarning,
        FutureWarning), ...)``. Not an edge case: ``FutureWarning`` exists for deprecations
        aimed at end users rather than developers, and numpy and pandas both use it heavily,
        so a library that deprecated in the documented way had its own
        ``deprecated_call()`` assertions fail under rustest.
        """
        with deprecated_call():
            warnings.warn("this will change", FutureWarning)

    def test_deprecated_call_expects_exactly_pytests_three_categories(self):
        checker = deprecated_call()
        assert checker.expected_warning == (
            DeprecationWarning,
            PendingDeprecationWarning,
            FutureWarning,
        )

    def test_deprecated_call_callable_form(self):
        """``deprecated_call(func, *args)`` -- pytest's other signature (l. 213-217)."""

        def old_api(value):
            warnings.warn("use v3", DeprecationWarning)
            return value * 2

        assert deprecated_call(old_api, 21) == 42


class TestPytestWarningHierarchy:
    """FINDING I5 -- ``pytest.Pytest*Warning`` must BE ``Warning`` subclasses.

    They used to come out of this module's catch-all ``__getattr__``, which manufactures a
    bare ``type(name, (), ...)``. An ``object`` subclass cannot be used as a warning category
    for anything a warning category is for, so every one of the tests below raised
    ``TypeError`` before the hierarchy was ported from `_pytest/warning_types.py`.
    """

    def test_pytest_warning_is_a_user_warning(self):
        from rustest.compat.pytest import PytestWarning

        assert issubclass(PytestWarning, UserWarning)

    def test_pytest_deprecation_warning_has_both_bases(self):
        """`warning_types.py` l. 47-50 -- ``PytestWarning`` AND ``DeprecationWarning``.

        The second base is what makes ``-W error::DeprecationWarning`` catch pytest's own
        deprecations and ``pytest.deprecated_call()`` accept them.
        """
        from rustest.compat.pytest import PytestDeprecationWarning, PytestWarning

        assert issubclass(PytestDeprecationWarning, PytestWarning)
        assert issubclass(PytestDeprecationWarning, DeprecationWarning)

    def test_experimental_api_warning_is_a_future_warning(self):
        from rustest.compat.pytest import PytestExperimentalApiWarning

        assert issubclass(PytestExperimentalApiWarning, FutureWarning)

    def test_a_pytest_warning_can_be_used_as_a_filter_category(self):
        """The failure this fixes: ``TypeError: category must be a Warning subclass``."""
        from rustest.compat.pytest import PytestDeprecationWarning

        with warnings.catch_warnings():
            warnings.simplefilter("error", PytestDeprecationWarning)
            with pytest.raises(PytestDeprecationWarning):
                warnings.warn(PytestDeprecationWarning("promoted to an error"))

    def test_a_pytest_warning_can_be_caught_by_warns(self):
        from rustest.compat.pytest import PytestCollectionWarning, PytestWarning

        with warns(PytestWarning):
            warnings.warn(PytestCollectionWarning("cannot collect"))

    def test_an_unenumerated_warning_name_is_still_a_warning(self):
        """The catch-all's ``*Warning`` arm: a name pytest has and this shim does not.

        A stub for a warning category is at least *shaped* like the thing it stands in for,
        so a plugin that filters on it does not crash on the filter.
        """
        import rustest.compat.pytest as _compat

        stub = _compat.PytestSomeUnenumeratedWarning
        assert issubclass(stub, _compat.PytestWarning)
        assert issubclass(stub, Warning)

    def test_a_non_warning_name_keeps_the_inert_object_stub(self):
        import rustest.compat.pytest as _compat

        stub = _compat.SomeUnknownPytestThing
        assert not issubclass(stub, Warning)
        assert "rustest compat stub" in repr(stub())


# =============================================================================
# Tests for capsys and capfd fixtures
# =============================================================================


class TestCaptureFixture:
    """Tests for the CaptureFixture class."""

    def test_capture_fixture_captures_stdout(self):
        """Test that CaptureFixture captures stdout."""
        capture = CaptureFixture()
        capture.start_capture()

        print("hello stdout")
        out, err = capture.readouterr()

        capture.stop_capture()

        assert out == "hello stdout\n"
        assert err == ""

    def test_capture_fixture_captures_stderr(self):
        """Test that CaptureFixture captures stderr."""
        import sys

        capture = CaptureFixture()
        capture.start_capture()

        print("hello stderr", file=sys.stderr)
        out, err = capture.readouterr()

        capture.stop_capture()

        assert out == ""
        assert err == "hello stderr\n"

    def test_capture_fixture_resets_on_readouterr(self):
        """Test that readouterr resets the capture buffers."""
        capture = CaptureFixture()
        capture.start_capture()

        print("first")
        out1, _ = capture.readouterr()

        print("second")
        out2, _ = capture.readouterr()

        capture.stop_capture()

        assert out1 == "first\n"
        assert out2 == "second\n"

    def test_capture_fixture_context_manager(self):
        """Test CaptureFixture as context manager."""
        with CaptureFixture() as capture:
            print("in context")
            out, err = capture.readouterr()

        assert out == "in context\n"

    def test_capture_fixture_restores_streams(self):
        """Test that CaptureFixture restores original streams."""
        import sys

        original_stdout = sys.stdout
        original_stderr = sys.stderr

        capture = CaptureFixture()
        capture.start_capture()
        capture.stop_capture()

        assert sys.stdout is original_stdout
        assert sys.stderr is original_stderr


# =============================================================================
# Tests for pytest.param()
# =============================================================================


class TestPytestParam:
    """Tests for pytest.param() functionality."""

    def test_param_creates_parameter_set(self):
        """Test that param() creates a ParameterSet."""
        result = param(1, 2, 3)

        assert isinstance(result, ParameterSet)
        assert result.values == (1, 2, 3)
        assert result.id is None

    def test_param_with_id(self):
        """Test param() with custom id."""
        result = param(1, 2, id="test_case")

        assert result.id == "test_case"
        assert result.values == (1, 2)

    def test_param_with_marks_is_implemented_and_no_longer_warns(self):
        """`marks=` used to be accepted, warned about and ignored.

        Phase 4 Task 1 implemented it (`_pytest/mark/structures.py::ParameterSet.param`), so
        the warning is gone and the marks are stored normalised. A value that is not a mark
        is now a `TypeError` rather than a silently kept string.
        """
        from rustest import mark

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            one = param(1, marks=mark.xfail(reason="known"))

        assert w == []
        assert [m.name for m in one.marks] == ["xfail"]

        with pytest.raises(TypeError, match="marks must be a mark or a collection of marks"):
            _ = param(1, marks="some_mark")

    def test_param_in_parametrize(self):
        """Test that param() works with parametrize decorator."""

        @parametrize(
            "x,y",
            [
                param(1, 2, id="small"),
                param(10, 20, id="large"),
            ],
        )
        def dummy_test(x, y):
            pass

        # Check that the test was decorated with parametrize data
        assert hasattr(dummy_test, "__rustest_parametrization__")
        cases = dummy_test.__rustest_parametrization__
        assert len(cases) == 2
        assert cases[0]["id"] == "small"
        assert cases[1]["id"] == "large"

    def test_param_single_value(self):
        """Test param() with single value."""
        result = param(42, id="answer")

        assert result.values == (42,)
        assert result.id == "answer"


# =============================================================================
# Tests for list parametrize (lists treated as tuples)
# =============================================================================


class TestListParametrize:
    """Tests for list values in parametrize being treated as tuples."""

    def test_list_values_unpacked_like_tuples(self):
        """Test that lists are unpacked like tuples in parametrize."""
        names = ("x", "y")
        values = [
            [1, 2],  # List should be unpacked
            (3, 4),  # Tuple should be unpacked
        ]

        # Two names, so `force_tuple` is False either way -- the values sequence is
        # taken verbatim and zipped against the names.
        cases = _build_cases(names, values, None, force_tuple=False)

        assert len(cases) == 2
        assert cases[0]["values"] == {"x": 1, "y": 2}
        assert cases[1]["values"] == {"x": 3, "y": 4}

    def test_mixed_list_tuple_values(self):
        """Test parametrize with mixed list and tuple values."""

        @parametrize(
            "a,b,c",
            [
                [1, 2, 3],  # List
                (4, 5, 6),  # Tuple
                [7, 8, 9],  # List
            ],
        )
        def dummy_test(a, b, c):
            pass

        cases = dummy_test.__rustest_parametrization__
        assert len(cases) == 3
        assert cases[0]["values"] == {"a": 1, "b": 2, "c": 3}
        assert cases[1]["values"] == {"a": 4, "b": 5, "c": 6}
        assert cases[2]["values"] == {"a": 7, "b": 8, "c": 9}

    def test_single_param_with_list_value(self):
        """A single *string* argname makes each argvalue exactly one value.

        `force_tuple=True` is what `parametrize("items", ...)` computes, so the
        list arrives whole. Wrapping it in a tuple first, as this test used to,
        is the shape pytest reads as a two-element... no: as a ONE-element value
        set whose single value is the tuple. Both spellings are pinned below.
        """
        names = ("items",)

        bare = _build_cases(names, [[1, 2, 3]], None, force_tuple=True)
        assert bare[0]["values"] == {"items": [1, 2, 3]}

        wrapped = _build_cases(names, [([1, 2, 3],)], None, force_tuple=True)
        assert wrapped[0]["values"] == {"items": ([1, 2, 3],)}

    def test_nested_list_in_parameters(self):
        """Test that nested lists work correctly."""

        @parametrize(
            "x,y",
            [
                [[1, 2], [3, 4]],  # Outer list unpacked, inner lists are values
            ],
        )
        def dummy_test(x, y):
            pass

        cases = dummy_test.__rustest_parametrization__
        assert cases[0]["values"] == {"x": [1, 2], "y": [3, 4]}


# =============================================================================
# Tests for pytest.importorskip()
# =============================================================================


class TestImportorskip:
    """Tests for importorskip() functionality."""

    def test_importorskip_returns_module(self):
        """Test that importorskip returns the imported module."""
        # Import a module that definitely exists
        os_module = importorskip("os")

        import os

        assert os_module is os

    def test_importorskip_with_missing_module(self):
        """A missing module raises `Skipped`, and it is a **BaseException** (finding I4).

        This test used to say ``pytest.raises(Exception)``; that stopped being true when the
        outcome hierarchy was aligned with pytest's, and naming the class is the stronger
        assertion anyway.
        """
        from rustest.compat.pytest import Skipped

        with pytest.raises(Skipped) as exc_info:
            importorskip("nonexistent_module_12345")
        assert exc_info.value.allow_module_level is True

    def test_importorskip_with_custom_reason(self):
        """Test importorskip with custom reason."""
        from rustest.compat.pytest import Skipped

        with pytest.raises(Skipped) as exc_info:
            importorskip("nonexistent_module", reason="custom reason")

        assert "custom reason" in str(exc_info.value)

    def test_importorskip_version_check(self):
        """Test importorskip with version requirement."""
        from rustest.compat.pytest import Skipped

        # `os` has no `__version__`, which pytest treats as a version MISS -- one branch,
        # one message (`module 'os' has __version__ None, required is: '0.0.1'`).
        with pytest.raises(Skipped, match=r"has __version__ None, required is: '0.0.1'"):
            importorskip("os", minversion="0.0.1")

    def test_importorskip_warns_with_pytests_own_category(self):
        """FINDING I5 -- the #11523 warning is a ``PytestDeprecationWarning`` (l. 302).

        A bare ``DeprecationWarning`` cannot be silenced without silencing every
        ``DeprecationWarning`` in the process. Because pytest's class derives from
        ``DeprecationWarning`` as well, everything that used to catch this still does -- which
        is the ``-W`` interaction this pins from both sides.
        """
        import importlib.abc
        import importlib.machinery
        import sys
        import types

        from rustest.compat.pytest import PytestDeprecationWarning, Skipped

        modname = "rustest_importorskip_probe_module"

        class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
            """A module that EXISTS but raises ``ImportError`` on import.

            That is exactly the shape #11523 is about: from the outside it is
            indistinguishable from an absent module, and the deprecation exists to say so.
            A ``ModuleNotFoundError`` would NOT warn -- that one really is absent.
            """

            def find_spec(self, fullname, path=None, target=None):
                if fullname != modname:
                    return None
                return importlib.machinery.ModuleSpec(fullname, self)

            def create_module(self, spec):
                return types.ModuleType(spec.name)

            def exec_module(self, module):
                raise ImportError("a real import error, not a missing module")

        finder = _Finder()
        sys.meta_path.insert(0, finder)
        try:
            with warns(PytestDeprecationWarning, match="was found, but when imported"):
                with pytest.raises(Skipped):
                    importorskip(modname)
            # `-W` interaction, the other direction: it is a `DeprecationWarning` too, so a
            # project's broad filter still catches it.
            with warns(DeprecationWarning):
                with pytest.raises(Skipped):
                    importorskip(modname)
            # ...and `exc_type=ImportError` is how a caller silences it, as pytest documents.
            # Under `simplefilter("error")` any warning at all would surface as an exception.
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                with pytest.raises(Skipped):
                    importorskip(modname, exc_type=ImportError)
        finally:
            sys.meta_path.remove(finder)
            sys.modules.pop(modname, None)


# =============================================================================
# Tests for ParameterSet in _build_cases
# =============================================================================


class TestParameterSetInBuildCases:
    """Tests for ParameterSet handling in _build_cases."""

    def test_parameter_set_id_takes_priority(self):
        """Test that ParameterSet id takes priority over ids parameter."""
        names = ("x",)
        values = [
            ParameterSet((1,), id="param_id"),
        ]

        # Even with ids parameter, ParameterSet id should win
        cases = _build_cases(names, values, ["override_id"], force_tuple=True)

        assert cases[0]["id"] == "param_id"

    def test_parameter_set_values_extracted(self):
        """Test that ParameterSet values are correctly extracted."""
        names = ("a", "b")
        values = [
            ParameterSet((10, 20), id="test"),
        ]

        cases = _build_cases(names, values, None, force_tuple=False)

        assert cases[0]["values"] == {"a": 10, "b": 20}

    def test_parameter_set_is_never_re_wrapped(self):
        """An existing ParameterSet is authoritative -- `force_tuple` cannot touch it.

        `ParameterSet.extract_from` returns it as it stands (l. 153-154), *before*
        the `force_tuple` branch, so `pytest.param(42)` is one value under one name
        whichever way the names were spelled.
        """
        names = ("x",)
        values = [ParameterSet((42,), id="single")]

        for force_tuple in (True, False):
            cases = _build_cases(names, values, None, force_tuple=force_tuple)
            assert cases[0]["values"] == {"x": 42}


# =============================================================================
# Integration tests
# =============================================================================


class TestPytestCompatIntegration:
    """Integration tests for pytest-compat features working together."""

    def test_param_with_list_values(self):
        """Test param() containing list values."""

        @parametrize(
            "items",
            [
                param([1, 2, 3], id="list_123"),
                param([4, 5], id="list_45"),
            ],
        )
        def dummy_test(items):
            pass

        cases = dummy_test.__rustest_parametrization__
        assert cases[0]["values"] == {"items": [1, 2, 3]}
        assert cases[1]["values"] == {"items": [4, 5]}

    def test_warns_and_deprecated_call_same_api(self):
        """Test that warns and deprecated_call have compatible APIs."""
        # Both should work with match parameter
        with warns(UserWarning, match="test"):
            warnings.warn("test message", UserWarning)

        with deprecated_call(match="old"):
            warnings.warn("old function", DeprecationWarning)


# =============================================================================
# Tests for new pytest compatibility features
# =============================================================================


class TestSkipifSignatures:
    """Tests for pytest.mark.skipif() with different signature forms."""

    def test_skipif_with_keyword_reason(self):
        """Test skipif with reason as keyword argument."""
        import sys
        from rustest.decorators import mark

        # This is the modern pytest style
        @mark.skipif(sys.platform == "nonexistent", reason="Never skips")
        def dummy_test():
            pass

        # Check that the mark was applied
        marks = getattr(dummy_test, "__rustest_marks__", [])
        assert len(marks) == 1
        assert marks[0]["name"] == "skipif"
        assert marks[0]["kwargs"]["reason"] == "Never skips"

    def test_skipif_with_positional_reason(self):
        """Test skipif with reason as positional argument (older pytest style)."""
        import sys
        from rustest.decorators import mark

        # This is the older pytest style - should also work
        @mark.skipif(sys.platform == "nonexistent", "Never skips")
        def dummy_test():
            pass

        # Check that the mark was applied with the reason
        marks = getattr(dummy_test, "__rustest_marks__", [])
        assert len(marks) == 1
        assert marks[0]["name"] == "skipif"
        assert marks[0]["kwargs"]["reason"] == "Never skips"

    def test_skipif_false_condition(self):
        """Test that skipif with False condition doesn't skip."""
        from rustest.decorators import mark

        @mark.skipif(False, reason="Should not skip")
        def dummy_test():
            return "executed"

        # Test should not be skipped
        result = dummy_test()
        assert result == "executed"


class TestSkipFunction:
    """Tests for pytest.skip() function for dynamic skipping."""

    def test_skip_function_exists(self):
        """Test that skip function exists in pytest compat."""
        from rustest.compat.pytest import skip

        assert callable(skip)

    def test_skip_function_raises_skipped(self):
        """Test that skip() raises Skipped exception."""
        from rustest.compat.pytest import skip, Skipped

        with pytest.raises(Skipped):
            skip("Test skipped dynamically")

    def test_skip_function_with_reason(self):
        """Test that skip() includes the reason in the exception."""
        from rustest.compat.pytest import skip, Skipped

        try:
            skip("Custom skip reason")
        except Skipped as e:
            assert "Custom skip reason" in str(e)

    def test_skip_function_in_conditional(self):
        """Test skip() in conditional logic."""
        from rustest.compat.pytest import skip

        condition = False
        if condition:
            skip("Should not be reached")

        # If we get here, skip wasn't called
        assert True

    def test_skip_exception_type_exported(self):
        """Test that Skipped exception is exported -- as a `BaseException` (finding I4)."""
        from rustest.compat.pytest import Skipped

        assert issubclass(Skipped, BaseException)
        assert not issubclass(Skipped, Exception)


class TestXFailFunction:
    """Tests for pytest.xfail() function for expected failures."""

    def test_xfail_function_exists(self):
        """Test that xfail function exists in pytest compat."""
        from rustest.compat.pytest import xfail

        assert callable(xfail)

    def test_xfail_function_raises_xfailed(self):
        """Test that xfail() raises XFailed exception."""
        from rustest.compat.pytest import xfail, XFailed

        with pytest.raises(XFailed):
            xfail("Test expected to fail")

    def test_xfail_function_with_reason(self):
        """Test that xfail() includes the reason in the exception."""
        from rustest.compat.pytest import xfail, XFailed

        try:
            xfail("Known bug in backend")
        except XFailed as e:
            assert "Known bug" in str(e)

    def test_xfail_function_in_conditional(self):
        """Test xfail() in conditional logic."""
        from rustest.compat.pytest import xfail
        import sys

        if sys.version_info < (3, 0):  # This is False for us
            xfail("Would fail on Python 2")

        # If we get here, xfail wasn't called
        assert True

    def test_xfail_exception_type_exported(self):
        """Test that XFailed exception is exported -- as a `Failed` subclass (finding I4)."""
        from rustest.compat.pytest import Failed as _Failed, XFailed

        assert issubclass(XFailed, _Failed)
        assert issubclass(XFailed, BaseException)
        assert not issubclass(XFailed, Exception)


class TestFailFunction:
    """Tests for pytest.fail() function."""

    def test_fail_function_exists(self):
        """Test that fail function exists."""
        from rustest.compat.pytest import fail

        assert callable(fail)

    def test_fail_function_raises_failed(self):
        """Test that fail() raises Failed exception."""
        from rustest.compat.pytest import fail, Failed

        with pytest.raises(Failed):
            fail("Test failed explicitly")

    def test_fail_function_with_reason(self):
        """Test that fail() includes the reason in the exception."""
        from rustest.compat.pytest import fail, Failed

        try:
            fail("Validation error occurred")
        except Failed as e:
            assert "Validation error" in str(e)

    def test_fail_in_conditional(self):
        """Test fail() in conditional logic."""
        from rustest.compat.pytest import fail

        data_valid = True
        if not data_valid:
            fail("Data validation failed")

        # If we get here, fail wasn't called
        assert True


class TestAllExceptionTypesExported:
    """Test that all exception types are properly exported."""

    def test_all_exceptions_accessible_from_pytest(self):
        """Test that all exception types are accessible via pytest compat."""
        from rustest.compat import pytest as pytest_compat

        assert hasattr(pytest_compat, "Failed")
        assert hasattr(pytest_compat, "Skipped")
        assert hasattr(pytest_compat, "XFailed")

    def test_exceptions_are_exceptions(self):
        """FINDING I4 -- the whole outcome hierarchy is on the ``BaseException`` side now.

        pytest declares ``class OutcomeException(BaseException)``, ``class
        Skipped(OutcomeException)``, ``class Failed(OutcomeException)`` and ``class
        XFailed(Failed)`` precisely so a test body's ``except Exception:`` cannot swallow the
        runner's own control flow.

        rustest arrived in two steps. Phase 4 Task 1's review moved ``Failed`` after measuring
        the cost (a ``raises`` block that did not raise reported **passed** here and
        **failed** under pytest). ``Skipped``/``XFailed`` were left behind with the note that
        "nothing in the corpus turns on a test body's ``except Exception`` swallowing a skip"
        -- a statement about the corpus, not about the class. The Phase 4 final polish wave
        finished the job: the same three-line ``try/except Exception: pass`` turns a skip into
        a **pass**, which is a silent green, the worst answer available.
        """
        from rustest.compat.pytest import Failed, Skipped, XFailed
        from rustest.decorators import OutcomeException

        for cls in (Failed, Skipped, XFailed):
            assert issubclass(cls, OutcomeException)
            assert issubclass(cls, BaseException)
            assert not issubclass(cls, Exception)
        # `class XFailed(Failed)` -- pytest's declaration, and what makes the worker's
        # "XFAILED before FAILED" classification order load-bearing rather than accidental.
        assert issubclass(XFailed, Failed)
        assert not issubclass(Failed, XFailed)

    def test_an_except_exception_body_cannot_swallow_an_outcome(self):
        """The defect in one shape, for each of the three outcome signals."""
        from rustest.compat.pytest import fail, skip, xfail

        for raiser in (lambda: skip("s"), lambda: fail("f"), lambda: xfail("x")):
            swallowed = True
            try:
                try:
                    raiser()
                except Exception:  # noqa: BLE001 - the hazard, reproduced deliberately
                    swallowed = True
                else:
                    swallowed = False
            except BaseException:
                swallowed = False
            assert swallowed is False

    def test_exceptions_have_distinct_types(self):
        """Test that exception types are distinct."""
        from rustest.compat.pytest import Failed, Skipped, XFailed

        assert Failed is not Skipped
        assert Failed is not XFailed
        assert Skipped is not XFailed


class TestAsyncioDecorator:
    """Tests for @mark.asyncio decorator compatibility."""

    def test_asyncio_decorator_on_async_function(self):
        """Test that @mark.asyncio works with async functions."""
        from rustest.decorators import mark
        import asyncio

        @mark.asyncio
        async def async_test():
            await asyncio.sleep(0)
            return "async_result"

        # The decorated function should have the asyncio mark
        marks = getattr(async_test, "__rustest_marks__", [])
        assert len(marks) >= 1
        # Check if any mark has name "asyncio"
        asyncio_marks = [m for m in marks if m.get("name") == "asyncio"]
        assert len(asyncio_marks) >= 1

    def test_asyncio_decorator_on_non_async_function(self):
        """Test that @mark.asyncio accepts non-async functions for pytest compat."""
        from rustest.decorators import mark

        # This should NOT raise TypeError (pytest compatibility)
        @mark.asyncio
        def sync_test():
            return "sync_result"

        # Test should be marked with asyncio
        marks = getattr(sync_test, "__rustest_marks__", [])
        assert len(marks) >= 1
        asyncio_marks = [m for m in marks if m.get("name") == "asyncio"]
        assert len(asyncio_marks) >= 1

        # Test should still run normally
        result = sync_test()
        assert result == "sync_result"

    def test_asyncio_decorator_with_loop_scope(self):
        """Test @mark.asyncio with loop_scope parameter."""
        from rustest.decorators import mark

        @mark.asyncio(loop_scope="function")
        def sync_with_scope():
            return "scoped"

        # Check that the mark includes loop_scope
        marks = getattr(sync_with_scope, "__rustest_marks__", [])
        asyncio_marks = [m for m in marks if m.get("name") == "asyncio"]
        assert len(asyncio_marks) >= 1
        # Check kwargs contains loop_scope
        assert asyncio_marks[0].get("kwargs", {}).get("loop_scope") == "function"

    def test_asyncio_decorator_on_class(self):
        """Test that @mark.asyncio can be applied to classes."""
        from rustest.decorators import mark

        # Classes should be supported - mark is applied to class
        @mark.asyncio
        class TestClass:
            async def test_method(self):
                return "async"

            def test_sync_method(self):
                return "sync"

        # Class should have the asyncio mark
        marks = getattr(TestClass, "__rustest_marks__", [])
        asyncio_marks = [m for m in marks if m.get("name") == "asyncio"]
        assert len(asyncio_marks) >= 1

    def test_asyncio_mark_applied_correctly_sync(self):
        """Test that asyncio mark is correctly applied to sync functions."""
        from rustest.decorators import mark

        @mark.asyncio
        def regular_test():
            pass

        # Verify mark structure
        marks = getattr(regular_test, "__rustest_marks__", [])
        asyncio_marks = [m for m in marks if m.get("name") == "asyncio"]
        assert len(asyncio_marks) == 1

        mark_data = asyncio_marks[0]
        assert mark_data["name"] == "asyncio"
        assert "kwargs" in mark_data

    def test_asyncio_decorator_preserves_function_metadata(self):
        """Test that @mark.asyncio preserves function name and docstring."""
        from rustest.decorators import mark

        @mark.asyncio
        def test_with_metadata():
            """Test function docstring."""
            pass

        assert test_with_metadata.__name__ == "test_with_metadata"
        assert test_with_metadata.__doc__ == "Test function docstring."

    def test_asyncio_decorator_multiple_marks(self):
        """Test that @mark.asyncio can be combined with other marks."""
        from rustest.decorators import mark

        @mark.asyncio
        @mark.slow
        def test_multi_marked():
            return "marked"

        marks = getattr(test_multi_marked, "__rustest_marks__", [])
        mark_names = [m.get("name") for m in marks]

        assert "asyncio" in mark_names
        assert "slow" in mark_names


class TestFixtureRequestFallback:
    """Tests for FixtureRequest.getfixturevalue fallback resolver."""

    def test_getfixturevalue_uses_python_registry(self):
        """Ensure fallback resolver passes the active request object."""

        def needs_request(request: FixtureRequest) -> FixtureRequest:
            return request

        register_fixtures({"needs_request": needs_request})
        try:
            request = FixtureRequest()
            result = request.getfixturevalue("needs_request")
            assert result is request
        finally:
            clear_registry()
