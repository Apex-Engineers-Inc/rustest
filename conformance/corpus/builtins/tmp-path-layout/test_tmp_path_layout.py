"""Where ``tmp_path`` lives: ``<temproot>/pytest-of-<user>/pytest-<n>/<name><i>``.

`_pytest/tmpdir.py::TempPathFactory.getbasetemp`. Under pytest-xdist each worker's base is
``pytest-<n>/popen-gw<k>``, one ``pytest-<n>`` for the session. Suites key on this shape, so it is graded, not cosmetic.
"""

import os
import re

import pytest


def _session_dir(base):
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker:
        assert base.name == f"popen-{worker}"
        return base.parent
    return base


def test_the_base_is_pytests(tmp_path, tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    session = _session_dir(base)
    assert re.fullmatch(r"pytest-\d+", session.name), session
    assert session.parent.name.startswith("pytest-of-"), session
    assert tmp_path.parent == base
    assert tmp_path.name == "test_the_base_is_pytests0"


def test_mktemp_numbers_per_name(tmp_path_factory):
    assert tmp_path_factory.mktemp("data").name == "data0"
    assert tmp_path_factory.mktemp("data").name == "data1"
    assert tmp_path_factory.mktemp("other").name == "other0"
    exact = tmp_path_factory.mktemp("exact", numbered=False)
    assert exact.name == "exact" and exact.parent == tmp_path_factory.getbasetemp()


@pytest.mark.parametrize("value", ["a b", "c"])
def test_a_parametrized_name_is_sanitised(tmp_path, value):
    # Truncated to 30 characters, so both parameters share a name and number on.
    expected = {"a b": "test_a_parametrized_name_is_sa0", "c": "test_a_parametrized_name_is_sa1"}
    assert tmp_path.name == expected[value]
