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
skip = decorators.skip
fail = decorators.fail
Failed = decorators.Failed

__all__ = [
    "Failed",
    "RunReport",
    "TestResult",
    "approx",
    "fail",
    "fixture",
    "main",
    "mark",
    "parametrize",
    "raises",
    "run",
    "skip",
]
