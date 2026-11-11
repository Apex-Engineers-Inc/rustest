"""``pytest`` as a test module sees it under rustest.

The worker installs this module as ``sys.modules["pytest"]`` before it imports anything
(``_worker.install_pytest_shim``), so ``import pytest`` in a suite resolves here. A public
name is one of three things:

* rustest's own implementation of pytest's (``fixture``, ``mark``, ``param``, ``raises``,
  ``approx``, ``warns``, ``skip``/``fail``/``xfail``/``exit``, ``importorskip``, ...);
* the class of the object a builtin fixture really hands a test (``FixtureRequest``,
  ``CaptureFixture``, ``LogCaptureFixture``, ``Cache``, ``Config``, ``MonkeyPatch``,
  ``TempPathFactory``, ``TempdirFactory``), so annotations and ``isinstance`` agree;
* for any other name pytest has, an inert stub class from :func:`__getattr__`, so a plugin
  that only *imports* a pytest internal still loads.
"""

from __future__ import annotations

import re
import os
import sys
import warnings
from pprint import pformat
from re import Pattern
from types import GenericAlias
from typing import TYPE_CHECKING, Any, NoReturn

# Import rustest's actual implementations
from rustest.decorators import (
    fixture as _rustest_fixture,
    parametrize as _rustest_parametrize,
    SkipMarkDecorator as _SkipMarkDecorator,
    BareOrFactoryMark as _BareOrFactoryMark,
    mark as _rustest_mark,
    raises as _rustest_raises,
    fail as _rustest_fail,
    Failed as _rustest_Failed,
    Skipped as _rustest_Skipped,
    XFailed as _rustest_XFailed,
    xfail as _rustest_xfail,
    skip as _rustest_skip_function,
    ExceptionInfo,
    ParameterSet,
)
from rustest._builtins import (
    Cache,
    CaptureFixture,
    Config,
    LogCaptureFixture,
    TempdirFactory,
    TempPathFactory,
)
from rustest._warnings import WarningsRecorder as _WarningsRecorder
from rustest.approx import approx as _rustest_approx
from rustest.builtin_fixtures import MonkeyPatch

if TYPE_CHECKING:
    # Bound at run time by `__getattr__`: `_worker` imports this module at its top level.
    from rustest._worker import SubRequest as FixtureRequest

__all__ = [
    "fixture",
    "parametrize",
    "mark",
    "skip",
    "xfail",
    "raises",
    "fail",
    "exit",
    "Exit",
    "Failed",
    "Skipped",
    "XFailed",
    "approx",
    "param",
    "warns",
    "deprecated_call",
    "importorskip",
    # pytest's warning hierarchy (`_pytest/warning_types.py`), real `Warning` subclasses.
    "PytestWarning",
    "PytestAssertRewriteWarning",
    "PytestCacheWarning",
    "PytestCollectionWarning",
    "PytestConfigWarning",
    "PytestDeprecationWarning",
    "PytestExperimentalApiWarning",
    "PytestFDWarning",
    "PytestRemovedIn9Warning",
    "PytestReturnNotNoneWarning",
    "PytestUnhandledThreadExceptionWarning",
    "PytestUnknownMarkWarning",
    "PytestUnraisableExceptionWarning",
    # The classes of what the builtin fixtures hand a test.
    "Cache",
    "CaptureFixture",
    "Config",
    "FixtureRequest",
    "LogCaptureFixture",
    "MonkeyPatch",
    "TempPathFactory",
    "TempdirFactory",
    "ExceptionInfo",
    # Pytest plugin decorator
    "hookimpl",
]


def hookimpl(*args: Any, **kwargs: Any) -> Any:
    """
    Stub for pytest.hookimpl decorator - used by pytest plugins.

    NOT FUNCTIONAL in rustest pytest-compat mode. Returns a no-op decorator
    that simply returns the function unchanged.
    """

    def decorator(func: Any) -> Any:
        return func

    if len(args) == 1 and callable(args[0]) and not kwargs:
        # Called as @hookimpl without parentheses
        return args[0]
    else:
        # Called as @hookimpl(...) with arguments
        return decorator


