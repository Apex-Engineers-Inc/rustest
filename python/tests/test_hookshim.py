"""``rustest._hookshim``: real conftest hooks run against the strict shim, or hand off.

The fixtures here are the hook bodies of real suites, copied verbatim (the provenance is in
each docstring), so what is proved is that **those** hooks run to completion against the shim and
have the effects pytest gives them.  Where an effect is observable from outside, the same
conftest is run under real pytest in a subprocess and the two are compared.

The supported surface, and the real suite that exercises each piece:

| attribute | exercised by |
|---|---|
| ``config.addinivalue_line("markers", ...)`` | pillow (inside ``try/except Exception``), networkx |
| ``config.getoption("--opt")``, ``config.option.x`` (declared defaults) | networkx (``--backend``, ``--runslow``), member-designer ``slow_guard`` |
| ``config.getini`` for ``addini`` keys, and ``markers`` | member-designer ``slow_guard`` (``addini``) |
| ``config.hook.pytest_deselected(items=...)`` | member-designer ``shards.py`` |
| user attributes on ``config`` (``config.backend = ...``) | networkx |
| ``hasattr(config, "workerinput")`` is false | member-designer ``shards.py`` |
| ``config.rootpath``, ``config.invocation_params.dir`` | pytest's documented ``Config`` contract |
| ``parser.addoption``, ``getgroup().addoption``, ``addini`` | networkx (literal ``addoption``), pytest docs |
| ``item.nodeid`` / ``name`` / ``path`` / ``config`` | member-designer ``shards.py`` (``nodeid``) |
| ``item.keywords`` (``in`` only) | networkx (``"slow" in item.keywords``) |
| ``item.get_closest_marker`` | dateutil |
| ``item.add_marker`` | dateutil, networkx |
| ``item.iter_markers`` | pytest's documented ``Node`` contract |
| ``isinstance(item, pytest.Function)`` | the common ``modifyitems`` filter |
| ``items[:] = ...`` | member-designer ``shards.py`` |
| ``session.config`` | pytest's documented ``Session`` contract |

Hooks that run during or after execution (``runtest_setup``, ``sessionfinish``, ``unconfigure``,
``itemcollected``) are not run natively: defining one hands the session off.

Everything else hands off, which the second half of the file pins one attribute at a time.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import warnings
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from rustest._hookshim import (
    HANDED_OFF_HOOKS,
    NATIVE_HOOKS,
    CollectionEffects,
    HookRunner,
    HookUnsupported,
    ItemRecord,
    Mark,
)

from .helpers import clean_env, make_tree, run


def _module(tmp_path: Path, name: str, source: str) -> ModuleType:
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(
    nodeid: str,
    *marks: Mark,
    parents: tuple[Mark, ...] = (),
    function_attrs: tuple[str, ...] = (),
    **fields: Any,
) -> ItemRecord:
    """A record for ``nodeid``: ``marks`` are the item's own, ``parents`` its enclosing nodes'."""
    path, _, rest = nodeid.partition("::")
    return ItemRecord(
        nodeid,
        rest.split("::")[-1],
        f"/root/{path}",
        markers=(*marks, *parents),
        own_marker_count=len(marks),
        module_name=path.rsplit("/", 1)[-1],
        function_attrs=frozenset(function_attrs),
        **fields,
    )


def _runner(**kwargs: Any) -> HookRunner:
    return HookRunner(rootpath="/root", **kwargs)


def _modify(runner: HookRunner, records: list[ItemRecord]) -> CollectionEffects:
    """Both ``modifyitems`` stages back to back, as for a run with no ``-k`` / ``-m``."""
    first = runner.modify_collection_before_selection(records)
    if runner.reasons:
        return first
    by_id = {r.nodeid: r for r in records}
    second = runner.modify_collection_after_selection([by_id[n] for n in first.order])
    return CollectionEffects(
        second.order,
        first.deselected + second.deselected,
        first.vanished + second.vanished,
        first.marks + second.marks,
    )


def _real_pytest(
    tree: Path, args: list[str], env: dict[str, str] | None = None, *, cache: bool = False
):
    full = clean_env("PYTEST_ADDOPTS", "PYTEST_PLUGINS", *(env or {}))
    full.update(env or {})
    full["PYTHONPATH"] = os.pathsep.join(filter(None, [str(tree), full.get("PYTHONPATH", "")]))
    plugins = [] if cache else ["-p", "no:cacheprovider"]
    return run([sys.executable, "-m", "pytest", *plugins, *args], tree, full)


def _compat_source(source: str) -> str:
    """A conftest written for pytest, loaded in this process against rustest's ``pytest``."""
    return source.replace("import pytest", "from rustest.compat import pytest")


# --------------------------------------------------------------------------------------
# attrs: `pytest_configure` that never touches `config` (tests/conftest.py, verbatim)
# --------------------------------------------------------------------------------------

_ATTRS = """
from datetime import timedelta
import pytest

class _Settings:
    calls = []
    def register_profile(self, name, profile): self.calls.append(("register", name))
    def load_profile(self, name): self.calls.append(("load", name))
    def __call__(self, **kw): return kw

settings = _Settings()

class HealthCheck:
    too_slow = "too_slow"

def pytest_configure(config):
    # HealthCheck.too_slow causes more trouble than good -- especially in CIs.
    settings.register_profile(
        "patience",
        settings(
            suppress_health_check=[HealthCheck.too_slow],
            deadline=timedelta(milliseconds=400),
        ),
    )
    settings.load_profile("patience")
"""


def test_attrs_configure_runs_and_touches_nothing_on_config(tmp_path: Path) -> None:
    """attrs' ``tests/conftest.py::pytest_configure`` (hypothesis stubbed): a hook that only has
    process-global effects, which is why it must run in *every* worker."""
    module = _module(tmp_path, "attrs_conftest", _ATTRS)
    runner = _runner()
    runner.register("tests/conftest.py", module)

    runner.configure()

    assert runner.reasons == []
    assert module.settings.calls == [("register", "patience"), ("load", "patience")]  # pyright: ignore


# --------------------------------------------------------------------------------------
# pillow: `addinivalue_line` inside `except Exception` (Tests/conftest.py, verbatim)
# --------------------------------------------------------------------------------------

_PILLOW = """
import pytest

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "pil_noop_mark: A conditional mark where nothing special happens",
    )

    # We're marking some tests to ignore valgrind errors and XFAIL them.
    # Ensure that the mark is defined
    # even in cases where pytest-valgrind isn't installed
    try:
        config.addinivalue_line(
            "markers",
            "valgrind_known_error: Tests that have known issues with valgrind",
        )
    except Exception:
        # valgrind is already installed
        pass
"""


def test_pillow_configure_registers_the_markers_real_pytest_registers(tmp_path: Path) -> None:
    module = _module(tmp_path, "pillow_conftest", _PILLOW)
    runner = _runner(ini={"markers": "slow: a slow test\n"})
    runner.register("Tests/conftest.py", module)

    runner.configure()

    assert runner.reasons == []
    assert runner.markers == [
        "slow: a slow test",
        "pil_noop_mark: A conditional mark where nothing special happens",
        "valgrind_known_error: Tests that have known issues with valgrind",
    ]

    tree = make_tree(
        tmp_path,
        "pillow_tree",
        {
            "conftest.py": textwrap.dedent(_PILLOW),
            "pytest.ini": "[pytest]\nmarkers =\n    slow: a slow test\n",
        },
    )
    shown = _real_pytest(tree, ["--markers"]).stdout
    for line in runner.markers:
        name, _, description = line.partition(": ")
        assert f"@pytest.mark.{name}: {description}" in shown


# --------------------------------------------------------------------------------------
# dateutil: `get_closest_marker` + `add_marker` (tests/conftest.py, verbatim)
# --------------------------------------------------------------------------------------

_DATEUTIL = """
import os
import pytest


# Configure pytest to ignore xfailing tests
# See: https://stackoverflow.com/a/53198349/467366
def pytest_collection_modifyitems(items):
    for item in items:
        marker_getter = getattr(item, 'get_closest_marker', None)

        # Python 3.3 support
        if marker_getter is None:
            marker_getter = item.get_marker

        marker = marker_getter('xfail')

        # Need to query the args because conditional xfail tests still have
        # the xfail mark even if they are not expected to fail
        if marker and (not marker.args or marker.args[0]):
            item.add_marker(pytest.mark.no_cover)
"""

_DATEUTIL_TESTS = """
import pytest

def test_plain(): pass

@pytest.mark.xfail
def test_xfail(): assert 0

@pytest.mark.xfail(False)
def test_conditional_off(): pass

@pytest.mark.xfail(True, reason="r")
def test_conditional_on(): assert 0
"""

_RECORD_NO_COVER = """
import os
import pytest

@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items):
    names = [i.nodeid for i in items if i.get_closest_marker("no_cover")]
    with open(os.environ["NO_COVER_LOG"], "w") as handle:
        handle.write("\\n".join(names))
"""


def test_dateutil_modifyitems_marks_the_items_pytest_marks(tmp_path: Path) -> None:
    module = _module(tmp_path, "dateutil_conftest", _DATEUTIL)
    runner = _runner()
    runner.register("tests/conftest.py", module)
    records = [
        _record("test_d.py::test_plain"),
        _record("test_d.py::test_xfail", Mark("xfail")),
        _record("test_d.py::test_conditional_off", Mark("xfail", (False,))),
        _record("test_d.py::test_conditional_on", Mark("xfail", (True,), {"reason": "r"})),
    ]

    effects = _modify(runner, records)

    assert runner.reasons == []
    assert [m.nodeid for m in effects.marks] == [
        "test_d.py::test_xfail",
        "test_d.py::test_conditional_on",
    ]
    assert {m.mark.name for m in effects.marks} == {"no_cover"}
    assert effects.order == [r.nodeid for r in records] and not effects.deselected

    log = tmp_path / "no_cover.log"
    tree = make_tree(
        tmp_path,
        "dateutil_tree",
        {
            "conftest.py": textwrap.dedent(_DATEUTIL),
            "rec_plugin.py": textwrap.dedent(_RECORD_NO_COVER),
            "test_d.py": textwrap.dedent(_DATEUTIL_TESTS),
        },
    )
    _ = _real_pytest(tree, ["-p", "rec_plugin", "--collect-only", "-q"], {"NO_COVER_LOG": str(log)})
    assert log.read_text(encoding="utf-8").split() == [m.nodeid for m in effects.marks]


