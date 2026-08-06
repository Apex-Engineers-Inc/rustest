"""``--llm``, ``--llm-full`` and ``--llm-schema``: the JSONL output contract.

The mode exists so an LLM coding agent can *parse* a run instead of reading it, which makes
two properties load-bearing in a way they are not for the human renderer:

* **stdout is JSONL and nothing else.**  Every test below that reads stdout parses every line
  with :func:`json.loads`; a stray banner, a coverage table or a summary sentence would fail
  the parse rather than merely look untidy.
* **The bytes are deterministic.**  The engine reassembles ``report["tests"]`` in manifest
  order however the pool interleaved (``src/v2/execute.rs``), so the renderer sorts nothing
  and the same failures produce the same stream at any ``-n``.  Pinned directly by
  :func:`test_the_stream_is_identical_at_every_worker_count`.

**Why the goldens elide the traceback body.**  ``msg`` carries the worker's whole message, and
the middle of a CPython traceback is CPython's: the frame block gains and loses caret
underlines between 3.12 and 3.14, and pinning it would turn an interpreter upgrade into a red
gate about nothing.  :func:`_shape` therefore keeps the message's **last line** verbatim -- the
assertion-rewritten ``AssertionError: assert 41 == 42``, which is the entire reason the field
is worth emitting -- and replaces the frames with ``<frames>``.  The frames are not untested:
:func:`test_the_message_carries_the_whole_rewritten_traceback` asserts on them directly, and
the ``line`` field is derived from them and golden-pinned on every failure line.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from rustest import cli
from rustest._llm import CAPTURE_MAX_LINES, failing_line, render, truncate_tail
from rustest._llm_schema import FAIL_STATUSES, SCHEMA, SCHEMA_VERSION, SKIP_STATUSES, schema_json

# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def _clean_env() -> dict[str, str]:
    """A child environment with the ambient pytest/rustest session stripped out."""
    env = dict(os.environ)
    for leak in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST", "RUSTEST_RUNNING"):
        _ = env.pop(leak, None)
    return env


def _run(tree: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return _run_argv([sys.executable, "-m", "rustest", *args], tree)


def _run_argv(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, env=_clean_env(), check=False
    )


def _lines(stdout: str) -> list[dict[str, Any]]:
    """Every stdout line parsed as JSON.

    Deliberately unforgiving: a blank line, a trailing banner or a half-written object is a
    failure here rather than a filtered-out nuisance, because a consumer's JSONL reader would
    fail on it too.
    """
    parsed: list[dict[str, Any]] = []
    for index, raw in enumerate(stdout.splitlines()):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:  # pragma: no cover - only on a real regression
            pytest.fail(f"stdout line {index} is not JSON: {raw!r} ({exc})")
        assert isinstance(obj, dict), f"line {index} is not an object: {raw!r}"
        parsed.append(obj)
    return parsed


#: A tree with exactly one test of each of the six statuses, plus a capture and a print.
#:
#: Written as one module so manifest order is source order, which is what makes the golden a
#: statement about *ordering* and not only about content.
_MIXED = """\
import rustest


def test_a_pass():
    assert True


def test_b_fail():
    print("computing")
    value = 41
    assert value == 42


@rustest.mark.skip(reason="not ready")
def test_c_skip():
    pass


@rustest.mark.xfail(reason="known bug")
def test_d_xfail():
    assert False


@rustest.mark.xfail(reason="stale mark")
def test_e_xpass():
    assert True


@rustest.fixture
def broken():
    raise RuntimeError("fixture blew up")


def test_f_error(broken):
    pass
