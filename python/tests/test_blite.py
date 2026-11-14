"""B-lite: test files that use pytest's ``pytester`` run under one pytest subprocess first, the
rest natively -- and every guard that sends the whole session to pytest instead."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from .helpers import clean_env, make_tree, run

_PYTESTER = (
    'pytest_plugins = ["pytester"]\n'
    "def test_inner(pytester):\n"
    '    pytester.makepyfile(test_inner="def test_x():\\n    pass\\n")\n'
    '    pytester.runpytest("-p", "no:cacheprovider").assert_outcomes(passed=1)\n'
    "def test_fails():\n"
    "    assert False\n"
)
_FILES = {
    "test_a.py": "def test_one():\n    pass\n",
    "test_b_pytester.py": _PYTESTER,
    "test_c.py": "def test_two():\n    pass\n",
}


def _rustest(
    tree: Path, *args: str, env: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    path = tree.parent / f"{tree.name}.json"
    result = run([sys.executable, "-m", "rustest", "--report-json", str(path), *args], tree, env)
    return result, json.loads(path.read_text(encoding="utf-8"))


def _ids(report: dict[str, object]) -> list[tuple[str, str]]:
    tests = report["tests"]
    assert isinstance(tests, list)
    return [(str(t["id"]), str(t["status"])) for t in tests]  # pyright: ignore[reportUnknownArgumentType]


def test_the_pytester_file_runs_first_under_pytest_and_the_rest_natively(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "split", _FILES)

    result, report = _rustest(tree)

    assert result.returncode == 1, result.stderr
    assert "running 1 file(s) under pytest first" in result.stderr
    assert "pytest_fallback" not in report
    assert _ids(report) == [
        ("test_a.py::test_one", "passed"),
        ("test_b_pytester.py::test_inner", "passed"),
        ("test_b_pytester.py::test_fails", "failed"),
        ("test_c.py::test_two", "passed"),
    ]
    lastfailed = json.loads((tree / ".rustest_cache/v/cache/lastfailed").read_text("utf-8"))
    assert list(lastfailed) == ["test_b_pytester.py::test_fails"]


def test_the_live_lines_cover_the_handed_tests_in_order(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "verbose", _FILES)

    result, _ = _rustest(tree, "-v")

    lines = [line.split(" ")[0] for line in result.stdout.splitlines() if "::" in line]
    assert lines[:4] == [
        "test_a.py::test_one",
        "test_b_pytester.py::test_inner",
        "test_b_pytester.py::test_fails",
        "test_c.py::test_two",
    ], result.stdout


def test_the_handed_files_warnings_are_the_runs(tmp_path: Path) -> None:
    files = dict(_FILES)
    files["test_b_pytester.py"] = (
        'import warnings\npytest_plugins = ["pytester"]\n'
        "def test_w():\n    warnings.warn(UserWarning('from handed'))\n"
    )
    tree = make_tree(tmp_path, "warns", files)

    result, report = _rustest(tree)

    assert result.returncode == 0, result.stderr
    summary = report["summary"]
    assert isinstance(summary, dict) and summary["warnings"] == 1
    assert "from handed" in result.stdout


def test_an_interrupt_in_a_handed_test_ends_the_session_once(tmp_path: Path) -> None:
    """pytest's session ends at a ``KeyboardInterrupt``: the handed test is not run twice and
    nothing runs after it (native files included), exit 2."""
    files = {
        "test_a.py": "def test_a():\n    open('side.txt', 'a').write('A')\n",
        "test_b_pytester.py": (
            'pytest_plugins = ["pytester"]\n'
            "def test_b():\n    open('side.txt', 'a').write('B')\n    raise KeyboardInterrupt\n"
        ),
    }
    tree = make_tree(tmp_path, "interrupt", files)

    result, _ = _rustest(tree)

    assert result.returncode == 2, result.stderr
    assert (tree / "side.txt").read_text(encoding="utf-8") == "B"
    assert "handing this session to pytest" not in result.stderr


def test_a_value_that_spells_a_path_stays_an_option_value(tmp_path: Path) -> None:
    files = {f"slow/{name}": body for name, body in _FILES.items()}
    tree = make_tree(tmp_path, "values", files)

    result, report = _rustest(tree, "-k", "slow", "slow")

    assert "expected one argument" not in result.stderr
    assert "running 1 file(s) under pytest first" in result.stderr
    assert "pytest_fallback" not in report


def test_the_handed_run_writes_no_nodeids_cache(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "nodeids", _FILES)

    _ = _rustest(tree)

    assert not (tree / ".rustest_cache/v/cache/nodeids").exists()


def test_an_unused_session_directory_costs_pytest_no_number(tmp_path: Path) -> None:
    """Nothing native uses ``tmp_path``: rustest's own ``pytest-<n>`` goes when the run ends,
    and only the handed pytest's (pytester uses ``tmp_path``) is left."""
    temproot = tmp_path / "temproot"
    temproot.mkdir()
    tree = make_tree(tmp_path, "numbers", _FILES)

    _ = _rustest(tree, env={**clean_env(), "PYTEST_DEBUG_TEMPROOT": str(temproot)})

    (root,) = temproot.glob("pytest-of-*")
    assert len([path for path in root.iterdir() if path.name.startswith("pytest-")]) == 1


