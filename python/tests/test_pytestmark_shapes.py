"""Every ``pytestmark`` shape pytest accepts, and what rustest does with it.

Oracle: `_pytest/mark/structures.py::get_unpacked_marks` (pytest 8.4.2, l. 407-437) and
``normalize_mark_list`` (l. 440-454).  Two rules there decide everything below:

* the attribute is unpacked **only if it is a `list`** — a *tuple* is appended whole and
  then fails ``normalize_mark_list`` with ``TypeError: got (...) instead of Mark``;
* every entry must expose a ``Mark`` (directly or via ``.mark``); anything else is a
  collection error, not a silently dropped mark.

The shapes were **measured**: `scratchpad/probe/pm` holds 15 one-file modules run under
pytest and then under flagless rustest (Phase 4 Task 1 report §3).  Three diverged, all for
the same root cause — a ``mark.<name>`` surface that is a *method* or returns a bare closure
has no ``.name``/``.args``/``.kwargs``, so it is not a mark when used as a **value**:

    pytestmark = pytest.mark.asyncio            # bound method   -> refused the whole file
    pytestmark = pytest.mark.asyncio(scope=..)  # inner closure  -> refused the whole file
    pytestmark = pytest.mark.parametrize(..)    # inner closure  -> refused the whole file

The first is Member Designer's shape: four modules use it, and because
``pytest_runtestloop`` raises ``Interrupted`` before the first item, four collection errors
meant **none** of that suite's 6 132 tests ran.  ``asyncio`` was the one standard mark that
kept its method form after #136/#137 converted ``skipif``/``xfail``/``usefixtures``; this
module pins it in the same family.

``parametrize`` remains a documented limitation — see
``test_module_level_parametrize_is_still_refused``.
"""

from __future__ import annotations

from typing import Any

import pytest

from rustest import mark as native_mark
from rustest.decorators import BareOrFactoryMark, MarkDecorator


def _spec(entry: Any) -> tuple[str, tuple[Any, ...], dict[str, Any]]:
    """What ``_v2_worker::_spec_from_pytestmark`` reads off one entry."""
    return (entry.name, tuple(entry.args), dict(entry.kwargs))


def _marks(obj: Any) -> list[dict[str, Any]]:
    return getattr(obj, "__rustest_marks__", [])


# ------------------------------------------------------------------ asyncio as a value


def test_bare_asyncio_is_a_mark_value() -> None:
    """``pytestmark = pytest.mark.asyncio`` — Member Designer's four modules."""
    assert isinstance(native_mark.asyncio, BareOrFactoryMark)
    assert _spec(native_mark.asyncio) == ("asyncio", (), {})


def test_called_asyncio_is_a_mark_value() -> None:
    """``pytestmark = pytest.mark.asyncio(loop_scope="module")`` returns a mark, not a closure."""
    entry = native_mark.asyncio(loop_scope="module")
    assert isinstance(entry, MarkDecorator)
    assert _spec(entry) == ("asyncio", (), {"loop_scope": "module"})


def test_asyncio_called_with_no_arguments_is_a_mark_value() -> None:
    entry = native_mark.asyncio()
    assert _spec(entry) == ("asyncio", (), {})


# -------------------------------------------------------- asyncio as a decorator, unchanged


def test_bare_asyncio_still_decorates_a_function() -> None:
    async def victim() -> None:
        pass

    decorated = native_mark.asyncio(victim)

    assert decorated is victim
    assert _marks(victim) == [{"name": "asyncio", "args": (), "kwargs": {}}]


def test_called_asyncio_still_decorates_a_function() -> None:
    async def victim() -> None:
        pass

    decorated = native_mark.asyncio(loop_scope="module", timeout=5)(victim)

    assert decorated is victim
    assert _marks(victim) == [
        {"name": "asyncio", "args": (), "kwargs": {"loop_scope": "module", "timeout": 5}}
    ]


