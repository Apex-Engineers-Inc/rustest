"""The v2 builtin fixtures, tested at the level the differential cannot reach.

``conformance/corpus/builtins/*`` and the throwaway per-builtin matrix compare *outcomes*
against real pytest, which is the right instrument for "does a suite behave the same". It
cannot see the things below: which descriptor a capture restores, whether a record list is
the same object across a phase boundary, the order two patches are unwound in, or which of
two differently-worded refusals a bad ``getini`` name gets. Those are pinned here.

Provenance for every assertion is in ``python/rustest/_v2_builtins.py``, which cites the
pytest (and pytest-mock) source each class is a port of.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

import rustest._v2_builtins as builtins_mod
import rustest._v2_worker as worker
from rustest._v2_builtins import (
    Cache,
    CaptureFixture,
    CaptureFixtureConflict,
    Config,
    FdCapture,
    LogCapture,
    LogCaptureFixture,
    MockerFixture,
    SysCapture,
    TempPathFactory,
    _capture_fixture_slot,  # pyright: ignore[reportPrivateUsage]
    _mk_tmp,  # pyright: ignore[reportPrivateUsage]
)


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


#: Windows opens descriptors in text mode by default, so a raw newline written through
#: one arrives as CR LF. The captures themselves are unaffected -- their temporary files
#: come from ``tempfile``, which is already binary -- but a probe file opened here has to
#: say so, or every byte assertion below is platform-dependent.
_O_BINARY = getattr(os, "O_BINARY", 0)


def _probe_fd(path: Path) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT | _O_BINARY)


def test_fd_capture_catches_a_raw_descriptor_write(tmp_path: Path) -> None:
    """The property the whole class exists for, and the one ``SysCapture`` cannot have."""
    handle = _probe_fd(tmp_path / "target")
    try:
        capture = FdCapture(handle)
        capture.start()
        try:
            _ = os.write(handle, b"through the descriptor\n")
            assert capture.snap() == "through the descriptor\n"
            assert capture.snap() == "", "snap truncates, so a second read is empty"
        finally:
            capture.done()
    finally:
        os.close(handle)


def test_fd_capture_restores_the_descriptor_on_done(tmp_path: Path) -> None:
    """``done()`` must put the *original* file back behind the fd number.

    Asserted by writing after ``done()`` and reading the file the fd pointed at first: a
    capture that leaked would leave the fd aimed at a closed temporary and the write would
    either vanish or raise.
    """
    target = tmp_path / "target"
    handle = _probe_fd(target)
    try:
        capture = FdCapture(handle)
        capture.start()
        _ = os.write(handle, b"captured\n")
        capture.done()
        _ = os.write(handle, b"restored\n")
    finally:
        os.close(handle)
    assert target.read_bytes() == b"restored\n"


def test_fd_capture_suspend_and_resume_split_the_stream(tmp_path: Path) -> None:
    """Suspension is what keeps boundary teardown out of the next test's capture."""
    target = tmp_path / "target"
    handle = _probe_fd(target)
    try:
        capture = FdCapture(handle)
        capture.start()
        _ = os.write(handle, b"inside\n")
        capture.suspend()
        _ = os.write(handle, b"outside\n")
        capture.resume()
        _ = os.write(handle, b"inside again\n")
        assert capture.snap() == "inside\ninside again\n"
        capture.done()
    finally:
        os.close(handle)
    assert target.read_bytes() == b"outside\n"


def test_fd_capture_writeorg_reaches_the_saved_descriptor(tmp_path: Path) -> None:
    """``writeorg`` is what ``pop_outerr_to_orig`` uses, so unread output is not lost."""
    target = tmp_path / "target"
    handle = _probe_fd(target)
    try:
        capture = FdCapture(handle)
        capture.start()
        capture.writeorg("straight through\n")
        assert capture.snap() == "", "writeorg bypasses the capture entirely"
        capture.done()
    finally:
        os.close(handle)
    assert target.read_bytes() == b"straight through\n"


def test_sys_capture_remembers_the_stream_it_replaced() -> None:
    """``_old`` is read in ``__init__``, not in ``start()`` — pytest's own choice.

    It is what makes a ``capsys`` built *inside* the worker's per-test capture hand its
    output back to that capture rather than to the terminal, and it is invisible unless the
    stream is swapped between construction and start.
    """
    original = sys.stdout
    capture = SysCapture(1)
    sys.stdout = object()  # pyright: ignore[reportAttributeAccessIssue]
    try:
        capture.start()
        print("into the capture")
        assert capture.snap() == "into the capture\n"
        capture.done()
    finally:
        sys.stdout = original
    assert capture._old is original  # pyright: ignore[reportPrivateUsage]