# Direct mappings - these already have identical signatures
fixture = _rustest_fixture
parametrize = _rustest_parametrize
raises = _rustest_raises
approx = _rustest_approx
skip = _rustest_skip_function  # pytest.skip() function (raises Skipped)
fail = _rustest_fail
Failed = _rustest_Failed
Skipped = _rustest_Skipped
XFailed = _rustest_XFailed
xfail = _rustest_xfail


class Exit(Exception):
    """Port of `_pytest/outcomes.py::Exit` (pytest 8.4.2, l. 69-77).

    ``Exception``, not ``BaseException``, because that is what pytest uses — and the
    difference is observable: a test body wrapping its work in ``except Exception`` swallows
    a ``pytest.exit()`` under pytest too. Reproducing the base class reproduces that.

    The rustest worker therefore lists this in ``_worker.ABORT_EXCEPTIONS`` and
    re-raises it ahead of its own ``except Exception`` handlers, which is exactly how pytest
    gets the same result with a plain ``Exception``: nothing between the test body and
    ``wrap_session`` catches it.
    """

    def __init__(self, msg: str = "unknown reason", returncode: int | None = None) -> None:
        self.msg = msg
        self.returncode = returncode
        super().__init__(msg)


def exit(reason: str = "", returncode: int | None = None) -> NoReturn:  # noqa: A001 - pytest's name
    """Port of `_pytest/outcomes.py::exit` (l. 105-122): ``raise Exit(reason, returncode)``.

    **Defined explicitly so it never reaches this module's catch-all ``__getattr__``.**  That
    fallback manufactures a do-nothing stub class for any unknown public attribute, which is
    right for a plugin merely *importing* a pytest internal and catastrophic for a *called*
    control-flow function: ``pytest.exit("stopping")`` used to construct a stub instance,
    return it, and let the test — and the whole session — carry on green. That is the
    silent-false-green class, and it is what the ``marks/pytest-exit`` conformance case pins.

    Fixing it here rather than by making the stub raise is deliberate: the stub table serves
    every other unknown attribute, and turning it into a trap would change behaviour for
    imports that are legitimately harmless.

    ``returncode`` is accepted and carried on the exception. Under the engine it is not
    yet honoured — the session-stop signal reaches the orchestrator as a worker exit code,
    which cannot carry a payload — so the run exits **2** (pytest's ``INTERRUPTED``, and
    pytest's own answer when ``returncode`` is omitted). Recorded in the
    ``marks/pytest-exit`` waiver.
    """
    raise Exit(reason, returncode)


# pytest exposes the exception class as an attribute of the function it is raised by
# (`_pytest/outcomes.py::_with_exception`), so `except pytest.exit.Exception` works.
exit.Exception = Exit  # pyright: ignore[reportFunctionMemberAccess]


class _PytestMarkCompat:
    """
    Compatibility wrapper for pytest.mark.

    Provides the same interface as pytest.mark by delegating to rustest.mark.

    Examples:
        @pytest.mark.slow
        @pytest.mark.integration
        def test_expensive():
            pass

        @pytest.mark.skipif(sys.platform == "win32", reason="Unix only")
        def test_unix():
            pass

        @pytest.mark.skip          # bare, no parentheses -- also ordinary pytest
        def test_never_runs():
            pass
    """

    def __init__(self) -> None:
        super().__init__()

        # `skip` cannot simply be delegated to `_rustest_mark`: the native `mark.skip` only
        # records a mark dict in `__rustest_marks__`, and the v1 Rust collector read skips
        # from the `__rustest_skip__` attribute alone (the deleted
        # src/discovery.rs::collect_tests). `_worker::_mark_specs` reads both, so the
        # routing survives the engine that required it -- see `SkipMarkDecorator`.
        # The compat surface therefore keeps its own routing to `skip_decorator`, wrapped in
        # the same bare-or-factory discrimination as the rest -- being a plain *method* is
        # what made the bare `@pytest.mark.skip` replace the test with a closure (#136).
        def _skip_factory(reason: str | None = None) -> _SkipMarkDecorator:
            return _SkipMarkDecorator("skip", (), {"reason": reason})

        self._skip = _BareOrFactoryMark(
            "skip",
            _skip_factory,
            bare=_SkipMarkDecorator("skip", (), {"reason": None}),
        )

    def __getattr__(self, name: str) -> Any:
        """Delegate all mark.* access to rustest.mark.*"""
        return getattr(_rustest_mark, name)

    @property
    def skip(self) -> Any:
        """Mark test as skipped, bare (``@pytest.mark.skip``) or called with a reason.

        Maps to rustest's ``skip_decorator()``.
        """
        return self._skip


