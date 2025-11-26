"""Public Python API for rustest."""

from __future__ import annotations

from . import decorators
from .approx import approx
from .cli import main
from .reporting import RunReport, TestResult
from .core import run

# Re-export fixture types for type annotations
from .builtin_fixtures import Cache as Cache
from .builtin_fixtures import CaptureFixture as CaptureFixture
from .builtin_fixtures import CaptureResult as CaptureResult
from .builtin_fixtures import LogCaptureFixture as LogCaptureFixture
from .builtin_fixtures import LogRecord as LogRecord
from .builtin_fixtures import MockerFixture as MockerFixture
from .builtin_fixtures import MonkeyPatch as MonkeyPatch
from .builtin_fixtures import TmpDirFactory as TmpDirFactory
from .builtin_fixtures import TmpPathFactory as TmpPathFactory
from .compat.pytest import FixtureRequest as FixtureRequest

# Re-export decorator utility types
from .decorators import ExceptionInfo as ExceptionInfo
from .decorators import MarkDecorator as MarkDecorator
from .decorators import ParameterSet as ParameterSet
from .decorators import RaisesContext as RaisesContext

# Re-export reporting types
from .reporting import CollectionError as CollectionError

fixture = decorators.fixture
mark = decorators.mark
parametrize = decorators.parametrize
raises = decorators.raises
skip = decorators.skip  # Function version that raises Skipped
skip_decorator = decorators.skip_decorator  # Decorator version (use via @mark.skip)
fail = decorators.fail
Failed = decorators.Failed
Skipped = decorators.Skipped
XFailed = decorators.XFailed
xfail = decorators.xfail

__all__ = [
    # Exception types
    "Failed",
    "Skipped",
    "XFailed",
    # Reporting types
    "CollectionError",
    "RunReport",
    "TestResult",
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
    "xfail",
    # Entry points
    "main",
    "run",
]
