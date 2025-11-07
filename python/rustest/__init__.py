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

__all__ = [
    "RunReport",
    "TestResult",
    "approx",
    "fixture",
    "main",
    "mark",
    "parametrize",
    "raises",
    "run",
    "skip",
]