# Create the mark instance
mark = _PytestMarkCompat()


def param(*values: Any, id: str | None = None, marks: Any = None, **kwargs: Any) -> ParameterSet:
    """
    Create a parameter set for use in @pytest.mark.parametrize.

    This function allows you to specify custom test IDs for individual
    parameter sets:

        @pytest.mark.parametrize("x,y", [
            pytest.param(1, 2, id="small"),
            pytest.param(100, 200, id="large"),
        ])

    Args:
        *values: The parameter values for this test case
        id: Optional custom test ID for this parameter set
        marks: Marks applied to **this parameter set alone**, as in pytest
            (`_pytest/mark/structures.py::ParameterSet.param`)

    Returns:
        A ParameterSet object that will be handled by parametrize
    """
    return ParameterSet(values=values, id=id, marks=marks)


class PytestWarning(UserWarning):
    """Port of `_pytest/warning_types.py::PytestWarning` (pytest 8.4.2, l. 13-16) — and of the
    twelve classes below it, which are the whole of pytest's warning hierarchy.

    **These used to be reached through this module's catch-all ``__getattr__``**, which
    manufactures a bare ``type(name, (), ...)`` — an ``object`` subclass. That is not a
    cosmetic difference: a warning category that is not a ``Warning`` subclass cannot be used
    for anything a warning category is for.

    Every reachable symptom is **in-process** — a conftest, a test body, or a fixture:

    * ``warnings.simplefilter("error", pytest.PytestDeprecationWarning)`` raises
      ``TypeError: category must be a Warning subclass``, so a project that promotes pytest's
      own deprecations to errors — the recommended posture — crashed on the filter itself;
    * ``pytest.warns(pytest.PytestWarning)`` raised ``TypeError`` from the checker's own
      type validation, and ``issubclass(record.category, ...)`` could never be true anyway;
    * ``warnings.warn(PytestWarning("..."))`` is a ``TypeError`` outright.

    **Not** a symptom, and this list claimed it was until the Phase 4 convergence wave:
    ``-W error::pytest.PytestUnraisableExceptionWarning`` "on the command line". No command
    line reaches these classes by that name, for reasons that have nothing to do with their
    bases. ``rustest`` has no ``-W`` option — it exits 4 naming the flag — and the
    *interpreter's* ``-W``/``PYTHONWARNINGS`` resolve a dotted category at startup, long
    before anything installs ``sys.modules["pytest"]``, so the filter is discarded with
    ``Invalid -W option ignored: invalid module name: 'pytest'`` whether or not the class is
    a ``Warning``. pytest's ``filterwarnings`` ini is not read either
    (``_worker.py::_ini_values`` refuses it by name rather than fabricating a value).
    All four probed.

    The bases are pytest's exactly, and the two multiple-inheritance ones are the reason this
    is a hierarchy rather than a list: ``PytestDeprecationWarning`` is **both** a
    ``PytestWarning`` and a ``DeprecationWarning`` (l. 47-50), so ``pytest.deprecated_call()``
    accepts it and a filter naming the *builtin* category catches it —
    ``-W error::DeprecationWarning`` is the one command-line spelling that does work here,
    precisely because ``DeprecationWarning`` is resolvable at interpreter startup and this
    class inherits from it (probed). ``PytestExperimentalApiWarning`` is a ``FutureWarning``
    (l. 60-67) for the same reason. Each also sets ``__module__ = "pytest"``, as pytest does,
    because that string is what appears in a filter spelling and in the rendered warning.
    """

    __module__ = "pytest"


class PytestAssertRewriteWarning(PytestWarning):
    """`warning_types.py` l. 20-23."""

    __module__ = "pytest"


class PytestCacheWarning(PytestWarning):
    """`warning_types.py` l. 27-30."""

    __module__ = "pytest"


class PytestConfigWarning(PytestWarning):
    """`warning_types.py` l. 34-37."""

    __module__ = "pytest"


