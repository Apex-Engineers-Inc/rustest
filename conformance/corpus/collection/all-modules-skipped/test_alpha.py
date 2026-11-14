"""Every module in this case skips itself, which is the exit-5 shape.

`collection/module-level-skip` pins the *mixed* tree: two skipping files beside two that
collect, where the surviving tests keep the exit code at 0 and the skips are invisible to
`--collect-only`. This case pins the degenerate end of the same mechanism, which is a
different assertion and was untested: when **nothing** collects, pytest prints `2 skipped`
and exits **5** (`_pytest/main.py::wrap_session` -> `ExitCode.NO_TESTS_COLLECTED`), not 0.

The two halves interact, which is why this is worth a case of its own. A module-level skip
contributes a `skipped` to the tally but **no node id**, so a runner that counted "did
anything get collected?" from the tally rather than from the id list would answer "yes, two
things" and exit 0 -- green, on a run that executed nothing at all. That is the same class
of defect as the v1 zero-collected gap `collection/empty-suite` records, reached by a
different road: `empty-suite` has an empty tally AND no ids, this one has a non-empty tally
and no ids.

Probed on pytest 8.4.2: `2 skipped in 0.01s`, exit code 5.
"""

import pytest

pytest.skip("alpha is not for this platform", allow_module_level=True)


def test_never_collected():
    raise AssertionError("collection must never reach this function")
