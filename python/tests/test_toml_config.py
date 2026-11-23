"""pytest 9's native TOML config: ``pytest.toml`` / ``.pytest.toml`` and a ``[tool.pytest]``
table of typed values, read only when the installed pytest is 9 or later.

Expected answers probed with pytest 9.0.2. This repository's ``.venv`` has pytest 8, so a
``pytest-9.0.0`` ``dist-info`` directory first on ``PYTHONPATH`` is what rustest reads as
pytest 9 installed (as in ``test_strict_ini.py``); a session rustest hands to pytest still
runs the ``.venv``'s pytest 8, so those cases assert the hand-off, not pytest's answer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from .helpers import clean_env, make_tree, run

_TESTS = "import pytest\n@pytest.mark.xfail\ndef test_x():\n    pass\ndef test_y():\n    pass\n"


def _pytest9(tmp_path: Path) -> dict[str, str]:
    site = tmp_path / "site9"
    (site / "pytest-9.0.0.dist-info").mkdir(parents=True)
    (site / "_pytest").mkdir()  # beside its package; an empty directory imports nothing
    return {**clean_env(), "PYTHONPATH": str(site)}


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    tree = make_tree(tmp_path, "toml", {"test_a.py": _TESTS})
    (tree / "pytest.ini").unlink()  # the case's own config files decide
    for name, body in files.items():
        _ = (tree / name).write_text(body, encoding="utf-8")
    return tree


def _rustest(tree: Path, env: dict[str, str] | None) -> tuple[int, str, dict[str, object]]:
    report = tree.parent / "report.json"
    result = run([sys.executable, "-m", "rustest", "--report-json", str(report)], tree, env)
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    return result.returncode, result.stdout + result.stderr, data


@pytest.mark.parametrize(
    ("files", "rc"),
    [
        # `[tool.pytest]` in TOML mode: a list for addopts, a native bool
        ({"pyproject.toml": "[tool.pytest]\naddopts = ['-q']\nxfail_strict = true\n"}, 1),
        # `pytest.toml` outranks `pytest.ini` in the same directory
        (
            {
                "pytest.toml": "[pytest]\nxfail_strict = true\n",
                "pytest.ini": "[pytest]\nxfail_strict = false\n",
            },
            1,
        ),
        # an empty `pytest.toml` is still the config
        ({"pytest.toml": "", "pytest.ini": "[pytest]\nxfail_strict = true\n"}, 0),
        ({".pytest.toml": "[pytest]\nxfail_strict = true\n"}, 1),
        ({"pyproject.toml": "[tool.pytest]\nstrict = true\n"}, 1),
    ],
    ids=[
        "tool-pytest",
        "pytest-toml-outranks-ini",
        "empty-pytest-toml",
        "dot-pytest-toml",
        "strict",
    ],
)
def test_pytest_9_toml_config_is_read(tmp_path: Path, files: dict[str, str], rc: int) -> None:
    """A strict xfail that passes fails (rc 1); a non-strict one is an xpass (rc 0)."""
    tree = _tree(tmp_path, files)

    code, output, report = _rustest(tree, _pytest9(tmp_path))

    assert code == rc, output
    assert "pytest_fallback" not in report, output


def test_both_toml_modes_at_once_is_pytest_9_s_usage_error(tmp_path: Path) -> None:
    files = {"pyproject.toml": "[tool.pytest]\nminversion = '8'\n[tool.pytest.ini_options]\n"}
    tree = _tree(
        tmp_path, {**files, "pyproject.toml": files["pyproject.toml"] + "addopts = '-q'\n"}
    )

    code, output, _ = _rustest(tree, _pytest9(tmp_path))

    assert code == 4, output
    assert (
        "Cannot use both [tool.pytest] (native TOML types) and [tool.pytest.ini_options]" in output
    )


@pytest.mark.parametrize(
    "body",
    [
        "[tool.pytest]\naddopts = '-q'\n",
        "[tool.pytest]\nmarkers = 'slow: slow'\n",
        "[tool.pytest]\nxfail_strict = 'true'\n",
        "[tool.pytest]\nminversion = 8\n",
        # keys rustest itself never reads, which pytest 9 still type-checks (rc 3, 3, 3, 1)
        "[tool.pytest]\ntmp_path_retention_count = 3\n",
        "[tool.pytest]\nfaulthandler_timeout = 5\n",
        "[tool.pytest]\nlog_level = 10\n",
        "[tool.pytest]\nconsider_namespace_packages = 'true'\n",
    ],
    ids=[
        "addopts-string",
        "markers-string",
        "bool-as-string",
        "minversion-int",
        "retention-count-int",
        "faulthandler-timeout-int",
        "log-level-int",
        "namespace-packages-string",
    ],
)
def test_a_wrongly_typed_toml_value_is_pytest_s_to_report(tmp_path: Path, body: str) -> None:
    """pytest 9 raises at the point it reads such a value, each in its own way (exit 1, 3 or
    4): the session goes to pytest, so its error and status are the user's."""
    tree = _tree(tmp_path, {"pyproject.toml": body})

    _, output, report = _rustest(tree, _pytest9(tmp_path))

    assert report.get("pytest_fallback"), output
    assert "the pytest 9 TOML config sets" in output