def test_two_capture_fixtures_at_once_is_pytests_refusal() -> None:
    """`CaptureManager.set_fixture` (l. 806-812), message verbatim.

    Probed on pytest 8.4.2: a test requesting both reports ERROR at setup with exactly this
    sentence. The order is "requested and current", so which fixture is named first depends
    on which was set up first — reproduced by taking the slot in the same order.
    """
    first = CaptureFixture(SysCapture)
    with _capture_fixture_slot("capsys", first):
        with pytest.raises(CaptureFixtureConflict) as excinfo:
            with _capture_fixture_slot("capfd", CaptureFixture(FdCapture)):
                pass  # pragma: no cover - the slot raises before the body runs
    assert str(excinfo.value) == "cannot use capfd and capsys at the same time"


def test_the_capture_slot_is_released_even_when_the_body_raises() -> None:
    """Otherwise one erroring test would refuse every later ``capsys`` in the worker."""
    with pytest.raises(RuntimeError):
        with _capture_fixture_slot("capsys", CaptureFixture(SysCapture)):
            raise RuntimeError("boom")
    assert builtins_mod._capture_fixture is None  # pyright: ignore[reportPrivateUsage]


def test_capture_fixture_accumulates_across_reads() -> None:
    """`CaptureFixture.readouterr` (l. 953-968) adds the live snap to what ``close`` banked."""
    fixture = CaptureFixture(SysCapture)
    fixture._start()  # pyright: ignore[reportPrivateUsage]
    print("one")
    fixture.close()
    assert fixture.readouterr().out == "one\n"
    assert fixture.readouterr().out == "", "the banked text is cleared by the read"


# ---------------------------------------------------------------------------
# caplog
# ---------------------------------------------------------------------------


def test_log_capture_resets_the_handler_per_phase() -> None:
    """`LoggingPlugin._runtest_for` (l. 813-835): each phase starts empty and is stashed.

    The stash keeps a *reference* to the list, and ``reset()`` rebinds rather than clearing,
    which is what lets ``get_records("setup")`` still answer during the call phase.
    """
    capture = LogCapture()
    with capture.phase("setup"):
        logging.getLogger("t").warning("setup message")
    with capture.phase("call"):
        logging.getLogger("t").warning("call message")
        assert [r.getMessage() for r in capture.handler.records] == ["call message"]

    assert [r.getMessage() for r in capture.records_by_phase["setup"]] == ["setup message"]
    assert [r.getMessage() for r in capture.records_by_phase["call"]] == ["call message"]
    assert capture.records_by_phase["setup"] is not capture.records_by_phase["call"]


def test_log_capture_removes_its_handler_after_the_phase() -> None:
    """A handler left on the root logger would capture the *next* test's records."""
    root = logging.getLogger()
    before = list(root.handlers)
    capture = LogCapture()
    with capture.phase("call"):
        assert capture.handler in root.handlers
    assert list(root.handlers) == before


def test_log_capture_changes_no_level() -> None:
    """The divergence v1 had, asserted as an absence.

    pytest passes ``level=self.log_level`` and that is ``None`` with no ``log_level`` ini, so
    ``catching_logs`` never touches a level. v1's ``caplog`` forced the root logger to DEBUG,
    which silently captured records pytest drops.
    """
    root = logging.getLogger()
    original = root.level
    capture = LogCapture()
    with capture.phase("call"):
        assert root.level == original
        logging.getLogger("quiet").info("dropped by the root level")
    assert capture.handler.records == []


def test_set_level_moves_both_the_logger_and_the_handler() -> None:
    """`LogCaptureFixture.set_level` (l. 529-552) — two levels, both restored."""
    capture = LogCapture()
    fixture = LogCaptureFixture(capture)
    logger = logging.getLogger("levels")
    with capture.phase("call"):
        fixture.set_level(logging.DEBUG, logger="levels")
        assert logger.level == logging.DEBUG
        assert capture.handler.level == logging.DEBUG
        logger.debug("visible")
        assert fixture.messages == ["visible"]
        fixture._finalize()  # pyright: ignore[reportPrivateUsage]
    assert logger.level == logging.NOTSET
    assert capture.handler.level == logging.NOTSET


