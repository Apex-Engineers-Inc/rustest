"""``asyncio_concurrency``: consecutive ``concurrent``-marked async tests overlap, up to N at a
time, each on its own function loop; everything else runs as it always has.

Every test records its ``[start, end]`` in ``spans/<name>``, so overlap is measured from the
tests' own clocks rather than inferred from wall time.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from .helpers import make_tree, run

_HEAD = """\
import asyncio, os, time
import pytest

SPANS = os.path.join(os.path.dirname(__file__), "spans")

async def span(name, seconds=0.3):
    start = time.time()
    await asyncio.sleep(seconds)
    os.makedirs(SPANS, exist_ok=True)
    with open(os.path.join(SPANS, name), "w") as handle:
        handle.write(f"{start} {time.time()}")
"""


def _sleepers(count: int, marked: bool = True, seconds: float = 0.3) -> str:
    mark = "@pytest.mark.concurrent\n" if marked else ""
    tests = "".join(
        f"\n{mark}async def test_{i}():\n    await span('t{i}', {seconds})\n" for i in range(count)
    )
    return _HEAD + tests


def _rustest(tree: Path, *args: str) -> tuple[dict[str, object], subprocess.CompletedProcess[str]]:
    report = tree / "report.json"
    result = run(
        [sys.executable, "-m", "rustest", "-n", "1", "--report-json", str(report), *args], tree
    )
    assert report.exists(), result.stdout + result.stderr
    return json.loads(report.read_text(encoding="utf-8")), result


def _tests(report: dict[str, object]) -> list[dict[str, object]]:
    return report["tests"]  # pyright: ignore[reportReturnType]


def _statuses(report: dict[str, object]) -> list[tuple[str, str]]:
    return [(str(test["id"]), str(test["status"])) for test in _tests(report)]


def _spans(tree: Path) -> dict[str, tuple[float, float]]:
    spans: dict[str, tuple[float, float]] = {}
    for path in sorted((tree / "spans").glob("*")):
        start, end = path.read_text(encoding="utf-8").split()
        spans[path.name] = (float(start), float(end))
        path.unlink()
    return spans


def _peak(spans: dict[str, tuple[float, float]]) -> int:
    """The most spans open at one moment."""
    events = sorted(
        [(start, 1) for start, _ in spans.values()] + [(end, -1) for _, end in spans.values()]
    )
    open_now = peak = 0
    for _, step in events:
        open_now += step
        peak = max(peak, open_now)
    return peak


def _alone(spans: dict[str, tuple[float, float]], name: str) -> bool:
    start, end = spans[name]
    return all(
        o_end <= start or o_start >= end
        for other, (o_start, o_end) in spans.items()
        if other != name
    )


@pytest.mark.parametrize(("args", "marked"), [((), True), (("--asyncio-concurrency", "8"), False)])
def test_nothing_overlaps_without_both_the_key_and_the_mark(
    tmp_path: Path, args: tuple[str, ...], marked: bool
) -> None:
    tree = make_tree(tmp_path, "off", {"test_s.py": _sleepers(4, marked)})
    report, result = _rustest(tree, *args)
    assert _peak(_spans(tree)) == 1
    assert _statuses(report) == [(f"test_s.py::test_{i}", "passed") for i in range(4)]
    assert "asyncio_concurrency" not in result.stderr


@pytest.mark.parametrize("source", ["flag", "pyproject"])
def test_marked_tests_overlap_and_report_in_collection_order(tmp_path: Path, source: str) -> None:
    tree = make_tree(tmp_path, "on", {"test_s.py": _sleepers(8)})
    args: tuple[str, ...] = ("--asyncio-concurrency", "8")
    if source == "pyproject":
        (tree / "pyproject.toml").write_text("[tool.rustest]\nasyncio_concurrency = 8\n", "utf-8")
        args = ()
    report, result = _rustest(tree, *args)
    assert _peak(_spans(tree)) >= 4
    assert _statuses(report) == [(f"test_s.py::test_{i}", "passed") for i in range(8)]
    assert "rustest: asyncio_concurrency: 8 tests overlapped" in result.stderr


@pytest.mark.parametrize("value", ["-1", "eight"])
def test_a_bad_count_is_a_usage_error(tmp_path: Path, value: str) -> None:
    tree = make_tree(tmp_path, "bad", {"test_s.py": _sleepers(1)})
    (tree / "pyproject.toml").write_text(
        f"[tool.rustest]\nasyncio_concurrency = {json.dumps(value)}\n", "utf-8"
    )
    result = run([sys.executable, "-m", "rustest"], tree)
    assert result.returncode == 4
    assert "asyncio_concurrency must be a whole number" in result.stderr


#: Each blocker on the middle one of three marked tests: that test runs alone, and says why.
_BLOCKERS = {
    "monkeypatch": ("monkeypatch", "uses monkeypatch", "    monkeypatch.setenv('X', '1')\n"),
    "capsys": ("capsys", "uses capsys", ""),
    "xfail": ("", "xfail-marked", ""),
    "module-loop-fixture": ("res", "async fixture 'res' runs on a module-scoped loop", ""),
    "patch": ("", "its body uses patch", "    from unittest.mock import patch\n    with patch('os.sep', '/'):\n        pass\n"),
    "getfixturevalue": ("request", "its body uses getfixturevalue", "    request.getfixturevalue('request')\n"),
}  # fmt: skip


@pytest.mark.parametrize("blocker", sorted(_BLOCKERS))
def test_a_marked_test_that_cannot_overlap_runs_alone_and_says_why(
    tmp_path: Path, blocker: str
) -> None:
    fixture, reason, body = _BLOCKERS[blocker]
    extra = "@pytest.mark.xfail(strict=False)\n" if blocker == "xfail" else ""
    source = _HEAD + (
        "\n@pytest.fixture(scope='module')\nasync def res():\n    return 1\n"
        if blocker == "module-loop-fixture"
        else ""
    )
    for i in range(5):
        params = fixture if i == 2 else ""
        lines = body if i == 2 else ""
        source += (
            f"\n@pytest.mark.concurrent\n{extra if i == 2 else ''}async def test_{i}({params}):\n"
            + lines
            + f"    await span('t{i}')\n"
        )
    tree = make_tree(tmp_path, "blocked", {"test_b.py": source})
    report, result = _rustest(tree, "--asyncio-concurrency", "8")
    spans = _spans(tree)
    assert _alone(spans, "t2"), spans
    assert not _alone(spans, "t0") and not _alone(spans, "t3"), spans
    assert f"ran alone ({reason}): test_b.py::test_2" in result.stderr, result.stderr
    assert "4 tests overlapped" in result.stderr, result.stderr
    assert {status for _, status in _statuses(report)} <= {"passed", "xpassed"}


_BOUNDARIES = (
    _HEAD
    + """
