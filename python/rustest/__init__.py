"""Public Python API for rustest.

**Every public name is bound lazily** (PEP 562 module ``__getattr__``), and that is a
latency decision with a number behind it rather than a style preference.

``python -m rustest`` executes this module before ``__main__`` — a package is always
imported before its ``__main__`` submodule — so whatever is imported here is on the critical
path of *every* rustest invocation: a ``--collect-only``, a ``--report-json`` run, and
each of the N worker subprocesses a run spawns. Eagerly importing the whole API cost ~390 ms
above a bare interpreter on the reference machine (``rich`` alone ~230 ms of it, ``inspect``
via ``decorators`` ~30 ms), none of which a collect-only run uses.

The lazy binding is behaviour-preserving for users:

* ``from rustest import fixture`` triggers ``__getattr__("fixture")`` and gets the same
  object as before;
* ``import rustest; rustest.fixture`` likewise;
* ``dir(rustest)`` still lists everything, via :func:`__dir__`;
* a genuinely missing name still raises ``AttributeError`` with the standard wording.

Type checkers see the eager form through ``TYPE_CHECKING``, so ``basedpyright`` resolves
every export exactly as it did — which is also what keeps ``__all__`` honest, since a name
listed here but absent from :data:`_LAZY` is a type error rather than a runtime surprise.
``test_public_api_is_importable_and_lazy`` asserts both halves: every ``__all__`` entry
resolves, and importing this module pulls in neither ``rich`` nor ``rustest.core``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

# The one export that CANNOT be lazy, and the reason is structural rather than a cost.
#
# `approx` is both a public name and the name of the submodule that defines it. PEP 562's
# `__getattr__` is consulted **only when normal attribute lookup fails**, and importing
# `rustest.approx` — which `rustest/compat/pytest.py` does, and which any user writing
# `from rustest.approx import approx` does — binds the *module* into this package's
# `__dict__` as a side effect of the import system. From that moment normal lookup succeeds,
# `__getattr__` is never reached, and `rustest.approx` is the module: `approx([0.3])` raises
# `TypeError: 'module' object is not callable`.
#
# Binding it eagerly wins that race permanently, and costs nothing — `approx.py` imports only
# `collections.abc` and `typing`. `test_no_lazy_export_collides_with_a_submodule` fails if any
# future export is given the same name as a module file, which is the general form of this bug.
from .approx import approx as approx

if TYPE_CHECKING:
    from .builtin_fixtures import Cache as Cache
    from .builtin_fixtures import CaptureFixture as CaptureFixture
    from .builtin_fixtures import CaptureResult as CaptureResult
    from .builtin_fixtures import LogCaptureFixture as LogCaptureFixture
    from .builtin_fixtures import LogRecord as LogRecord
    from .builtin_fixtures import MockerFixture as MockerFixture
    from .builtin_fixtures import MonkeyPatch as MonkeyPatch
    from .builtin_fixtures import TmpDirFactory as TmpDirFactory
    from .builtin_fixtures import TmpPathFactory as TmpPathFactory
    from .cli import main as main
    from .compat.pytest import FixtureRequest as FixtureRequest
    from .core import run as run
    from .decorators import ExceptionInfo as ExceptionInfo
    from .decorators import Failed as Failed
    from .decorators import MarkDecorator as MarkDecorator
    from .decorators import ParameterSet as ParameterSet
    from .decorators import RaisesContext as RaisesContext
    from .decorators import Skipped as Skipped
    from .decorators import XFailed as XFailed
    from .decorators import fail as fail
    from .decorators import fixture as fixture
    from .decorators import mark as mark
    from .decorators import parametrize as parametrize
    from .decorators import raises as raises
    from .decorators import skip as skip
    from .decorators import skip_decorator as skip_decorator
    from .decorators import xfail as xfail

#: ``public name -> (submodule, attribute)``.  The submodule is relative to this package.
#:
#: ``skip`` and ``skip_decorator`` are two *different* objects in ``decorators`` and both are
#: exported: the first raises ``Skipped`` when called, the second is the decorator form used
#: via ``@mark.skip``.  Collapsing them was a real bug once, so they are listed side by side.
_LAZY: Final[dict[str, tuple[str, str]]] = {
    # Decorators and functions
    "fixture": ("decorators", "fixture"),
    "mark": ("decorators", "mark"),
    "parametrize": ("decorators", "parametrize"),
    "raises": ("decorators", "raises"),
    "skip": ("decorators", "skip"),
    "skip_decorator": ("decorators", "skip_decorator"),
    "fail": ("decorators", "fail"),
    "xfail": ("decorators", "xfail"),
    # Exception types
    "Failed": ("decorators", "Failed"),
    "Skipped": ("decorators", "Skipped"),
    "XFailed": ("decorators", "XFailed"),
    # Decorator utility types
    "ExceptionInfo": ("decorators", "ExceptionInfo"),
    "MarkDecorator": ("decorators", "MarkDecorator"),
    "ParameterSet": ("decorators", "ParameterSet"),
    "RaisesContext": ("decorators", "RaisesContext"),
    # (`approx` is deliberately absent: it is bound eagerly above — see the comment there.)
    #
    # `CollectionError`, `RunReport` and `TestResult` were exported here until Phase 4 Task 2.
    # They were `rustest.reporting`'s dataclasses over v1's `PyRunReport`, and there is no v2
    # object to repoint them at: the engine's report is a JSON document (schema v2, six
    # status buckets to v1's three), written by `--report-json` and described in
    # `src/engine/execute.rs`. Re-exporting a name backed by nothing is worse than removing it.
    # Fixture types
    "Cache": ("builtin_fixtures", "Cache"),
    "CaptureFixture": ("builtin_fixtures", "CaptureFixture"),
    "CaptureResult": ("builtin_fixtures", "CaptureResult"),
    "LogCaptureFixture": ("builtin_fixtures", "LogCaptureFixture"),
    "LogRecord": ("builtin_fixtures", "LogRecord"),
    "MockerFixture": ("builtin_fixtures", "MockerFixture"),
    "MonkeyPatch": ("builtin_fixtures", "MonkeyPatch"),
    "TmpDirFactory": ("builtin_fixtures", "TmpDirFactory"),
    "TmpPathFactory": ("builtin_fixtures", "TmpPathFactory"),
    "FixtureRequest": ("compat.pytest", "FixtureRequest"),
    # Entry points
    "main": ("cli", "main"),
    "run": ("core", "run"),
}

__all__ = [
    # Exception types
    "Failed",
    "Skipped",
    "XFailed",
    # Fixture types
    "Cache",
    "CaptureFixture",
    "CaptureResult",
    "FixtureRequest",
    "LogCaptureFixture",
    "LogRecord",
    "MockerFixture",
    "MonkeyPatch",
    "TmpDirFactory",
    "TmpPathFactory",
    # Decorator utility types
    "ExceptionInfo",
    "MarkDecorator",
    "ParameterSet",
    "RaisesContext",
    # Utility classes/functions
    "approx",
    # Decorators/functions
    "fail",
    "fixture",
    "mark",
    "parametrize",
    "raises",
    "skip",
    "skip_decorator",
    "xfail",
    # Entry points
    "main",
    "run",
]


def __getattr__(name: str) -> Any:
    """Resolve a public name on first use, then cache it in the module dict.

    The ``globals()[name] = value`` write is what makes this cost one dictionary miss per
    name for the whole process: after it, ordinary attribute lookup finds the binding and
    ``__getattr__`` is never called for that name again (PEP 562 only consults it when
    normal lookup fails).

    The ``AttributeError`` wording matches CPython's own so a typo reads identically to what
    an eagerly-populated module would have produced.
    """
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    from importlib import import_module

    value = getattr(import_module(f".{module_name}", __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """``dir(rustest)`` lists the public API whether or not it has been touched yet.

    Without this, tab completion in a REPL would show only the names somebody had already
    used — the one user-visible way a lazy module differs from an eager one.
    """
    return sorted(set(__all__) | set(globals()))
