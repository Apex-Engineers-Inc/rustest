"""The bare (uncalled) mark decorator forms — defects #136 and #137.

``@pytest.mark.skip`` and ``@pytest.mark.xfail`` written *without parentheses* are ordinary
pytest, and both were broken in rustest's shim in different ways:

* **#136** — ``_PytestMarkCompat.skip`` was a plain *method*, so the bare form applied the
  method itself to the test: the test function arrived as ``reason`` and the module
  attribute became ``skip_decorator``'s inner closure. The body was destroyed and no skip
  metadata was recorded.
* **#137** — ``mark.xfail`` / ``mark.skipif`` were *properties* returning bound methods, so
  the bare form bound the test function to ``condition`` and the module attribute became a
  :class:`~rustest.decorators.MarkDecorator`. Not a function, so the test **silently
  vanished from collection** under v1 *and* v2 — exit 0 with a test missing.

The fix ports pytest's own discrimination verbatim, from
``_pytest/mark/structures.py::MarkDecorator.__call__`` (pytest 8.4.2)::

    if args and not kwargs:
        func = args[0]
        is_class = inspect.isclass(func)
        unwrapped_func = func
        if isinstance(func, (staticmethod, classmethod)):
            unwrapped_func = func.__func__
        if len(args) == 1 and (istestfunc(unwrapped_func) or is_class):
            store_mark(unwrapped_func, self.mark, stacklevel=3)
            return func
    return self.with_args(*args, **kwargs)

with ``istestfunc(func) = callable(func) and getattr(func, "__name__", "<lambda>") !=
"<lambda>"``. Every branch of that rule is pinned below, including the two edges that are
easy to get wrong and that this suite verified against real pytest 8.4.2 by running it:

* a **lambda** positional is *not* a decoration (it has no usable ``__name__``), so
  ``@pytest.mark.slow(lambda: 1)`` is a factory call and the test passes;
* a **named callable** positional *is* a decoration even where the user plainly meant a
  condition, so ``@pytest.mark.xfail(named_condition)`` marks ``named_condition``, returns
  it, and then calls it with the test function — a collection ``TypeError``. rustest must
  reproduce that, not "improve" on it.
"""

from __future__ import annotations

from typing import Any

from rustest import mark as native_mark
from rustest.compat import pytest as compat_pytest
from rustest.decorators import MarkDecorator


def _marks(obj: Any) -> list[dict[str, Any]]:
    return getattr(obj, "__rustest_marks__", [])


def _named_condition() -> bool:
    """A zero-argument named callable — pytest's ``istestfunc`` says True."""
    return True


# --------------------------------------------------------------------------------------
# #136 — bare skip
# --------------------------------------------------------------------------------------


def test_bare_compat_skip_returns_the_test_function_itself() -> None:
    """The identity check is the whole of #136: the body must survive the decorator."""

    def victim() -> str:
        return "body"

    decorated = compat_pytest.mark.skip(victim)

    assert decorated is victim
    assert decorated() == "body"
    assert hasattr(decorated, "__rustest_skip__")


def test_bare_compat_skip_records_skip_metadata_not_a_function_reason() -> None:
    """``__rustest_skip__`` is the only skip source v1's Rust collector reads.

    Before the fix it was never set at all (the method *was* the decorator); the closest
    the bug came to metadata was handing ``skip_decorator`` the test function as ``reason``
    one call later, which is why ``_v2_worker::_skip_kwargs`` still coerces to ``str``.
    """

    def victim() -> None:
        pass

    _ = compat_pytest.mark.skip(victim)

    reason = getattr(victim, "__rustest_skip__")
    assert isinstance(reason, str)
    assert not callable(reason)


def test_called_compat_skip_is_still_a_factory() -> None:
    """The control: parentheses keep meaning "give me a decorator"."""

    def victim() -> None:
        pass

    decorator = compat_pytest.mark.skip(reason="later")
    assert not hasattr(victim, "__rustest_skip__")

    assert decorator(victim) is victim
    assert getattr(victim, "__rustest_skip__") == "later"


def test_compat_skip_accepts_a_positional_reason() -> None:
    """pytest's ``Skip(*mark.args)`` makes ``skip("why")`` legal; a str is not a testfunc."""

    def victim() -> None:
        pass

    _ = compat_pytest.mark.skip("positional")(victim)

    assert getattr(victim, "__rustest_skip__") == "positional"


# --------------------------------------------------------------------------------------
# #137 — bare xfail and its latent twin, bare skipif
# --------------------------------------------------------------------------------------


def test_bare_xfail_returns_the_function_and_records_an_unconditional_xfail() -> None:
    """Empty ``args`` is what makes it unconditional — ``_v2_worker::_conditions``."""

    def victim() -> None:
        pass

    decorated = compat_pytest.mark.xfail(victim)

    assert decorated is victim
    assert _marks(victim) == [{"name": "xfail", "args": (), "kwargs": {}}]


