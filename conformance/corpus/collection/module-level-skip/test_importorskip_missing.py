"""`pytest.importorskip` at module scope, for a module that is not installed.

The overwhelmingly common shape of the same mechanism: pytest's `importorskip` raises
`Skipped(reason, allow_module_level=True)` (`_pytest/outcomes.py` l. 285), so an
uninstalled optional dependency skips the file rather than breaking the run.
"""

import pytest

missing = pytest.importorskip("rustest_no_such_module_9f3c")


def test_never_collected():
    raise AssertionError("collection must never reach this function")