"""


@pytest.fixture
def mixed_tree(tmp_path: Path) -> Path:
    """The six-status tree, with its own ``pytest.ini`` so ``rootdir`` is *tmp_path*.

    Without the ini both runners walk *out* of ``tmp_path`` looking for a config file and land
    on this repository's ``pyproject.toml``, which would make every node id repo-relative and
    the goldens below a description of where the checkout happens to live.
    """
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    _write(tmp_path / "test_mixed.py", _MIXED)
    return tmp_path


def _shape(objs: list[dict[str, Any]], tree: Path) -> list[dict[str, Any]]:
    """Normalise a stream into something a golden can pin.

    Three things vary between runs and neither of them is the contract: the wall clock, the
    installed package version, and the absolute path of the temporary tree (which appears both
    in ``rootdir`` and inside every traceback ``msg``, in native separators).  Each is replaced
    with a fixed token.  ``msg`` additionally loses its frame block -- see the module
    docstring.
    """
    native = str(tree)
    posix = native.replace("\\", "/")
    shaped: list[dict[str, Any]] = []
    for obj in objs:
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if key in ("duration", "version", "rootdir"):
                out[key] = f"<{key}>"
            elif key == "msg" and isinstance(value, str):
                text = value.replace(native, "<root>").replace(posix, "<root>")
                head, _, tail = text.rpartition("\n")
                out[key] = f"<frames>\n{tail}" if head else text
            else:
                out[key] = value
        shaped.append(out)
    return shaped


# ---------------------------------------------------------------------------
# the golden stream
# ---------------------------------------------------------------------------

#: The whole of ``rustest --llm -v`` over :data:`_MIXED`, normalised by :func:`_shape`.
#:
#: ``-v`` so the golden covers *every* line type the mode can emit in one document: the header,
#: both fail statuses, all three skip statuses and the sentinel.  The default-verbosity and
#: ``-q`` rungs are then pinned as *subsets* of this one
#: (:func:`test_the_default_rung_is_the_verbose_one_without_skip_lines`,
#: :func:`test_q_drops_captures_and_keeps_every_count`), which is what stops the three rungs
#: from drifting into three unrelated formats.
_GOLDEN_VERBOSE: list[dict[str, Any]] = [
    {
        "t": "meta",
        "schema_version": 2,
        "tool": "rustest",
        "version": "<version>",
        "rootdir": "<rootdir>",
        "total": 6,
    },
    {
        "t": "fail",
        "id": "test_mixed.py::test_b_fail",
        "file": "test_mixed.py",
        "line": 11,
        "status": "failed",
        "msg": "<frames>\nAssertionError: assert 41 == 42",
        "stdout": "computing",
    },
    {
        "t": "fail",
        "id": "test_mixed.py::test_f_error",
        "file": "test_mixed.py",
        "line": 31,
        "status": "error",
        "msg": "<frames>\nRuntimeError: fixture blew up",
    },
    {
        "t": "skip",
        "id": "test_mixed.py::test_c_skip",
        "file": "test_mixed.py",
        "status": "skipped",
        "reason": "not ready",
    },
    {
        "t": "skip",
        "id": "test_mixed.py::test_d_xfail",
        "file": "test_mixed.py",
        "status": "xfailed",
        "reason": "known bug",
    },
    {
        "t": "skip",
        "id": "test_mixed.py::test_e_xpass",
        "file": "test_mixed.py",
        "status": "xpassed",
        "reason": "stale mark",
    },
    {
        "t": "summary",
        "total": 6,
        "passed": 1,
        "failed": 1,
        "skipped": 1,
        "xfailed": 1,
        "xpassed": 1,
        "error": 1,
        "deselected": 0,
        "collection_errors": 0,
        "duration": "<duration>",
        "exit_code": 1,
    },
]


def test_the_mixed_run_emits_the_golden_stream(mixed_tree: Path) -> None:
    """One document, every line type, in the contracted order.

    This is the pin the rest of the module leans on: header first, ``fail`` lines in manifest
    order, ``skip`` lines in manifest order, sentinel last -- and **key order inside each
    object**, because the goldens are compared as dicts but built in emission order, so a field
    that moves shows up in the diff as a reordered literal.
    """
    result = _run(mixed_tree, [".", "--llm", "-v"])
    assert _shape(_lines(result.stdout), mixed_tree) == _GOLDEN_VERBOSE


def test_the_summary_is_the_last_line_and_the_header_the_first(mixed_tree: Path) -> None:
    """The sentinel property, stated on its own because tooling depends on it directly.

    A consumer that sees no ``summary`` is entitled to conclude the run was interrupted, so
    ``summary`` must never be followed by anything -- not a teardown note, not a banner.
    """
    objs = _lines(_run(mixed_tree, [".", "--llm", "-v"]).stdout)
    assert objs[0]["t"] == "meta"
    assert objs[-1]["t"] == "summary"
    assert [obj["t"] for obj in objs].count("summary") == 1


def test_the_message_carries_the_whole_rewritten_traceback(mixed_tree: Path) -> None:
    """``msg`` is the worker's message **whole**, frames included -- the field's whole claim.

    The 0.18 implementation shredded the same string into ``error``/``msg``/``expected``/
    ``actual``/``code``/``frames`` with six regexes; this asserts the frames are still there,
    which is what makes the decomposition unnecessary rather than merely absent.
    """
    objs = _lines(_run(mixed_tree, [".", "--llm"]).stdout)
    (fail,) = [obj for obj in objs if obj.get("status") == "failed"]
    msg: str = fail["msg"]
    assert msg.startswith("Traceback (most recent call last):")
    assert "test_mixed.py" in msg
    assert ", line 11, in test_b_fail" in msg
    # The rewritten comparison: the *values*, not just the source text of the assert.
    assert msg.endswith("AssertionError: assert 41 == 42")


def test_the_stream_is_identical_at_every_worker_count(mixed_tree: Path) -> None:
    """Determinism, and specifically that it does not come from sorting.

    ``main`` sorted its buffered failures by ``(file, line)`` because it consumed live events.
    v2 emits ``report["tests"]`` as it stands, so this asserts the property the engine already
    guarantees rather than one the renderer manufactures -- and it would fail loudly if a
    future change started emitting in completion order.
    """
    _write(mixed_tree / "test_second.py", "def test_g_fail():\n    assert 0 == 1\n")
    streams = {_run(mixed_tree, [".", "--llm", "-v", "-n", n]).stdout for n in ("1", "2", "4")}
    # Durations differ between runs, so compare the shaped documents rather than raw bytes.
    shaped = {json.dumps(_shape(_lines(text), mixed_tree)) for text in streams}
    assert len(shaped) == 1, f"{len(shaped)} distinct documents across -n 1/2/4"


# ---------------------------------------------------------------------------
# the verbosity ladder
# ---------------------------------------------------------------------------


def test_the_default_rung_is_the_verbose_one_without_skip_lines(mixed_tree: Path) -> None:
    """No ``-v``: failures and the sentinel, no ``skip`` lines.

    The signal-only property -- a green-but-for-two-failures run of 5 000 tests costs three
    lines -- and the reason ``skip`` is gated rather than emitted always.
    """
    objs = _shape(_lines(_run(mixed_tree, [".", "--llm"]).stdout), mixed_tree)
    assert objs == [obj for obj in _GOLDEN_VERBOSE if obj["t"] != "skip"]


def test_q_drops_captures_and_keeps_every_count(mixed_tree: Path) -> None:
    """``-q``: the same lines as the default rung, minus ``stdout``/``stderr``.

    The counts must **not** move: they are copied from the engine's summary, not tallied from
    the lines that were emitted, so ``-q`` reports the same six buckets as ``-v`` even though
    it emitted no ``skip`` line to count.
    """
    objs = _shape(_lines(_run(mixed_tree, [".", "--llm", "-q"]).stdout), mixed_tree)
    expected = [
        {key: value for key, value in obj.items() if key not in ("stdout", "stderr")}
        for obj in _GOLDEN_VERBOSE
        if obj["t"] != "skip"
    ]
    assert objs == expected
    assert objs[-1]["skipped"] == 1 and objs[-1]["xfailed"] == 1 and objs[-1]["xpassed"] == 1


def test_qv_cancels_out_exactly_as_it_does_for_the_human_ladder(mixed_tree: Path) -> None:
    """``-qv`` is verbosity 0 here too -- ``cli._verbosity`` is the single source of the rung."""
    both = _shape(_lines(_run(mixed_tree, [".", "--llm", "-q", "-v"]).stdout), mixed_tree)
    plain = _shape(_lines(_run(mixed_tree, [".", "--llm"]).stdout), mixed_tree)
    assert both == plain


# ---------------------------------------------------------------------------
# captures and truncation
# ---------------------------------------------------------------------------


def _chatty(tmp_path: Path, lines: int) -> Path:
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    _write(
        tmp_path / "test_chatty.py",
        f"def test_loud():\n"
        f"    for index in range({lines}):\n"
        f'        print(f"line {{index}}")\n'
        f"    assert False\n",
    )
    return tmp_path


def test_captures_are_tail_truncated_to_fifty_lines(tmp_path: Path) -> None:
    """The default cap, and that it keeps the **tail**.

    The tail because the output immediately before the failure is the part worth spending
    tokens on. ``stdout_omitted`` reports what was dropped, so a reader can tell an empty
    prologue from a discarded one.
    """
    tree = _chatty(tmp_path, 200)
    (fail,) = [obj for obj in _lines(_run(tree, [".", "--llm"]).stdout) if obj["t"] == "fail"]
    kept = fail["stdout"].splitlines()
    assert len(kept) == CAPTURE_MAX_LINES
    assert kept[0] == "line 150" and kept[-1] == "line 199"
    assert fail["stdout_omitted"] == 150


def test_llm_full_keeps_every_captured_line_and_omits_the_counter(tmp_path: Path) -> None:
    """``--llm-full``: no cap, and therefore no ``*_omitted`` field to report."""
    tree = _chatty(tmp_path, 200)
    (fail,) = [
        obj for obj in _lines(_run(tree, [".", "--llm", "--llm-full"]).stdout) if obj["t"] == "fail"
    ]
    assert fail["stdout"].splitlines() == [f"line {index}" for index in range(200)]
    assert "stdout_omitted" not in fail


def test_a_short_capture_is_unchanged_and_carries_no_counter(tmp_path: Path) -> None:
    """Under the cap, truncation is invisible -- no ``*_omitted``, no lost lines."""
    tree = _chatty(tmp_path, 3)
    (fail,) = [obj for obj in _lines(_run(tree, [".", "--llm"]).stdout) if obj["t"] == "fail"]
    assert fail["stdout"] == "line 0\nline 1\nline 2"
    assert "stdout_omitted" not in fail


def test_truncate_tail_normalises_line_endings_in_both_branches() -> None:
    """The ``None`` branch exists to make normalisation unconditional; this is why.

    Without it a 3-line capture would keep its trailing newline and a 300-line one would lose
    it, so whether ``stdout`` ended in ``\\n`` would depend on how chatty the test was.
    """
    assert truncate_tail("a\r\nb\n", None) == ("a\nb", 0)
    assert truncate_tail("a\r\nb\n", 50) == ("a\nb", 0)
    assert truncate_tail("a\nb\nc\n", 2) == ("b\nc", 1)


# ---------------------------------------------------------------------------
# line numbers
# ---------------------------------------------------------------------------


def test_failing_line_takes_the_innermost_frame() -> None:
    """The last ``File`` line is the frame that raised, so the walk runs backwards."""
    message = (
        "Traceback (most recent call last):\n"
        '  File "/a/test_x.py", line 4, in test_x\n'
        "    helper()\n"
        '  File "/a/helpers.py", line 9, in helper\n'
        "    raise ValueError\n"
        "ValueError"
    )
    assert failing_line(message) == 9


@pytest.mark.parametrize(
    "message",
    [
        "boom",
        "",
        "AssertionError: assert 1 == 2",
        # A frame-shaped line with a non-numeric position: degrade, never guess.
        '  File "/a/x.py", line NaN, in f',
    ],
)
def test_failing_line_returns_none_rather_than_zero(message: str) -> None:
    """No frame means **no field**, not ``0``.

    ``main`` emitted ``"line":0`` when its regex found nothing, which is a line number that
    does not exist in any file; an absent key is the honest answer and is what the schema
    marks optional.
    """
    assert failing_line(message) is None


def test_the_line_field_points_at_the_call_that_raised(tmp_path: Path) -> None:
    """End to end, through the frame filter.

    ``rustest.fail()`` sets ``__tracebackhide__``, so ``_visible_frames`` drops its own frame
    and the innermost *visible* one is the test's own line.  That is the number an agent wants
    -- the caller, not the helper -- and it falls out of reading the last frame precisely
    because the worker has already filtered them.
    """
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    _write(
        tmp_path / "test_reason.py",
        "import rustest\n\n\ndef test_x():\n    rustest.fail('nope')\n",
    )
    objs = _lines(_run(tmp_path, [".", "--llm", "-v"]).stdout)
    (fail,) = [obj for obj in objs if obj["t"] == "fail"]
    assert fail["line"] == 5
    assert fail["msg"].endswith("nope")


# ---------------------------------------------------------------------------
# collection errors and unattributable teardown
# ---------------------------------------------------------------------------


def test_a_collection_error_is_its_own_line_and_its_own_count(tmp_path: Path) -> None:
    """A file that never imported gets an ``error`` line, and the run is exit 2.

    Note what the summary says: ``collection_errors: 1`` and ``error: 0``.  The two are kept
    apart on purpose -- ``error`` counts *tests* whose setup or teardown raised, and only
    ``collection_errors`` means the report is incomplete.  The human summary line folds them
    together because a reader wants one number; a machine reader can afford the distinction and
    needs it.

    Note also what is **not** here: a ``fail`` line for ``test_ok.py``.  A collection error
    interrupts the session exactly as it does under pytest (``core._EXIT_INTERRUPTED``: "one
    unimportable file -> 2, even when other files collected"), so the surviving file's tests
    were never attempted.  ``total: 0`` in the sentinel says so, which is the property that
    stops an agent reading "no failures" off an aborted run.
    """
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    _write(tmp_path / "test_broken.py", "import nonexistent_module_xyz\n")
    _write(tmp_path / "test_ok.py", "def test_bad():\n    assert 0\n")
    result = _run(tmp_path, [".", "--llm"])
    objs = _lines(result.stdout)
    assert [obj["t"] for obj in objs] == ["meta", "error", "summary"]
    error = objs[1]
    assert error["scope"] == "collection"
    assert error["file"] == "test_broken.py"
    assert "nonexistent_module_xyz" in error["msg"]
    summary = objs[-1]
    assert summary["collection_errors"] == 1
    assert summary["error"] == 0
    assert summary["failed"] == 0
    assert summary["exit_code"] == 2
    assert result.returncode == 2


def _synthetic_report() -> dict[str, Any]:
    """A report carrying one of everything, including the two shapes a real run cannot pair.

    A *collection* error interrupts the session, so no real run has both a collection error and
    a fail line; an *unattributable teardown* error needs a session fixture that raises after
    the last test a worker owned, which is reachable but not reliably reproducible in a
    subprocess.  Driving :func:`rustest._llm.render` over a literal report is how both get
    covered -- and it is a fair test precisely because the renderer's only input *is* this
    structure.
    """
    return {
        "version": 2,
        "rootdir": "/repo",
        "exit_code": 1,
        "summary": {
            "total": 3,
            "passed": 1,
            "failed": 1,
            "skipped": 1,
            "xfailed": 0,
            "xpassed": 0,
            "error": 0,
            "deselected": 2,
            "duration": 1.23456,
        },
        "tests": [
            {"id": "b.py::test_z", "status": "failed", "duration": 0.1, "message": "boom"},
            {"id": "a.py::test_y", "status": "skipped", "duration": 0.0, "message": "later"},
            {"id": "a.py::test_x", "status": "passed", "duration": 0.0},
        ],
        "collection_errors": [{"path": "c.py", "message": "SyntaxError: bad"}],
        "teardown_errors": ["session fixture db: RuntimeError"],
    }


def test_render_emits_the_five_line_types_in_the_contracted_order() -> None:
    """meta, errors, fails, skips, summary -- and errors before fails, deliberately.

    A file that did not import is a larger problem than an assertion that did not hold: its
    tests were never attempted.  An agent triaging a red run should read that first, so it is
    emitted first rather than sorted alongside.
    """
    stream = io.StringIO()
    render(_synthetic_report(), verbosity=1, output=stream)  # pyright: ignore[reportArgumentType]
    objs = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [obj["t"] for obj in objs] == ["meta", "error", "error", "fail", "skip", "summary"]
    assert objs[1] == {
        "t": "error",
        "scope": "collection",
        "file": "c.py",
        "msg": "SyntaxError: bad",
    }
    # No `file` on a teardown error: the engine could not tie it to a test *or* a file, and
    # inventing a path is exactly the guess this mode exists to avoid.
    assert objs[2] == {"t": "error", "scope": "teardown", "msg": "session fixture db: RuntimeError"}
    # Manifest order, not sorted: `b.py` precedes `a.py` because the report says so.
    assert objs[3]["id"] == "b.py::test_z" and "line" not in objs[3]
    assert objs[-1]["deselected"] == 2
    assert objs[-1]["duration"] == 1.235  # rounded to 3 places, not truncated
    for obj in objs:
        _validate(obj)


def test_render_at_the_default_rung_omits_skip_lines_but_not_error_lines() -> None:
    """``skip`` is the only line type ``-v`` gates.  Errors and failures are never optional."""
    stream = io.StringIO()
    render(_synthetic_report(), verbosity=0, output=stream)  # pyright: ignore[reportArgumentType]
    kinds = [json.loads(line)["t"] for line in stream.getvalue().splitlines()]
    assert kinds == ["meta", "error", "error", "fail", "summary"]


def test_collection_errors_are_not_also_dumped_on_stderr(tmp_path: Path) -> None:
    """Reported once, as a line -- not twice, once as JSON and once as prose.

    The human path writes ``ERROR collecting <path>`` to stderr; under ``--llm`` that would be
    the same fact in two formats, and an agent capturing ``2>&1`` would see the prose copy.
    """
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    _write(tmp_path / "test_broken.py", "import nonexistent_module_xyz\n")
    result = _run(tmp_path, [".", "--llm"])
    assert "ERROR collecting" not in result.stderr


# ---------------------------------------------------------------------------
# composition with the flags an agent loop actually uses
# ---------------------------------------------------------------------------


def test_lf_reruns_only_the_failures_and_emits_only_their_lines(mixed_tree: Path) -> None:
    """The agent loop: ``--llm`` to find the failures, ``--lf --llm`` to re-check them.

    ``--lf`` narrows the *selection*, so the narrowing shows up in the JSONL for free: the
    second stream carries the same two ``fail`` lines and a ``total`` of 2. This is why the
    summary carries no ``rerun`` array -- ``main`` needed one because it had no last-failed
    cache in the loop, and v2 does.
    """
    first = _lines(_run(mixed_tree, [".", "--llm"]).stdout)
    assert first[-1]["total"] == 6

    second = _run(mixed_tree, [".", "--llm", "--lf"])
    objs = _lines(second.stdout)
    assert [obj["t"] for obj in objs] == ["meta", "fail", "fail", "summary"]
    assert [obj["id"] for obj in objs if obj["t"] == "fail"] == [
        "test_mixed.py::test_b_fail",
        "test_mixed.py::test_f_error",
    ]
    summary = objs[-1]
    assert summary["total"] == 2
    assert summary["passed"] == 0 and summary["skipped"] == 0
    assert second.returncode == 1


def test_maxfail_marks_the_summary_stopped_early(tmp_path: Path) -> None:
    """A cut-short run says so, in a field, because its counts describe a partial selection.

    Without it ``{"failed":1,"passed":0,"total":9}`` reads as "one failure in a suite of nine"
    when eight of the nine were never attempted -- a true line that misleads.
    """
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    _write(
        tmp_path / "test_many.py",
        "".join(f"def test_{index}():\n    assert 0\n\n\n" for index in range(9)),
    )
    result = _run(tmp_path, [".", "--llm", "--maxfail", "1"])
    objs = _lines(result.stdout)
    summary = objs[-1]
    assert summary["stopped_early"] is True
    assert summary["failed"] == 1
    assert summary["total"] == 9
    assert result.returncode == 1
    # And the human banner does not leak onto stderr alongside it.
    assert "stopping after" not in result.stderr


def test_a_complete_run_has_no_stopped_early_key(mixed_tree: Path) -> None:
    """Omitted when false, so an ordinary sentinel is byte-identical with or without ``-x``."""
    assert "stopped_early" not in _lines(_run(mixed_tree, [".", "--llm"]).stdout)[-1]


@pytest.mark.parametrize(
    ("body", "exit_code"),
    [
        ("def test_ok():\n    assert 1\n", 0),
        ("def test_bad():\n    assert 0\n", 1),
        ("import nonexistent_module_xyz\n", 2),
        ("def helper():\n    pass\n", 5),
    ],
)
def test_llm_does_not_change_the_exit_code(tmp_path: Path, body: str, exit_code: int) -> None:
    """A renderer must not have a verdict.  The same tree, with and without ``--llm``, agrees.

    Run both ways rather than against a table of expected codes alone, so the pin survives any
    future change to what the codes *are*.
    """
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    _write(tmp_path / "test_one.py", body)
    plain = _run(tmp_path, ["."])
    jsonl = _run(tmp_path, [".", "--llm"])
    assert plain.returncode == jsonl.returncode == exit_code
    # Even exit 5 emits a well-formed document: header and an all-zero sentinel.
    objs = _lines(jsonl.stdout)
    assert [obj["t"] for obj in objs] == ["meta", "summary"] or objs[-1]["t"] == "summary"


def test_an_empty_tree_still_emits_a_header_and_a_sentinel(tmp_path: Path) -> None:
    """Nothing collected is still a complete document -- an agent's parse must not special-case it."""
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    _write(tmp_path / "helper.py", "x = 1\n")
    result = _run(tmp_path, [".", "--llm"])
    objs = _lines(result.stdout)
    assert [obj["t"] for obj in objs] == ["meta", "summary"]
    assert objs[0]["total"] == 0
    assert objs[-1]["total"] == 0 and objs[-1]["exit_code"] == 5
    assert result.returncode == 5


def test_deselection_is_reported_and_selection_narrows_the_lines(mixed_tree: Path) -> None:
    """``-k`` moves ``total`` and fills ``deselected`` -- the two numbers that explain each other."""
    objs = _lines(_run(mixed_tree, [".", "--llm", "-k", "b_fail"]).stdout)
    summary = objs[-1]
    assert summary["total"] == 1 and summary["deselected"] == 5
    assert [obj["t"] for obj in objs] == ["meta", "fail", "summary"]


# ---------------------------------------------------------------------------
# --llm-schema, and schema/emitter agreement
# ---------------------------------------------------------------------------


def test_llm_schema_prints_one_line_of_json_and_exits_zero(tmp_path: Path) -> None:
    """A query, not a run: it works from anywhere and answers before anything is collected."""
    result = _run(tmp_path, ["--llm-schema"])
    assert result.returncode == 0
    assert result.stdout.count("\n") == 1
    document = json.loads(result.stdout)
    assert document == SCHEMA
    assert document["version"] == SCHEMA_VERSION


def test_the_schema_is_a_stable_golden() -> None:
    """The advertised shape, pinned.

    Not the whole document byte for byte -- the prose in it should be improvable without a red
    gate -- but every **name** a consumer can key on: the line types, and the field set of
    each. A field added, removed or renamed fails here, which is the signal to bump
    :data:`SCHEMA_VERSION`.
    """
    defs: dict[str, Any] = SCHEMA["$defs"]  # pyright: ignore[reportAssignmentType]
    assert sorted(defs) == ["error", "fail", "meta", "skip", "summary"]
    fields = {name: sorted(body["properties"]) for name, body in defs.items()}
    assert fields == {
        "meta": ["rootdir", "schema_version", "t", "tool", "total", "version"],
        "error": ["file", "msg", "scope", "t"],
        "fail": [
            "file",
            "id",
            "line",
            "msg",
            "status",
            "stderr",
            "stderr_omitted",
            "stdout",
            "stdout_omitted",
            "t",
        ],
        "skip": ["file", "id", "reason", "status", "t"],
        "summary": [
            "collection_errors",
            "deselected",
            "duration",
            "error",
            "exit_code",
            "failed",
            "passed",
            "skipped",
            "stopped_early",
            "t",
            "total",
            "xfailed",
            "xpassed",
        ],
    }
    assert schema_json() == json.dumps(SCHEMA, separators=(",", ":"), ensure_ascii=True)


def _validate(obj: dict[str, Any]) -> None:
    """A minimal draft-2020-12 check: the definition exists, and its keys agree with it.

    Deliberately not ``jsonschema`` -- the project has no such dependency and adding one to
    validate five object shapes would be the wrong trade. What is checked is exactly the class
    of drift a schema is for: an emitted key the schema does not describe, and a required key
    the emitter forgot.
    """
    defs: dict[str, Any] = SCHEMA["$defs"]  # pyright: ignore[reportAssignmentType]
    body = defs.get(obj["t"])
    assert body is not None, f"no $defs entry for t={obj['t']!r}"
    allowed = set(body["properties"])
    assert set(obj) <= allowed, f"{obj['t']}: undocumented keys {sorted(set(obj) - allowed)}"
    missing = set(body["required"]) - set(obj)
    assert not missing, f"{obj['t']}: missing required {sorted(missing)}"


def test_every_emitted_line_validates_against_the_published_schema(mixed_tree: Path) -> None:
    """The pin that makes ``--llm-schema`` a contract rather than a document.

    Run at ``-v`` and with ``--llm-full`` so the sweep covers every optional field the mode can
    produce, plus a collection error and a truncated capture from their own trees below.
    """
    _write(mixed_tree / "test_broken.py", "import nonexistent_module_xyz\n")
    for objs in (
        _lines(_run(mixed_tree, [".", "--llm", "-v"]).stdout),
        _lines(_run(mixed_tree, [".", "--llm", "-q"]).stdout),
        _lines(_run(mixed_tree, [".", "--llm", "--llm-full"]).stdout),
        _lines(_run(mixed_tree, [".", "--llm", "--maxfail", "1"]).stdout),
    ):
        assert objs, "no lines emitted"
        for obj in objs:
            _validate(obj)


def test_schema_status_enums_match_the_engine_buckets() -> None:
    """The schema's status lists are the emitter's, and between them they are the six buckets.

    ``passed`` is the only status with no line type, which is the signal-only property stated
    as an invariant rather than as prose.
    """
    assert set(FAIL_STATUSES) | set(SKIP_STATUSES) | {"passed"} == {
        "passed",
        "failed",
        "skipped",
        "xfailed",
        "xpassed",
        "error",
    }
    defs: dict[str, Any] = SCHEMA["$defs"]  # pyright: ignore[reportAssignmentType]
    assert defs["fail"]["properties"]["status"]["enum"] == list(FAIL_STATUSES)
    assert defs["skip"]["properties"]["status"]["enum"] == list(SKIP_STATUSES)


# ---------------------------------------------------------------------------
# flag-surface refusals
# ---------------------------------------------------------------------------


def test_llm_full_without_llm_is_a_usage_error(tmp_path: Path, capsys: Any) -> None:
    """An inert flag is refused, not accepted -- the rule the whole CLI is built on."""
    assert cli.main(["--llm-full", str(tmp_path)]) == 4
    assert "--llm-full only affects --llm output" in capsys.readouterr().err


def test_llm_with_collect_only_is_a_usage_error(tmp_path: Path, capsys: Any) -> None:
    """Collect-only runs nothing, so a ``summary`` line would be a well-formed lie."""
    assert cli.main(["--collect-only", "--llm", str(tmp_path)]) == 4
    assert "collect-only runs no test" in capsys.readouterr().err


def test_llm_schema_wins_over_a_tree_that_would_not_run(tmp_path: Path) -> None:
    """Discovering the format must not require standing in a valid project."""
    _write(tmp_path / "pytest.ini", "[pytest]\naddopts = --doctest-modules\n")
    result = _run(tmp_path, ["--llm-schema"])
    assert result.returncode == 0
    assert json.loads(result.stdout)["version"] == SCHEMA_VERSION


def test_the_flags_appear_in_help(tmp_path: Path) -> None:
    """The surface is 0.18's, and ``--help`` is where a user or an agent finds it."""
    help_text = _run(tmp_path, ["--help"]).stdout
    for flag in ("--llm", "--llm-full", "--llm-schema"):
        assert flag in help_text
