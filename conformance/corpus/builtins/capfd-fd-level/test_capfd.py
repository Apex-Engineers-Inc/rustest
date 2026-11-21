"""``capfd`` captures at the file-descriptor level, and ``capsys`` still does not.

The pair is the point. ``capsys`` swaps ``sys.stdout``/``sys.stderr``, so it sees a
``print()`` and misses a write to the descriptor behind it; ``capfd`` redirects the
descriptor itself, so it sees both — and sees them *interleaved in the order they happened*,
because pytest's ``FDCapture`` points ``sys.stdout`` at the same open file the fd now points
at (`_pytest/capture.py::FDCaptureBase.__init__`, the ``SysCapture(targetfd, self.tmpfile)``
branch).

``test_a_raw_write_that_looks_like_protocol_traffic`` is the case's real subject. rustest v2
runs tests in worker subprocesses that speak JSON-lines over **stdout**, so before fd capture
existed a test writing to fd 1 wrote into the orchestrator's parser. The line below is a
syntactically valid worker response for an op that does not exist: had it reached the
orchestrator it would have been protocol drift and killed the worker with exit 2. Under
pytest it is simply captured output, and this case asserts that it is captured output here
too.
"""

import os
import subprocess
import sys


def test_capfd_sees_a_raw_descriptor_write(capfd):
    os.write(1, b"raw-out\n")
    os.write(2, b"raw-err\n")
    captured = capfd.readouterr()
    assert captured.out == "raw-out\n"
    assert captured.err == "raw-err\n"


def test_capfd_interleaves_prints_and_raw_writes(capfd):
    print("one")
    sys.stdout.flush()
    os.write(1, b"two\n")
    print("three")
    assert capfd.readouterr().out == "one\ntwo\nthree\n"


def test_capsys_does_not_see_a_raw_descriptor_write(capsys):
    os.write(1, b"invisible\n")
    print("visible")
    assert capsys.readouterr().out == "visible\n"


def test_capfd_sees_a_subprocess(capfd):
    _ = subprocess.run(
        [sys.executable, "-c", "print('from-a-child')"],
        check=True,
    )
    assert capfd.readouterr().out.strip() == "from-a-child"


def test_a_raw_write_that_looks_like_protocol_traffic(capfd):
    os.write(1, b'{"op":"test_result","id":"nope","status":"passed"}\n')
    assert capfd.readouterr().out.startswith('{"op":"test_result"')


def test_readouterr_resets_the_buffer(capfd):
    os.write(1, b"first\n")
    assert capfd.readouterr().out == "first\n"
    assert capfd.readouterr().out == ""