def test_k_deselects_the_handed_files_once(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "deselect", _FILES)

    result, report = _rustest(tree, "-k", "not fails")

    assert result.returncode == 0, result.stderr
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert (summary["passed"], summary["deselected"]) == (3, 1)


@pytest.mark.parametrize(
    ("name", "extra", "args"),
    [
        ("maxfail", {}, ("-x",)),
        ("node-id", {}, ("test_b_pytester.py::test_inner",)),
        (
            "fixture",
            {"test_d.py": "def test_d(pytester):\n    assert pytester.path.is_dir()\n"},
            (),
        ),
        ("aggregator", {"conftest.py": "def pytest_sessionfinish(session):\n    pass\n"}, ()),
        ("error", {"test_d.py": "import nope\n"}, ()),
        (
            "reorder",
            {"conftest.py": "def pytest_collection_modifyitems(items):\n    items.reverse()\n"},
            (),
        ),
        ("capture", {}, ("-s",)),
        ("last-failed", {}, ("--lf",)),
    ],
)
def test_what_ties_the_file_to_the_session_sends_the_whole_session(
    tmp_path: Path, name: str, extra: dict[str, str], args: tuple[str, ...]
) -> None:
    tree = make_tree(tmp_path, name, {**_FILES, **extra})

    result, report = _rustest(tree, *args)

    assert "running 1 file(s) under pytest first" not in result.stderr
    assert report.get("pytest_fallback"), result.stderr
    if name in ("node-id", "fixture"):
        assert result.returncode in (0, 1), result.stderr  # pytest's answer, not a usage error


def test_a_conftest_naming_pytester_still_sends_the_whole_session(tmp_path: Path) -> None:
    files = {"conftest.py": 'pytest_plugins = ["pytester"]\n', "test_a.py": "def test_a(): pass\n"}
    tree = make_tree(tmp_path, "conftest", files)

    result, report = _rustest(tree)

    assert result.returncode == 0, result.stderr
    assert report.get("pytest_fallback")


def test_without_an_ini_conftests_above_the_rootdir_stay_out(tmp_path: Path) -> None:
    """The handed pytest gets an empty ``-c``; ``--confcutdir`` keeps it from loading a conftest
    above the rootdir, as the session itself does not."""
    outer = tmp_path / "outer"
    (outer / "proj").mkdir(parents=True)
    _ = (outer / "conftest.py").write_text(
        "import pytest\n@pytest.fixture(autouse=True)\ndef boom():\n"
        "    raise RuntimeError('conftest above rootdir loaded')\n",
        encoding="utf-8",
    )
    _ = (outer / "proj" / "setup.py").write_text("", encoding="utf-8")
    for name, body in _FILES.items():
        _ = (outer / "proj" / name).write_text(body, encoding="utf-8")

    result, report = _rustest(outer / "proj")

    assert "conftest above rootdir loaded" not in result.stdout + result.stderr
    assert "pytest_fallback" not in report


def test_without_fallback_the_pytester_file_is_a_collection_error(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "native", _FILES)

    result, report = _rustest(tree, "--no-pytest-fallback")

    assert result.returncode == 2
    errors = report["collection_errors"]
    assert isinstance(errors, list) and len(errors) == 1  # pyright: ignore[reportUnknownArgumentType]