def test_a_conftest_ini_key_read_from_toml_mode_goes_to_pytest(tmp_path: Path) -> None:
    """pytest 9 checks a TOML-mode value against the key's declared type; the hook runtime
    does not yet, so a conftest reading one hands the session over."""
    conftest = (
        "def pytest_addoption(parser):\n"
        "    parser.addini('flavour', 'a conftest key')\n"
        "def pytest_configure(config):\n"
        "    config.getini('flavour')\n"
    )
    tree = _tree(
        tmp_path, {"pyproject.toml": "[tool.pytest]\nflavour = 'x'\n", "conftest.py": conftest}
    )

    _, output, report = _rustest(tree, _pytest9(tmp_path))

    reasons = report.get("pytest_fallback")
    assert isinstance(reasons, list), output
    assert any("native TOML config" in str(reason) for reason in reasons), reasons


def test_under_pytest_8_the_pytest_9_toml_config_is_not_config(tmp_path: Path) -> None:
    """pytest 8 reads neither ``pytest.toml`` nor ``[tool.pytest]``: the ``pytest.ini`` is the
    config, and its non-strict xfail passes."""
    tree = _tree(
        tmp_path,
        {"pytest.toml": "[pytest]\nxfail_strict = true\n", "pytest.ini": "[pytest]\n"},
    )

    code, output, report = _rustest(tree, None)

    assert code == 0, output
    assert "pytest_fallback" not in report, output


def test_a_toml_array_element_with_spaces_stays_one_argument(tmp_path: Path) -> None:
    """``addopts = ["-k", "test_y or test_x"]`` is two arguments, not four, and a marker line
    with a description is one line, so ``--strict-markers`` accepts the mark."""
    config = (
        "[tool.pytest]\n"
        'addopts = ["-k", "test_y or test_x", "--strict-markers"]\n'
        'markers = ["slow: takes long"]\n'
    )
    tests = (
        "import pytest\n"
        "@pytest.mark.slow\n"
        "def test_x():\n    pass\n"
        "def test_y():\n    pass\n"
        "def test_z():\n    pass\n"
    )
    tree = _tree(tmp_path, {"pyproject.toml": config, "test_a.py": tests})

    code, output, report = _rustest(tree, _pytest9(tmp_path))

    assert code == 0, output
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert (summary["passed"], summary["deselected"]) == (2, 1), output


def test_pytest_major_reads_a_source_checkout_s_version_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ``_pytest`` with no dist-info beside it (a source checkout, an editable install)
    answers from its generated ``_version.py``."""
    from rustest import _fallback

    package = tmp_path / "src" / "_pytest"
    package.mkdir(parents=True)
    _ = (package / "_version.py").write_text(
        "__version__ = version = '9.0.2'\n__version_tuple__ = version_tuple = (9, 0, 2)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "path", [str(tmp_path / "src")])

    assert _fallback.pytest_major() == 9
