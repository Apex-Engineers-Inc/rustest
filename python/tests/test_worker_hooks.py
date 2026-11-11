"""The worker's half of the native hook runtime, driven over the real wire in a subprocess.

``init`` carries ``conftests`` (and an opaque ``hook_context``); the worker registers them in
order, runs ``configure`` / ``sessionstart`` before any test module is imported, serves
``request.config`` from what the hooks declared, runs the two ``modify_items`` stages when asked,
and reports every hand-off reason *before a test executes*.  No engine is involved: the messages
are the ones ``src/engine`` sends.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from rustest._worker import PROTOCOL_VERSION

PREFIX = "rustest's hook runtime: "


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def _init(
    root: Path,
    conftests: list[tuple[str, bool]] | None = None,
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "op": "init",
        "protocol_version": PROTOCOL_VERSION,
        "rootdir": root.as_posix(),
        "invocation_dir": root.as_posix(),
        "python_files": ["test_*.py", "*_test.py"],
        "python_classes": ["Test"],
        "python_functions": ["test"],
    }
    if conftests is not None:
        message["conftests"] = [
            {"path": (root / name).as_posix(), "initial": initial} for name, initial in conftests
        ]
    if context is not None:
        message["hook_context"] = context
    return message


def _talk(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Send every message, return every response line."""
    proc = subprocess.run(
        [sys.executable, "-m", "rustest._worker"],
        input="".join(json.dumps(m) + "\n" for m in [*messages, {"op": "shutdown"}]),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def _result(responses: list[dict[str, Any]], test_id: str) -> dict[str, Any]:
    return next(r for r in responses if r.get("op") == "test_result" and r["id"] == test_id)


_CONTEXT = {
    "args": ["-q", "tests"],
    "options": {"verbose": 0, "--verbose": 0, "keyword": "", "-k": ""},
    "ini": {"markers": "slow: a slow test", "lines": "a\n  b"},
}

_CONFTEST = """
import os
import sys
import pytest

SEEN = {"version": os.environ.get("PYTEST_VERSION"), "argv": list(sys.argv)}

def pytest_addoption(parser):
    parser.addoption("--runslow", action="store_true", default=False, help="run slow tests")
    parser.addoption("--db-url", dest="url", default="sqlite://")
    parser.addini("lines", "l", type="linelist")

def pytest_configure(config):
    config.addinivalue_line("markers", "fast: a fast test")
    config.backend = "nx"
"""

_TEST_CONFIG = """
import conftest

def test_the_config(request):
    config = request.config
    assert config.getoption("--runslow") is False
    assert config.getoption("--runslow", default=True) is False   # declared: the default= is ignored
    assert config.getoption("--db-url", default="postgres://") == "sqlite://"
    assert config.option.runslow is False and config.option.url == "sqlite://"
    assert config.backend == "nx"
    assert config.getini("lines") == ["a", "b"]
    assert config.getini("markers")[:2] == ["slow: a slow test", "fast: a fast test"]
    assert config.getoption("verbose") == 0
    assert getattr(config.option, "undeclared", "absent") == "absent"
    assert not hasattr(config, "workerinput")

def test_process_state():
    import os, sys
    assert conftest.SEEN["version"] == "8.4.2"
    assert conftest.SEEN["argv"] == ["pytest", "-q", "tests"]
"""


def test_a_session_without_conftests_on_init_behaves_as_before(tmp_path: Path) -> None:
    """No ``conftests``: the hook runtime is off, the hook is an unrun hook, nothing else changes."""
    _ = _write(tmp_path / "conftest.py", _CONFTEST)
    _ = _write(tmp_path / "test_a.py", "def test_a(): pass\n")

    responses = _talk(
        [_init(tmp_path), {"op": "collect_file", "path": (tmp_path / "test_a.py").as_posix()}]
    )

    assert responses[0] == {"op": "ready", "protocol_version": PROTOCOL_VERSION}
    collected = responses[1]
    assert set(collected["hooks"]) == {
        "conftest.py::pytest_addoption",
        "conftest.py::pytest_configure",
    }
    assert "record" not in collected["tests"][0]


def test_conftest_hooks_run_natively_and_request_config_answers_from_them(tmp_path: Path) -> None:
    _ = _write(tmp_path / "conftest.py", _CONFTEST)
    _ = _write(tmp_path / "test_config.py", _TEST_CONFIG)
    path = (tmp_path / "test_config.py").as_posix()
    ids = ["test_config.py::test_the_config", "test_config.py::test_process_state"]

    responses = _talk(
        [
            _init(tmp_path, [("conftest.py", True)], context=_CONTEXT),
            {"op": "collect_file", "path": path},
            {"op": "execute_batch", "ids": ids, "stop_on_failure": False},
        ]
    )

    ready, collected = responses[0], responses[1]
    assert "hooks" not in ready and "modify_items" not in ready  # nothing to hand off
    assert "hooks" not in collected  # addoption / configure are no longer 'unrun'
    for test_id in ids:
        result = _result(responses, test_id)
        assert result["status"] == "passed", result.get("message")


def test_a_hook_the_shim_cannot_run_is_a_reason_on_ready_before_any_test(tmp_path: Path) -> None:
    """pillow's pattern: the swallow in ``except Exception`` does not hide it."""
    _ = _write(
        tmp_path / "conftest.py",
        """
        def pytest_configure(config):
            try:
                config.rootdir
            except Exception:
                pass
        """,
    )
    _ = _write(tmp_path / "test_a.py", "def test_a(): pass\n")

    responses = _talk([_init(tmp_path, [("conftest.py", True)])])

    (reason,) = responses[0]["hooks"]
    assert (
        reason.startswith(PREFIX) and "conftest.py::pytest_configure uses config.rootdir" in reason
    )


def test_a_hook_the_runtime_does_not_run_is_named_once_as_unrun(tmp_path: Path) -> None:
    """One reason per hook: the runtime is silent about it, the worker names it as unrun."""
    _ = _write(
        tmp_path / "conftest.py",
        "def pytest_runtest_setup(item):\n    pass\ndef pytest_generate_tests(metafunc):\n    pass\n",
    )
    _ = _write(tmp_path / "test_a.py", "def test_a(): pass\n")

    responses = _talk(
        [
            _init(tmp_path, [("conftest.py", True)]),
            {"op": "collect_file", "path": (tmp_path / "test_a.py").as_posix()},
        ]
    )

    assert "hooks" not in responses[0]
    assert responses[1]["hooks"] == [
        "conftest.py::pytest_generate_tests",
        "conftest.py::pytest_runtest_setup",
    ]


def test_conftests_register_in_the_order_the_engine_sends_and_late_ones_miss_sessionstart(
    tmp_path: Path,
) -> None:
    log = tmp_path / "order.log"
    for name, label in (("conftest.py", "root"), ("sub/conftest.py", "sub")):
        _ = _write(
            tmp_path / name,
            f"""
            def _log(text):
                with open({log.as_posix()!r}, "a") as handle:
                    handle.write(text + "\\n")

            def pytest_configure(config): _log("{label}:configure")
            def pytest_sessionstart(session): _log("{label}:sessionstart")
            """,
        )
    _ = _write(tmp_path / "test_a.py", "def test_a(): pass\n")

    init = _init(tmp_path, [("conftest.py", True), ("sub/conftest.py", False)])
    _ = _talk([init])
    # a later conftest is not imported at init: the walk reaches it later (review M1)
    assert log.read_text(encoding="utf-8").split() == ["root:configure", "root:sessionstart"]
    log.unlink()

    _ = _talk([init, {"op": "import_conftests", "dirs": [(tmp_path / "sub").as_posix()]}])

    # root is initial: configure, sessionstart; sub is registered afterwards (pytest registers it
    # while collecting): configure at registration, no sessionstart
    assert log.read_text(encoding="utf-8").split() == [
        "root:configure",
        "root:sessionstart",
        "sub:configure",
    ]


_MODIFYITEMS = """
import pytest

def pytest_collection_modifyitems(config, items):
    skip_slow = pytest.mark.skip(reason="need --runslow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)

@pytest.hookimpl(trylast=True)
def _unused(): pass
"""

_SLOW_TESTS = """
import pytest

def test_fast():
    pass

@pytest.mark.slow
def test_slow():
    raise AssertionError("must have been skipped")

class TestK:
    @pytest.mark.parametrize("x", [1, 2])
    def test_p(self, x):
        pass
"""


def test_modifyitems_stages_run_over_records_and_their_marks_reach_this_workers_plans(
    tmp_path: Path,
) -> None:
    _ = _write(tmp_path / "conftest.py", _MODIFYITEMS)
    _ = _write(tmp_path / "test_slow.py", _SLOW_TESTS)
    path = (tmp_path / "test_slow.py").as_posix()

    first = _talk(
        [
            _init(tmp_path, [("conftest.py", True)], context={"options": {}}),
            {"op": "collect_file", "path": path},
        ]
    )
    assert first[0]["modify_items"] is True
    tests = first[1]["tests"]
    records = [t["record"] for t in tests]
    assert [r["nodeid"] for r in records] == [t["id"] for t in tests]
    by_name = {r["name"]: r for r in records}
    assert by_name["test_slow"]["markers"][0][:3] == ["slow", [], {}]
    assert by_name["test_p[1]"]["parametrized"] is True and by_name["test_p[1]"]["class_names"] == [
        "TestK"
    ]
    assert by_name["test_fast"]["rootdir_name"] == tmp_path.name
    assert (
        by_name["test_fast"]["module_name"] == "test_slow.py"
        and by_name["test_fast"]["dir_names"] == []
    )

    responses = _talk(
        [
            _init(tmp_path, [("conftest.py", True)], context={"options": {}}),
            {"op": "collect_file", "path": path},
            {"op": "modify_items", "stage": 1, "records": records},
            {
                "op": "execute_batch",
                "ids": [t["id"] for t in tests],
                "stop_on_failure": False,
                "commit_marks": True,
            },
        ]
    )

    modified = next(r for r in responses if r["op"] == "items_modified")
    assert modified["order"] == [t["id"] for t in tests] and "hooks" not in modified
    assert modified["marks"] == [
        {
            "nodeid": "test_slow.py::test_slow",
            "mark": {"name": "skip", "kwargs": {"reason": "need --runslow option to run"}},
            "append": True,
        }
    ]
    assert _result(responses, "test_slow.py::test_slow")["status"] == "skipped"
    assert _result(responses, "test_slow.py::test_fast")["status"] == "passed"

    # A stage the orchestrator did not accept leaves no mark (review S1).
    rejected = _talk(
        [
            _init(tmp_path, [("conftest.py", True)], context={"options": {}}),
            {"op": "collect_file", "path": path},
            {"op": "modify_items", "stage": 1, "records": records},
            {"op": "execute_batch", "ids": ["test_slow.py::test_slow"], "stop_on_failure": False},
        ]
    )
    assert _result(rejected, "test_slow.py::test_slow")["status"] == "failed"


def test_stage_two_runs_only_trylast_hooks_over_what_selection_left(tmp_path: Path) -> None:
    log = tmp_path / "stages.log"
    for name, decorator in (("first_plug", ""), ("last_plug", "@pytest.hookimpl(trylast=True)")):
        _ = _write(
            tmp_path / f"{name}.py",
            f"""
            import pytest

            {decorator}
            def pytest_collection_modifyitems(items):
                with open({log.as_posix()!r}, "a") as handle:
                    handle.write("{name}:" + ",".join(i.name for i in items) + "\\n")
            """,
        )
    _ = _write(tmp_path / "conftest.py", "pytest_plugins = ['first_plug', 'last_plug']\n")
    _ = _write(
        tmp_path / "test_a.py", "def test_a(): pass\ndef test_b(): pass\ndef test_c(): pass\n"
    )
    path = (tmp_path / "test_a.py").as_posix()
    tests = _talk([_init(tmp_path, [("conftest.py", True)]), {"op": "collect_file", "path": path}])[
        1
    ]["tests"]
    log.unlink(missing_ok=True)
    records = [t["record"] for t in tests]

    _ = _talk(
        [
            _init(tmp_path, [("conftest.py", True)]),
            {"op": "collect_file", "path": path},
            {"op": "modify_items", "stage": 1, "records": records},
            {"op": "modify_items", "stage": 2, "records": records[:2]},  # `-k` kept two
        ]
    )

    assert log.read_text(encoding="utf-8").split() == [
        "first_plug:test_a,test_b,test_c",
        "last_plug:test_a,test_b",
    ]


def test_a_record_the_hook_runtime_cannot_model_hands_off_when_a_hook_is_shown_it(
    tmp_path: Path,
) -> None:
    """A mark argument JSON cannot carry (a class): the hook must not see a different value."""
    _ = _write(tmp_path / "conftest.py", "def pytest_collection_modifyitems(items):\n    pass\n")
    _ = _write(
        tmp_path / "test_a.py",
        "import pytest\n\n@pytest.mark.xfail(raises=ValueError)\ndef test_a(): raise ValueError\n",
    )
    path = (tmp_path / "test_a.py").as_posix()
    base = [_init(tmp_path, [("conftest.py", True)]), {"op": "collect_file", "path": path}]
    record = _talk(base)[1]["tests"][0]["record"]
    assert record["kind"] == "unsendable"

    responses = _talk([*base, {"op": "modify_items", "stage": 1, "records": [record]}])

    modified = next(r for r in responses if r["op"] == "items_modified")
    assert any("cannot model (unsendable)" in r for r in modified["hooks"])


def test_a_shim_used_while_a_test_runs_errors_that_test_even_if_it_was_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backstop: ``late_reasons`` grew during the test, so it is an error, loudly."""
    import rustest._worker as worker
    from rustest import _hookwire

    _ = _write(tmp_path / "test_a.py", "def test_a(): pass\n")
    saved_state = worker._state  # pyright: ignore[reportPrivateUsage]
    counter = {"late": 0}

    class FakeHooks:
        REASON_PREFIX = PREFIX

        @staticmethod
        def late_count() -> int:
            return counter["late"]

        @staticmethod
        def late_reasons_since(count: int) -> list[str]:
            counter["late"] += 1  # a shim was used while the test ran
            return ["conftest.py::pytest_configure uses config.rootpath after the hook returned"]

        @staticmethod
        def pending_reasons() -> list[str]:
            return []

        @staticmethod
        def wants_records() -> bool:
            return False

    try:
        _ = worker.handle_init(_init(tmp_path))
        _ = worker.collect_file((tmp_path / "test_a.py").as_posix())
        monkeypatch.setattr(worker, "_hookwire_mod", FakeHooks)
        result = worker.execute_test("test_a.py::test_a")
    finally:
        worker._state = saved_state  # pyright: ignore[reportPrivateUsage]
        _ = _hookwire

    assert result["status"] == "error"
    assert PREFIX in result["message"] and "after the hook returned" in result["message"]
