"""``WarningsRecorder`` — the warning-capture machinery ``recwarn`` and ``warns`` share.

Port of `_pytest/recwarn.py::WarningsRecorder` (pytest 8.4.2, l. 168-255).

It lives in its own module for the reason pytest keeps it in one place: ``recwarn`` and
``pytest.warns`` are the *same* recorder, and pytest expresses that literally --
``class WarningsChecker(WarningsRecorder)`` (l. 258). Two independent implementations
would be free to disagree about what "a recorded warning" is, and a suite that uses both
would see two different objects for one concept.

**MECHANISM M5 of the Phase 4 Task 1b sweep.** ``recwarn`` was listed in the worker's
``UNSUPPORTED_BUILTIN_FIXTURES`` on the recorded grounds that it "needs a warnings channel,
which the v2 wire does not have". That reasoning was wrong, and usefully so: ``recwarn``
captures warnings **in-process**, inside the test's own call phase, and never has to tell
the orchestrator anything. What needs a wire is *reporting* warnings in the summary, which
is a different feature. attrs' ``tests/test_packaging.py`` reads ``recwarn.list`` and cost
4 tests to the gap.
"""

from __future__ import annotations

import warnings
from types import TracebackType
from typing import Iterator


class WarningsRecorder(warnings.catch_warnings):
    """Records warnings raised inside its ``with`` block.

    A ``warnings.catch_warnings`` subclass, exactly as pytest's is, so entering it installs
    a real filter stack and leaving it restores the previous one. Each recorded entry is a
    :class:`warnings.WarningMessage`.

    ``simplefilter("always")`` on entry is pytest's (l. 236): without it Python's
    once-per-location ``__warningregistry__`` de-duplication makes the second run of the
    same test record nothing, which is the classic "passes alone, fails in the suite"
    warning bug. The ``recwarn`` *fixture* then relaxes it to ``"default"``, which is
    pytest's own choice at `recwarn.py` l. 39.
    """

    def __init__(self) -> None:
        super().__init__(record=True)
        self._entered = False
        self._list: list[warnings.WarningMessage] = []

    @property
    def list(self) -> list[warnings.WarningMessage]:
        """The recorded warnings, in the order they were raised."""
        return self._list

    def __getitem__(self, i: int) -> warnings.WarningMessage:
        return self._list[i]

    def __iter__(self) -> Iterator[warnings.WarningMessage]:
        return iter(self._list)

    def __len__(self) -> int:
        return len(self._list)

    def pop(self, cls: type[Warning] = Warning) -> warnings.WarningMessage:
        """Remove and return the first recorded warning matching *cls*.

        Port of l. 206-223, **including the tie-break**, which is the part a paraphrase gets
        wrong: an *exact* category match wins immediately, and among inexact matches the one
        chosen is the one whose category is not a subclass of an earlier candidate's -- i.e.
        the most general match, not the first. So ``recwarn.pop(Warning)`` over a
        ``[DeprecationWarning, UserWarning]`` list does not simply hand back element 0.

        Raises ``AssertionError`` when nothing matches, with pytest's message.
        """
        best_idx: int | None = None
        for i, w in enumerate(self._list):
            if w.category == cls:
                return self._list.pop(i)  # exact match, stop looking
            if issubclass(w.category, cls) and (
                best_idx is None or not issubclass(w.category, self._list[best_idx].category)
            ):
                best_idx = i
        if best_idx is not None:
            return self._list.pop(best_idx)
        __tracebackhide__ = True
        raise AssertionError(f"{cls!r} not found in warning list")

    def clear(self) -> None:
        """Empty the recorded list **in place** (l. 225-227)."""
        self._list[:] = []

    # `catch_warnings.__enter__` is typed as returning `list[WarningMessage] | None`, and
    # pytest's override returns `Self` -- a genuine LSP violation that pytest carries too
    # (its own annotation is `-> Self`, with `catch_warnings` untyped-generic underneath).
    # Reproducing the *return value* is the compatibility requirement; the checker's
    # complaint is about a stdlib signature neither project controls.
    def __enter__(self) -> "WarningsRecorder":  # pyright: ignore[reportIncompatibleMethodOverride]
        """Port of l. 229-238.

        Returns ``self``, not the raw list ``catch_warnings(record=True)`` hands back --
        which is what makes ``with pytest.warns(...) as rec: rec.pop(...)`` work at all.
        Re-entry is a ``RuntimeError`` rather than a silently nested recorder.
        """
        if self._entered:
            __tracebackhide__ = True
            raise RuntimeError(f"Cannot enter {self!r} twice")
        recorded = super().__enter__()
        assert recorded is not None  # record=True guarantees a list
        self._list = recorded
        warnings.simplefilter("always")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Port of l. 240-255, including the reusability fix-up.

        ``catch_warnings`` does not reset its own ``_entered``, so pytest clears it here to
        let one recorder be entered again later. Reproduced, because a fixture that yields a
        recorder outlives the block that first entered it.
        """
        if not self._entered:
            __tracebackhide__ = True
            raise RuntimeError(f"Cannot exit {self!r} without entering first")
        super().__exit__(exc_type, exc_val, exc_tb)
        self._entered = False