def test_bare_xfail_does_not_bind_the_test_function_as_a_condition() -> None:
    """The exact shape of #137: the function must not end up in ``args``."""

    def victim() -> None:
        pass

    _ = compat_pytest.mark.xfail(victim)

    recorded = _marks(victim)
    assert recorded, "the mark must actually be recorded, not merely absent from args"
    assert all(victim not in entry["args"] for entry in recorded)


def test_called_xfail_is_still_a_factory_with_full_kwargs() -> None:
    """The control. rustest normalises absent keywords to ``None``/defaults; unchanged."""

    decorator = compat_pytest.mark.xfail(reason="known", strict=True)

    assert isinstance(decorator, MarkDecorator)
    assert decorator.name == "xfail"
    assert decorator.args == ()
    assert decorator.kwargs == {"reason": "known", "raises": None, "run": True, "strict": True}


def test_bare_skipif_is_an_unconditional_skip_like_pytests() -> None:
    """Verified against pytest 8.4.2: ``@pytest.mark.skipif`` bare *skips* (reason "Skipped").

    ``_pytest/skipping.py::evaluate_skip_marks`` l. 177-179 treats a ``skipif`` with no
    conditions as unconditional, so this is a skip and not an error.
    """

    def victim() -> None:
        pass

    decorated = compat_pytest.mark.skipif(victim)

    assert decorated is victim
    assert _marks(victim) == [{"name": "skipif", "args": (), "kwargs": {}}]


def test_called_skipif_is_still_a_factory_in_both_reason_spellings() -> None:
    """The control, including the legacy positional ``reason`` the shim has always taken."""

    keyword = compat_pytest.mark.skipif(True, reason="kw")
    positional = compat_pytest.mark.skipif(True, "pos")

    assert isinstance(keyword, MarkDecorator) and keyword.args == (True,)
    assert keyword.kwargs["reason"] == "kw"
    assert isinstance(positional, MarkDecorator) and positional.kwargs["reason"] == "pos"


# --------------------------------------------------------------------------------------
# The discrimination rule itself, branch by branch
# --------------------------------------------------------------------------------------


def test_a_lambda_positional_is_a_factory_call_not_a_decoration() -> None:
    """``istestfunc`` excludes lambdas by name, so this is a *condition*, not a target.

    Verified against pytest 8.4.2: ``@pytest.mark.slow(lambda: 1)`` collects and passes.
    rustest's old ``_MarkDecoratorFactory`` used ``hasattr(args[0], "__name__")``, which a
    lambda satisfies — it decorated the lambda and then called it with the test function.
    """
    produced = native_mark.slow(lambda: 1)

    assert isinstance(produced, MarkDecorator)
    assert produced.name == "slow"
    assert len(produced.args) == 1 and callable(produced.args[0])


def test_a_named_callable_positional_is_a_decoration_even_as_a_condition() -> None:
    """pytest's documented sharp edge, reproduced rather than smoothed over.

    Verified against pytest 8.4.2: ``@pytest.mark.xfail(named_condition)`` raises
    ``TypeError: named_condition() takes 0 positional arguments but 1 was given`` at
    collection, because the decorator expression evaluates to ``named_condition`` itself.
    """
    produced = compat_pytest.mark.xfail(_named_condition)

    assert produced is _named_condition
    assert _marks(_named_condition) == [{"name": "xfail", "args": (), "kwargs": {}}]
    _marks(_named_condition).clear()  # module-level target: do not leak into other tests


def test_a_class_positional_is_a_decoration() -> None:
    """``is_class`` is pytest's second acceptance branch, evaluated on the *original*."""

    class Suite:
        pass

    decorated = compat_pytest.mark.xfail(Suite)

    assert decorated is Suite
    assert _marks(Suite) == [{"name": "xfail", "args": (), "kwargs": {}}]


def test_the_is_class_branch_is_load_bearing_for_a_class_named_lambda() -> None:
    """The single input that separates pytest's two acceptance clauses.

    ``istestfunc`` already answers True for any ordinary class -- classes are callable and
    carry a ``__name__`` -- so ``or is_class`` looks redundant until the class is named
    ``"<lambda>"``, at which point ``istestfunc`` says False and only ``is_class`` keeps it a
    decoration. Verified against pytest 8.4.2: ``istestfunc`` returns ``False`` for this
    class, ``pytest.mark.xfail`` still returns it unchanged, and its ``pytestmark`` holds
    ``Mark(name='xfail', args=(), kwargs={})``.

    Contrived, and pinned anyway: without it the ``or is_class`` clause can be deleted with
    every test still green, which is a clause of a ported rule going unverified.
    """
    weird = type("<lambda>", (), {})

    decorated = compat_pytest.mark.xfail(weird)

    assert decorated is weird
    assert _marks(weird) == [{"name": "xfail", "args": (), "kwargs": {}}]


