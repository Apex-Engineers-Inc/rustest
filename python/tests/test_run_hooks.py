"""The per-test hooks, run in the worker as an xdist worker runs them.

``pytest_runtest_*`` and ``pytest_fixture_setup``: the corpus (``conformance/corpus/hooks``)
grades their order and outcomes against pytest.  These pin what the corpus cannot see -- the
hand-offs (up front and mid-run), the exit codes of a hook that raises, the discarded results,
several workers, and the item's attribute surface against pytest's own.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rustest._worker import PYTEST_FIXTUREDEF_NAMES, PYTEST_FUNCTION_NAMES

from .helpers import make_tree, run


def _rustest(tree: Path, *args: str, workers: int = 1) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, "-m", "rustest", "-n", str(workers), *args], tree)


def _report(tree: Path, *args: str, workers: int = 1) -> tuple[int, dict[str, object]]:
    path = tree.parent / f"{tree.name}-report.json"
    result = _rustest(tree, "--report-json", str(path), *args, workers=workers)
    return result.returncode, json.loads(path.read_text(encoding="utf-8"))


def _statuses(report: dict[str, object]) -> dict[str, str]:
    tests = report["tests"]
    assert isinstance(tests, list)
    return {str(t["id"]): str(t["status"]) for t in tests}  # pyright: ignore[reportUnknownArgumentType]


def _reasons(report: dict[str, object]) -> str:
    return "; ".join(str(r) for r in report.get("pytest_fallback", []))  # pyright: ignore[reportUnknownArgumentType,reportUnknownVariableType,reportAttributeAccessIssue]


_PASS = "def test_a():\n    pass\n"


# -- the item's surface, against pytest's own --------------------------------------------


def test_the_item_and_fixturedef_name_sets_are_pytests(tmp_path: Path) -> None:
    """A name pytest's ``Function`` (or ``FixtureDef``) has is a hand-off when the shim lacks it;
    any other name is pytest's own ``AttributeError``.  The sets are read off real items."""
    tree = make_tree(
        tmp_path,
        "names",
        {
            "conftest.py": (
                "import json\n\n\n"
                "def pytest_collection_modifyitems(items):\n"
                "    names = set()\n"
                "    for item in items:\n"
                "        names.update(n for n in dir(item) if not n.startswith('__'))\n"
                "    fixturedef = items[0]._fixtureinfo.name2fixturedefs['f'][0]\n"
                "    defs = sorted(n for n in dir(fixturedef) if not n.startswith('__'))\n"
                "    with open('names.json', 'w') as handle:\n"
                "        json.dump({'item': sorted(names), 'fixturedef': defs}, handle)\n"
            ),
            "test_n.py": (
                "import pytest\n\n\n@pytest.fixture\ndef f():\n    return 1\n\n\n"
                "def test_a(f):\n    pass\n\n\n"
                "@pytest.mark.parametrize('x', [1])\ndef test_p(x):\n    pass\n"
            ),
        },
    )
    result = run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:asyncio",
            "-p",
            "no:hypothesispytest",
            "-p",
            "no:pytest_codeblocks",
            "--collect-only",
        ],
        tree,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    names = json.loads((tree / "names.json").read_text(encoding="utf-8"))
    assert sorted(PYTEST_FUNCTION_NAMES) == names["item"]
    assert sorted(PYTEST_FIXTUREDEF_NAMES) == names["fixturedef"]


def test_a_name_pytests_item_lacks_is_a_plain_attribute_error_in_a_hook(tmp_path: Path) -> None:
    """The ``rep_call`` idiom: ``hasattr(item, "rep_call")`` before the wrapper set it."""
    tree = make_tree(
        tmp_path,
        "repcall",
        {
            "conftest.py": (
                "import pytest\n\n\n"
                "@pytest.hookimpl(wrapper=True)\n"
                "def pytest_runtest_makereport(item, call):\n"
                "    assert hasattr(item, 'rep_' + call.when) is False\n"
                "    report = yield\n"
                "    setattr(item, 'rep_' + call.when, report)\n"
                "    return report\n"
            ),
            "test_a.py": _PASS,
        },
    )

    code, report = _report(tree)

    assert code == 0 and not _reasons(report)


