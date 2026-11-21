"""What hypothesis's pytest plugin (``_hypothesis_pytestplugin``) does to outcomes, done here.

pytest auto-loads that plugin wherever hypothesis is installed. rustest does not load pytest
plugins, so the parts that change what a test reports are ported, from the plugin as shipped
in hypothesis 6.140.0 to 6.170.15, and accepted in a later release only while the plugin's code
is unchanged (:func:`supported`, :func:`fingerprint`); otherwise the session goes to pytest
(``_fallback.autoload_reasons``).

Ported:

* the initialization window: ``_hypothesis_globals.in_initialization`` is raised when the plugin
  loads and lowered after the initial conftests import (:func:`start`, :func:`initialization`);
  a ``HypothesisSideeffectWarning`` there is a hand-off reason, since pytest reports it;
* ``pytest_runtest_call`` (:func:`around_call`): the usage errors for ``@example`` / ``@seed`` /
  ``@settings`` / ``@reproduce_failure`` on a test without ``@given``, and for a collected
  ``@composite`` strategy; the function-scoped-fixture health check; the ``differing_executors``
  suppression and per-node-id database key for a parametrized test; ``running_under_pytest``
  and ``pytest_shows_exceptiongroups``;
* ``pytest_collection_modifyitems``: the ``hypothesis`` mark on every hypothesis test, unittest
  ones included (:func:`is_test`, applied by the worker where it builds the test);
* ``pytest_addoption``: ``getoption`` answers the five options' defaults (the CLI hands a
  session that sets one to pytest, which applies them in ``pytest_configure``);
* ``pytest_ignore_collect``: a ``.hypothesis`` directory ``norecursedirs`` does not prune is a
  hand-off reason (the walk reports it), since pytest reports the plugin's warning;
* the ``@pytest.fixture``-on-``@given`` ``RuntimeError`` (``decorators.fixture``).

Not ported, all output only: the ``Hypothesis`` report section and statistics (hypothesis prints
its report to the test's captured stdout instead), the terminal summary and the patch file it
writes under ``.hypothesis/patches``, the report header, and a failing example's traceback
formatted through ``item.repr_failure``.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Final

#: The hypothesis releases whose plugin this module ports (``major.minor``, inclusive).
VERIFIED: Final = ((6, 140), (6, 170))
#: :func:`fingerprint` of the plugin in each verified release (6.140-141, 6.142-150,
#: 6.155-170): a later release whose plugin's code matches one is the same port.
FINGERPRINTS: Final = frozenset({"2cfcd8c60c506f12", "af79667689b1efc4", "375ab09d8570297f"})

_USAGE: Final = "Using `@%s` on a test without `@given` is completely pointless."
_DECORATOR_ATTRIBUTES: Final = (
    ("example", "hypothesis_explicit_examples"),
    ("seed", "_hypothesis_internal_use_seed"),
    ("settings", "_hypothesis_internal_settings_applied"),
    ("reproduce_example", "_hypothesis_internal_use_reproduce_failure"),
)


def supported(distribution: str) -> bool:
    """Is ``hypothesis-<version>`` (a dist-info stem) one whose plugin is ported here: a
    verified release, or a later one whose plugin has the same code (:data:`FINGERPRINTS`)?"""
    _, _, version = distribution.partition("-")
    try:
        key = tuple(int(part) for part in version.split(".")[:2])
    except ValueError:
        return False
    if key < VERIFIED[0]:
        return False
    if key <= VERIFIED[1]:
        return True
    import importlib.util

    spec = importlib.util.find_spec("_hypothesis_pytestplugin")
    try:
        with open(str(spec and spec.origin), encoding="utf-8") as handle:
            return fingerprint(handle.read()) in FINGERPRINTS
    except OSError:
        return False


def fingerprint(source: str) -> str:
    """The plugin's code without its words: its tokens bar comments and string literals, so
    a release that rewords a message or a docstring keeps the fingerprint (stable across
    Python 3.12-3.14, which all tokenize f-strings into parts)."""
    import hashlib
    import io
    import tokenize

    words = (tokenize.COMMENT, tokenize.NL, tokenize.STRING, tokenize.FSTRING_MIDDLE)
    tokens = [
        token.string if token.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER) else ""
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in words
    ]
    return hashlib.sha256("\x00".join(tokens).encode()).hexdigest()[:16]


def start() -> None:
    """The plugin's import: raise the initialization count (the conftests import next)."""
    if not os.environ.get("HYPOTHESIS_EXTEND_INITIALIZATION"):
        _globals().in_initialization += 1