# --------------------------------------------------------------------------------------
# networkx: addoption + getoption + user attribute + keywords + add_marker
# (networkx/conftest.py, verbatim except the `networkx`-specific backend blocks)
# --------------------------------------------------------------------------------------

_NETWORKX = """
import os
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--runslow", action="store_true", default=False, help="run slow tests"
    )
    parser.addoption(
        "--backend",
        action="store",
        default=None,
        help="Run tests with a backend by auto-converting nx graphs to backend graphs",
    )
    parser.addoption(
        "--fallback-to-nx",
        action="store_true",
        default=False,
        help="Run nx function if a backend doesn't implement a dispatchable function"
        " (use with --backend)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: mark test as slow to run")
    backend = config.getoption("--backend")
    if backend is None:
        backend = os.environ.get("NETWORKX_TEST_BACKEND")
    config.backend = backend
    if backend:
        fallback_to_nx = config.getoption("--fallback-to-nx")
        if not fallback_to_nx:
            fallback_to_nx = os.environ.get("NETWORKX_FALLBACK_TO_NX")


def pytest_collection_modifyitems(config, items):
    if config.backend:
        raise RuntimeError("the --backend blocks are not part of this fixture")

    if config.getoption("--runslow"):
        # --runslow given in cli: do not skip slow tests
        return
    skip_slow = pytest.mark.skip(reason="need --runslow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
"""

_NETWORKX_TESTS = """
import pytest

def test_fast(): pass

@pytest.mark.slow
def test_slow(): pass

@pytest.mark.slow
def test_slow_too(): pass
"""


def test_networkx_conftest_skips_slow_tests_as_pytest_does(tmp_path: Path) -> None:
    module = _module(tmp_path, "networkx_conftest", _NETWORKX)
    runner = _runner()
    runner.register("networkx/conftest.py", module)
    runner.configure()
    runner.sessionstart()
    records = [
        _record("test_n.py::test_fast"),
        _record("test_n.py::test_slow", Mark("slow")),
        _record("test_n.py::test_slow_too", Mark("slow")),
    ]

    effects = _modify(runner, records)

    assert runner.reasons == []
    assert runner.markers == ["slow: mark test as slow to run"]
    skipped = [(m.nodeid, m.mark.name, dict(m.mark.kwargs)) for m in effects.marks]
    assert skipped == [
        ("test_n.py::test_slow", "skip", {"reason": "need --runslow option to run"}),
        ("test_n.py::test_slow_too", "skip", {"reason": "need --runslow option to run"}),
    ]

    tree = make_tree(
        tmp_path,
        "networkx_tree",
        {"conftest.py": textwrap.dedent(_NETWORKX), "test_n.py": textwrap.dedent(_NETWORKX_TESTS)},
    )
    result = _real_pytest(tree, ["-rs", "-q"])
    assert "2 skipped" in result.stdout and "1 passed" in result.stdout
    assert "need --runslow option to run" in result.stdout


