"""Under ``-n 2`` (two files, so rustest's pool is two as well): where ``tmp_path`` lives: ``<temproot>/pytest-of-<user>/pytest-<n>/<name><i>``.

`_pytest/tmpdir.py::TempPathFactory.getbasetemp`. Under pytest-xdist each worker's base is
``pytest-<n>/popen-gw<k>``, one ``pytest-<n>`` for the session. Suites key on this shape, so it is graded, not cosmetic.
"""

import os
import re


def _session_dir(base):
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker:
        assert base.name == f"popen-{worker}"
        return base.parent
    return base


def test_another_file_has_its_own_numbering(tmp_path, tmp_path_factory):
    assert os.environ.get("PYTEST_XDIST_WORKER")  # the popen-gw<k> path is the one exercised
    base = tmp_path_factory.getbasetemp()
    session = _session_dir(base)
    assert re.fullmatch(r"pytest-\d+", session.name), session
    assert session.parent.name.startswith("pytest-of-"), session
    assert tmp_path.parent == base
    assert tmp_path.name == "test_another_file_has_its_own_0"
