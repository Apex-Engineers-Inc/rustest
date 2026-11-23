"""The second skipping module -- see `test_alpha.py` for what the case is about.

Spelled with `importorskip` rather than a bare `skip(..., allow_module_level=True)` so the
case covers both spellings of the same mechanism: `_pytest/outcomes.py::importorskip` raises
`Skipped(reason, allow_module_level=True)` (l. 285, l. 313), which is the identical exception
the sibling raises by hand.

The module name is one that cannot exist, so this file's verdict does not depend on what is
installed -- the lesson `builtins/approx-numpy` records the hard way.
"""

import pytest

missing = pytest.importorskip("rustest_no_such_module_5b1e")


def test_never_collected():
    raise AssertionError("collection must never reach this function")