class PytestCollectionWarning(PytestWarning):
    """`warning_types.py` l. 41-44."""

    __module__ = "pytest"


class PytestDeprecationWarning(PytestWarning, DeprecationWarning):
    """`warning_types.py` l. 47-50 — a ``PytestWarning`` **and** a ``DeprecationWarning``."""

    __module__ = "pytest"


class PytestRemovedIn9Warning(PytestDeprecationWarning):
    """`warning_types.py` l. 53-56."""

    __module__ = "pytest"


class PytestExperimentalApiWarning(PytestWarning, FutureWarning):
    """`warning_types.py` l. 60-67 — a ``PytestWarning`` **and** a ``FutureWarning``."""

    __module__ = "pytest"


class PytestReturnNotNoneWarning(PytestWarning):
    """`warning_types.py` l. 75-82."""

    __module__ = "pytest"


class PytestUnknownMarkWarning(PytestWarning):
    """`warning_types.py` l. 86-92."""

    __module__ = "pytest"


class PytestUnraisableExceptionWarning(PytestWarning):
    """`warning_types.py` l. 96-104."""

    __module__ = "pytest"


class PytestUnhandledThreadExceptionWarning(PytestWarning):
    """`warning_types.py` l. 108-114."""

    __module__ = "pytest"


class PytestFDWarning(PytestWarning):
    """`warning_types.py` l. 138-141."""

    __module__ = "pytest"