def test_a_staticmethod_is_unwrapped_but_the_descriptor_is_returned() -> None:
    """pytest stores on ``func.__func__`` and returns ``func`` — marks are read off the
    function object later, and returning the plain function would strip the descriptor."""

    def plain() -> None:
        pass

    descriptor = staticmethod(plain)
    returned = compat_pytest.mark.xfail(descriptor)

    assert returned is descriptor
    assert _marks(plain) == [{"name": "xfail", "args": (), "kwargs": {}}]
    assert not _marks(descriptor)


def test_a_classmethod_is_unwrapped_but_the_descriptor_is_returned() -> None:
    def plain(cls: type) -> None:
        pass

    descriptor = classmethod(plain)
    returned = compat_pytest.mark.skipif(descriptor)

    assert returned is descriptor
    assert _marks(plain) == [{"name": "skipif", "args": (), "kwargs": {}}]


def test_any_keyword_argument_forces_the_factory_branch() -> None:
    """pytest's guard is ``args and not kwargs`` — one keyword and it is never a decoration."""

    def victim() -> None:
        pass

    produced = native_mark.slow(victim, why="because")

    assert isinstance(produced, MarkDecorator)
    assert produced.args == (victim,)
    assert not _marks(victim)


def test_two_positionals_force_the_factory_branch() -> None:
    """``len(args) == 1`` is a separate clause from ``args and not kwargs``."""

    def victim() -> None:
        pass

    produced = native_mark.slow(victim, victim)

    assert isinstance(produced, MarkDecorator)
    assert produced.args == (victim, victim)
    assert not _marks(victim)


def test_a_non_callable_positional_is_a_factory_call() -> None:
    """The ordinary condition case: ``skipif(True)`` must stay a factory."""
    produced = compat_pytest.mark.skipif(True)

    assert isinstance(produced, MarkDecorator)
    assert produced.args == (True,)


# --------------------------------------------------------------------------------------
# The rest of the mark surface
# --------------------------------------------------------------------------------------


def test_bare_custom_marks_still_work() -> None:
    """The pre-existing behaviour ``_MarkDecoratorFactory`` already had, preserved."""

    def victim() -> None:
        pass

    decorated = native_mark.slow(victim)

    assert decorated is victim
    assert _marks(victim) == [{"name": "slow", "args": (), "kwargs": {}}]


def test_bare_usefixtures_has_no_effect_instead_of_deleting_the_test() -> None:
    """Verified against pytest 8.4.2: bare ``usefixtures`` warns and passes. It was the
    third member of the vanishing family — it returned a ``MarkDecorator`` too."""

    def victim() -> None:
        pass

    decorated = native_mark.usefixtures(victim)

    assert decorated is victim
    assert _marks(victim) == [{"name": "usefixtures", "args": (), "kwargs": {}}]


def test_bare_asyncio_still_works() -> None:
    """``MarkGenerator.asyncio`` has always had its own ``func is not None`` bare branch."""

    async def victim() -> None:
        pass

    decorated = native_mark.asyncio(victim)

    assert decorated is victim
    assert [entry["name"] for entry in _marks(victim)] == ["asyncio"]


def test_bare_parametrize_is_still_a_loud_typeerror() -> None:
    """pytest errors on bare ``parametrize`` too (``_parse_parametrize_args`` missing
    ``argnames``/``argvalues``), so rustest's earlier, clearer refusal is kept."""

    def victim() -> None:
        pass

    try:
        _ = native_mark.parametrize(victim)
    except TypeError as exc:
        assert "must be called with arguments" in str(exc)
    else:  # pragma: no cover - the assertion below is the failure report
        raise AssertionError("bare @mark.parametrize must raise TypeError")


# --------------------------------------------------------------------------------------
# The uncalled mark object itself
# --------------------------------------------------------------------------------------


def test_an_uncalled_mark_duck_types_as_a_mark_for_module_level_pytestmark() -> None:
    """``pytestmark = pytest.mark.xfail`` puts the *uncalled* object in the list.

    ``_v2_worker::_spec_from_pytestmark`` reads ``name``/``args``/``kwargs`` off it. A bound
    method (the old shape) has none of those and refused the whole file.
    """
    bare = compat_pytest.mark.xfail

    assert bare.name == "xfail"
    assert bare.args == ()
    assert bare.kwargs == {}


def test_uncalled_marks_are_stable_objects() -> None:
    """Repeated attribute access must not hand back a fresh object each time for the
    standard marks — ``pytest.mark.xfail is pytest.mark.xfail`` holds in pytest too only
    by value, but a stable identity keeps ``add_marker`` bookkeeping predictable."""
    assert compat_pytest.mark.xfail is compat_pytest.mark.xfail
    assert compat_pytest.mark.skipif is compat_pytest.mark.skipif
    assert compat_pytest.mark.skip is compat_pytest.mark.skip
