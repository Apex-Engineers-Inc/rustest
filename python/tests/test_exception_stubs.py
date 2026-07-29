"""Tests for the ``_pytest.outcomes`` compatibility surface.

Two halves. The attribute tests below pin the *data* shape a suite reaching into
``_pytest.outcomes`` sees. The differential probes at the bottom pin the thing that actually
cost verdicts: ``_pytest.outcomes``' classes must be the **same objects** the compat shim
raises, because ``except`` and ``isinstance`` in a user's test body resolve before the worker
ever sees the exception. See ``python/rustest/_pytest_stub/outcomes.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_failed_exception_has_msg_attribute() -> None:
    """Test that Failed exception has .msg attribute."""
    from rustest._pytest_stub.outcomes import Failed

    exc = Failed("test message")
    assert hasattr(exc, "msg"), "Failed exception should have .msg attribute"
    assert exc.msg == "test message"


def test_failed_exception_has_pytrace_attribute() -> None:
    """Test that Failed exception has .pytrace attribute."""
    from rustest._pytest_stub.outcomes import Failed

    exc = Failed("test", pytrace=False)
    assert hasattr(exc, "pytrace"), "Failed exception should have .pytrace attribute"
    assert exc.pytrace is False

    exc2 = Failed("test")
    assert exc2.pytrace is True  # Default value


def test_skipped_exception_has_msg_attribute() -> None:
    """Test that Skipped exception has .msg attribute."""
    from rustest._pytest_stub.outcomes import Skipped

    exc = Skipped("skip reason")
    assert hasattr(exc, "msg"), "Skipped exception should have .msg attribute"
    assert exc.msg == "skip reason"


def test_skipped_exception_has_allow_module_level() -> None:
    """Test that Skipped exception has .allow_module_level attribute."""
    from rustest._pytest_stub.outcomes import Skipped

    exc = Skipped("skip", allow_module_level=True)
    assert hasattr(exc, "allow_module_level"), "Skipped exception should have .allow_module_level"
    assert exc.allow_module_level is True

    exc2 = Skipped("skip")
    assert exc2.allow_module_level is False  # Default value


def test_failed_exception_with_raises() -> None:
    """Test using Failed exception with pytest.raises."""
    from rustest._pytest_stub.outcomes import Failed

    with pytest.raises(Failed) as excinfo:
        raise Failed("custom message", pytrace=False)

    assert excinfo.value.msg == "custom message"
    assert excinfo.value.pytrace is False


def test_skipped_exception_with_raises() -> None:
    """Test using Skipped exception with pytest.raises."""
    from rustest._pytest_stub.outcomes import Skipped

    with pytest.raises(Skipped) as excinfo:
        raise Skipped("skip this", allow_module_level=True)

    assert excinfo.value.msg == "skip this"
    assert excinfo.value.allow_module_level is True


def test_failed_exception_string_representation() -> None:
    """Test that Failed exception has proper string representation."""
    from rustest._pytest_stub.outcomes import Failed

    exc = Failed("error occurred")
    assert str(exc) == "error occurred"


def test_skipped_exception_string_representation() -> None:
    """Test that Skipped exception has proper string representation."""
    from rustest._pytest_stub.outcomes import Skipped

    exc = Skipped("reason for skip")
    assert str(exc) == "reason for skip"


def test_exception_attributes_in_except_block() -> None:
    """Test accessing exception attributes in except block."""
    from rustest._pytest_stub.outcomes import Failed

    try:
        raise Failed("test failure", pytrace=True)
    except Failed as e:
        # Should be able to access attributes without AttributeError
        assert e.msg == "test failure"
        assert e.pytrace is True
        # `BaseException`, not `Exception`, and that is pytest's hierarchy: `OutcomeException`
        # derives from `BaseException` precisely so a test body's `except Exception:` cannot
        # swallow the runner's own control flow. This assertion said `Exception` while the
        # stub declared its own classes -- i.e. it pinned the divergence.
        assert isinstance(e, BaseException)
        assert not isinstance(e, Exception)


# --------------------------------------------------------------------------------------
# The aliasing invariant, and the two catch paths it exists to fix
# --------------------------------------------------------------------------------------


def test_pytest_outcomes_aliases_the_shims_own_outcome_classes() -> None:
    """``_pytest.outcomes.X`` **is** ``rustest.decorators.X`` -- one object, not two.

    Identity, not equality or a subclass relation. A parallel class hierarchy passes every
    ``hasattr``/``isinstance``-against-itself test above and still swaps verdicts in the two
    probes below, which is exactly how it survived until the Phase 4 v1 deletion.
    """
    from rustest import _pytest_stub
    from rustest.decorators import Failed, OutcomeException, Skipped, XFailed, fail, skip, xfail

    outcomes = _pytest_stub.outcomes
    assert outcomes.Skipped is Skipped
    assert outcomes.Failed is Failed
    assert outcomes.XFailed is XFailed
    assert outcomes.OutcomeException is OutcomeException
    assert outcomes.skip is skip
    assert outcomes.fail is fail
    assert outcomes.xfail is xfail


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for leak in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST", "RUSTEST_RUNNING"):
        _ = env.pop(leak, None)
    return env


def _tree(tmp_path: Path, name: str, body: str) -> Path:
    """An isolated one-file tree with its own ``pytest.ini``.

    Without the ini both runners walk *out* of ``tmp_path`` and adopt this repository's
    ``pyproject.toml`` as rootdir.
    """
    tree = tmp_path / name
    tree.mkdir(parents=True, exist_ok=True)
    _ = (tree / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    _ = (tree / "test_probe.py").write_text(body, encoding="utf-8")
    return tree


def _tally(tree: Path, argv: list[str]) -> str:
    """Run *argv* in *tree* and return the one-line outcome tally, whichever stream it is on.

    pytest ``-q`` puts its summary on stdout; rustest puts its on stderr. Both are searched so
    one helper can ask the same question of either runner.
    """
    proc = subprocess.run(
        argv, cwd=str(tree), capture_output=True, text=True, env=_clean_env(), check=False
    )
    for stream in (proc.stdout, proc.stderr):
        for line in reversed(stream.strip().splitlines()):
            if "passed" in line or "skipped" in line or "failed" in line:
                # pytest's `1 skipped in 0.01s` and rustest's `1 skipped in 0.01s` share the
                # tail; the leading count-and-word pair is the claim being compared.
                return " ".join(line.split(" in ")[0].split())
    raise AssertionError(f"no tally line in:\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}")


#: ``except Exception:`` wrapped around ``_pytest.outcomes.skip()``.
#:
#: pytest: the skip *survives* the handler, because ``Skipped`` is a ``BaseException`` -- the
#: test reports SKIPPED. With the old parallel stub class (an ordinary ``Exception``) rustest
#: swallowed it and reported PASSED: a skip silently turned green.
_SKIP_THROUGH_EXCEPT_EXCEPTION = """\
from _pytest.outcomes import skip