class WarningsChecker(_WarningsRecorder):
    """``pytest.warns()`` — a :class:`rustest._warnings.WarningsRecorder` that also asserts.

    Port of `_pytest/recwarn.py::WarningsChecker` (pytest 8.4.2, l. 255-357).

    **Subclassing the recorder is pytest's own arrangement** (l. 256:
    ``class WarningsChecker(WarningsRecorder)``) and it is what makes ``warns`` and
    ``recwarn`` the same object with the same API. Phase 4 Task 1c unified them; before it,
    ``warns`` carried a private ``catch_warnings`` of its own and ``recwarn`` did not exist,
    so "a recorded warning" had two independent definitions and only one of them had a
    ``.list``, a ``.pop()`` or a ``.clear()``.

    One consequence is visible to callers and is the pytest-correct direction:
    ``with warns(...) as rec`` binds the **recorder**, not the raw list. Everything the list
    supported still works (``len(rec)``, ``rec[0].message``, iteration), and ``rec.list`` /
    ``rec.pop(SomeWarning)`` work in addition.

    **The assertion half was rustest's own until the Phase 4 final polish wave, and it had a
    false green in it.** ``expected_warning`` defaulted to ``None``, and ``None`` was read as
    "assert nothing" — so::

        with pytest.warns():
            some_function_that_warns_about_nothing()

    passed unconditionally. pytest's default is ``Warning`` (l. 259 and the ``warns``
    signature at l. 224), i.e. *some* warning must be emitted, and that spelling is common in
    the wild precisely because a caller who does not care which warning still cares that one
    happened. Four further behaviours came back with the port:

    * **categories are validated** as ``Warning`` subclasses, with pytest's message
      (``exceptions must be derived from Warning, not %s``), instead of failing later inside
      ``issubclass``;
    * **``BaseException`` passes straight through** (l. 297-305): ``pytest.skip()``,
      ``pytest.fail()``, ``pytest.exit()`` and Ctrl-C inside a ``warns`` block must not be
      replaced by "DID NOT WARN". The old code returned early for *any* exception, which
      swallowed the check for a plain ``Exception`` too — pytest deliberately still fails a
      block that raised a normal exception without warning;
    * **unmatched warnings are re-emitted** on exit (l. 328-338, pytest 8.0+), so a warning
      the block raised but the assertion did not claim reaches the enclosing filter stack
      rather than being silently eaten by the recorder;
    * **the message shape is pytest's**, ``DID NOT WARN. No warnings of type ... were
      emitted.`` with the ``pformat``-ed list of what *was* emitted, and a distinct wording
      for "the category matched but the regex did not" — which is the more common failure and
      the one the old single message could not distinguish.
    """

    def __init__(
        self,
        expected_warning: type[Warning] | tuple[type[Warning], ...] = Warning,
        match_expr: str | Pattern[str] | None = None,
    ) -> None:
        super().__init__()

        # Every check below is "unnecessary" to a type checker and load-bearing at runtime:
        # the annotation says `type[Warning]`, the argument comes from user test code, and
        # `pytest.warns(ValueError)` is the mistake this exists to name (`recwarn.py`
        # l. 267-277). pytest carries the same runtime validation under the same annotation.
        msg = "exceptions must be derived from Warning, not %s"
        if isinstance(expected_warning, tuple):
            for exc in expected_warning:
                if not issubclass(exc, Warning):  # pyright: ignore[reportUnnecessaryIsInstance]
                    raise TypeError(msg % type(exc))
            expected_warning_tup = expected_warning
        elif isinstance(expected_warning, type) and issubclass(  # pyright: ignore[reportUnnecessaryIsInstance]
            expected_warning, Warning
        ):
            expected_warning_tup = (expected_warning,)
        else:
            raise TypeError(msg % type(expected_warning))

        self.expected_warning: tuple[type[Warning], ...] = expected_warning_tup
        self.match_expr: str | Pattern[str] | None = match_expr

    def matches(self, warning: warnings.WarningMessage) -> bool:
        """Category **and** regex, as one predicate — l. 284-288."""
        return issubclass(warning.category, self.expected_warning) and bool(
            self.match_expr is None or re.search(self.match_expr, str(warning.message))
        )

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        super().__exit__(exc_type, exc_val, exc_tb)

        __tracebackhide__ = True

        # BaseExceptions like pytest.{skip,fail,xfail,exit} or Ctrl-C within pytest.warns
        # should *not* trigger "DID NOT WARN" and get suppressed when the warning doesn't
        # happen. Control-flow exceptions should always propagate. (`Exit` is an `Exception`,
        # not a `BaseException`, for some reason -- pytest's own comment.)
        if exc_val is not None and (
            not isinstance(exc_val, Exception) or isinstance(exc_val, Exit)
        ):
            return

        def found_str() -> str:
            return pformat([record.message for record in self], indent=2)

        try:
            if not any(issubclass(w.category, self.expected_warning) for w in self):
                _rustest_fail(
                    f"DID NOT WARN. No warnings of type {self.expected_warning} were emitted.\n"
                    + f" Emitted warnings: {found_str()}."
                )
            elif not any(self.matches(w) for w in self):
                _rustest_fail(
                    f"DID NOT WARN. No warnings of type {self.expected_warning} matching the "
                    + f"regex were emitted.\n Regex: {self.match_expr}\n"
                    + f" Emitted warnings: {found_str()}."
                )
        finally:
            # Whether or not any warnings matched, we want to re-emit all unmatched warnings.
            for w in self:
                if not self.matches(w):
                    warnings.warn_explicit(
                        message=w.message,
                        category=w.category,
                        filename=w.filename,
                        lineno=w.lineno,
                        module=w.__module__,
                        source=w.source,
                    )

            # Currently in Python it is possible to pass other types than an `str` message
            # when creating `Warning` instances, however this causes an exception when
            # `warnings.filterwarnings` is used to filter those warnings. See
            # https://github.com/python/cpython/issues/103577. While this can be considered a
            # bug in CPython, we put guards in pytest as the error message produced without
            # this check in place is confusing (#10865).
            for w in self:
                if type(w.message) is not UserWarning:
                    # If the warning was of an incorrect type then `warnings.warn()` creates a
                    # UserWarning. Any other warning must have been specified explicitly.
                    continue
                if not w.message.args:
                    # UserWarning() without arguments must have been specified explicitly.
                    continue
                msg = w.message.args[0]
                if isinstance(msg, str):
                    continue
                raise TypeError(
                    f"Warning must be str or Warning, got {msg!r} (type {type(msg).__name__})"
                )