def test_called_asyncio_still_propagates_into_a_class() -> None:
    """v1 behaviour kept: a class decoration also marks each async method, so the
    method carries ``loop_scope`` even when read without its owner."""

    @native_mark.asyncio(loop_scope="class")
    class TestBox:
        async def test_one(self) -> None:
            pass

    assert _marks(TestBox)[0]["name"] == "asyncio"
    assert _marks(TestBox.test_one)[0]["kwargs"] == {"loop_scope": "class"}


def test_bare_asyncio_propagates_into_a_class_too() -> None:
    @native_mark.asyncio
    class TestBox:
        async def test_one(self) -> None:
            pass

    assert _marks(TestBox)[0]["name"] == "asyncio"
    assert _marks(TestBox.test_one)[0]["name"] == "asyncio"


def test_asyncio_validation_still_fires_at_decoration() -> None:
    with pytest.raises(ValueError, match="Invalid loop_scope"):
        native_mark.asyncio(loop_scope="sesion")
    with pytest.raises(ValueError, match="Invalid scope"):
        native_mark.asyncio(scope="sesion")
    with pytest.raises(TypeError, match="timeout must be a number"):
        native_mark.asyncio(timeout="fast")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timeout must be positive"):
        native_mark.asyncio(timeout=0)


# ------------------------------------------------------------------ the rest of the family


@pytest.mark.parametrize(
    "name",
    ["skipif", "xfail", "usefixtures", "asyncio", "slow", "smoke"],
)
def test_every_bare_mark_surface_answers_the_three_attributes(name: str) -> None:
    """``_spec_from_pytestmark`` needs ``.name``/``.args``/``.kwargs`` off the *uncalled*
    object; pytest's uncalled ``MarkDecorator`` answers the same three empty values."""
    entry = getattr(native_mark, name)
    assert _spec(entry) == (name, (), {})


def test_module_level_parametrize_is_still_refused() -> None:
    """**Documented limitation.** pytest accepts ``pytestmark =
    pytest.mark.parametrize("x", [1, 2])`` and parametrizes every test in the module;
    rustest's ``parametrize`` is decoration-time metadata written onto one function, so the
    value it returns is a plain closure and the file is refused at collection with
    ``malformed pytestmark entry``.  Measured: pytest ``2 passed``, rustest ``1 error``.
    None of the four real-world suites uses the shape, so it is recorded rather than built;
    closing it means applying a module-level parametrize spec inside ``_collect_function``.
    """
    entry = native_mark.parametrize("x", [1, 2])
    assert not hasattr(entry, "name")


def test_called_skip_is_a_mark_value() -> None:
    """``pytestmark = pytest.mark.skip(reason=...)`` — matplotlib's shape.

    The compat surface routes ``skip`` to its own decorator so v1's Rust collector can read
    ``__rustest_skip__``; the *called* form returned that function's inner closure, which
    answers no ``.name``/``.args``/``.kwargs``, so the module was refused with ``malformed
    pytestmark entry``. It is now a ``SkipMarkDecorator`` — a real ``MarkDecorator`` whose
    ``__call__`` writes the attribute instead of a second mark entry.
    """
    from rustest.compat import pytest as compat_pytest
    from rustest.decorators import SkipMarkDecorator

    entry = compat_pytest.mark.skip(reason="later")
    assert isinstance(entry, SkipMarkDecorator)
    assert _spec(entry) == ("skip", (), {"reason": "later"})


def test_bare_skip_is_still_a_mark_value_and_still_decorates() -> None:
    from rustest.compat import pytest as compat_pytest

    assert _spec(compat_pytest.mark.skip) == ("skip", (), {})

    def victim() -> None:
        pass

    decorated = compat_pytest.mark.skip(reason="why")(victim)
    assert decorated is victim
    assert victim.__rustest_skip__ == "why"


def test_a_decorated_skip_records_exactly_one_skip() -> None:
    """Writing ``__rustest_skip__`` *and* a ``__rustest_marks__`` entry would make
    ``_mark_specs`` report the same skip twice."""
    from rustest.compat import pytest as compat_pytest

    def victim() -> None:
        pass

    _ = compat_pytest.mark.skip(reason="once")(victim)
    assert _marks(victim) == []
    assert victim.__rustest_skip__ == "once"