def test_at_level_restores_on_the_way_out_of_the_block() -> None:
    capture = LogCapture()
    fixture = LogCaptureFixture(capture)
    logger = logging.getLogger("scoped")
    with capture.phase("call"):
        with fixture.at_level(logging.DEBUG, logger="scoped"):
            logger.debug("inside")
        logger.debug("outside")
        assert fixture.messages == ["inside"]
        assert logger.level == logging.NOTSET


def test_set_level_undoes_a_logging_disable() -> None:
    """`_force_enable_logging` (l. 495-527), including the restore.

    ``logging.disable(N)`` suppresses every record at or below ``N`` process-wide, ahead of
    any logger level, so without this a suite that calls it in a conftest would find
    ``caplog.set_level`` silently ineffective.
    """
    capture = LogCapture()
    fixture = LogCaptureFixture(capture)
    logging.disable(logging.CRITICAL)
    try:
        with capture.phase("call"):
            fixture.set_level(logging.INFO, logger="disabled")
            logging.getLogger("disabled").info("re-enabled")
            assert fixture.messages == ["re-enabled"]
            fixture._finalize()  # pyright: ignore[reportPrivateUsage]
        assert logging.root.manager.disable == logging.CRITICAL
    finally:
        logging.disable(logging.NOTSET)


def test_text_is_the_formatted_stream_not_a_join_of_messages() -> None:
    """v1's ``text`` was ``"\\n".join(messages)``; pytest's carries the default log format."""
    capture = LogCapture()
    fixture = LogCaptureFixture(capture)
    with capture.phase("call"):
        logging.getLogger("fmt").warning("body")
    assert fixture.text.startswith("WARNING  fmt:test_v2_builtins.py:")
    assert fixture.text.endswith("body\n")


def test_filtering_adds_and_removes_a_filter() -> None:
    capture = LogCapture()
    fixture = LogCaptureFixture(capture)
    drop_all = logging.Filter(name="nothing-matches-this")
    with capture.phase("call"):
        with fixture.filtering(drop_all):
            logging.getLogger("filtered").warning("dropped")
        logging.getLogger("filtered").warning("kept")
    assert fixture.messages == ["kept"]


@contextmanager
def _isolated_import_state() -> Iterator[None]:
    """Undo what ``build_registry`` leaves in the interpreter.

    Importing a generated ``conftest.py`` binds ``sys.modules["conftest"]`` to *that* file,
    and the next test that generates one hits the worker's own import-mismatch refusal — the
    same contract ``test_v2_worker_fixtures.isolated_import_state`` exists for. Kept local
    rather than imported across test modules so this file has no cross-file dependency.
    """
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    saved_conftests = dict(worker._conftest_modules)  # pyright: ignore[reportPrivateUsage]
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for name in set(sys.modules) - set(saved_modules):
            del sys.modules[name]
        sys.modules.update(saved_modules)
        # `reset_registry_caches` also drops the per-worker chain registries and the
        # builtins' FixtureDefs, which are pure derivations of the conftest modules and
        # whose whole purpose is to share session values -- across two unrelated trees
        # that sharing is exactly what must not survive.
        worker.reset_registry_caches()
        worker._conftest_modules.update(saved_conftests)  # pyright: ignore[reportPrivateUsage]


def test_the_worker_installs_a_log_capture_only_for_tests_that_want_one(tmp_path: Path) -> None:
    """``_log_capture_for`` keys on the **closure**, so an autouse fixture pulls it in too."""
    _ = (tmp_path / "conftest.py").write_text(
        textwrap.dedent(
            """
            import rustest


            @rustest.fixture(autouse=True)
            def logs(caplog):
                yield
            """
        ).lstrip(),
        encoding="utf-8",
    )
    target = tmp_path / "test_auto.py"
    _ = target.write_text("def test_never_names_caplog():\n    pass\n", encoding="utf-8")

    with _isolated_import_state():
        module, registry = worker.build_registry(target, tmp_path)
        _entries, plans = worker.collect_module(
            module, target, tmp_path, worker.DEFAULT_NAMING, registry
        )
        assert worker._log_capture_for(plans[0]) is not None  # pyright: ignore[reportPrivateUsage]