def warns(
    expected_warning: type[Warning] | tuple[type[Warning], ...] = Warning,
    *args: Any,
    match: str | Pattern[str] | None = None,
    **kwargs: Any,
) -> Any:
    r"""Assert that the block (or the call) raises a warning of the given class.

    Port of `_pytest/recwarn.py::warns` (pytest 8.4.2, l. 222-252).

    **The default is ``Warning``, not ``None``.** ``pytest.warns()`` with no argument asserts
    that *something* was warned; rustest read the bare call as "assert nothing" and passed
    unconditionally. See :class:`WarningsChecker` for the rest of what came back with the port.

    **The callable form is pytest's second signature**, not a convenience::

        pytest.warns(UserWarning, f, 1, 2)      # runs f(1, 2), returns its value

    It is the form the standard library's own ``assertWarns`` heritage put into a lot of
    suites, and it is why ``*args``/``**kwargs`` exist on this signature at all. Passing
    keyword arguments *without* a callable is pytest's ``TypeError`` with its "Use
    context-manager form instead?" hint, because that shape is almost always a typo for
    ``match=``.

    Note the asymmetry pytest carries and this port keeps: the callable form does **not**
    forward ``match``. ``kwargs`` there belong to the function being called.
    """
    __tracebackhide__ = True
    if not args:
        if kwargs:
            argnames = ", ".join(sorted(kwargs))
            raise TypeError(
                f"Unexpected keyword arguments passed to pytest.warns: {argnames}"
                + "\nUse context-manager form instead?"
            )
        return WarningsChecker(expected_warning, match_expr=match)
    else:
        func = args[0]
        if not callable(func):
            raise TypeError(f"{func!r} object (type: {type(func)}) must be callable")
        with WarningsChecker(expected_warning):
            return func(*args[1:], **kwargs)


def deprecated_call(func: Any = None, *args: Any, **kwargs: Any) -> Any:
    """Assert a ``DeprecationWarning``, ``PendingDeprecationWarning`` **or ``FutureWarning``**.

    Port of `_pytest/recwarn.py::deprecated_call` (pytest 8.4.2, l. 199-219).

    ``FutureWarning`` is the third member and was missing. It is not an edge case: the whole
    point of ``FutureWarning`` is a deprecation aimed at *end users* rather than developers,
    so a library that chose it — numpy and pandas both do, extensively — had its
    ``deprecated_call()`` assertions fail under rustest for warning about the right thing in
    the documented way. Note also that ``PytestExperimentalApiWarning`` is a ``FutureWarning``
    subclass, so pytest's own experimental-API warnings are caught by this too.

    The callable form (``deprecated_call(f, 1, 2)``) is pytest's other signature and simply
    prepends *func* to ``args`` before handing the whole thing to :func:`warns`.
    """
    __tracebackhide__ = True
    if func is not None:
        args = (func, *args)
    return warns((DeprecationWarning, PendingDeprecationWarning, FutureWarning), *args, **kwargs)


