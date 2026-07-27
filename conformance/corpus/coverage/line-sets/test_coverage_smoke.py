"""The coverage smoke suite: exercises `covered.py` in a way both runners must agree on.

Two gates read this directory and they ask different questions:

* the three conformance gates run it with **no coverage flags at all**, because pytest has no
  `--cov` without pytest-cov (which this repository deliberately does not install), so what
  they grade is that the suite itself collects and runs identically under pytest and rustest;
* `python/tests/test_v2_coverage.py` measures the same tree twice -- `rustest --cov` and
  `coverage run -m pytest` -- and asserts the executed line sets for `covered.py` are equal,
  and equal to a pinned literal.

The suite is written so its coverage of `covered.py` is *partial and specific*: `never_called`
is never called, and `clamp`'s ceiling arm is taken exactly once. A suite that covered
everything would pass the differential no matter what either tool did with an untaken branch.
"""

import covered


def test_clamp_scales():
    assert covered.clamp(2, 100) == 6


def test_clamp_hits_the_ceiling():
    assert covered.clamp(50, 10) == 10


def test_guarded_catches():
    assert covered.guarded(0) is None


def test_guarded_divides():
    assert covered.guarded(5) == 2


def test_doubled():
    assert covered.doubled([1, 2]) == [2, 4]


def test_counted():
    assert list(covered.counted(3)) == [0, 1, 2]


def test_shape_area():
    assert covered.Shape().area(3.0) == 9.0