def test_no_log_capture_for_a_test_that_does_not_ask(tmp_path: Path) -> None:
    target = tmp_path / "test_plain.py"
    _ = target.write_text("def test_plain():\n    pass\n", encoding="utf-8")
    with _isolated_import_state():
        module, registry = worker.build_registry(target, tmp_path)
        _entries, plans = worker.collect_module(
            module, target, tmp_path, worker.DEFAULT_NAMING, registry
        )
        assert worker._log_capture_for(plans[0]) is None  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


def test_cache_uses_pytests_value_and_directory_layout(tmp_path: Path) -> None:
    """``v/<key>`` and ``d/<name>`` — the layout ``src/v2/cache.rs`` now writes into."""
    cache = Cache(tmp_path)
    cache.set("plugin/state", {"n": 1})
    assert json.loads((tmp_path / "v" / "plugin" / "state").read_text(encoding="UTF-8")) == {"n": 1}
    assert cache.mkdir("plugin") == tmp_path / "d" / "plugin"


def test_cache_get_returns_the_default_for_a_miss_and_for_junk(tmp_path: Path) -> None:
    """`Cache.get` (l. 153-170): "the value cannot be read" and "no value" are one answer."""
    cache = Cache(tmp_path)
    assert cache.get("nothing/here", "fallback") == "fallback"
    corrupt = tmp_path / "v" / "broken"
    corrupt.parent.mkdir(parents=True)
    _ = corrupt.write_text("{not json", encoding="UTF-8")
    assert cache.get("broken", "fallback") == "fallback"


def test_cache_mkdir_refuses_a_path_separator(tmp_path: Path) -> None:
    """pytest's own check and message (l. 138-148): the cache is a flat namespace."""
    with pytest.raises(ValueError, match="path separators"):
        _ = Cache(tmp_path).mkdir("a/b")


def test_cache_set_survives_an_unwritable_store(tmp_path: Path) -> None:
    """pytest warns and returns; there is no warnings channel, so this returns quietly.

    Forced by putting a *file* where the value directory has to be, which makes ``mkdir``
    raise ``NotADirectoryError`` — an ``OSError`` — on every platform.
    """
    _ = (tmp_path / "v").write_text("not a directory", encoding="UTF-8")
    Cache(tmp_path).set("plugin/state", {"n": 1})
    assert (tmp_path / "v").is_file()


# ---------------------------------------------------------------------------
# tmp_path / tmpdir
# ---------------------------------------------------------------------------


def test_temp_path_factory_numbers_and_cleans_up() -> None:
    factory = TempPathFactory()
    first = factory.mktemp("thing")
    second = factory.mktemp("thing")
    base = factory.getbasetemp()
    assert first != second and first.is_dir() and second.is_dir()
    assert first.parent == base
    factory.cleanup()
    assert not base.exists()


def test_unnumbered_mktemp_refuses_a_second_use() -> None:
    """``numbered=False`` is pytest's "this exact name", so a collision must raise."""
    factory = TempPathFactory()
    try:
        _ = factory.mktemp("once", numbered=False)
        with pytest.raises(FileExistsError):
            _ = factory.mktemp("once", numbered=False)
    finally:
        factory.cleanup()


class _NodeStub:
    def __init__(self, name: str) -> None:
        self.name = name


class _RequestStub:
    def __init__(self, name: str) -> None:
        self.node = _NodeStub(name)


def test_the_tmp_path_directory_is_named_after_the_test() -> None:
    """`_pytest/tmpdir.py::_mk_tmp` (l. 248-253): non-word characters out, 30 characters max.

    The parametrized id is the case that matters — ``test_x[1-2]`` is not a legal directory
    name on Windows, and truncation is what keeps a long parametrized id from blowing the
    path limit.
    """
    factory = TempPathFactory()
    try:
        assert _mk_tmp(_RequestStub("test_x[a-b]"), factory).name.startswith("test_x_a_b_")
        long_name = _mk_tmp(_RequestStub("test_" + "z" * 60), factory).name
        assert len(long_name.rstrip("0123456789")) == 30
    finally:
        factory.cleanup()


# ---------------------------------------------------------------------------
# mocker
# ---------------------------------------------------------------------------


class _Holder:
    value = "original"