def importorskip(
    modname: str,
    minversion: str | None = None,
    reason: str | None = None,
    *,
    exc_type: type[ImportError] | None = None,
) -> Any:
    """Port of `_pytest/outcomes.py::importorskip` (pytest 8.4.2, l. 208-317).

    Rewritten from a paraphrase into a port in Phase 4 Task 1c, because the paraphrase's
    one structural difference cost two whole suites. **The skip it raises must be a
    module-level skip.** pytest's is ``Skipped(reason, allow_module_level=True)`` (l. 285 and
    l. 313); rustest called its own ``skip()``, which defaults the flag to ``False``, so the
    overwhelmingly common shape::

        np = pytest.importorskip("numpy")   # at module scope

    raised a *non*-module-level skip during collection.  That is a collection error, and a
    collection error aborts the session -- Pillow and FastAPI both ended at 0 tests
    collected (Task 1b sweep, §5 M7).

    Four other differences from the paraphrase, all of them pytest's behaviour:

    * ``exc_type`` defaults to ``None``, not ``ImportError``.  ``None`` means "catch
      ``ImportError`` **and** warn if what we caught was not a ``ModuleNotFoundError``" --
      pytest's #11523 deprecation, where a module that exists but raises on import looks
      identical to one that is absent.  Passing ``exc_type=ImportError`` explicitly is how a
      caller silences that, and it could not be expressed at all while ``None`` was
      unrepresentable.
    * the warning is raised **outside** the ``catch_warnings`` block, as pytest is careful to
      do (l. 258-259, 299-300): raising it inside would have it swallowed by the very
      ``simplefilter("ignore")`` that is there to suppress ``ImportWarning`` from namespace
      directories.
    * the module comes from ``sys.modules[modname]`` after ``__import__``, not from
      ``importlib.import_module``'s return value.  For a dotted name those differ:
      ``__import__("a.b")`` returns ``a``, and pytest's ``sys.modules`` lookup is what makes
      ``pytest.importorskip("os.path")`` return ``os.path``.  (``import_module`` also returns
      the leaf, so this direction is unchanged -- it is written pytest's way so the two
      cannot drift.)
    * a ``minversion`` miss and a missing ``__version__`` are **one** branch with one message
      (``module 'x' has __version__ None, required is: '1.0'``), and it ignores ``reason``.
      The paraphrase had three branches, two messages, and let ``reason`` override them.

    ``packaging.version.Version`` is imported lazily inside the ``minversion`` branch, as
    pytest does (l. 309-310), so the common no-version call pays nothing for it.
    """
    import warnings

    __tracebackhide__ = True
    compile(modname, "", "eval")  # to catch syntaxerrors

    if exc_type is None:
        exc_type = ImportError
        warn_on_import_error = True
    else:
        warn_on_import_error = False

    skipped: _rustest_Skipped | None = None
    warning: Warning | None = None

    with warnings.catch_warnings():
        # Ignore ImportWarnings raised by a directory that shares the module's name but has
        # no `__init__.py` (pytest l. 274-277).
        warnings.simplefilter("ignore")

        try:
            __import__(modname)
        except exc_type as exc:
            if reason is None:
                reason = f"could not import {modname!r}: {exc}"
            skipped = _rustest_Skipped(reason, allow_module_level=True)

            if warn_on_import_error and not isinstance(exc, ModuleNotFoundError):
                lines = [
                    "",
                    f"Module '{modname}' was found, but when imported by pytest it raised:",
                    f"    {exc!r}",
                    "In pytest 9.1 this warning will become an error by default.",
                    "You can fix the underlying problem, or alternatively overwrite this"
                    + " behavior and silence this warning by passing exc_type=ImportError"
                    + " explicitly.",
                    "See https://docs.pytest.org/en/stable/deprecations.html"
                    + "#pytest-importorskip-default-behavior-regarding-importerror",
                ]
                # `PytestDeprecationWarning`, which is what pytest raises here (l. 302) --
                # NOT a bare `DeprecationWarning`. The distinction is the difference between
                # a project being able to silence pytest's own #11523 deprecation
                # (`-W ignore::pytest.PytestDeprecationWarning`) and having to silence every
                # DeprecationWarning in the process to do it. Because
                # `PytestDeprecationWarning` derives from `DeprecationWarning` as well,
                # everything that used to catch this still does.
                warning = PytestDeprecationWarning("\n".join(lines))

    if warning:
        warnings.warn(warning, stacklevel=2)
    if skipped:
        raise skipped

    mod = sys.modules[modname]
    if minversion is None:
        return mod
    verattr = getattr(mod, "__version__", None)
    # Imported lazily to improve start-up time, exactly as pytest does.
    from packaging.version import Version

    if verattr is None or Version(verattr) < Version(minversion):
        raise _rustest_Skipped(
            f"module {modname!r} has __version__ {verattr!r}, required is: {minversion!r}",
            allow_module_level=True,
        )
    return mod


def install_pytest_stubs() -> None:
    """Install the ``_pytest`` stub modules, so ``from _pytest.config import Config`` and
    similar imports load (each stub warns that ``_pytest`` is pytest's private API).

    Does nothing when the real ``_pytest`` package is already imported, i.e. when real
    pytest is the runner.
    """
    if "_pytest" in sys.modules and hasattr(sys.modules["_pytest"], "__path__"):
        return

    from rustest import _pytest_stub

    sys.modules["_pytest"] = _pytest_stub
    sys.modules["_pytest.monkeypatch"] = _pytest_stub.monkeypatch
    sys.modules["_pytest.config"] = _pytest_stub.config
    sys.modules["_pytest.outcomes"] = _pytest_stub.outcomes
    sys.modules["_pytest.nodes"] = _pytest_stub.nodes
    sys.modules["_pytest.mark"] = _pytest_stub.mark
    sys.modules["_pytest.mark.structures"] = _pytest_stub.mark.structures
    sys.modules["_pytest.assertion"] = _pytest_stub.assertion
    sys.modules["_pytest.assertion.rewrite"] = _pytest_stub.assertion.rewrite
    sys.modules["_pytest.main"] = _pytest_stub.main
    _expose_vendored_py()