EVENTS = []

@pytest.fixture(scope="class")
def per_class(request):
    EVENTS.append(f"setup {request.cls.__name__}")
    yield
    EVENTS.append(f"teardown {request.cls.__name__}")

@pytest.fixture(scope="module", params=["m1", "m2"])
def wide(request):
    EVENTS.append(f"setup {request.param}")
    yield request.param
    EVENTS.append(f"teardown {request.param}")

@pytest.mark.concurrent
class TestA:
    async def test_a0(self, per_class): await span("a0")
    async def test_a1(self, per_class): await span("a1")

@pytest.mark.concurrent
class TestB:
    async def test_b0(self, per_class): await span("b0")
    async def test_b1(self, per_class): await span("b1")

# one function, so the ids are grouped by the module param under both runners (rustest does
# not reorder items across functions: conformance/corpus/fixtures/module-param-reorder)
@pytest.mark.concurrent
@pytest.mark.parametrize("i", range(2))
async def test_w(wide, i): await span(f"w{i}-{wide}")

def test_zz_events():
    with open(os.path.join(os.path.dirname(__file__), "events.txt"), "w") as handle:
        handle.write("\\n".join(EVENTS))
"""
)


def test_windows_break_at_a_class_and_at_a_wide_param_change(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "bounds", {"test_c.py": _BOUNDARIES})
    serial, _ = _rustest(tree)
    _ = _spans(tree)
    serial_events = (tree / "events.txt").read_text(encoding="utf-8")
    overlapped, _ = _rustest(tree, "--asyncio-concurrency", "8")
    spans = _spans(tree)
    assert _statuses(overlapped) == _statuses(serial)
    assert (tree / "events.txt").read_text(encoding="utf-8") == serial_events
    # inside a class, and inside one param, the tests overlapped; never across
    for left, right in (("a0", "a1"), ("b0", "b1"), ("w0-m1", "w1-m1"), ("w0-m2", "w1-m2")):
        assert not _alone({left: spans[left], right: spans[right]}, left), (left, right, spans)
    for left, right in (
        ("a1", "b0"),
        ("a0", "b1"),
        ("w0-m1", "w0-m2"),
        ("w1-m1", "w0-m2"),
        ("w1-m1", "w1-m2"),
    ):
        assert _alone({left: spans[left], right: spans[right]}, left), (left, right, spans)


_WIDE = (
    _HEAD
    + """
SETUPS = []

@pytest.fixture(scope="module")
def shared():
    SETUPS.append(1)
    return len(SETUPS)

@pytest.fixture(scope="module")
def broken():
    raise RuntimeError("module fixture failed")

@pytest.fixture
def mine(request):
    return [request.node.name]

@pytest.mark.concurrent
@pytest.mark.parametrize("i", range(4))
async def test_shared(shared, mine, i):
    await span(f"s{i}")
    assert shared == 1 and SETUPS == [1]
    assert mine == [f"test_shared[{i}]"]
    mine.append("x")

@pytest.mark.concurrent
@pytest.mark.parametrize("i", range(2))
async def test_broken(broken, i):
    await span(f"b{i}")
