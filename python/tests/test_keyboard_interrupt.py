"""A test that raises ``KeyboardInterrupt`` ends the session as it does under pytest.

pytest 8.4.2 (probed, and re-run here as the oracle): the tests before it keep their results,
the interrupted test and everything after it -- other files included -- never report, the
run exits 2, and ``TerminalReporter._report_keyboardinterrupt`` prints a ``!`` rule around
``KeyboardInterrupt``, the ``path:line`` that raised it, and a ``--full-trace`` hint. rustest
used to lose the worker instead and exit 3 with an internal error.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from .helpers import make_tree as _tree, run as _run

_FILES = {
    "test_k.py": "def test_a():\n    pass\n\n\ndef test_b():\n    raise KeyboardInterrupt\n\n\n"
    "def test_c():\n    pass\n",
    "test_z.py": "def test_other():\n    pass\n",
}


def test_a_raised_keyboard_interrupt_ends_the_session_like_pytest(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "kbi", _FILES)
    pytest = _run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"], tree)
    rustest = _run([sys.executable, "-m", "rustest", "-n", "1"], tree)

    for proc in (pytest, rustest):
        output = proc.stdout + proc.stderr
        assert proc.returncode == 2, output
        assert re.search(r"^!+ KeyboardInterrupt !+$", output, re.MULTILINE), output
        assert re.search(r"test_k\.py:6: KeyboardInterrupt$", output, re.MULTILINE), output
        assert "(to show a full traceback on KeyboardInterrupt use --full-trace)" in output
        assert re.search(r"^1 passed in ", output, re.MULTILINE), output
    assert "INTERNALERROR" not in rustest.stderr
    # Piped, the progress is pytest's line for what ran, with no final percent: cut short.
    assert rustest.stdout.splitlines()[0] == "test_k.py ."
