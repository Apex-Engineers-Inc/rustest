"""The **known file** the coverage smoke case asserts a line set for.

Deliberately *not* a test module: it is the thing under measurement, so its executed lines
are a property of what the suite next door does rather than of how the runner collects. Every
construct in it exists to make one line-accounting question concrete, and the expected set is
pinned in `python/tests/test_v2_coverage.py`:

* a module-level constant and an import   -- import-time lines, which coverage.py counts
  because it starts before collection;
* a branch with an untaken arm            -- one line executed, one not;
* a function nobody calls                 -- its `def` runs at import, its body never does;
* a class body and a method               -- two separate code objects;
* a comprehension and a generator         -- code objects that are not plain functions;
* a `try`/`except` whose handler runs      -- the exception path;
* a conditional import guard              -- a line whose branch is never taken.
"""

from __future__ import annotations

import math

SCALE = 3


def clamp(value: int, ceiling: int) -> int:
    scaled = value * SCALE
    if scaled > ceiling:
        return ceiling
    return scaled


def never_called() -> str:
    return "unreachable from the suite"


def guarded(divisor: int) -> int | None:
    try:
        return 10 // divisor
    except ZeroDivisionError:
        return None


def doubled(values: list[int]) -> list[int]:
    return [value * 2 for value in values]


def counted(limit: int):
    for index in range(limit):
        yield index


class Shape:
    sides = 4

    def area(self, size: float) -> float:
        return math.pow(size, self.sides / 2)