def _expose_vendored_py() -> None:
    """Make ``import py`` work while ``_pytest`` is the stub.

    pytest ships ``py.py``, a two-line compat module whose whole body is
    ``import _pytest._py.error`` / ``import _pytest._py.path``; the vendored ``py.path.local``
    behind ``tmpdir`` and ``tmpdir_factory`` lives there.  With the stub standing in for
    ``_pytest`` that import has nowhere to look, and every ``tmpdir`` test errors.  So the
    real package's ``_py`` directory -- stdlib-only modules -- is registered as the stub's
    ``_pytest._py``, found by path without importing real ``_pytest`` (which would pull its
    internals in beside the stubs).  Without pytest installed there is nothing to expose, and
    ``import py`` is the real ``py`` library or nothing.
    """
    import importlib.machinery
    import types

    spec = importlib.machinery.PathFinder.find_spec("_pytest")
    if spec is None or not spec.submodule_search_locations:
        return
    package = types.ModuleType("_pytest._py")
    package.__path__ = [os.path.join(where, "_py") for where in spec.submodule_search_locations]
    sys.modules["_pytest._py"] = package
    from rustest import _pytest_stub

    setattr(_pytest_stub, "_py", package)  # `import _pytest._py.error as e` walks attributes


# Module-level version to match pytest
__version__ = "rustest-compat"

# Cache for dynamically generated stub classes
_dynamic_stubs: dict[str, type] = {}


def __getattr__(name: str) -> Any:
    """
    Dynamically provide stub classes for any pytest attribute not explicitly defined.

    This allows pytest plugins (like pytest_asyncio) to import any pytest internal
    without errors, while these remain non-functional stubs.

    This is the recommended Python 3.7+ way to handle "catch-all" module imports.
    """
    if name == "FixtureRequest":
        # The class of every `request` a test receives. Imported here rather than at the
        # top because `_worker` imports this module at its own top level.
        from rustest._worker import SubRequest

        return SubRequest

    # Check if we've already created this stub
    if name in _dynamic_stubs:
        return _dynamic_stubs[name]

    # Don't intercept private attributes or special methods
    if name.startswith("_"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    # A name ending in "Warning" is a warning CATEGORY, and a category that is not a
    # `Warning` subclass is unusable: `warnings.simplefilter("error", cat)` raises
    # `TypeError: category must be a Warning subclass`, `warnings.warn(cat("..."))` raises,
    # and `issubclass(record.category, cat)` can never be true. pytest's own thirteen are
    # defined explicitly above (`PytestWarning` and friends); this arm covers a name some
    # plugin reaches for that pytest has and this shim does not yet enumerate, so it is at
    # least *shaped* like the thing it stands in for. Everything else keeps the inert
    # `object` stub, which is right for a name that is only ever imported.
    bases: tuple[type, ...] = (PytestWarning,) if name.endswith("Warning") else ()

    # Create a stub class dynamically
    def stub_init(self: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def stub_repr(self: Any) -> str:
        return f"<{name} (rustest compat stub)>"

    members: dict[str, Any] = {}
    if not bases:
        members["__init__"] = stub_init
        members["__repr__"] = stub_repr

    members["__doc__"] = (
        f"Dynamically generated stub for pytest.{name}.\n\n"
        f"NOT FUNCTIONAL in rustest pytest-compat mode. This stub exists\n"
        f"to allow pytest plugins to import without errors."
    )
    members["__module__"] = __name__
    # pytest's generic classes (`FixtureDef`, `CallInfo`, `RaisesExc`, ...) are annotated
    # subscripted; the stub must not fail the annotation it stands in for.
    members["__class_getitem__"] = classmethod(GenericAlias)
    stub_class = type(name, bases, members)

    # Cache it so subsequent imports get the same class
    _dynamic_stubs[name] = stub_class
    return stub_class
