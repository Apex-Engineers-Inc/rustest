"""A module that skips itself: `pytest.skip(..., allow_module_level=True)`.

pytest reports this as one `skipped` with **no node id** -- it never appears in
`--collect-only -q` -- and the session carries on to the other files. Before Phase 4
Task 1c rustest raised it as an unhandled exception during collection, which is a
collection error, which aborts the session: Pillow's six such modules cost all 4 036 of
its tests in the Task 1b sweep.
"""

import pytest

pytest.skip("this module is not for this platform", allow_module_level=True)


def test_never_collected():
    raise AssertionError("collection must never reach this function")