def test_item_config_in_a_hook_is_the_hooks_config(tmp_path: Path) -> None:
    """``item.config.getoption`` answers natively; ``item.config.stash`` -- something the hook
    runtime's ``config`` does not provide -- hands the session off instead of erroring tests."""
    native = make_tree(
        tmp_path,
        "getoption",
        {
            "conftest.py": (
                "def pytest_addoption(parser):\n"
                "    parser.addoption('--flag', action='store_true')\n\n\n"
                "def pytest_runtest_setup(item):\n"
                "    assert item.config.getoption('--flag') is False\n"
            ),
            "test_a.py": _PASS,
        },
    )
    stash = make_tree(
        tmp_path,
        "stash",
        {
            "conftest.py": "def pytest_runtest_setup(item):\n    item.config.stash\n",
            "test_a.py": _PASS,
        },
    )

    code, report = _report(native)
    assert code == 0 and not _reasons(report)

    result = _rustest(stash)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "discarding the 1 results so far" in result.stderr
    assert "config.stash" in result.stderr


# -- hand-offs: up front and mid-run ----------------------------------------------------


@pytest.mark.parametrize(
    "hook",
    [
        "def pytest_sessionfinish(session, exitstatus):\n    pass\n",
        "def pytest_unconfigure(config):\n    pass\n",
        "def pytest_runtest_teardown(item, nextitem):\n    pass\n",
    ],
    ids=["sessionfinish", "unconfigure", "teardown-nextitem"],
)
def test_a_controller_or_nextitem_hook_hands_off_before_any_test(tmp_path: Path, hook: str) -> None:
    tree = make_tree(tmp_path, "upfront", {"conftest.py": hook, "test_a.py": _PASS})

    code, report = _report(tree)

    assert code == 0 and _reasons(report)
    assert "discarded" not in report


def test_a_violation_mid_run_discards_the_results_and_reruns_under_pytest(tmp_path: Path) -> None:
    """The late hand-off: the second test's report hook touches what the runtime cannot answer.
    The batch stops there, the results so far are said to be discarded, the session reruns
    under pytest, and the native attempt wrote no duration record."""
    tree = make_tree(
        tmp_path,
        "late",
        {
            "conftest.py": (
                "def pytest_runtest_logreport(report):\n"
                "    if report.when == 'call' and report.nodeid.endswith('test_2'):\n"
                "        report.capstdout\n"
            ),
            "test_l.py": "".join(f"def test_{n}():\n    pass\n" for n in range(1, 5)),
        },
    )

    result = _rustest(tree)

    assert result.returncode == 0
    assert "rustest: discarding the 2 results so far" in result.stderr
    assert "report.capstdout" in result.stderr
    assert "reports an error" in result.stderr  # what --no-pytest-fallback would do instead
    assert "4 passed" in result.stdout  # pytest's own run of the whole session
    assert not (tree / ".rustest_cache" / "v" / "rustest" / "durations").exists()


@pytest.mark.parametrize("hook", ["logstart", "logfinish"])
def test_a_violation_in_logstart_or_logfinish_is_caught_like_any_other(
    tmp_path: Path, hook: str
) -> None:
    tree = make_tree(
        tmp_path,
        hook,
        {
            "conftest.py": (
                f"def pytest_runtest_{hook}(nodeid, location):\n"
                "    import pytest\n"
                "    pytest.TestReport.capstdout.fget(None)\n"
            ),
            "test_l.py": "".join(f"def test_{n}():\n    pass\n" for n in range(1, 4)),
        },
    )

    code, report = _report(tree, "--no-pytest-fallback")

    assert code == 1, report
    assert list(_statuses(report).values()) == ["error", "error", "error"]


def test_without_fallback_a_violation_mid_run_errors_that_test_only(tmp_path: Path) -> None:
    tree = make_tree(
        tmp_path,
        "nofallback",
        {
            "conftest.py": (
                "def pytest_runtest_logreport(report):\n"
                "    if report.when == 'call' and report.nodeid.endswith('test_2'):\n"
                "        report.capstdout\n"
            ),
            "test_l.py": "".join(f"def test_{n}():\n    pass\n" for n in range(1, 4)),
        },
    )

    code, report = _report(tree, "--no-pytest-fallback")

    assert list(_statuses(report).values()) == ["passed", "error", "passed"]
    assert code == 1 and "pytest_fallback" not in report