def test_stopall_unwinds_newest_first() -> None:
    """``MockCache.clear``'s ``reversed`` — the reason the cache is a list.

    Two patches of one attribute nest. Stopping them in registration order restores the
    *intermediate* value permanently, which is a silently wrong global left behind rather
    than an error.
    """
    mocker = MockerFixture()
    _ = mocker.patch.object(_Holder, "value", "first")
    _ = mocker.patch.object(_Holder, "value", "second")
    assert _Holder.value == "second"
    mocker.stopall()
    assert _Holder.value == "original"


def test_stopall_is_idempotent() -> None:
    """The fixture's teardown calls it; a test may have called it already."""
    mocker = MockerFixture()
    _ = mocker.patch.object(_Holder, "value", "patched")
    mocker.stopall()
    mocker.stopall()
    assert _Holder.value == "original"


def test_stop_takes_one_patch_and_refuses_a_stranger() -> None:
    mocker = MockerFixture()
    try:
        patched = mocker.patch.object(_Holder, "value", "patched")
        mocker.stop(patched)
        assert _Holder.value == "original"
        with pytest.raises(ValueError, match="not registered"):
            mocker.stop(mocker.MagicMock())
    finally:
        mocker.stopall()


def test_resetall_skips_a_patch_that_did_not_produce_a_mock() -> None:
    """pytest-mock issue #237: ``patch.dict`` hands back a plain ``dict``.

    ``resetall`` has to walk past it rather than call ``reset_mock`` on it, and the guard is
    a ``hasattr`` in the oracle — reproduced, and pinned, because the obvious refactor
    ("reset everything in the cache") reintroduces the ``AttributeError``.
    """
    mocker = MockerFixture()
    try:
        data: dict[str, int] = {"a": 1}
        _ = mocker.patch.dict(data, {"b": 2})
        mock = mocker.patch.object(_Holder, "value", mocker.MagicMock())
        mock()
        mocker.resetall()
        assert mock.call_count == 0
    finally:
        mocker.stopall()


def test_spy_records_the_return_and_the_exception() -> None:
    class Thing:
        def ok(self, n: int) -> int:
            return n * 2

        def bad(self) -> None:
            raise ValueError("nope")

    mocker = MockerFixture()
    try:
        thing = Thing()
        good = mocker.spy(thing, "ok")
        assert thing.ok(3) == 6
        good.assert_called_once_with(3)
        assert good.spy_return == 6
        assert good.spy_return_list == [6]
        assert good.spy_exception is None

        bad = mocker.spy(thing, "bad")
        with pytest.raises(ValueError):
            thing.bad()
        assert isinstance(bad.spy_exception, ValueError)
        assert bad.spy_return is None
    finally:
        mocker.stopall()


def test_a_stub_is_callable_and_has_no_auto_attributes() -> None:
    """The ``spec`` is what does both — see ``_accepts_anything``."""
    stub = MockerFixture().stub(name="cb")
    stub(1, key=2)
    stub.assert_called_once_with(1, key=2)
    with pytest.raises(AttributeError):
        _ = stub.not_a_real_attribute


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def _config(tmp_path: Path) -> Config:
    return Config(tmp_path, tmp_path, {"python_files": ["test_*.py"], "asyncio_mode": "auto"})