def test_probe():
    try:
        skip("deliberate")
    except Exception:
        pass
"""

#: ``except Skipped:`` (imported from ``_pytest.outcomes``) wrapped around ``pytest.skip()``.
#:
#: pytest: one class, so the handler catches and the test reports PASSED. With the old
#: parallel stub class the ``except`` did not match what ``pytest.skip()`` raises, so rustest
#: reported SKIPPED -- the same divergence, in the other direction.
_SKIP_CAUGHT_BY_ITS_OWN_CLASS = """\
import pytest
from _pytest.outcomes import Skipped


def test_probe():
    try:
        pytest.skip("deliberate")
    except Skipped:
        pass
"""


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (_SKIP_THROUGH_EXCEPT_EXCEPTION, "1 skipped"),
        (_SKIP_CAUGHT_BY_ITS_OWN_CLASS, "1 passed"),
    ],
    ids=["except-Exception-cannot-swallow-a-skip", "except-Skipped-catches-pytest-skip"],
)
def test_outcome_catch_paths_match_real_pytest(tmp_path: Path, body: str, expected: str) -> None:
    """Both catch paths report the same outcome under rustest as under real pytest.

    Differential rather than asserted against a literal alone: the literal records what was
    *measured* on pytest 8.4.2, and the pytest leg re-measures it on whatever pytest is
    installed, so a change in the oracle shows up as a failure here rather than as a silently
    stale expectation.
    """
    tree = _tree(tmp_path, "probe", body)
    oracle = _tally(
        tree, [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider"]
    )
    ours = _tally(tree, [sys.executable, "-m", "rustest"])
    assert oracle == expected, oracle
    assert ours == oracle, f"rustest={ours!r} pytest={oracle!r}"
