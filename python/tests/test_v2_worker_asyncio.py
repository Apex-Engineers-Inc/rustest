"""The v2 worker's **asyncio loop-scope model** (`rustest._v2_worker`).

`pytest_asyncio/plugin.py` (pytest-asyncio 1.2.0, installed in this repo's venv) is the
oracle for every rule here, and every rule cites the line it ports. Where the oracle's
behaviour was ambiguous it was probed by running pytest itself; those probes are named in
the docstring of the test that pins them.

Four groups:

1. **Resolution** — what loop scope a test or a fixture gets, and from which of the three
   sources (`@mark.asyncio(loop_scope=...)`, the two ini defaults, the fixture's own
   caching scope). Pure functions, so these are cheap and exhaustive.
2. **Lifetime** — that a loop of scope S is created once and closed exactly when S's
   teardown bucket drains, with every async fixture that ran on it already unwound.
3. **Mode** — `auto` vs `strict`, which is the only thing that decides whether an unmarked
   `async def` test runs at all, and whether an `async def` fixture is awaited.
4. **rustest's extensions** — the `timeout=` kwarg on the asyncio mark, and the decision
   *not* to batch same-loop tests, which is pinned here because "we deliberately do not do
   this" is invisible in the source.

The differential against real pytest lives in the corpus (`conformance/corpus/async/*`,
graded by `python -m conformance --v2-run`); these pin the worker-level mechanics that a
whole-run comparison cannot localise.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
import sys
import textwrap
import time
import warnings

import pytest

from rustest._v2_worker import (
    ASYNC_NOT_SUPPORTED_MESSAGE,
    DEFAULT_NAMING,
    AsyncioConfig,
    ExecutionPlan,
    FixtureDef,
    FixtureRunner,
    MarkSpec,
    ScopeMismatch,
    WorkerState,
    build_registry,
    collect_module,
    execute_batch,
    execute_test,
    handle_init,
)
import rustest._v2_worker as worker


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@contextmanager
def worker_for(rootdir: Path, config: AsyncioConfig) -> Iterator[None]:
    """Install worker state carrying *config*, then undo every global it touched.

    Broader than ``test_v2_worker_execute.isolated_worker_state`` by exactly one global —
    ``_state``, which is where the asyncio configuration lives and which nothing else in the
    suite needs to vary.  Leaking it would silently apply one test's ``asyncio_mode`` to the
    next, and the symptom (an unmarked async test passing where it should fail) is precisely
    the thing these tests are here to catch.
    """
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    saved_conftests = dict(worker._conftest_modules)  # pyright: ignore[reportPrivateUsage]
    saved_plans = dict(worker._execution_plans)  # pyright: ignore[reportPrivateUsage]
    saved_runner = worker._runner  # pyright: ignore[reportPrivateUsage]
    saved_state = worker._state  # pyright: ignore[reportPrivateUsage]
    worker._runner = None  # pyright: ignore[reportPrivateUsage]
    worker._state = WorkerState(  # pyright: ignore[reportPrivateUsage]
        rootdir=rootdir, naming=DEFAULT_NAMING, asyncio=config
    )
    try:
        worker.install_pytest_shim()
        yield
    finally:
        _ = worker.drain_at_shutdown()
        worker._state = saved_state  # pyright: ignore[reportPrivateUsage]
        worker._runner = saved_runner  # pyright: ignore[reportPrivateUsage]
        worker._execution_plans.clear()  # pyright: ignore[reportPrivateUsage]
        worker._execution_plans.update(saved_plans)  # pyright: ignore[reportPrivateUsage]
        sys.path[:] = saved_path
        for name in set(sys.modules) - set(saved_modules):
            del sys.modules[name]
        sys.modules.update(saved_modules)
        worker._conftest_modules.clear()  # pyright: ignore[reportPrivateUsage]
        worker._conftest_modules.update(saved_conftests)  # pyright: ignore[reportPrivateUsage]


def write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def run_tree(target: Path, rootdir: Path, config: AsyncioConfig) -> dict[str, str]:
    """Collect and execute one file under *config*, returning ``{nodeid: status}``."""
    with worker_for(rootdir, config):
        module, registry = build_registry(target, rootdir)
        _entries, plans = collect_module(module, target, rootdir, DEFAULT_NAMING, registry, config)
        for plan in plans:
            worker._execution_plans[plan.id] = plan  # pyright: ignore[reportPrivateUsage]
        results = [execute_test(plan.id) for plan in plans]
    return {result["id"]: result["status"] for result in results}


def message_for(target: Path, rootdir: Path, config: AsyncioConfig, nodeid: str) -> str:
    with worker_for(rootdir, config):
        module, registry = build_registry(target, rootdir)
        _entries, plans = collect_module(module, target, rootdir, DEFAULT_NAMING, registry, config)
        for plan in plans:
            worker._execution_plans[plan.id] = plan  # pyright: ignore[reportPrivateUsage]
        results = [execute_test(plan.id) for plan in plans]
    return next(r for r in results if r["id"] == nodeid).get("message", "")


def fixturedef(name: str, scope: str, func: object) -> FixtureDef:
    return FixtureDef(
        name=name,
        func=func,  # pyright: ignore[reportArgumentType]
        scope=scope,
        params=None,
        autouse=False,
        baseid="",
        argnames=(),
    )


async def _noop() -> None: ...  # a body for a fixturedef that is never executed


def plan_with(marks: tuple[MarkSpec, ...], func: object = _noop) -> ExecutionPlan:
    """The smallest plan `test_loop_scope` reads: marks plus the body it decides about."""
    return ExecutionPlan(
        id="t.py::test_x",
        path=Path("t.py"),
        module=sys.modules[__name__],
        parts=("test_x",),
        func=func,  # pyright: ignore[reportArgumentType]
        owner=None,
        closure=None,  # pyright: ignore[reportArgumentType]
        fixture_params={},
        direct_params={},
        argnames=(),
        marks=marks,
    )


# ---------------------------------------------------------------------------
# 1. resolution
# ---------------------------------------------------------------------------


def test_init_carries_the_three_asyncio_values(tmp_path: Path) -> None:
    """`init` -> :class:`AsyncioConfig`, with the omitted fixture scope staying ``None``.

    `src/v2/protocol.rs` omits `asyncio_default_fixture_loop_scope` from the wire when it is
    unset, and the absence is a **third answer**: `plugin.py::pytest_fixture_setup`
    (l. 736-741) falls back to the fixture's own caching scope only for that case.  Reading
    the missing key as ``"function"`` would move every module- and session-scoped async
    fixture onto a loop that dies before it does.
    """
    saved = worker._state  # pyright: ignore[reportPrivateUsage]
    try:
        _ = handle_init(
            {
                "op": "init",
                "protocol_version": 4,
                "rootdir": tmp_path.as_posix(),
                "python_files": ["test_*.py"],
                "python_classes": ["Test"],
                "python_functions": ["test"],
                "asyncio_mode": "strict",
                "asyncio_default_test_loop_scope": "module",
            }
        )
        state = worker._state  # pyright: ignore[reportPrivateUsage]
        assert state is not None
        assert state.asyncio == AsyncioConfig(
            mode="strict", default_fixture_loop_scope=None, default_test_loop_scope="module"
        )

        _ = handle_init(
            {
                "op": "init",
                "protocol_version": 4,
                "rootdir": tmp_path.as_posix(),
                "python_files": ["test_*.py"],
                "python_classes": ["Test"],
                "python_functions": ["test"],
                "asyncio_mode": "auto",
                "asyncio_default_fixture_loop_scope": "session",
                "asyncio_default_test_loop_scope": "session",
            }
        )
        state = worker._state  # pyright: ignore[reportPrivateUsage]
        assert state is not None
        assert state.asyncio.default_fixture_loop_scope == "session"
    finally:
        worker._state = saved  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("marked", "default", "fixture_scope", "expected"),
    [
        # `mark ?? asyncio_default_fixture_loop_scope ?? fixturedef.scope` — plugin.py
        # l. 736-741, one row per rung of the ladder.  Every row's loop scope is at least as
        # wide as the fixture's own, because a narrower one is a ScopeMismatch — see
        # `test_a_fixture_may_not_run_on_a_narrower_loop` for that half.
        ("session", "class", "module", "session"),
        (None, "session", "module", "session"),
        (None, None, "module", "module"),
        (None, None, "function", "function"),
        (None, None, "session", "session"),
        ("module", None, "function", "module"),
    ],
)
def test_fixture_loop_scope_precedence(
    marked: str | None, default: str | None, fixture_scope: str, expected: str
) -> None:
    """The three-rung fallback, exhaustively — the rung that surprises is the last.

    With ``asyncio_default_fixture_loop_scope`` unset, an async fixture's loop scope is its
    own *caching* scope, so a ``scope="module"`` fixture gets a module-lived loop with
    nobody having configured one.  That is what makes the unset option different from
    ``"function"`` rather than a synonym for it.
    """

    async def body() -> None: ...

    if marked is not None:
        body._loop_scope = marked  # pyright: ignore[reportFunctionMemberAccess]
    runner = FixtureRunner(AsyncioConfig(default_fixture_loop_scope=default))
    assert runner.fixture_loop_scope(fixturedef("f", fixture_scope, body)) == expected


@pytest.mark.parametrize(
    ("marked", "default", "fixture_scope"),
    [
        # Reached from the mark...
        ("function", None, "session"),
        ("class", "session", "module"),
        # ...and from the ini alone, which is the shape a whole suite can land on at once.
        (None, "function", "session"),
        (None, "module", "session"),
    ],
)
def test_a_fixture_may_not_run_on_a_narrower_loop(
    marked: str | None, default: str | None, fixture_scope: str
) -> None:
    """A wider fixture on a narrower loop is a ``ScopeMismatch``, as it is under pytest.

    The oracle never had to write this rule down: it acquires the loop by *requesting* the
    ``_{loop_scope}_scoped_runner`` fixture (`plugin.py::pytest_fixture_setup` l. 742-743),
    so ordinary fixture scope checking rejects it. This port calls ``loop_runner`` directly
    and so had to be told — :meth:`FixtureRunner._check_loop_scope`.

    Probed on pytest 8.4.2 + pytest-asyncio 1.2.0 with
    ``@pytest_asyncio.fixture(scope="session", loop_scope="function")``: **2 errors**, with
    the message this port reproduces. Before the check, rustest ran the same file **green**,
    resuming the async generator's teardown on a newly built loop after the setup loop had
    closed (``teardown same=False setup_loop_closed=True``).
    """

    async def body() -> None:
        yield  # pyright: ignore[reportGeneralTypeIssues] - shape only; never executed

    if marked is not None:
        body._loop_scope = marked  # pyright: ignore[reportFunctionMemberAccess]
    runner = FixtureRunner(AsyncioConfig(default_fixture_loop_scope=default))

    with pytest.raises(ScopeMismatch) as excinfo:
        _ = runner.fixture_loop_scope(fixturedef("wide", fixture_scope, body))

    expected_loop = marked or default
    assert (
        f"You tried to access the {expected_loop} scoped fixture "
        f"_{expected_loop}_scoped_runner with a {fixture_scope} scoped request object"
    ) in str(excinfo.value), str(excinfo.value)


def test_a_narrow_fixture_on_a_wider_loop_is_allowed() -> None:
    """The other direction is legal and is the whole point of the acceptance shape.

    ``asyncio_default_fixture_loop_scope = session`` puts every fixture, function-scoped ones
    included, on the session loop — which is what lets a function-scoped async fixture talk
    to a session-scoped client. A check written as ``!=`` rather than ``>=`` would reject the
    configuration this phase exists to support.
    """

    async def body() -> None: ...

    runner = FixtureRunner(AsyncioConfig(default_fixture_loop_scope="session"))
    assert runner.fixture_loop_scope(fixturedef("narrow", "function", body)) == "session"


@pytest.mark.parametrize(
    ("mode", "marks", "default", "expected"),
    [
        # auto marks every async function (plugin.py l. 609-612), so an unmarked test still
        # gets a scope; strict leaves it unconverted, and `None` is "rustest must not run it".
        ("auto", (), "function", "function"),
        ("auto", (), "session", "session"),
        ("strict", (), "function", None),
        ("strict", (MarkSpec(name="asyncio"),), "function", "function"),
        (
            "auto",
            (MarkSpec(name="asyncio", kwargs={"loop_scope": "module"}),),
            "function",
            "module",
        ),
        # The deprecated `scope=` alias (l. 771-777) resolves to the same thing.
        ("auto", (MarkSpec(name="asyncio", kwargs={"scope": "class"}),), "function", "class"),
        # A `timeout=`-only mark is rustest's extension and must not disturb the scope.
        ("auto", (MarkSpec(name="asyncio", kwargs={"timeout": 5.0}),), "session", "session"),
    ],
)
def test_test_loop_scope_resolution(
    mode: str, marks: tuple[MarkSpec, ...], default: str, expected: str | None
) -> None:
    runner = FixtureRunner(AsyncioConfig(mode=mode, default_test_loop_scope=default))
    assert runner.test_loop_scope(plan_with(marks)) == expected


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        # plugin.py::_get_marked_loop_scope l. 767-770 and l. 771-774.
        ({"nonsense": 1}, "mark.asyncio accepts only a keyword argument 'loop_scope'."),
        ({"loop_scope": "module", "scope": "module"}, 'defines both "scope" and "loop_scope"'),
        ({"loop_scope": "sesion"}, "is not a valid loop_scope"),
    ],
)
def test_a_malformed_asyncio_mark_is_rejected(kwargs: dict[str, object], fragment: str) -> None:
    """Every branch of the mark validator, including the one the oracle leaves as an
    ``assert``.

    pytest-asyncio ends `_get_marked_loop_scope` with ``assert scope in {...}``, which
    vanishes under ``-O``; a typo'd ``loop_scope`` would then fall through to whatever the
    dict lookup produced.  Raised as a real error here, because "the loop scope quietly
    became something else" is a wrong answer with no message attached to it.
    """
    runner = FixtureRunner(AsyncioConfig(mode="auto"))
    with pytest.raises(ValueError, match=fragment.replace("(", r"\(").replace(")", r"\)")):
        _ = runner.test_loop_scope(plan_with((MarkSpec(name="asyncio", kwargs=kwargs),)))


def test_a_malformed_mark_fails_in_the_setup_phase(tmp_path: Path) -> None:
    """...and it fails at **setup**, which is a different outcome word from failing at call.

    `PytestAsyncioFunction.setup` (l. 459-463) resolves ``_loop_scope`` while requesting the
    runner fixture, so pytest reports a bad mark as ``ERROR`` and exits 1.  Measured both
    ways during this task: resolving it lazily at call time instead reported ``failed``.
    """
    target = write(
        tmp_path / "test_bad_mark.py",
        """
        import pytest


        @pytest.mark.asyncio(loop_scope="module", scope="module")
        async def test_dup():
            assert True
        """,
    )
    assert run_tree(target, tmp_path, AsyncioConfig(mode="auto")) == {
        "test_bad_mark.py::test_dup": "error"
    }


# ---------------------------------------------------------------------------
# 2. lifetime
# ---------------------------------------------------------------------------


def test_one_runner_per_scope_name_and_it_is_cached() -> None:
    """A loop is per **scope name**, not per bucket and not per request.

    `package` and `session` share a teardown bucket in this worker (`_SCOPE_BUCKET`) but are
    two distinct runner fixtures in pytest-asyncio (l. 832-835, one per `Scope` member), so
    they must be two distinct loops or a test comparing ``get_running_loop()`` across the two
    scopes would see them compare equal where pytest keeps them apart.
    """
    runner = FixtureRunner(AsyncioConfig())
    try:
        function_loop = runner.loop_runner("function")
        assert runner.loop_runner("function") is function_loop
        scopes = ["function", "class", "module", "package", "session"]
        loops = [runner.loop_runner(scope).get_loop() for scope in scopes]
        assert len({id(loop) for loop in loops}) == len(scopes), loops
    finally:
        runner.teardown_all()


def test_a_loop_is_closed_when_its_scope_drains() -> None:
    """The lifetime claim: narrow loops die at their boundary, the session loop survives.

    This is what makes the model a *scope* model rather than a naming convention — under the
    one-loop-per-worker stopgap this replaced, all five of these were the same object.
    """
    runner = FixtureRunner(AsyncioConfig())
    try:
        function_loop = runner.loop_runner("function").get_loop()
        module_loop = runner.loop_runner("module").get_loop()
        session_loop = runner.loop_runner("session").get_loop()

        runner.teardown("function")
        assert function_loop.is_closed()
        assert not module_loop.is_closed()
        assert not session_loop.is_closed()
        # ...and a request after the drain builds a NEW one rather than handing back a
        # closed loop, which is the shape that fails with "Event loop is closed" three
        # frames into somebody's test.
        assert runner.loop_runner("function").get_loop() is not function_loop

        runner.teardown("module")
        assert module_loop.is_closed()
        assert not session_loop.is_closed()
    finally:
        runner.teardown_all()
        assert session_loop.is_closed()


def test_an_async_fixture_is_torn_down_before_its_loop_closes(tmp_path: Path) -> None:
    """Ordering, asserted through a side effect rather than by inspecting the stacks.

    A loop's close finalizer is pushed when the loop is built, which is *inside* the body of
    whichever fixture first asked for it — and a fixture's own finalizer is pushed only after
    its body returns.  The bucket drains LIFO, so the fixture unwinds first and resumes on a
    live loop.  Get this backwards and the async generator's second half raises
    ``RuntimeError: Event loop is closed`` during teardown, which is reported as an error on
    an unrelated test.
    """
    target = write(
        tmp_path / "test_ordering.py",
        """
        import asyncio

        import pytest

        EVENTS = []


        @pytest.fixture(scope="module")
        async def resource():
            EVENTS.append(("setup", id(asyncio.get_running_loop())))
            yield "live"
            EVENTS.append(("teardown", id(asyncio.get_running_loop())))


        def test_uses_it(resource):
            assert resource == "live"
        """,
    )
    config = AsyncioConfig(mode="auto")
    with worker_for(tmp_path, config):
        module, registry = build_registry(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry, config)
        for plan in plans:
            worker._execution_plans[plan.id] = plan  # pyright: ignore[reportPrivateUsage]
        results = [execute_test(plan.id) for plan in plans]
        assert [r["status"] for r in results] == ["passed"]
        assert [name for name, _loop in module.EVENTS] == ["setup"]
        # `worker_for` drains at exit; the teardown must have run there, on the same loop.
    assert [name for name, _loop in module.EVENTS] == ["setup", "teardown"]
    assert len({loop for _name, loop in module.EVENTS}) == 1, module.EVENTS


def test_a_session_loop_survives_a_module_boundary(tmp_path: Path) -> None:
    """Two files, one worker, one session loop — the corpus case's mechanism, localised.

    `conformance/corpus/async/session-loop-shared` grades this end to end against pytest;
    this pins that the *module* boundary specifically does not take the session loop with
    it, which is the boundary `note_module_boundary` drives and therefore the one most
    likely to be widened by accident.
    """
    runner = FixtureRunner(AsyncioConfig(default_test_loop_scope="session"))
    try:
        session_loop = runner.loop_runner("session").get_loop()
        runner.teardown("module")
        assert not session_loop.is_closed()
        assert runner.loop_runner("session").get_loop() is session_loop
    finally:
        runner.teardown_all()


# ---------------------------------------------------------------------------
# 3. mode
# ---------------------------------------------------------------------------


def test_strict_mode_refuses_an_unmarked_async_test(tmp_path: Path) -> None:
    """Probed on pytest 8.4.2 + pytest-asyncio 1.2.0: **failed**, with pytest's own message.

    In strict mode the collection hook does not convert the item (l. 606-614), so the body
    reaches `_pytest/python.py::pytest_pyfunc_call`, returns a coroutine and is failed by
    ``async_fail``.  The marked twin in the same file runs, which is what says the mode is
    being read rather than async support being switched off wholesale.
    """
    target = write(
        tmp_path / "test_modes.py",
        """
        import pytest


        async def test_unmarked():
            assert True


        @pytest.mark.asyncio
        async def test_marked():
            assert True
        """,
    )
    statuses = run_tree(target, tmp_path, AsyncioConfig(mode="strict"))
    assert statuses == {
        "test_modes.py::test_unmarked": "failed",
        "test_modes.py::test_marked": "passed",
    }
    message = message_for(
        target, tmp_path, AsyncioConfig(mode="strict"), "test_modes.py::test_unmarked"
    )
    assert ASYNC_NOT_SUPPORTED_MESSAGE in message, message


def test_the_async_generator_xfail_is_mode_dependent(tmp_path: Path) -> None:
    """`xfail(run=False)` in auto, a plain failure in strict — and the difference is real.

    pytest-asyncio synthesises the mark inside ``AsyncGenerator._from_function`` (l. 520-531),
    which is only reached for an item it converted.  Applying it unconditionally turned
    strict mode's **failed** into a green ``xfailed``, which is worse than the silent pass it
    replaced: an xfail is a result a reader trusts.  Both halves probed.
    """
    target = write(
        tmp_path / "test_agen.py",
        """
        async def test_asyncgen():
            assert 1 == 2
            yield
        """,
    )
    assert run_tree(target, tmp_path, AsyncioConfig(mode="auto")) == {
        "test_agen.py::test_asyncgen": "xfailed"
    }
    assert run_tree(target, tmp_path, AsyncioConfig(mode="strict")) == {
        "test_agen.py::test_asyncgen": "failed"
    }


def test_strict_mode_leaves_an_unmarked_async_fixture_unawaited(tmp_path: Path) -> None:
    """The oracle's behaviour, reproduced deliberately even though it looks like a bug.

    `plugin.py::pytest_fixture_setup` l. 729-733 returns early for any fixture without
    ``_force_asyncio_fixture`` when the mode is strict, so the test is handed a **coroutine
    object**.  Probed: pytest 8.4.2 passes such a test and emits its own
    ``PytestRemovedIn9Warning`` about it.  The escape hatches are the oracle's two —
    ``@pytest_asyncio.fixture`` or ``asyncio_mode = auto`` — and the second half of this test
    is that the marked spelling *is* awaited.
    """
    target = write(
        tmp_path / "test_strict_fixture.py",
        """
        import inspect

        import pytest
        import pytest_asyncio


        @pytest.fixture
        async def unmarked():
            return 7


        @pytest_asyncio.fixture
        async def marked():
            return 9


        def test_unmarked_is_a_coroutine(unmarked):
            assert inspect.iscoroutine(unmarked), type(unmarked)


        def test_marked_is_awaited(marked):
            assert marked == 9
        """,
    )
    assert run_tree(target, tmp_path, AsyncioConfig(mode="strict")) == {
        "test_strict_fixture.py::test_unmarked_is_a_coroutine": "passed",
        "test_strict_fixture.py::test_marked_is_awaited": "passed",
    }


def test_auto_mode_awaits_an_unmarked_async_fixture(tmp_path: Path) -> None:
    """The same file under ``auto``: the plain ``@pytest.fixture`` is awaited too.

    Deliberately the mirror image of the test above, sharing its source shape, so the pair
    reads as one table: the *only* thing that moved is the mode.
    """
    target = write(
        tmp_path / "test_auto_fixture.py",
        """
        import inspect

        import pytest


        @pytest.fixture
        async def unmarked():
            return 7


        def test_unmarked_is_awaited(unmarked):
            assert unmarked == 7
            assert not inspect.iscoroutine(unmarked)
        """,
    )
    assert run_tree(target, tmp_path, AsyncioConfig(mode="auto")) == {
        "test_auto_fixture.py::test_unmarked_is_awaited": "passed"
    }


def test_a_pytest_asyncio_fixture_carries_the_oracles_two_attributes() -> None:
    """The shim's whole contract with the worker (`compat/pytest_asyncio.py`).

    `_force_asyncio_fixture` is what strict mode keys on and `_loop_scope` is the only way to
    give a fixture a loop scope different from its caching scope; both are set by
    ``_make_asyncio_fixture_function`` (plugin.py l. 191-197).  Asserted on the decorated
    object rather than through a run, because a run would pass for either attribute alone.
    """
    import rustest.compat.pytest_asyncio as shim

    @shim.fixture(scope="session", loop_scope="session")
    async def wide() -> int:
        return 1

    assert getattr(wide, "_force_asyncio_fixture", False) is True
    assert getattr(wide, "_loop_scope", None) == "session"

    @shim.fixture
    async def plain() -> int:
        return 1

    assert getattr(plain, "_force_asyncio_fixture", False) is True
    assert getattr(plain, "_loop_scope", "unset") is None


# ---------------------------------------------------------------------------
# 4. rustest's extensions
# ---------------------------------------------------------------------------


def test_the_timeout_kwarg_cancels_an_overrunning_test(tmp_path: Path) -> None:
    """`@mark.asyncio(timeout=...)` — rustest's, not the oracle's.

    pytest-asyncio has no timeout at all; `docs/guide/async-testing.md` advertises this as
    what rustest adds.  v1 implemented it in the Rust executor (`src/execution.rs` l. 825-830
    reading the kwarg, `async_executor.py` l. 58-60 applying ``asyncio.wait_for``), and the
    semantics ported are those: the coroutine is **cancelled**, not merely reported late, and
    the message is v1's.
    """
    target = write(
        tmp_path / "test_timeout.py",
        """
        import asyncio

        import pytest

        CANCELLED = []


        @pytest.mark.asyncio(timeout=0.05)
        async def test_overruns():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                CANCELLED.append(True)
                raise


        @pytest.mark.asyncio(timeout=30)
        async def test_within_budget():
            await asyncio.sleep(0)
        """,
    )
    started = time.perf_counter()
    statuses = run_tree(target, tmp_path, AsyncioConfig(mode="auto"))
    elapsed = time.perf_counter() - started

    assert statuses == {
        "test_timeout.py::test_overruns": "failed",
        "test_timeout.py::test_within_budget": "passed",
    }
    # The 30 s sleep was cancelled rather than waited out -- the claim the outcome alone
    # cannot make.
    assert elapsed < 20, elapsed
    message = message_for(
        target, tmp_path, AsyncioConfig(mode="auto"), "test_timeout.py::test_overruns"
    )
    assert "Test timed out after 0.05 seconds" in message, message


def test_tests_sharing_a_loop_scope_run_sequentially(tmp_path: Path) -> None:
    """The **batching decision**, pinned because a deliberate absence is invisible in source.

    rustest v1 collected every async test in a loop scope wider than ``function`` into one
    ``asyncio.gather`` (`async_executor.py::run_coroutines_parallel`, dispatched from
    `src/execution.rs` l. 92-131).  v2 does not, because the oracle does not: pytest-asyncio
    drives each coroutine through ``asyncio.Runner.run``
    (`plugin.py::_synchronize_coroutine` l. 708-723), which runs one coroutine to completion
    and cannot be re-entered.  Probed on pytest 8.4.2 + pytest-asyncio 1.2.0 with this exact
    shape: 0.613 s wall for two 0.30 s sleeps on one session loop, the second starting 3 ms
    after the first ended.

    Asserted on the interleaving rather than the wall clock, so it does not become a timing
    test: under ``gather`` both tests would enter before either left.

    **Driven through :func:`execute_batch`, not a loop over :func:`execute_test`**, because
    that is the layer a gather would be re-added at: `execute_batch` is the only place that
    holds more than one test at a time, so it is the only place that *could* overlap them. A
    pin that ran the tests one `execute_test` at a time would be asserting that a function
    given one coroutine runs one coroutine — true, unfalsifiable, and green on the day
    somebody batches inside the op it never calls.
    """
    target = write(
        tmp_path / "test_sequential.py",
        """
        import asyncio

        import pytest

        ORDER = []


        @pytest.mark.asyncio(loop_scope="module")
        async def test_one():
            ORDER.append("one-enter")
            await asyncio.sleep(0.01)
            ORDER.append("one-leave")


        @pytest.mark.asyncio(loop_scope="module")
        async def test_two():
            ORDER.append("two-enter")
            await asyncio.sleep(0.01)
            ORDER.append("two-leave")
        """,
    )
    config = AsyncioConfig(mode="auto")
    with worker_for(tmp_path, config):
        module, registry = build_registry(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry, config)
        for plan in plans:
            worker._execution_plans[plan.id] = plan  # pyright: ignore[reportPrivateUsage]
        emitted: list[Mapping[str, object]] = []
        done = execute_batch([plan.id for plan in plans], False, emitted.append)

        assert done == {"op": "batch_done", "executed": 2, "stopped": False}
        assert [result["status"] for result in emitted] == ["passed", "passed"]
        assert module.ORDER == ["one-enter", "one-leave", "two-enter", "two-leave"]


def test_the_event_loop_policy_fixture_is_honoured(tmp_path: Path) -> None:
    """A user ``event_loop_policy`` override builds the loop (plugin.py l. 804-811, 838-841).

    Resolved through the ordinary fixture path rather than a special case, so an override can
    itself request fixtures and is scope-checked like any other.  The assertion is that the
    *policy* made the loop, which is the only thing that distinguishes "the override was
    honoured" from "a loop exists".
    """
    target = write(
        tmp_path / "test_policy.py",
        """
        import asyncio

        import pytest

        MADE = []


        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            _BasePolicy = asyncio.DefaultEventLoopPolicy


        class MarkerPolicy(_BasePolicy):
            def new_event_loop(self):
                loop = super().new_event_loop()
                MADE.append(id(loop))
                return loop


        @pytest.fixture(scope="session")
        def event_loop_policy():
            return MarkerPolicy()


        async def test_policy_built_this_loop():
            assert id(asyncio.get_running_loop()) in MADE, MADE
        """,
    )
    assert run_tree(target, tmp_path, AsyncioConfig(mode="auto")) == {
        "test_policy.py::test_policy_built_this_loop": "passed"
    }


def test_the_policy_context_restores_the_previous_policy_and_loop() -> None:
    """Port of `plugin.py::_temporary_event_loop_policy` (l. 619-631), both restores.

    Restoring the *loop* matters as much as restoring the policy: ``set_event_loop_policy``
    resets the policy's idea of the current loop, so without it a nested scope's loop
    creation would detach the outer scope's loop from ``asyncio.get_event_loop()``.
    """
    # The policy APIs are deprecated from 3.12 and the plugin suppresses the warning at
    # every call site (`_get_event_loop_policy` l. 634-637); a test of the port has to do
    # the same or it fails the suite on its own instrumentation.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        before_policy = asyncio.get_event_loop_policy()
        outer = asyncio.new_event_loop()
        asyncio.set_event_loop(outer)
        try:
            with worker._temporary_event_loop_policy(  # pyright: ignore[reportPrivateUsage]
                asyncio.DefaultEventLoopPolicy()
            ):
                pass
            assert asyncio.get_event_loop_policy() is before_policy
            assert asyncio.get_event_loop() is outer
        finally:
            asyncio.set_event_loop(None)
            outer.close()
            asyncio.set_event_loop_policy(before_policy)


def test_a_worker_without_init_uses_the_documented_defaults() -> None:
    """A :class:`FixtureRunner` built with no config is ``auto`` / unset / ``function``.

    `_execution_runner` reads the config off ``_state``, and a runner created before ``init``
    — which only this suite does — must land on the documented defaults rather than on
    whatever a previous test left behind.
    """
    assert AsyncioConfig() == AsyncioConfig(
        mode="auto", default_fixture_loop_scope=None, default_test_loop_scope="function"
    )
    assert FixtureRunner().asyncio == AsyncioConfig()