def test_user_attributes_round_trip_and_workerinput_is_absent(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "attrs_on_config",
        """
        got = {}
        def pytest_configure(config):
            config.backend = "nx"
            got["has_workerinput"] = hasattr(config, "workerinput")
            got["workeroutput"] = getattr(config, "workeroutput", "absent")

        def pytest_sessionstart(session):
            got["backend"] = session.config.backend
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)
    runner.configure()
    runner.sessionstart()
    assert runner.reasons == []
    assert module.got == {"has_workerinput": False, "workeroutput": "absent", "backend": "nx"}  # pyright: ignore


# --------------------------------------------------------------------------------------
# member-designer shards.py: trylast hookimpl, `config.hook.pytest_deselected`, `items[:] =`
# (main/server/tests/shards.py, the hook and everything it calls, verbatim)
# --------------------------------------------------------------------------------------

_SHARDS = '''
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import pytest

SHARD_ENV = "APEX_TEST_SHARD"
TIMES_ENV = "APEX_TEST_SHARD_TIMES"
WEIGHTS_PATH = Path(__file__).with_name("shard_weights.json")
UNKNOWN_FILE_SECONDS = 1.0


def parse_shard(value: str) -> tuple[int, int]:
    """``"2/4"`` -> ``(2, 4)``; shards count from 1."""
    index, _, count = value.partition("/")
    if not (index.isdigit() and count.isdigit() and 1 <= int(index) <= int(count)):
        raise pytest.UsageError(f"{SHARD_ENV} must look like 2/4 (shard 2 of 4), not {value!r}")
    return int(index), int(count)


def assign_shards(files: set[str], weights: dict[str, float], count: int) -> list[set[str]]:
    """Each file into one of ``count`` shards, largest first into the least-loaded shard (ties by
    name, so every xdist worker of a job picks the same files)."""
    shards: list[set[str]] = [set() for _ in range(count)]
    loads = [0.0] * count
    for file in sorted(files, key=lambda f: (-weights.get(f, UNKNOWN_FILE_SECONDS), f)):
        lightest = loads.index(min(loads))
        shards[lightest].add(file)
        loads[lightest] += weights.get(file, UNKNOWN_FILE_SECONDS)
    return shards


def _file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def _load_weights() -> dict[str, float]:
    return json.loads(WEIGHTS_PATH.read_text(encoding="utf-8")) if WEIGHTS_PATH.exists() else {}


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not (value := os.environ.get(SHARD_ENV)):
        return
    index, count = parse_shard(value)
    mine = assign_shards({_file_of(item.nodeid) for item in items}, _load_weights(), count)[index - 1]
    deselected = [item for item in items if _file_of(item.nodeid) not in mine]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = [item for item in items if _file_of(item.nodeid) in mine]


def pytest_configure(config: pytest.Config) -> None:
    if (path := os.environ.get(TIMES_ENV)) and not hasattr(config, "workerinput"):
        config.pluginmanager.register(object())
'''

_SHARD_FILES = {f"test_{name}.py": "def test_x(): pass\ndef test_y(): pass\n" for name in "abcde"}


def test_shards_deselects_the_files_pytest_deselects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module(tmp_path, "shards", _SHARDS)
    monkeypatch.setenv("APEX_TEST_SHARD", "1/2")
    runner = _runner()
    runner.register("tests/shards.py", module)
    runner.configure()  # TIMES unset: `hasattr(config, "workerinput")` is never reached
    records = [_record(f"test_{n}.py::test_{t}") for n in "abcde" for t in "xy"]

    effects = _modify(runner, records)

    assert runner.reasons == []
    assert (
        effects.vanished == [] and effects.deselected
    )  # counted, via config.hook.pytest_deselected
    assert effects.order and set(effects.order).isdisjoint(effects.deselected)

    tree = make_tree(
        tmp_path, "shards_tree", {"shards_plugin.py": textwrap.dedent(_SHARDS), **_SHARD_FILES}
    )
    result = _real_pytest(
        tree, ["-p", "shards_plugin", "--collect-only", "-q"], {"APEX_TEST_SHARD": "1/2"}
    )
    real_ids = [line for line in result.stdout.splitlines() if "::" in line]
    assert real_ids == effects.order
    assert f"{len(effects.deselected)} deselected" in result.stdout


def test_shards_registering_a_plugin_hands_the_session_to_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``config.pluginmanager.register`` is exactly what the runtime does not do, and
    ``hasattr(config, "workerinput")`` must answer False (a controller) to get that far."""
    module = _module(tmp_path, "shards_times", _SHARDS)
    monkeypatch.setenv("APEX_TEST_SHARD_TIMES", str(tmp_path / "times.json"))
    runner = _runner()
    runner.register("tests/shards.py", module)

    runner.configure()

    assert runner.reasons == [
        "tests/shards.py::pytest_configure uses config.pluginmanager.register, "
        "which rustest's hook runtime does not support"
    ]


# --------------------------------------------------------------------------------------
# the gap rule: anything unsupported records, raises a BaseException, and hands off
# --------------------------------------------------------------------------------------


def test_a_gap_swallowed_by_except_exception_still_hands_off(tmp_path: Path) -> None:
    """pillow's pattern: the hook wraps its ``config`` use in ``except Exception: pass``, and
    ``config.rootdir`` is not in the shim.  The raise is a ``BaseException``, so it escapes."""
    module = _module(
        tmp_path,
        "swallow",
        """
        def pytest_configure(config):
            try:
                config.rootdir
            except Exception:
                pass
            config.after_the_swallow = True
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)

    runner.configure()

    assert runner.reasons == [
        "conftest.py::pytest_configure uses config.rootdir, which rustest's hook runtime does not support"
    ]


def test_a_bare_except_cannot_erase_the_evidence(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "bare",
        """
        def pytest_configure(config):
            try:
                config.inifile
            except:
                pass
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)
    runner.configure()
    assert runner.reasons and "config.inifile" in runner.reasons[0]


def test_the_shim_exception_is_not_an_exception() -> None:
    assert not issubclass(HookUnsupported, Exception) and issubclass(HookUnsupported, BaseException)


def test_after_a_violation_no_more_hooks_run(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "stops",
        """
        calls = []
        def pytest_configure(config):
            calls.append("configure")
            config.rootdir
        def pytest_sessionstart(session):
            calls.append("sessionstart")
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)
    runner.configure()
    runner.sessionstart()
    assert module.calls == ["configure"]  # pyright: ignore[reportAttributeAccessIssue]


_GAPS = {
    "config.rootdir": "config.rootdir",
    "config.inifile": "config.inifile",
    "config.invocation_params": "config.invocation_params",
    "config.cache": "config.cache",
    "config.pluginmanager.hasplugin('x')": "config.pluginmanager.hasplugin",
    "config.getoption('--undeclared')": "config.getoption('--undeclared')",
    "config.getoption('verbose', default=0)": "config.getoption('verbose')",
    "config.option.undeclared": "config.option.undeclared",
    "config.getini('testpaths')": "config.getini('testpaths')",
    "config.addinivalue_line('filterwarnings', 'x')": "config.addinivalue_line('filterwarnings'",
    "setattr(config, 'rootpath', 1)": "config.rootpath",
    "setattr(config.option, 'x', 1)": "config.option.x",
    "config.hook.pytest_collection_modifyitems": "config.hook.pytest_collection_modifyitems",
    "config.stash": "config.stash",
    "config._xyz": "config._xyz",
    "config.__dict__": "config.__dict__",
    "vars(config.option)": "config.option.__dict__",
}


@pytest.mark.parametrize(("expression", "named"), list(_GAPS.items()))
def test_every_unsupported_config_attribute_is_a_hand_off_reason(
    tmp_path: Path, expression: str, named: str
) -> None:
    module = _module(tmp_path, "gap_config", f"def pytest_configure(config):\n    {expression}\n")
    runner = _runner()
    runner.register("conftest.py", module)

    runner.configure()

    assert len(runner.reasons) == 1, expression
    assert named in runner.reasons[0] and runner.reasons[0].startswith(
        "conftest.py::pytest_configure "
    )


_ITEM_GAPS = {
    "item.fspath": "item.fspath",
    "item.obj": "item.obj",
    "item.cls": "item.cls",
    "item.module": "item.module",
    "item.function": "item.function",
    "item.parent": "item.parent",
    "item.session": "item.session",
    "item.own_markers": "item.own_markers",
    "item.fixturenames": "item.fixturenames",
    "item.user_properties": "item.user_properties",
    "list(item.keywords)": "item.keywords",
    "item.keywords['x']": "item.keywords",
    "item.originalname": "item.originalname",
    "setattr(item, 'extra', 1)": "item.extra",
    "item.add_marker(object())": "item.add_marker something that is not a mark",
    "item.listchain()": "item.listchain",
}


@pytest.mark.parametrize(("expression", "named"), list(_ITEM_GAPS.items()))
def test_every_unsupported_item_attribute_is_a_hand_off_reason(
    tmp_path: Path, expression: str, named: str
) -> None:
    module = _module(
        tmp_path,
        "gap_item",
        f"def pytest_collection_modifyitems(items):\n    for item in items:\n        {expression}\n",
    )
    runner = _runner()
    runner.register("conftest.py", module)

    effects = _modify(runner, [_record("t.py::t")])

    assert len(runner.reasons) == 1, expression
    assert named.split(" ")[0] in runner.reasons[0]
    assert effects.order == []  # nothing is applied from a hook that handed off


def test_session_exposes_only_config(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "session_gap",
        """
        def pytest_sessionstart(session):
            assert session.config is not None
            session.exitstatus = 5
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)
    runner.sessionstart()
    assert runner.reasons == [
        "conftest.py::pytest_sessionstart sets session.exitstatus, which rustest's hook runtime does not support"
    ]


# --------------------------------------------------------------------------------------
# options and ini: declared defaults, the recording parser
# --------------------------------------------------------------------------------------


def test_declared_defaults_follow_the_argparse_action(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "declare",
        """
        def pytest_addoption(parser, pluginmanager):
            parser.addoption("--flag", action="store_true")
            parser.addoption("--noflag", action="store_false")
            parser.addoption("--plain")
            parser.addoption("--many", action="append")
            parser.addoption("--count", "-C", action="count")
            parser.addoption("--level", type=int, default="3")
            parser.addoption("--given", default="x", dest="other")
            parser.getgroup("mine", "My options").addoption("--grouped", action="store", default=7)
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)
    probe = _module(
        tmp_path,
        "probe",
        """
        seen = {}
        def pytest_configure(config):
            for name in ("--flag", "--noflag", "--plain", "--many", "--count", "-C", "--level",
                         "--given", "other", "--grouped"):
                seen[name] = config.getoption(name)
            seen["option.plain"] = config.option.plain
            seen["option.other"] = config.option.other
        """,
    )
    runner.register("probe.py", probe)

    runner.configure()

    assert runner.reasons == []
    assert probe.seen == {  # pyright: ignore[reportAttributeAccessIssue]
        "--flag": False,
        "--noflag": True,
        "--plain": None,
        "--many": None,
        "--count": None,
        "-C": None,
        "--level": 3,
        "--given": "x",
        "other": "x",
        "--grouped": 7,
        "option.plain": None,
        "option.other": "x",
    }


def test_the_same_declarations_give_pytest_the_same_values(tmp_path: Path) -> None:
    source = """
        def pytest_addoption(parser):
            parser.addoption("--flag", action="store_true")
            parser.addoption("--noflag", action="store_false")
            parser.addoption("--plain")
            parser.addoption("--level", type=int, default="3")

        def pytest_configure(config):
            import json, os
            names = ("--flag", "--noflag", "--plain", "--level")
            with open(os.environ["VALUES"], "w") as handle:
                json.dump({n: config.getoption(n) for n in names}, handle)
        """
    module = _module(tmp_path, "same", source)
    runner = _runner()
    values = tmp_path / "shim.json"
    os.environ["VALUES"] = str(values)
    try:
        runner.register("conftest.py", module)
        runner.configure()
    finally:
        del os.environ["VALUES"]
    tree = make_tree(
        tmp_path, "same_tree", {"conftest.py": textwrap.dedent(source), "test_x.py": ""}
    )
    real = tmp_path / "real.json"
    _ = _real_pytest(tree, ["--collect-only", "-q"], {"VALUES": str(real)})
    assert runner.reasons == []
    assert values.read_text(encoding="utf-8") == real.read_text(encoding="utf-8")


def test_an_option_that_declares_something_the_shim_cannot_model_hands_off(tmp_path: Path) -> None:
    for line, named in (
        ('parser.addoption("--x", action="version")', "action='version'"),
        ('parser.addoption("--x", type=lambda v: v, default="1")', "--x with a custom type"),
        (
            'parser.addoption("--x", default=1)\n    parser.addoption("--x", default=2)',
            "--x, which is already an option",
        ),
    ):
        module = _module(tmp_path, "weird", f"def pytest_addoption(parser):\n    {line}\n")
        runner = _runner()
        runner.register("conftest.py", module)
        assert runner.reasons and named in runner.reasons[0], line


def test_an_option_that_collides_with_a_builtin_hands_off(tmp_path: Path) -> None:
    module = _module(
        tmp_path, "collide", 'def pytest_addoption(parser):\n    parser.addoption("--maxfail")\n'
    )
    runner = _runner(builtin_options={"--maxfail": 0, "maxfail": 0})
    runner.register("conftest.py", module)
    assert runner.reasons and "--maxfail, which is already an option" in runner.reasons[0]


def test_builtin_options_the_worker_supplies_are_readable(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "builtin_read",
        """
        got = {}
        def pytest_configure(config):
            got["verbose"] = config.getoption("verbose")
            got["maxfail"] = config.getoption("--maxfail")
            got["option"] = config.option.verbose
        """,
    )
    runner = _runner(builtin_options={"verbose": 1, "--maxfail": 2})
    runner.register("conftest.py", module)
    runner.configure()
    assert runner.reasons == []
    assert module.got == {"verbose": 1, "maxfail": 2, "option": 1}  # pyright: ignore


def test_an_option_declared_after_argparse_without_a_default_has_no_value(tmp_path: Path) -> None:
    """pytest sets an option's attribute at declaration only when a default was given; a
    conftest reached by the collection walk comes after the parse that fills in the rest."""
    late = _module(
        tmp_path,
        "late",
        'def pytest_addoption(parser):\n    parser.addoption("--late")\n'
        '    parser.addoption("--late-default", default="d")\n'
        'def pytest_configure(config):\n    config.getoption("--late-default")\n'
        '    config.getoption("--late")\n',
    )
    runner = _runner()
    runner.configure()
    runner.register("sub/conftest.py", late, initial=False)
    assert runner.reasons and "config.getoption('--late')" in runner.reasons[0]


def test_ini_declarations_and_types(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "ini",
        """
        got = {}
        def pytest_addoption(parser):
            parser.addini("plain", "p")
            parser.addini("flag", "f", type="bool", default=False)
            parser.addini("lines", "l", type="linelist")
            parser.addini("cmd", "c", type="args", default=["x"])
            parser.addini("count", "n", type="int")
            parser.addini("ratio", "r", type="float", default=0.5)

        def pytest_configure(config):
            for name in ("plain", "flag", "lines", "cmd", "count", "ratio"):
                got[name] = config.getini(name)
        """,
    )
    ini = {"flag": "yes", "lines": "a\n  b\n\n", "cmd": "-k 'not slow'", "count": "4"}
    runner = _runner(ini=ini)
    runner.register("conftest.py", module)
    runner.configure()
    assert runner.reasons == []
    assert module.got == {  # pyright: ignore
        "plain": "",
        "flag": True,
        "lines": ["a", "b"],
        "cmd": ["-k", "not slow"],
        "count": 4,
        "ratio": 0.5,
    }


def test_an_ini_type_the_shim_does_not_model_hands_off(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "ini_paths",
        'def pytest_addoption(parser):\n    parser.addini("p", "p", type="paths")\n'
        'def pytest_configure(config):\n    config.getini("p")\n',
    )
    runner = _runner(ini={"p": "a b"})
    runner.register("conftest.py", module)
    runner.configure()
    assert runner.reasons and "ini option p with type 'paths'" in runner.reasons[0]


def test_skip_true_on_an_unset_option_hands_off_instead_of_skipping(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "skipping",
        'def pytest_addoption(parser):\n    parser.addoption("--x")\n'
        'def pytest_configure(config):\n    config.getoption("--x", skip=True)\n',
    )
    runner = _runner()
    runner.register("conftest.py", module)
    runner.configure()
    assert runner.reasons and "skip=True" in runner.reasons[0]


# --------------------------------------------------------------------------------------
# items and the collection list
# --------------------------------------------------------------------------------------


def test_markers_are_looked_up_closest_first_and_add_marker_orders_like_pytest(
    tmp_path: Path,
) -> None:
    module = _module(
        tmp_path,
        "marks",
        """
        import pytest
        seen = []
        def pytest_collection_modifyitems(items):
            item = items[0]
            seen.append([m.args for m in item.iter_markers("m")])
            seen.append(item.get_closest_marker("m").args)
            item.add_marker("m")
            item.add_marker(pytest.mark.m("front"), append=False)
            seen.append([m.args for m in item.iter_markers("m")])
            seen.append(item.get_closest_marker("m").args)
            seen.append("m" in item.keywords)
            seen.append(item.get_closest_marker("absent", "dflt"))
            seen.append(item.nodeid + "|" + item.name + "|" + item.path.name)
        """,
    )
    runner = _runner(ini={"markers": "m: a marker"})
    runner.register("conftest.py", module)
    record = _record(
        "pkg/test_m.py::TestC::test_one",
        Mark("m", ("own",)),
        parents=(Mark("m", ("class",)), Mark("m", ("module",))),
    )

    effects = _modify(runner, [record])

    assert runner.reasons == []
    assert module.seen == [  # pyright: ignore[reportAttributeAccessIssue]
        [("own",), ("class",), ("module",)],
        ("own",),
        [("front",), ("own",), (), ("class",), ("module",)],
        ("front",),
        True,
        "dflt",
        "pkg/test_m.py::TestC::test_one|test_one|test_m.py",
    ]
    assert [(m.mark.name, m.mark.args, m.append) for m in effects.marks] == [
        ("m", (), True),
        ("m", ("front",), False),
    ]


def test_a_removed_item_is_counted_only_if_the_hook_said_so(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "removals",
        """
        def pytest_collection_modifyitems(config, items):
            quiet, loud, kept = items
            config.hook.pytest_deselected(items=[loud])
            items.remove(quiet)
            items.remove(loud)
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)

    effects = _modify(runner, [_record("a.py::q"), _record("a.py::l"), _record("a.py::k")])

    assert runner.reasons == []
    assert (effects.order, effects.deselected, effects.vanished) == (
        ["a.py::k"],
        ["a.py::l"],
        ["a.py::q"],
    )


def test_sort_and_slice_reorder_the_list(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "reorder",
        """
        def pytest_collection_modifyitems(items):
            items.sort(key=lambda item: item.name, reverse=True)
            items[:] = items[1:] + items[:1]
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)

    effects = _modify(runner, [_record(f"a.py::{n}") for n in "abc"])

    assert effects.order == ["a.py::b", "a.py::a", "a.py::c"]


@pytest.mark.parametrize(
    "body",
    [
        "items.append(items[0])",  # twice
        "items[:] = [object()]",  # something that is not an item
        "items.insert(0, __import__('types').SimpleNamespace(nodeid='x'))",
        "config.hook.pytest_deselected(items=[items[0]])",  # reported, but kept
        "config.hook.pytest_deselected(items=[object()])",
    ],
)
def test_a_hook_that_does_anything_else_to_the_list_hands_off(tmp_path: Path, body: str) -> None:
    module = _module(
        tmp_path,
        "oddlist",
        f"def pytest_collection_modifyitems(config, items):\n    {body}\n",
    )
    runner = _runner()
    runner.register("conftest.py", module)

    effects = _modify(runner, [_record("a.py::x"), _record("a.py::y")])

    assert runner.reasons and effects.order == []


@pytest.mark.parametrize("hook", sorted(HANDED_OFF_HOOKS))
def test_a_hook_that_runs_during_or_after_execution_hands_the_session_off(
    tmp_path: Path, hook: str
) -> None:
    """Their effects land after tests have run, where a hand-off would run them twice."""
    module = _module(tmp_path, "late_hook", f"def {hook}(*args):\n    pass\n")
    runner = _runner()
    runner.register("conftest.py", module)
    assert runner.reasons == [
        f"conftest.py::{hook} is a hook that runs during or after test execution, "
        "which rustest's hook runtime does not support"
    ]


def test_the_hook_sets_do_not_overlap() -> None:
    assert not NATIVE_HOOKS & HANDED_OFF_HOOKS
    assert NATIVE_HOOKS == {
        "pytest_addoption",
        "pytest_configure",
        "pytest_sessionstart",
        "pytest_collection_modifyitems",
    }


# --------------------------------------------------------------------------------------
# what a hook may be, and what it may do
# --------------------------------------------------------------------------------------


def test_wrappers_specnames_generators_and_odd_signatures_hand_off(tmp_path: Path) -> None:
    cases = {
        "wrapper": "@pytest.hookimpl(hookwrapper=True)\ndef pytest_configure(config):\n    yield",
        "newwrapper": "@pytest.hookimpl(wrapper=True)\ndef pytest_configure(config):\n    return (yield)",
        "specname": "@pytest.hookimpl(specname='pytest_configure')\ndef my_configure(config):\n    pass",
        "specname_elsewhere": "@pytest.hookimpl(specname='pytest_runtest_call')\ndef pytest_configure(config):\n    pass",
        "generator": "def pytest_configure(config):\n    yield",
        "unknown_arg": "def pytest_configure(config, nope):\n    pass",
    }
    for name, source in cases.items():
        module = _module(tmp_path, f"odd_{name}", "import pytest\n" + source + "\n")
        runner = _runner()
        runner.register("conftest.py", module)
        runner.configure()
        assert len(runner.reasons) == 1, name


_PLUGGY_ARGS = """
import os

def _log(text):
    with open(os.environ["HOOK_LOG"], "a") as handle:
        handle.write(text + "\\n")

def pytest_configure(config=None): _log(f"configure:{config}")
def pytest_sessionstart(session, *rest): _log(f"sessionstart:{rest}")
def pytest_collection_modifyitems(config, items=None): _log(f"modify:{items}")
"""


def test_hooks_are_called_as_pluggy_calls_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the parameters without defaults are passed (``items=None`` stays ``None``, and
    ``*rest`` is empty), which a hook that does nothing for ``None`` depends on."""
    tree = make_tree(
        tmp_path,
        "pluggy_args",
        {"conftest.py": textwrap.dedent(_PLUGGY_ARGS), "test_a.py": "def test_a(): pass\n"},
    )
    real_log = tmp_path / "real.log"
    _ = _real_pytest(tree, ["-q"], {"HOOK_LOG": str(real_log)})

    shim_log = tmp_path / "shim.log"
    monkeypatch.setenv("HOOK_LOG", str(shim_log))
    runner = _runner()
    runner.register("conftest.py", _module(tmp_path, "pluggy_args_conftest", _PLUGGY_ARGS))
    runner.configure()
    runner.sessionstart()
    _ = _modify(runner, [_record("test_a.py::test_a")])

    assert runner.reasons == []
    assert shim_log.read_text(encoding="utf-8") == real_log.read_text(encoding="utf-8")
    assert real_log.read_text(encoding="utf-8").split() == [
        "configure:None",
        "sessionstart:()",
        "modify:None",
    ]


@pytest.mark.parametrize("hook", ["pytest_configure", "pytest_collection_modifyitems"])
@pytest.mark.parametrize(
    "body",
    ["pytest.skip('s')", "pytest.fail('f')", "pytest.exit('e')", "raise SystemExit(3)"],
)
def test_an_outcome_or_exit_raised_by_a_hook_is_a_hand_off(
    tmp_path: Path, hook: str, body: str
) -> None:
    """pytest treats ``skip`` / ``fail`` / ``exit`` / ``SystemExit`` out of a hook each its own
    way (a skip there is not a test's outcome); none is emulated, all are recorded."""
    argument = "config" if hook == "pytest_configure" else "items"
    module = _module(
        tmp_path,
        "outcomes",
        f"from rustest.compat import pytest\ndef {hook}({argument}):\n    {body}\n",
    )
    runner = _runner()
    runner.register("conftest.py", module)

    if hook == "pytest_configure":
        runner.configure()
    else:
        _ = _modify(runner, [_record("a.py::t")])

    assert len(runner.reasons) == 1 and f"{hook} raised " in runner.reasons[0]


def test_a_keyboard_interrupt_in_a_hook_is_not_swallowed(tmp_path: Path) -> None:
    module = _module(
        tmp_path, "interrupt", "def pytest_configure(config):\n    raise KeyboardInterrupt\n"
    )
    runner = _runner()
    runner.register("conftest.py", module)
    with pytest.raises(KeyboardInterrupt):
        runner.configure()


def test_a_shim_kept_past_its_hook_goes_stale_instead_of_working(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backstop for what the reference-count check cannot see (it is switched off here): a
    kept shim must not work (nothing keeps it in step with the worker's real objects), and it is
    not a hand-off either (tests have run by then): each use raises inside the user and is
    listed in ``late_reasons``, which the worker must turn into an error of *that* test even if
    the user swallowed the exception."""
    monkeypatch.setattr(HookRunner, "_kept", lambda self, tracked, before: None)
    monkeypatch.setattr(HookRunner, "_items_released", lambda self, items, shown, before: True)
    module = _module(
        tmp_path,
        "stale",
        """
        saved = {}
        def pytest_configure(config):
            config.backend = "nx"
            saved["config"] = config
        def pytest_collection_modifyitems(items):
            saved["item"] = items[0]
            saved["keywords"] = items[0].keywords
            saved["mark"] = items[0].get_closest_marker("m")
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)
    runner.configure()
    _ = _modify(runner, [_record("a.py::t", Mark("m", (), {"k": 0}))])
    saved: dict[str, Any] = module.saved  # pyright: ignore[reportAttributeAccessIssue]
    assert runner.reasons == [] and runner.user_attributes == {"backend": "nx"}

    uses = {
        "rootpath": lambda: saved["config"].rootpath,
        "user attribute": lambda: saved["config"].backend,
        "workerinput": lambda: getattr(saved["config"], "workerinput", None),
        "item.nodeid": lambda: saved["item"].nodeid,
        "item.add_marker": lambda: saved["item"].add_marker("m"),
        "keywords": lambda: "m" in saved["keywords"],
        "mark kwargs write": lambda: saved["mark"].kwargs.update(k=1),
    }
    for label, use in uses.items():
        with pytest.raises(HookUnsupported) as raised:
            use()
        assert "after the hook returned" in raised.value.reason, label
    assert len(runner.late_reasons) == len(uses)
    assert runner.reasons == []  # not a hand-off
    assert saved["mark"].name == "m" and dict(saved["mark"].kwargs) == {"k": 0}  # plain data


_KEEPERS = {
    "global": "saved.append(config)",
    "closure": "saved.append(lambda: config)",
    "default argument": "saved.append(lambda c=config: c)",
    "attribute of an object": "object.__setattr__(holder, 'kept', config)",
    "session": "saved.append(session)",
    "config option": "saved.append(config.option)",
    "config hook": "saved.append(config.hook)",
    "pluginmanager": "saved.append(config.pluginmanager)",
    "stored exception": (
        "try:\n"
        "        raise RuntimeError('x')\n"
        "    except RuntimeError as exc:\n"
        "        saved.append(exc)"
    ),
}


@pytest.mark.parametrize("name", sorted(_KEEPERS))
def test_a_hook_that_keeps_a_shim_hands_off_before_any_test_runs(tmp_path: Path, name: str) -> None:
    """``CFG = config`` (and its disguises) is caught at the hook's return by the reference
    count, while a hand-off is still free."""
    source = (
        "saved = []\n"
        "class Holder: pass\n"
        "holder = Holder()\n"
        "def pytest_configure(config):\n"
        f"    {_KEEPERS[name]}\n"
        "    pass\n"
    )
    source = source.replace(
        "def pytest_configure(config):", "def pytest_configure(config, session=None):"
    )
    if name == "session":
        source = source.replace(
            "def pytest_configure(config, session=None):", "def pytest_sessionstart(session):"
        )
    module = _module(tmp_path, f"keeper_{abs(hash(name))}", source)
    runner = _runner()
    runner.register("conftest.py", module)
    runner.configure()
    runner.sessionstart()
    assert len(runner.reasons) == 1 and "kept a reference to" in runner.reasons[0], runner.reasons
    assert runner.late_reasons == []


def test_a_parser_group_or_item_part_kept_by_a_hook_hands_off(tmp_path: Path) -> None:
    parser_module = _module(
        tmp_path,
        "keeps_group",
        "saved = []\ndef pytest_addoption(parser):\n    saved.append(parser.getgroup('g'))\n",
    )
    runner = _runner()
    runner.register("conftest.py", parser_module)
    assert len(runner.reasons) == 1 and "kept a reference to parser.getgroup()" in runner.reasons[0]

    parser_kept = _module(
        tmp_path,
        "keeps_parser",
        "saved = []\ndef pytest_addoption(parser):\n    saved.append(parser)\n",
    )
    runner = _runner()
    runner.register("conftest.py", parser_kept)
    assert len(runner.reasons) == 1 and "kept a reference to parser" in runner.reasons[0]

    for body in (
        "saved.append(items[0])",
        "saved.append(items[0].keywords)",
        "saved.append(items[0].config)",
        "saved.append(iter(items[0].iter_markers()))",
    ):
        module = _module(
            tmp_path,
            f"keeps_item_{abs(hash(body))}",
            f"saved = []\ndef pytest_collection_modifyitems(items):\n    {body}\n",
        )
        runner = _runner()
        runner.register("conftest.py", module)
        effects = _modify(runner, [_record("a.py::t", Mark("m"))])
        assert len(runner.reasons) == 1 and "kept a reference to" in runner.reasons[0], body
        assert effects.order == [] and runner.late_reasons == []


def test_references_that_die_with_the_hook_are_not_a_retention(tmp_path: Path) -> None:
    """Locals, containers, a caught exception and a cycle that is already garbage: the same
    reference count, or a count that returns to it after a collection."""
    module = _module(
        tmp_path,
        "ephemeral",
        """
        def pytest_configure(config):
            local = [config, config]
            table = {"k": config.option}
            try:
                raise ValueError(config)
            except ValueError:
                pass
            def cycle():
                return config
            cycle.me = cycle

        def pytest_collection_modifyitems(config, items):
            for item in items:
                names = [item.nodeid, item.keywords, item.config]
                marks = list(item.iter_markers())
                def cycle():
                    return item
                cycle.me = cycle
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)
    runner.configure()
    effects = _modify(runner, [_record("a.py::t", Mark("m")), _record("a.py::u")])
    assert runner.reasons == [] and effects.order == ["a.py::t", "a.py::u"]


def test_a_shim_from_an_earlier_hook_is_not_valid_in_a_later_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each shim belongs to the phase that made it (the retention check normally catches the
    keeping first; it is switched off to reach this rule)."""
    monkeypatch.setattr(HookRunner, "_kept", lambda self, tracked, before: None)
    module = _module(
        tmp_path,
        "phases",
        """
        saved = []
        def pytest_addoption(parser):
            saved.append(parser)
        def pytest_configure(config):
            saved[0].addoption("--late-use")
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)
    runner.configure()
    assert len(runner.reasons) == 1 and "from an earlier hook" in runner.reasons[0]


def test_config_and_session_are_the_same_objects_in_every_hook(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "same_objects",
        """
        ids = []
        def pytest_configure(config):
            ids.append((id(config), id(config.option), id(config.invocation_params)))
        def pytest_sessionstart(session):
            c = session.config
            ids.append((id(c), id(c.option), id(c.invocation_params)))
            assert c.invocation_params is c.invocation_params
        """,
    )
    runner = _runner(invocation_dir="/work")
    runner.register("conftest.py", module)
    runner.configure()
    runner.sessionstart()
    assert runner.reasons == [] and len(set(module.ids)) == 1  # pyright: ignore


def test_marks_from_an_earlier_run_of_stage_one_are_not_applied_twice(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "twice_marks",
        """
        import pytest
        def pytest_collection_modifyitems(items):
            for item in items:
                item.add_marker(pytest.mark.skip)
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)
    records = [_record("a.py::t")]
    _ = runner.modify_collection_before_selection(records)
    second = runner.modify_collection_before_selection(records)  # a replan
    stage_two = runner.modify_collection_after_selection(records)
    assert len(second.marks) == 1 and stage_two.marks == []
    assert len(runner._st.added["a.py::t"]) == 1  # pyright: ignore[reportPrivateUsage]


def test_the_runners_option_lookups_answer_as_pytest_does_for_the_workers_config(
    tmp_path: Path,
) -> None:
    """``request.config.getoption("--db-url", default="postgres://")`` is ``sqlite://`` in pytest
    when a conftest declared ``dest="url", default="sqlite://"``: the declared value wins."""
    module = _module(
        tmp_path,
        "lookups",
        """
        def pytest_addoption(parser):
            parser.addoption("--db-url", dest="url", default="sqlite://")
            parser.addoption("--plain")
            parser.addini("lines", "l", type="linelist")
            parser.addini("level", "n", type="int", default=3)
        """,
    )
    runner = _runner(ini={"lines": "a\n b"})
    runner.register("conftest.py", module)

    assert runner.option_flags == {"--db-url": "url", "--plain": "plain"}
    assert runner.lookup_option("--db-url") == (True, "sqlite://")
    assert runner.lookup_option("url") == (True, "sqlite://")
    assert runner.lookup_option("--plain") == (True, None)
    assert runner.lookup_option("--never-declared") == (False, None)
    assert runner.lookup_ini("lines") == (True, ["a", "b"])
    assert runner.lookup_ini("level") == (True, 3)
    assert runner.lookup_ini("unknown") == (False, None)

    tree = make_tree(
        tmp_path,
        "lookups_tree",
        {
            "conftest.py": textwrap.dedent(
                """
                import json, os
                def pytest_addoption(parser):
                    parser.addoption("--db-url", dest="url", default="sqlite://")
                def pytest_configure(config):
                    with open(os.environ["OUT"], "w") as handle:
                        json.dump(config.getoption("--db-url", default="postgres://"), handle)
                """
            ),
            "test_x.py": "def test_x(): pass\n",
        },
    )
    out = tmp_path / "lookup.json"
    _ = _real_pytest(tree, ["-q"], {"OUT": str(out)})
    assert json.loads(out.read_text(encoding="utf-8")) == "sqlite://"


def test_every_option_string_pytests_core_registers_is_a_conflict(tmp_path: Path) -> None:
    """The literal was frozen from pytest 8.4.2 with plugin autoload disabled; this fails (on
    purpose) when the installed pytest registers a name the literal lacks."""
    from rustest._hookshim import (  # pyright: ignore[reportPrivateUsage]
        _PYTEST_CORE_DESTS,
        _PYTEST_CORE_FLAGS,
    )

    out = tmp_path / "opts.json"
    source = """
import json, os

def pytest_configure(config):
    parser = config._parser
    flags, dests = set(), set()
    for group in [parser._anonymous, *parser._groups]:
        for opt in group.options:
            flags.update(opt.names())
            if opt.dest:
                dests.add(opt.dest)
    with open(os.environ["OUT"], "w") as handle:
        json.dump([sorted(flags), sorted(dests)], handle)
"""
    tree = make_tree(tmp_path, "opts", {"conftest.py": source, "test_x.py": "def test_x(): pass\n"})
    _ = _real_pytest(
        tree, ["-q"], {"OUT": str(out), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}, cache=True
    )
    flags, dests = (set(v) for v in json.loads(out.read_text(encoding="utf-8")))
    assert flags <= _PYTEST_CORE_FLAGS, sorted(flags - _PYTEST_CORE_FLAGS)
    assert dests <= _PYTEST_CORE_DESTS, sorted(dests - _PYTEST_CORE_DESTS)
    module = _module(
        tmp_path, "core_conflict", 'def pytest_addoption(parser):\n    parser.addoption("--lf")\n'
    )
    runner = _runner()
    runner.register("conftest.py", module)
    assert runner.reasons and "--lf, which is already an option" in runner.reasons[0]


@pytest.mark.parametrize(
    "runner_kwargs",
    [
        {"builtin_options": {"strict_markers": True}},
        {"ini": {"filterwarnings": "error::pytest.PytestUnknownMarkWarning"}},
    ],
)
def test_an_unregistered_decorator_marker_is_refused_where_pytest_would_error(
    tmp_path: Path, runner_kwargs: dict[str, Any]
) -> None:
    """``item.add_marker(pytest.mark.unknown)``: pytest warns on creating the mark (an error
    under ``--strict-markers`` or ``filterwarnings = error``); rustest's ``pytest.mark`` does not."""
    source = (
        "from rustest.compat import pytest\n"
        "def pytest_collection_modifyitems(items):\n"
        "    items[0].add_marker(pytest.mark.unknown_zz)\n"
    )
    strict = _runner(**runner_kwargs)
    strict.register("conftest.py", _module(tmp_path, "strict_a", source))
    _ = _modify(strict, [_record("a.py::t")])
    assert (
        strict.reasons
        and "unregistered marker 'unknown_zz' under strict markers" in strict.reasons[0]
    )

    lax = _runner()
    lax.register("conftest.py", _module(tmp_path, "strict_b", source))
    _ = _modify(lax, [_record("a.py::t")])
    assert lax.reasons == []  # a warning-summary line in pytest; no outcome differs


def test_the_data_a_hook_leaves_behind_is_exported_for_the_workers_own_config(
    tmp_path: Path,
) -> None:
    module = _module(
        tmp_path,
        "export",
        """
        def pytest_addoption(parser):
            parser.addoption("--flag", action="store_true")
            parser.addoption("--level", default="2", type=int)
            parser.addini("lines", "l", type="linelist")
        def pytest_configure(config):
            config.addinivalue_line("markers", "slow: s")
            config.backend = "nx"
        def pytest_sessionstart(session):
            session.config.second = [1]
        """,
    )
    runner = _runner(ini={"markers": "a: first"})
    runner.register("conftest.py", module)
    runner.configure()
    runner.sessionstart()
    assert runner.reasons == []
    assert runner.user_attributes == {"backend": "nx", "second": [1]}
    assert runner.option_values == {"flag": False, "level": 2}
    assert runner.ini_declarations == {"lines": ("linelist", [])}
    assert runner.markers == ["a: first", "slow: s"]


def test_invocation_dir_is_offered_only_when_the_worker_supplied_it(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "invocation",
        "got = []\ndef pytest_configure(config):\n    got.append(config.invocation_params.dir)\n",
    )
    runner = _runner(invocation_dir=tmp_path)
    runner.register("conftest.py", module)
    runner.configure()
    assert runner.reasons == [] and module.got == [tmp_path]  # pyright: ignore

    bare = _runner()
    bare.register("conftest.py", module)
    bare.configure()
    assert bare.reasons and "config.invocation_params" in bare.reasons[0]


def test_copying_a_shim_is_a_violation_not_a_recursion_error(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "copying",
        """
        import copy
        def pytest_collection_modifyitems(items):
            copy.copy(items[0])
        """,
    )
    runner = _runner()
    runner.register("conftest.py", module)
    _ = _modify(runner, [_record("a.py::t")])
    assert len(runner.reasons) == 1 and "RecursionError" not in runner.reasons[0]


# --------------------------------------------------------------------------------------
# what pytest's own parser and Node reject, compared case by case with real pytest
# --------------------------------------------------------------------------------------

_DECLARATIONS = {
    "lowercase short": 'parser.addoption("-z", action="store_true")',
    "uppercase short": 'parser.addoption("-Z", action="store_true")',
    "bad long": 'parser.addoption("-xyz", action="store_true")',
    "bare dash": 'parser.addoption("-", action="store_true")',
    "no names": 'parser.addoption(action="store_true")',
    "duplicate name": '(parser.addoption("--dn"), parser.addoption("--dn"))',
    "bad ini type": 'parser.addini("zz", "h", type="nonsense")',
    "nargs": 'parser.addoption("--na", nargs="*")',
    "choices with a default outside them": 'parser.addoption("--ch", choices=["a"], default="b")',
}

_ITEM_ATTEMPTS = {
    "add a Mark": 'item.add_marker(item.get_closest_marker("skip"))',
    "add an int": "item.add_marker(3)",
    "add a decorator": "item.add_marker(pytest.mark.skip)",
    "add a registered string": 'item.add_marker("skip")',
}

_ATTEMPT_CONFTEST = """
import json
import os
import pytest

RESULTS = {{}}

def attempt(label, fn):
    try:
        fn()
        RESULTS[label] = "ok"
    except BaseException as exc:
        RESULTS[label] = type(exc).__name__

def pytest_addoption(parser):
{declarations}

def pytest_collection_modifyitems(items):
    item = items[0]
{attempts}
    with open(os.environ["OUT"], "w") as handle:
        json.dump(RESULTS, handle)
"""


def test_declarations_and_marks_are_rejected_exactly_when_pytest_rejects_them(
    tmp_path: Path,
) -> None:
    declarations = "\n".join(
        f"    attempt({label!r}, lambda: {code})" for label, code in _DECLARATIONS.items()
    )
    attempts = "\n".join(
        f"    attempt({label!r}, lambda: {code})" for label, code in _ITEM_ATTEMPTS.items()
    )
    conftest = _ATTEMPT_CONFTEST.format(declarations=declarations, attempts=attempts)
    out = tmp_path / "real.json"
    tree = make_tree(
        tmp_path,
        "attempts",
        {
            "conftest.py": conftest,
            "test_x.py": "import pytest\n@pytest.mark.skip\ndef test_x(): pass\n",
        },
    )
    _ = _real_pytest(tree, ["-q", "-p", "no:warnings"], {"OUT": str(out)})
    real: dict[str, str] = json.loads(out.read_text(encoding="utf-8"))
    assert set(real) == set(_DECLARATIONS) | set(_ITEM_ATTEMPTS)
    assert "ok" in real.values() and any(v != "ok" for v in real.values())

    for n, (label, code) in enumerate(_DECLARATIONS.items()):
        module = _module(tmp_path, f"decl_{n}", f"def pytest_addoption(parser):\n    {code}\n")
        runner = _runner()
        runner.register("conftest.py", module)
        assert (real[label] != "ok") == bool(runner.reasons), (label, real[label])
    for n, (label, code) in enumerate(_ITEM_ATTEMPTS.items()):
        module = _module(
            tmp_path,
            f"attempt_{n}",
            _compat_source(
                f"import pytest\ndef pytest_collection_modifyitems(items):\n    item = items[0]\n    {code}\n"
            ),
        )
        runner = _runner()
        runner.register("conftest.py", module)
        _ = _modify(runner, [_record("test_x.py::test_x", Mark("skip"))])
        assert (real[label] != "ok") == bool(runner.reasons), (label, real[label])


@pytest.mark.parametrize(
    ("line", "named"),
    [
        ('parser.addoption("--rq", required=True)', "required="),
        ('parser.addoption("--x", bogus=1)', "bogus="),
        ('parser.addoption("--x", type="int")', "string type"),
        ('parser.addoption("--a", dest="d")\n    parser.addoption("--b", dest="d")', "dest 'd'"),
    ],
)
def test_declarations_pytest_fails_on_later_hand_off(tmp_path: Path, line: str, named: str) -> None:
    """argparse raises for these when pytest parses the command line, after the hook returned;
    the shim has no parse, so it must not accept them."""
    module = _module(tmp_path, "later", f"def pytest_addoption(parser):\n    {line}\n")
    runner = _runner()
    runner.register("conftest.py", module)
    assert runner.reasons and named in runner.reasons[0], line


def test_an_unregistered_marker_string_hands_off_because_pytest_warns(tmp_path: Path) -> None:
    """``add_marker("name")`` is accepted by pytest with a ``PytestUnknownMarkWarning`` (an error
    under ``--strict-markers``): the warning is output, so the session goes to pytest."""
    module = _module(
        tmp_path,
        "unregistered",
        "def pytest_collection_modifyitems(items):\n    items[0].add_marker('neverheardof')\n",
    )
    runner = _runner()
    runner.register("conftest.py", module)
    _ = _modify(runner, [_record("a.py::t")])
    assert runner.reasons and "unregistered marker 'neverheardof'" in runner.reasons[0]


@pytest.mark.parametrize(
    ("body", "named"),
    [
        ("item.get_closest_marker('m').kwargs['k'] = 1", "modifies the kwargs of a mark"),
        ("item.get_closest_marker('m').kwargs.update(k=1)", "modifies the kwargs of a mark"),
        ("del item.get_closest_marker('m').kwargs['a']", "modifies the kwargs of a mark"),
        ("item.get_closest_marker('m').name = 'z'", "sets a mark's .name"),
        ("item.get_closest_marker('m').args = ()", "sets a mark's .args"),
        ("item.get_closest_marker('m').combined_with(None)", "uses a mark's .combined_with"),
    ],
)
def test_a_mark_a_hook_reads_cannot_be_mutated(tmp_path: Path, body: str, named: str) -> None:
    """A mutation of a mark persists in pytest and would otherwise be silently lost here."""
    module = _module(
        tmp_path,
        "mutate",
        f"def pytest_collection_modifyitems(items):\n    item = items[0]\n    {body}\n",
    )
    runner = _runner()
    runner.register("conftest.py", module)
    _ = _modify(runner, [_record("a.py::t", Mark("m", (), {"a": 1}))])
    assert runner.reasons and named in runner.reasons[0], body


def test_every_public_name_of_pytests_config_is_reserved(tmp_path: Path) -> None:
    """The literal in ``_hookshim`` was taken from pytest 8.4.2; this fails (loudly, on purpose)
    when the installed pytest has a public ``Config`` name the literal lacks."""
    from rustest._hookshim import _RESERVED_CONFIG  # pyright: ignore[reportPrivateUsage]

    out = tmp_path / "names.json"
    source = """
import json, os
from _pytest.config import Config

def pytest_configure(config):
    names = {n for n in dir(Config) if not n.startswith("_")}
    names |= {n for n in vars(config) if not n.startswith("_")}
    with open(os.environ["OUT"], "w") as handle:
        json.dump(sorted(names), handle)
"""
    tree = make_tree(
        tmp_path, "names", {"conftest.py": source, "test_x.py": "def test_x(): pass\n"}
    )
    _ = _real_pytest(tree, ["-q"], {"OUT": str(out)})
    names = set(json.loads(out.read_text(encoding="utf-8")))
    assert names and names <= _RESERVED_CONFIG, sorted(names - _RESERVED_CONFIG)


@pytest.mark.parametrize(
    "assignment",
    ["config.args = ['tests/unit']", "config.cache = 1", "config.inicfg = {}", "config.stash = 0"],
)
def test_assigning_to_a_real_config_attribute_is_not_stored_as_a_user_attribute(
    tmp_path: Path, assignment: str
) -> None:
    """``config.args = [...]`` changes what pytest collects."""
    module = _module(tmp_path, "reserved", f"def pytest_configure(config):\n    {assignment}\n")
    runner = _runner()
    runner.register("conftest.py", module)
    runner.configure()
    assert runner.reasons and "sets config." in runner.reasons[0] and runner.user_attributes == {}


# --------------------------------------------------------------------------------------
# the item, compared with real pytest's: class, repr, keywords, marker order
# --------------------------------------------------------------------------------------

_CLASS_PROBE = """
import json
import os
import pytest

def pytest_collection_modifyitems(items):
    out = {}
    for item in items:
        out[item.nodeid] = [
            type(item).__name__,
            repr(item),
            [isinstance(item, c) for c in (
                pytest.Function, pytest.Item, pytest.Class,
                pytest.Module, pytest.Session, pytest.Collector,
            )],
        ]
    with open(os.environ["OUT"], "w") as handle:
        json.dump(out, handle)
"""

_CLASS_TESTS = """
import unittest

def test_plain(): pass

class TestU(unittest.TestCase):
    def test_u(self): pass
"""


def test_the_item_is_an_instance_of_what_pytest_function_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``isinstance`` never reaches ``__getattr__``, so an item that merely resembled a
    ``Function`` would answer False silently; ``type(item).__name__`` and ``repr`` too."""
    out = tmp_path / "real.json"
    tree = make_tree(
        tmp_path,
        "classes",
        {"conftest.py": _CLASS_PROBE, "test_f.py": textwrap.dedent(_CLASS_TESTS)},
    )
    _ = _real_pytest(tree, ["-q"], {"OUT": str(out)})
    real = json.loads(out.read_text(encoding="utf-8"))
    assert real["test_f.py::test_plain"][0] == "Function"
    assert real["test_f.py::TestU::test_u"][0] == "TestCaseFunction"

    shim_out = tmp_path / "shim.json"
    monkeypatch.setenv("OUT", str(shim_out))
    runner = _runner()
    runner.register("conftest.py", _module(tmp_path, "classes_probe", _compat_source(_CLASS_PROBE)))
    effects = _modify(
        runner,
        [
            _record("test_f.py::test_plain"),
            _record("test_f.py::TestU::test_u", kind="unittest"),
        ],
    )
    assert runner.reasons == [] and effects.order == list(real)
    assert json.loads(shim_out.read_text(encoding="utf-8")) == real


def test_the_item_is_also_a_node_of_the_nodes_stub() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from rustest._pytest_stub import nodes
        from rustest.compat import pytest as compat

    answers: list[tuple[bool, bool]] = []

    def hook(items: list[Any]) -> None:
        item = items[0]
        answers.append(
            (
                isinstance(
                    item, (nodes.Item, nodes.Node, compat.Item, compat.Node, compat.Function)
                ),
                isinstance(item, (nodes.Collector, compat.Class, compat.Module)),
            )
        )

    module = ModuleType("nodes_probe")
    module.pytest_collection_modifyitems = hook  # pyright: ignore[reportAttributeAccessIssue]
    runner = _runner()
    runner.register("conftest.py", module)
    _ = _modify(runner, [_record("a.py::t")])
    assert runner.reasons == [] and answers == [(True, False)]


def test_an_item_kind_the_shim_cannot_be_hands_off(tmp_path: Path) -> None:
    module = _module(tmp_path, "kinds", "def pytest_collection_modifyitems(items):\n    pass\n")
    runner = _runner()
    runner.register("conftest.py", module)
    _ = _modify(runner, [_record("README.md::block", kind="doctest")])
    assert runner.reasons and "collected a doctest item" in runner.reasons[0]


_KEYS_TESTS = {
    "pytest.ini": "[pytest]\nmarkers =\n    slow\n    other\n    pmark\n    clsmark\n    modmark\n"
    "    basemark\n",
    "test_top.py": "def test_plain(): pass\n",
    "sub/test_k.py": """
import pytest

pytestmark = pytest.mark.modmark


class Base:
    pytestmark = [pytest.mark.basemark]


@pytest.mark.clsmark
class TestC(Base):
    @pytest.mark.slow
    @pytest.mark.other
    @pytest.mark.parametrize("a,b", [pytest.param("a", 1, marks=pytest.mark.pmark), ("c", 2)])
    def test_p(self, a, b): pass

    test_p.fattr = True

    @pytest.mark.xfail(reason="r", strict=True)
    def test_x(self): pass


class TestD:
    def test_d(self): pass
""",
}

_KEYS_NAMES = [
    "", "keys", "sub", "test_k.py", "test_top.py", "TestC", "TestD", "Base", "test_p",
    "test_p[a-1]", "test_p[c-2]", "a-1", "c-2", "test_x", "fattr", "slow", "other", "pmark",
    "clsmark", "modmark", "basemark", "parametrize", "xfail", "pytestmark", "nope", "skip",
]  # fmt: skip

_KEYS_COMMON = f"""
import json
import os

NAMES = {_KEYS_NAMES!r}

def norm(value):
    return json.loads(json.dumps(value, default=str))

def mark_data(m):
    return [m.name, norm(list(m.args)), norm(dict(m.kwargs))]

def closest(item, name):
    m = item.get_closest_marker(name)
    return None if m is None else mark_data(m)

def answers(item):
    return {{
        "keywords": {{n: n in item.keywords for n in NAMES}},
        "iter": [mark_data(m) for m in item.iter_markers()],
        "slow": [mark_data(m) for m in item.iter_markers("slow")],
        "closest": {{n: closest(item, n) for n in ("slow", "xfail", "parametrize", "pmark", "nope")}},
    }}
"""

_KEYS_REAL = (
    _KEYS_COMMON
    + """
import pytest

def record_from_pytest_item(item):
    \"\"\"``ItemRecord``'s fields, read off a real pytest item: the mapping the worker implements.\"\"\"
    chain = item.listchain()  # Session, the rootdir, each directory, Module, Class..., Function
    at = next(i for i, node in enumerate(chain) if isinstance(node, pytest.Module))
    return {
        "nodeid": item.nodeid,
        "name": item.name,
        "path": str(item.path),
        "markers": [mark_data(m) for m in item.iter_markers()],
        "own_marker_count": len(item.own_markers),
        "rootdir_name": chain[1].name,
        "dir_names": [node.name for node in chain[2:at]],
        "module_name": chain[at].name,
        "class_names": [node.name for node in chain[at + 1 : -1]],
        "function_attrs": sorted(item.obj.__dict__),
        "param_id": item.callspec.id if hasattr(item, "callspec") else None,
    }

def pytest_collection_modifyitems(items):
    out = {i.nodeid: {"record": record_from_pytest_item(i), "answers": answers(i)} for i in items}
    with open(os.environ["OUT"], "w") as handle:
        json.dump(out, handle, default=str)
"""
)

_KEYS_SHIM = (
    _KEYS_COMMON
    + """
def pytest_collection_modifyitems(items):
    out = {i.nodeid: answers(i) for i in items}
    with open(os.environ["OUT"], "w") as handle:
        json.dump(out, handle, default=str)
"""
)


def _record_from_dump(data: dict[str, Any]) -> ItemRecord:
    fields = dict(data)
    fields["markers"] = tuple(Mark(n, tuple(a), k) for n, a, k in fields["markers"])
    fields["dir_names"] = tuple(fields["dir_names"])
    fields["class_names"] = tuple(fields["class_names"])
    fields["function_attrs"] = frozenset(fields["function_attrs"])
    return ItemRecord(**fields)


def test_keywords_and_markers_answer_as_pytest_does_for_a_record_built_from_pytests_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe tree has a directory, a class with a base, a parametrized test with a
    ``pytest.param`` mark, decorators, a function attribute and module marks: what ``in
    item.keywords`` and ``iter_markers`` / ``get_closest_marker`` answer for each is compared
    with real pytest's, from a record made by ``record_from_pytest_item`` (the worker's spec)."""
    out = tmp_path / "real.json"
    tree = make_tree(tmp_path, "keys", {"conftest.py": _KEYS_REAL, **_KEYS_TESTS})
    _ = _real_pytest(tree, ["-q"], {"OUT": str(out)})
    real = json.loads(out.read_text(encoding="utf-8"))
    assert len(real) == 5
    one = real["sub/test_k.py::TestC::test_p[a-1]"]["record"]
    assert one["own_marker_count"] < len(one["markers"]) and one["rootdir_name"] == "keys"

    shim_out = tmp_path / "shim.json"
    monkeypatch.setenv("OUT", str(shim_out))
    runner = _runner()
    runner.register("conftest.py", _module(tmp_path, "keys_probe", _compat_source(_KEYS_SHIM)))
    records = [_record_from_dump(entry["record"]) for entry in real.values()]
    effects = _modify(runner, records)

    assert runner.reasons == [] and effects.order == list(real)
    shim = json.loads(shim_out.read_text(encoding="utf-8"))
    for nodeid, entry in real.items():
        assert shim[nodeid] == entry["answers"], nodeid
    # the answers are not vacuous: directory, class, module, param id, rootdir, "" and a
    # function attribute are all keywords in pytest
    keywords = real["sub/test_k.py::TestC::test_p[a-1]"]["answers"]["keywords"]
    for name in ("", "keys", "sub", "test_k.py", "TestC", "test_p[a-1]", "a-1", "fattr", "pmark"):
        assert keywords[name] is True, name
    assert keywords["nope"] is False and keywords["TestD"] is False


# --------------------------------------------------------------------------------------
# the order contract: where the hooks run relative to -k / -m / --lf, compared with pytest
# --------------------------------------------------------------------------------------

_ORDER_PLUGINS = {
    "plug_first.py": """
import pytest
from _log import log

@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items): log("first", items)
""",
    "plug_plain.py": """
import pytest
from _log import log

def pytest_collection_modifyitems(items):
    log("plain", items)
    for item in items:
        if item.name == "test_b":
            item.add_marker(pytest.mark.pick)
""",
    "plug_last.py": """
import pytest
from _log import log

@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items): log("last", items)
""",
    "_log.py": """
import os

def log(tag, items):
    names = [i.name + ("+pick" if i.get_closest_marker("pick") else "") for i in items]
    with open(os.environ["HOOK_LOG"], "a") as handle:
        handle.write(tag + ":" + ",".join(names) + "\\n")
""",
    "pytest.ini": "[pytest]\nmarkers =\n    pick\n",
    "test_s.py": "def test_a(): pass\ndef test_b(): pass\ndef test_c(): assert 0\n",
}
_ORDER_FLAGS = ["-p", "plug_first", "-p", "plug_plain", "-p", "plug_last"]


_ALL = "first:test_a,test_b,test_c", "plain:test_a,test_b,test_c"


@pytest.mark.parametrize(
    ("selection", "narrow", "keep", "expected"),
    [
        (
            ["-k", "test_a or test_c", "--ff"],
            lambda name: True,
            lambda effects, name: name.endswith(("test_a", "test_c")),
            [*_ALL, "last:test_a,test_c"],
        ),
        (
            ["-m", "pick"],
            lambda name: True,
            lambda effects, name: any(m.nodeid == name for m in effects.marks),
            [*_ALL, "last:test_b+pick"],
        ),
        (
            ["--lf"],
            lambda name: name.endswith("test_c"),
            lambda effects, name: True,
            ["first:test_c", "plain:test_c", "last:test_c"],
        ),
    ],
    ids=["-k then --ff", "-m on a hook's mark", "--lf narrows first"],
)
def test_modifyitems_runs_around_selection_as_pytest_does(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: list[str],
    narrow: Callable[[str], bool],
    keep: Callable[[CollectionEffects, str], bool],
    expected: list[str],
) -> None:
    """The order contract of the module docstring, step by step, against real pytest: ``--lf``
    narrows the collection before any hook sees it; ``tryfirst`` and plain hooks then see all of
    it; ``-k`` / ``-m`` select (on a mark a plain hook added, for ``-m``); ``trylast`` hooks see
    what is left, with that mark on the item; ``--ff`` reorders after all of them."""
    tree = make_tree(tmp_path, "around", _ORDER_PLUGINS)
    seed = tmp_path / "seed.log"
    _ = _real_pytest(tree, [*_ORDER_FLAGS, "-q"], {"HOOK_LOG": str(seed)}, cache=True)
    real_log = tmp_path / "real.log"
    result = _real_pytest(
        tree, [*_ORDER_FLAGS, "-q", *selection], {"HOOK_LOG": str(real_log)}, cache=True
    )
    assert real_log.read_text(encoding="utf-8").split() == expected, result.stdout

    shim_log = tmp_path / "shim.log"
    monkeypatch.setenv("HOOK_LOG", str(shim_log))
    monkeypatch.syspath_prepend(str(tree))
    runner = _runner(ini={"markers": "pick: p"})
    for name in ("plug_first", "plug_plain", "plug_last"):
        runner.register(f"{name}.py", importlib.import_module(name))
    records = {n: _record(f"test_s.py::{n}") for n in ("test_a", "test_b", "test_c")}
    collected = [records[n] for n in records if narrow(n)]

    first = runner.modify_collection_before_selection(collected)
    kept = [name for name in first.order if keep(first, name)]
    _ = runner.modify_collection_after_selection([records[n.split("::")[1]] for n in kept])

    assert runner.reasons == []
    assert shim_log.read_text(encoding="utf-8").split() == expected


_LF_CLASS_ONLY = {
    **{k: v for k, v in _ORDER_PLUGINS.items() if k != "test_s.py"},
    "test_d.py": "def test_d(): pass\n",
    "test_mod.py": (
        "def test_mod_ok(): pass\n\n"
        "class TestC:\n"
        "    def test_fail(self): assert 0\n"
        "    def test_ok(self): pass\n"
    ),
}


def test_lf_acts_twice_a_class_only_failure_leaves_the_collection_alone_and_deselects_after_all_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--lf`` has two halves.  The collection filter (before every hook) only switches on for a
    *module-level* failure; here the only failure is in a class, so every hook, ``trylast``
    included, sees every test, and the deselection happens after all of them (``3 deselected``)."""
    tree = make_tree(tmp_path, "lfclass", _LF_CLASS_ONLY)
    seed = tmp_path / "seed.log"
    _ = _real_pytest(tree, [*_ORDER_FLAGS, "-q"], {"HOOK_LOG": str(seed)}, cache=True)
    real_log = tmp_path / "real.log"
    result = _real_pytest(
        tree, [*_ORDER_FLAGS, "-q", "--lf"], {"HOOK_LOG": str(real_log)}, cache=True
    )
    lines = real_log.read_text(encoding="utf-8").split()
    names = lines[0].split(":", 1)[1].split(",")
    assert sorted(names) == ["test_d", "test_fail", "test_mod_ok", "test_ok"]
    assert lines == [
        f"first:{','.join(names)}",
        f"plain:{','.join(names)}",
        f"last:{','.join(names)}",
    ]
    assert "1 failed, 3 deselected" in result.stdout

    shim_log = tmp_path / "shim.log"
    monkeypatch.setenv("HOOK_LOG", str(shim_log))
    monkeypatch.syspath_prepend(str(tree))
    runner = _runner(ini={"markers": "pick: p"})
    for name in ("plug_first", "plug_plain", "plug_last"):
        runner.register(f"{name}.py", importlib.import_module(name))
    records = [_record(f"t.py::{n}") for n in names]
    first = runner.modify_collection_before_selection(records)
    _ = runner.modify_collection_after_selection([r for r in records if r.nodeid in first.order])

    assert runner.reasons == []
    assert shim_log.read_text(encoding="utf-8").split() == lines


def test_a_plugin_reported_deselection_twice_is_counted_twice(tmp_path: Path) -> None:
    source = """
def pytest_collection_modifyitems(config, items):
    victim = items[0]
    config.hook.pytest_deselected(items=[victim])
    config.hook.pytest_deselected(items=[victim])
    items.remove(victim)
"""
    tree = make_tree(
        tmp_path,
        "twice",
        {"conftest.py": source, "test_t.py": "def test_a(): pass\ndef test_b(): pass\n"},
    )
    result = _real_pytest(tree, ["--collect-only", "-q"])
    assert "2 deselected" in result.stdout

    runner = _runner()
    runner.register("conftest.py", _module(tmp_path, "twice_conftest", source))
    effects = _modify(runner, [_record("test_t.py::test_a"), _record("test_t.py::test_b")])
    assert effects.deselected == ["test_t.py::test_a"] * 2 and effects.vanished == []
    assert effects.order == ["test_t.py::test_b"]


def test_an_item_hook_cannot_be_called_when_nothing_registers_one(tmp_path: Path) -> None:
    runner = _runner()
    runner.register("conftest.py", _module(tmp_path, "empty", "x = 1\n"))
    effects = runner.modify_collection_before_selection([_record("a.py::t")])
    assert effects.order == ["a.py::t"] and runner.reasons == []


def test_the_runtime_does_not_import_pytest() -> None:
    code = "import sys\nimport rustest._hookshim\nprint('pytest' in sys.modules, '_pytest' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.stdout.strip() == "False False", out.stderr


def test_it_is_off_the_import_rustest_path() -> None:
    code = "import sys\nimport rustest\nprint('rustest._hookshim' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.stdout.strip() == "False", out.stderr


# --------------------------------------------------------------------------------------
# ordering: pluggy's, checked against real pytest
# --------------------------------------------------------------------------------------

_ORDER_TEMPLATE = """
import os
import pytest
{extra}
def _log(text):
    with open(os.environ["HOOK_LOG"], "a") as handle:
        handle.write(text + "\\n")

{decorate_configure}
def pytest_configure(config): _log("{name}:configure")
def pytest_sessionstart(session): _log("{name}:sessionstart")
{decorate_modify}
def pytest_collection_modifyitems(items): _log("{name}:modifyitems")
"""


def _order_sources() -> dict[str, str]:
    def source(name: str, extra: str = "", configure: str = "", modify: str = "") -> str:
        return _ORDER_TEMPLATE.format(
            name=name, extra=extra, decorate_configure=configure, decorate_modify=modify
        )

    return {
        "conftest.py": source(
            "root", 'pytest_plugins = ["plug"]\n', modify="@pytest.hookimpl(trylast=True)"
        ),
        "plug.py": source("plug", configure="@pytest.hookimpl(tryfirst=True)"),
        "sub/conftest.py": source("sub", configure="@pytest.hookimpl(trylast=True)"),
        "sub/inner/conftest.py": source("inner", modify="@pytest.hookimpl(tryfirst=True)"),
        "test_a.py": "def test_a(): pass\n",
        "sub/test_b.py": "def test_b(): pass\n",
        "sub/inner/test_c.py": "def test_c(): pass\n",
    }


def test_hooks_run_in_the_order_pluggy_and_pytest_give_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = _order_sources()
    tree = make_tree(tmp_path, "order", sources)
    real_log = tmp_path / "real.log"
    _ = _real_pytest(tree, ["-q"], {"HOOK_LOG": str(real_log)})
    expected = real_log.read_text(encoding="utf-8").split()

    shim_log = tmp_path / "shim.log"
    monkeypatch.setenv("HOOK_LOG", str(shim_log))
    modules = {
        label: _module(tmp_path, f"order_{i}", sources[label])
        for i, label in enumerate(
            ("conftest.py", "plug.py", "sub/conftest.py", "sub/inner/conftest.py")
        )
    }
    runner = _runner()
    runner.register("conftest.py", modules["conftest.py"])
    runner.register("plug.py", modules["plug.py"])
    runner.configure()
    runner.sessionstart()
    runner.register("sub/conftest.py", modules["sub/conftest.py"], initial=False)
    runner.register("sub/inner/conftest.py", modules["sub/inner/conftest.py"], initial=False)
    # pytest collects `sub/inner`, then `sub/test_b.py`, then `test_a.py` (entries are sorted)
    records = [
        _record("sub/inner/test_c.py::test_c"),
        _record("sub/test_b.py::test_b"),
        _record("test_a.py::test_a"),
    ]
    _ = _modify(runner, records)

    assert runner.reasons == []
    assert shim_log.read_text(encoding="utf-8").split() == expected
