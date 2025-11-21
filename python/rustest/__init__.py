"""Public Python API for rustest."""

from __future__ import annotations

from . import decorators
from .approx import approx
from .cli import main
from .reporting import RunReport, TestResult
from .core import run

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
    "Failed",
    "RunReport",
    "Skipped",
    "TestResult",
    "XFailed",
    "approx",
    "fail",
    "fixture",
    "main",
    "mark",
    "parametrize",
    "raises",
    "run",
    "skip",
    "xfail",
]
