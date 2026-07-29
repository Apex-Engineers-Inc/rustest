"""Stub for ``_pytest.outcomes`` — **aliases**, not a parallel implementation.

``_pytest.outcomes`` is pytest's internal home for the outcome exceptions, and real suites
reach it: ``from _pytest.outcomes import Skipped`` appears in FastAPI-class projects, in
plugin code, and in any test that wants to catch a skip by type. The import surface therefore
has to survive. What it must **not** do is invent its own classes.

**Why every name here is an alias and not a class statement.** Until the Phase 4 v1 deletion
this module declared its own ``Failed(Exception)`` and ``Skipped(Exception)``, which were
different objects from the ones ``pytest.skip()`` / ``pytest.fail()`` actually raise
(:mod:`rustest.decorators`). Two divergences from pytest followed directly, both probed:

* ``from _pytest.outcomes import skip, Skipped`` then ``try: skip(...) except Exception:
  pass`` — pytest reports **SKIPPED** (its ``Skipped`` is a ``BaseException``, so an
  ``except Exception`` cannot swallow it); the old stub's ``Skipped`` *was* an ``Exception``,
  so rustest reported **PASSED**. A skip silently turned green.
* ``except Skipped:`` (imported from ``_pytest.outcomes``) wrapped around ``pytest.skip()`` —
  pytest reports **PASSED** (one class, so the ``except`` catches); rustest raised the
  *shim's* ``Skipped``, which the stub's ``except`` clause did not match, and reported
  **SKIPPED**.

Both are catch-path verdict swaps, and neither is reachable by an ``isinstance`` table on the
worker's side: the classification happens inside *user* code, before the worker ever sees the
exception. The only fix that closes both is identity — ``_pytest.outcomes.Skipped`` must
**be** ``rustest.decorators.Skipped``, the same object, so ``isinstance`` and ``except``
behave identically whichever import path a suite used.

``python/tests/test_exception_stubs.py`` pins the identity and both catch paths.
"""

import warnings

# The real outcome surface. Re-exported by identity: `_pytest.outcomes.Skipped is
# rustest.decorators.Skipped` is the invariant this module exists to hold. `fail`/`skip`/
# `xfail` come along for the same reason -- a suite that calls the internal `skip()` must
# raise the class a suite that calls `pytest.skip()` raises, or the first bullet above
# reappears through the function instead of through the class.
from rustest.decorators import (
    Failed as Failed,
    OutcomeException as OutcomeException,
    Skipped as Skipped,
    XFailed as XFailed,
    fail as fail,
    skip as skip,
    xfail as xfail,
)

# Show warning on import. Kept from the original stub: reaching into `_pytest` is still
# something a suite should migrate away from, and the warning is the only place that says so.
# It is emitted *after* the aliases are bound so a `catch_warnings` block around the import
# cannot leave the module half-initialised.
warnings.warn(
    "Importing from _pytest.outcomes is not recommended. "
    "Use pytest.fail() and pytest.skip() instead. "
    "These exceptions are provided for compatibility only.",
    DeprecationWarning,
    stacklevel=2,
)


# `_pytest/outcomes.py::_with_exception` (l. 92-98) sets these on the real module's helpers,
# so a plugin that reached for the internal module still finds `fail.Exception`.
# `rustest.decorators` already sets them on these very function objects (l. 2008-2010); the
# assertions below are the pin that says so, because a *rebinding* here would silently
# re-create the parallel-types bug this module was rewritten to remove.
assert fail.Exception is Failed  # type: ignore[attr-defined]
assert skip.Exception is Skipped  # type: ignore[attr-defined]
assert xfail.Exception is XFailed  # type: ignore[attr-defined]


# Pytest internal - outcome tuple for internal pytest use.
# This is not part of pytest's public API.
TEST_OUTCOME = (Failed, Skipped)