def test_a_wide_teardown_failure_a_report_hook_would_see_hands_off(tmp_path: Path) -> None:
    """pytest reports a module fixture's failed teardown in the last test's teardown report;
    rustest tears the module down at the next test, so a report hook would see a pass."""
    tree = make_tree(
        tmp_path,
        "wide",
        {
            "conftest.py": (
                "import pytest\n\n\n"
                "@pytest.fixture(scope='module')\ndef shared():\n    yield\n"
                "    raise RuntimeError('module teardown')\n\n\n"
                "def pytest_runtest_logreport(report):\n    pass\n"
            ),
            "test_a.py": "def test_one(shared):\n    pass\n",
            "test_b.py": "def test_two():\n    pass\n",
        },
    )

    code, report = _report(tree)

    assert code == 1
    assert "session-scoped fixture teardown" in _reasons(report)


def test_a_teardown_hook_raising_before_pytests_teardown_hands_off(tmp_path: Path) -> None:
    tree = make_tree(
        tmp_path,
        "tdraise",
        {
            "conftest.py": (
                "def pytest_runtest_teardown(item):\n"
                "    if item.name == 'test_1':\n"
                "        raise RuntimeError('before the fixtures')\n"
            ),
            "test_l.py": "def test_1():\n    pass\n\n\ndef test_2():\n    pass\n",
        },
    )

    code, report = _report(tree)

    assert code == 1
    assert "raised before test_l.py::test_1's fixtures were torn down" in _reasons(report)


# -- exit codes, several workers --------------------------------------------------------


def test_a_logreport_that_raises_is_an_internal_error(tmp_path: Path) -> None:
    """Not inside ``CallInfo``: pytest reports INTERNALERROR and exits 3."""
    tree = make_tree(
        tmp_path,
        "internal",
        {
            "conftest.py": "def pytest_runtest_logreport(report):\n    raise RuntimeError('lr')\n",
            "test_a.py": _PASS,
        },
    )

    result = _rustest(tree)

    assert result.returncode == 3
    assert "RuntimeError: lr" in result.stderr


def test_hooks_run_in_every_worker_and_the_run_stays_native(tmp_path: Path) -> None:
    """Three files over two workers: each worker registers the conftest and runs its hooks
    for its own tests, as each xdist worker does."""
    tree = make_tree(
        tmp_path,
        "pool",
        {
            "conftest.py": (
                "import pytest\n\n\n"
                "@pytest.hookimpl(tryfirst=True)\n"
                "def pytest_runtest_setup(item):\n"
                "    if item.get_closest_marker('later'):\n"
                "        pytest.skip('later')\n\n\n"
                "@pytest.hookimpl(wrapper=True)\n"
                "def pytest_runtest_makereport(item, call):\n"
                "    report = yield\n"
                "    report.user_properties.append(('seen', call.when))\n"
                "    return report\n"
            ),
            "pytest.ini": "[pytest]\nmarkers =\n    later: skipped by the conftest\n",
            **{
                f"test_{n}.py": (
                    "import pytest\n\n\n@pytest.mark.later\ndef test_skip():\n    pass\n\n\n"
                    "def test_run():\n    pass\n"
                )
                for n in "abc"
            },
        },
    )

    code, report = _report(tree, workers=2)

    assert code == 0 and not _reasons(report)
    statuses = _statuses(report)
    assert sorted(statuses.values()) == ["passed"] * 3 + ["skipped"] * 3


# -- fixtures ---------------------------------------------------------------------------


def test_a_fixture_finalizer_runs_even_when_its_setup_failed(tmp_path: Path) -> None:
    """``FixtureDef.execute`` schedules the finalizer "even if the setup failed"."""
    tree = make_tree(
        tmp_path,
        "finalizer",
        {
            "test_a.py": (
                "import pytest\n\nLOG = []\n\n\n"
                "@pytest.fixture\ndef broken(request):\n"
                "    request.addfinalizer(lambda: LOG.append('finalized'))\n"
                "    raise RuntimeError('setup')\n\n\n"
                "def test_one(broken):\n    pass\n\n\n"
                "def test_two():\n    assert LOG == ['finalized']\n"
            ),
        },
    )

    code, report = _report(tree)

    assert _statuses(report) == {"test_a.py::test_one": "error", "test_a.py::test_two": "passed"}
    assert code == 1