"""
)


def test_a_wide_fixture_is_set_up_once_and_its_error_reaches_every_dependent(
    tmp_path: Path,
) -> None:
    tree = make_tree(tmp_path, "wide", {"test_w.py": _WIDE})
    report, _ = _rustest(tree, "--asyncio-concurrency", "8")
    statuses = dict(_statuses(report))
    assert [statuses[f"test_w.py::test_shared[{i}]"] for i in range(4)] == ["passed"] * 4
    assert [statuses[f"test_w.py::test_broken[{i}]"] for i in range(2)] == ["error"] * 2
    for test in _tests(report):
        if "broken" in str(test["id"]):
            assert "module fixture failed" in str(test.get("message")), test
    assert _peak(_spans(tree)) >= 2


_PRINTS = (
    _HEAD
    + """
@pytest.mark.concurrent
@pytest.mark.parametrize("i", range(6))
async def test_p(i):
    print(f"before-{i}")
    await span(f"p{i}", 0.2)
    print(f"after-{i}")
"""
)


def test_each_overlapped_test_gets_its_own_print_output(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "prints", {"test_p.py": _PRINTS})
    report, _ = _rustest(tree, "--asyncio-concurrency", "6")
    assert _peak(_spans(tree)) >= 3
    for i, test in enumerate(_tests(report)):
        assert test["stdout"] == f"before-{i}\nafter-{i}\n", test


_SHARED_GLOBAL = (
    _HEAD
    + """
CURRENT = None

@pytest.mark.concurrent
async def test_a():
    global CURRENT
    CURRENT = "a"
    await span("a")
    assert CURRENT == "a"

@pytest.mark.concurrent
async def test_b():
    global CURRENT
    CURRENT = "b"
    await span("b")
    assert CURRENT == "b"
"""
)


def test_a_test_that_fails_only_overlapped_is_rerun_alone_and_reported_so(
    tmp_path: Path,
) -> None:
    tree = make_tree(tmp_path, "global", {"test_g.py": _SHARED_GLOBAL})
    report, result = _rustest(tree, "--asyncio-concurrency", "8")
    assert _statuses(report) == [("test_g.py::test_a", "passed"), ("test_g.py::test_b", "passed")]
    assert (
        "failed overlapped; serial rerun after 1 overlapped neighbours: passed" in result.stderr
    ), result.stderr
    # ...and on the test itself, where a reader of the report will see it.
    assert "failed while overlapped" in json.dumps(report)


_FAILS = _HEAD + "".join(
    f"\n@pytest.mark.concurrent\nasync def test_{i}():\n    await span('f{i}', 0.1 * {i % 3})\n"
    + ("    assert False\n" if i == 3 else "")
    for i in range(8)
)


def test_exitfirst_stops_where_a_serial_run_stops(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "x", {"test_x.py": _FAILS})
    serial, _ = _rustest(tree, "-x")
    overlapped, result = _rustest(tree, "-x", "--asyncio-concurrency", "8")
    assert _statuses(overlapped) == _statuses(serial)
    assert _statuses(serial)[-1] == ("test_x.py::test_3", "failed")
    assert serial["exit_code"] == overlapped["exit_code"] == 1
    assert "serial rerun after" in result.stderr


_UNRAISABLE = (
    _HEAD
    + """
import threading

class Noisy:
    def __del__(self):
        raise ValueError("from del")

@pytest.mark.concurrent
async def test_quiet():
    await span("q")

@pytest.mark.concurrent
async def test_unraisable():
    Noisy()
    await span("u")

@pytest.mark.concurrent
async def test_thread():
    def boom():
        raise KeyError("from thread")
    worker = threading.Thread(target=boom)
    worker.start()
    worker.join()
    await span("t")
"""
)


def test_an_unraisable_exception_is_attributed_to_the_test_that_raised_it(
    tmp_path: Path,
) -> None:
    tree = make_tree(tmp_path, "unraisable", {"test_u.py": _UNRAISABLE})
    report, _ = _rustest(tree, "--asyncio-concurrency", "8")
    by_id = {str(test["id"]): test for test in _tests(report)}
    warned = {test_id: json.dumps(test.get("warnings", [])) for test_id, test in by_id.items()}
    assert "from del" in warned["test_u.py::test_unraisable"]
    assert "from del" not in warned["test_u.py::test_quiet"]
    # A thread the test started has no context to route by (before 3.14's free-threaded
    # build): its exception is still reported, if not necessarily on this test.
    everything = json.dumps(report)
    assert "from thread" in everything


_TMP = (
    _HEAD
    + """
@pytest.mark.concurrent
@pytest.mark.parametrize("i", range(16))
async def test_a_function_name_long_enough_to_share_a_truncated_prefix(tmp_path, i):
    (tmp_path / "mine").write_text(str(i))
    await span(f"t{i}", 0.3)
    assert (tmp_path / "mine").read_text() == str(i)
"""
)


def test_tmp_path_in_many_overlapped_tests_never_collides(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "tmp", {"test_t.py": _TMP})
    report, _ = _rustest(tree, "--asyncio-concurrency", "16")
    assert {status for _, status in _statuses(report)} == {"passed"}
    assert len(_statuses(report)) == 16
    assert _peak(_spans(tree)) >= 4