@contextlib.contextmanager
def initialization(reasons: list[str]) -> Iterator[None]:
    """The initial conftests' import, inside the window :func:`start` opened, closed after
    as the plugin's ``pytest_sessionstart`` closes it. A ``HypothesisSideeffectWarning``
    shown there is pytest's to report (its summary counts it), so it is a hand-off *reason*;
    any other warning is shown as it would have been."""
    import warnings

    show = warnings.showwarning

    def record(message: Any, category: Any, *args: Any, **kwargs: Any) -> None:
        if getattr(category, "__name__", "") == "HypothesisSideeffectWarning":
            reasons.append(
                "rustest's hook runtime: hypothesis warns of a side effect while the conftests "
                + f"import ({message}), which pytest reports"
            )
        else:
            show(message, category, *args, **kwargs)

    warnings.showwarning = record
    try:
        yield
    finally:
        if warnings.showwarning is record:
            warnings.showwarning = show
        _globals().in_initialization -= 1


def _globals() -> Any:
    """``_hypothesis_globals``: stdlib-only, importable without importing hypothesis."""
    import _hypothesis_globals

    return _hypothesis_globals


def is_test(func: object) -> bool:
    """A ``@given`` test, which the plugin's ``pytest_collection_modifyitems`` marks."""
    if "hypothesis" not in sys.modules:
        return False
    from hypothesis import is_hypothesis_test

    return bool(is_hypothesis_test(func))


def ban_fixture(function: object) -> None:
    """The plugin's patch of ``FixtureFunctionMarker.__call__``."""
    if is_test(function):
        name = getattr(function, "__name__", repr(function))
        raise RuntimeError(
            f"Can't apply @pytest.fixture() to {name} because "
            + "it is already decorated with @hypothesis.given()"
        )


@contextlib.contextmanager
def around_call(
    obj: Any,
    nodeid: str,
    fixture_defs: Mapping[str, Sequence[Any]],
    parametrized: bool,
) -> Iterator[None]:
    """The plugin's ``pytest_runtest_call`` wrapper, around the test body.

    *obj* is the test as pytest's ``item.obj`` holds it (bound for a method); *fixture_defs*
    every fixture of the closure with its definitions, nearest last (``name2fixturedefs``);
    *parametrized*: the test has a ``parametrize`` mark.
    """
    if "hypothesis" not in sys.modules:
        yield
        return
    from hypothesis import core, is_hypothesis_test

    core.pytest_shows_exceptiongroups = True  # pytest >= 7.2
    core.running_under_pytest = True
    if not is_hypothesis_test(obj):
        from hypothesis.errors import InvalidArgument

        if getattr(obj, "is_hypothesis_strategy_function", False):
            raise InvalidArgument(
                f"{nodeid} is a function that returns a Hypothesis strategy, "
                + "but pytest has collected it as a test function.  This is useless "
                + "as the function body will never be executed.  To define a test "
                + "function, use @given instead of @composite."
            )
        for name, attribute in _DECORATOR_ATTRIBUTES:
            if hasattr(obj, attribute):
                raise InvalidArgument(_USAGE % (name,))
        yield
        return

    from hypothesis import HealthCheck, settings as Settings
    from hypothesis.internal.healthcheck import fail_health_check

    settings: Any = getattr(obj, "_hypothesis_internal_use_settings", Settings.default)
    fixture_params = False
    if not set(settings.suppress_health_check).issuperset(
        {HealthCheck.function_scoped_fixture, HealthCheck.differing_executors}
    ):
        argnames = frozenset(inspect.signature(obj).parameters)
        for argname, defs in fixture_defs.items():
            for definition in defs:
                fixture_params |= bool(definition.params)
            if argname in argnames and defs and defs[-1].scope == "function":
                fail_health_check(
                    settings, _fixture_message(nodeid, argname), HealthCheck.function_scoped_fixture
                )

    if fixture_params or parametrized:
        fn = getattr(obj, "__func__", obj)
        fn._hypothesis_internal_use_settings = Settings(
            parent=settings,
            suppress_health_check={HealthCheck.differing_executors}
            | set(settings.suppress_health_check),
        )
        obj.hypothesis.inner_test._hypothesis_internal_add_digest = nodeid.encode()
    yield


def _fixture_message(nodeid: str, argname: str) -> str:
    return (
        f"{nodeid!r} uses a function-scoped fixture {argname!r}."
        + "\n\n"
        + "Function-scoped fixtures are not reset between inputs "
        + "generated by `@given(...)`, which is often surprising and "
        + "can cause subtle test bugs."
        + "\n\n"
        + "If you were expecting the fixture to run separately "
        + "for each generated input, then unfortunately you "
        + "will need to find a different way to achieve your "
        + "goal (for example, replacing the fixture with a similar "
        + "context manager inside of the test)."
        + "\n\n"
        + "If you are confident that your test will work correctly "
        + "even though the fixture is not reset between generated "
        + "inputs, you can suppress this health check with "
        + "@settings(suppress_health_check=[HealthCheck.function_scoped_fixture]). "
        + "See "
        + "https://hypothesis.readthedocs.io/en/latest/reference/api.html#hypothesis.HealthCheck "
        + "for details."
    )