def test_getini_answers_the_carried_names(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.getini("python_files") == ["test_*.py"]
    assert config.getini("asyncio_mode") == "auto"


def test_getini_words_its_two_refusals_differently(tmp_path: Path) -> None:
    """A real pytest ini this worker does not carry is not a typo, and must not read as one."""
    config = _config(tmp_path)
    with pytest.raises(ValueError, match="not available to a rustest v2 worker"):
        _ = config.getini("markers")
    with pytest.raises(ValueError, match="unknown configuration value"):
        _ = config.getini("not_an_ini_name_at_all")


def test_getoption_returns_a_default_and_otherwise_refuses(tmp_path: Path) -> None:
    """The v2 wire carries no options, so a fabricated answer is the thing to avoid."""
    config = _config(tmp_path)
    assert config.getoption("verbose", 0) == 0
    with pytest.raises(ValueError, match="no option named 'verbose'"):
        _ = config.getoption("verbose")


def test_config_carries_the_paths_the_worker_knows(tmp_path: Path) -> None:
    config = Config(tmp_path, tmp_path / "sub", {})
    assert config.rootpath == tmp_path
    assert config.invocation_params.dir == tmp_path / "sub"
    assert config.inipath is None
    assert isinstance(config.cache, Cache)


def test_the_worker_configures_the_builtins_from_init(tmp_path: Path) -> None:
    """``handle_init`` is the only place the fixtures' whole-run context comes from."""
    saved = builtins_mod._context  # pyright: ignore[reportPrivateUsage]
    saved_state = worker._state  # pyright: ignore[reportPrivateUsage]
    try:
        _ = worker.handle_init(
            {
                "rootdir": str(tmp_path),
                "invocation_dir": str(tmp_path / "sub"),
                "asyncio_mode": "strict",
                "asyncio_default_test_loop_scope": "session",
            }
        )
        config = builtins_mod.current_config()
        assert config.rootpath == tmp_path
        assert config.invocation_params.dir == tmp_path / "sub"
        assert config.getini("asyncio_mode") == "strict"
        assert config.getini("asyncio_default_test_loop_scope") == "session"
        # Unset stays unset, and is *carried* as such: `None` is the option's real third
        # state, so a caller has to be able to tell it from "not on the wire".
        assert config.getini("asyncio_default_fixture_loop_scope") is None
    finally:
        builtins_mod._context = saved  # pyright: ignore[reportPrivateUsage]
        worker._state = saved_state  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# the protocol channel
# ---------------------------------------------------------------------------


_DETACH_PROBE = """
import os
import sys

import rustest._v2_worker as worker

protocol = worker._detach_protocol_stream(sys.stdout)
os.write(1, b"this must not reach the protocol\\n")
print("neither must this")
protocol.write("PROTOCOL-ONLY\\n")
protocol.flush()
"""


def test_detach_moves_the_protocol_off_fd_one() -> None:
    """The structural half of the raw-fd-write fix, asserted across a real process boundary.

    In-process this cannot be tested honestly: pytest's own capture owns fd 1 and fd 2, so
    "what came out of stdout" is a question only a subprocess can answer. After the detach,
    the child's **stdout** must contain the protocol line and nothing else, and both the raw
    descriptor write and the ``print`` must land on **stderr**.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _DETACH_PROBE],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "PROTOCOL-ONLY\n"
    assert "this must not reach the protocol" in proc.stderr
    assert "neither must this" in proc.stderr


def test_reset_capture_is_safe_to_call_twice() -> None:
    """The isolation helper calls it on entry and on exit, including when nothing was built."""
    worker.reset_capture()
    worker.reset_capture()
    assert worker._capture is None  # pyright: ignore[reportPrivateUsage]


def test_disabling_an_already_suspended_capture_leaves_it_suspended() -> None:
    """The ``finally`` in ``_Capture.disabled`` must not *start* a capture that was off.

    ``FdCapture.suspend`` is idempotent and ``FdCapture.resume`` is not conditional, so a
    naive suspend/resume pair called between tests would leave the capture live — and the
    next boundary drain's teardown output would be attributed to whichever test ran next.
    pytest guards the same way (``global_and_fixture_disabled``, l. 840-843).
    """
    saved = worker._capture  # pyright: ignore[reportPrivateUsage]
    try:
        worker.reset_capture()
        capture = worker._worker_capture()  # pyright: ignore[reportPrivateUsage]
        with capture.window():
            pass  # builds the two fd captures, then suspends them again
        out = capture._out  # pyright: ignore[reportPrivateUsage]
        assert out is not None and not out.started
        with capture.disabled():
            pass
        assert not out.started, "disabled() resumed a capture that was not running"
    finally:
        worker.reset_capture()
        worker._capture = saved  # pyright: ignore[reportPrivateUsage]


def test_the_worker_capture_is_shared_and_rebuilt_after_a_reset() -> None:
    saved = worker._capture  # pyright: ignore[reportPrivateUsage]
    try:
        worker.reset_capture()
        first = worker._worker_capture()  # pyright: ignore[reportPrivateUsage]
        assert worker._worker_capture() is first  # pyright: ignore[reportPrivateUsage]
        worker.reset_capture()
        assert worker._worker_capture() is not first  # pyright: ignore[reportPrivateUsage]
    finally:
        worker.reset_capture()
        worker._capture = saved  # pyright: ignore[reportPrivateUsage]


def test_every_registered_builtin_exists_and_is_a_fixture() -> None:
    """``BUILTIN_FIXTURES`` is a list of names; a typo in it is a lookup failure at run time."""
    for name in worker.BUILTIN_FIXTURES:
        func: Any = getattr(builtins_mod, name)
        assert getattr(func, "__rustest_fixture__", False), name
