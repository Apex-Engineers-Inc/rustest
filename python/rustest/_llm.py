"""``--llm``: the run report as JSONL, for tools that parse test output instead of reading it.

One JSON object per line, written to stdout at completion.  The output contract -- line order,
field meanings, verbosity ladder -- is ``docs/guide/llm-output.md``; the machine-readable form
is :mod:`rustest._llm_schema`, which ``--llm-schema`` prints.

**This is a projection, not a parser.**  The 0.18 implementation on ``main`` was a live event
consumer that rebuilt each failure by running six regexes over a formatted traceback: one for
the ``File "...", line N, in fn`` frames, one for the exception type, one for an
``__RUSTEST_ASSERTION_VALUES__`` block, one for the failing source line.  Every one of those
was a second, independent description of a string the runner had already composed -- so a
change to the traceback format could silently degrade the JSON without failing a test that
looked at the traceback.

v2 does not need any of it.  :func:`rustest.core.v2_run` already holds the finished
``RunReport``: a list of tests in manifest order, each with its status, its message, and its
captures, plus the six-bucket summary and the exit code.  This module walks that structure and
writes it out.  The **one** thing still read out of message text is the failing line number
(:func:`failing_line`), because the wire does not carry one -- and that is a prefix test and
two ``partition`` calls over the last frame, not a grammar, and it degrades to an omitted field
rather than to a wrong one.

**Determinism** is inherited rather than manufactured.  ``main`` sorted its buffered failures
by ``(file, line)`` because events arrived in worker-completion order.  ``report["tests"]`` is
already in manifest order however the pool interleaved -- ``src/v2/execute.rs``
(``report_order_is_manifest_order_however_many_workers_run_it``) reassembles by report slot --
so this module sorts nothing, and identical failures produce identical bytes at any ``-n``.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from ._llm_schema import FAIL_STATUSES, SCHEMA_VERSION, SKIP_STATUSES

if TYPE_CHECKING:
    from typing import IO, Any

    # The report shapes are `core`'s private `TypedDict`s and are *deliberately* re-used here
    # rather than re-declared: they describe the schema-v2 wire (`src/v2/execute.rs`), and a
    # second copy would be a second thing to keep in step with the Rust struct. The underscore
    # means "not public API", which this module is not either -- both are private to the
    # package, and `reportPrivateUsage` cannot express "private to the package".
    from .core import _ReportTest, _RunReport  # pyright: ignore[reportPrivateUsage]

#: How many lines of a capture survive without ``--llm-full``, counted from the **end**.
#:
#: 50, which is ``main``'s number and kept deliberately: the flag surface is the part of the
#: 0.18 feature there is no reason to re-litigate, and a project that pinned a golden against
#: ``rustest --llm`` on 0.18 should find the truncation behaves the same way even though the
#: fields around it changed.
#:
#: The **tail** rather than the head, because the last thing a test printed before it blew up
#: is the thing worth spending tokens on; the first 50 lines of a chatty fixture are not.
CAPTURE_MAX_LINES = 50

#: The frame marker in a CPython traceback: the closing quote of the filename, the comma, and
#: the word. Anchoring on the closing quote is what keeps a path containing ``, line `` from
#: being mistaken for the frame's own line number -- see :func:`failing_line`.
_FRAME_MARKER = '", line '

_FAIL = frozenset(FAIL_STATUSES)
_SKIP = frozenset(SKIP_STATUSES)


def failing_line(message: str) -> int | None:
    """The line number of the innermost traceback frame in *message*, or ``None``.

    The one field ``--llm`` derives from message text rather than from the report, because the
    worker->orchestrator wire carries a formatted message and no line number
    (``src/v2/execute.rs::TestOutcome``).  Adding one would mean a protocol version, a schema
    change and a Rust round trip for a field that is *already in the string*; reading it back
    out costs a reversed walk over at most a few dozen lines.

    **Why this is not the regex parsing v2 refuses.**  It answers one question with one
    anchor.  The last ``File "..."`` line of a traceback is the innermost frame -- the frame
    that raised -- so the walk goes backwards and stops at the first hit.  There is no grammar
    for exception types, no expected/actual extraction, no frame chain: those were the parts of
    ``main``'s ``_llm_extract`` that could be quietly wrong, and they are gone because ``msg``
    now carries the whole message instead of a decomposition of it.

    Returns ``None`` -- and the caller omits the field -- for every message that is not a
    traceback: a ``rustest.fail("reason")`` string, a skip reason, a collection message that is
    only an exception line.  An absent ``line`` is honest; a ``0`` (which is what ``main``
    emitted) is a line number that does not exist.
    """
    for raw in reversed(message.splitlines()):
        stripped = raw.strip()
        if not stripped.startswith('File "'):
            continue
        _, marker, tail = stripped.partition(_FRAME_MARKER)
        if not marker:
            continue
        number = tail.partition(",")[0].strip()
        if number.isdigit():
            return int(number)
    return None


def truncate_tail(text: str, max_lines: int | None) -> tuple[str, int]:
    """Keep the last *max_lines* lines of *text*; return ``(kept, dropped)``.

    ``dropped`` is reported rather than swallowed so a reader can tell "the test printed
    nothing before this" from "the test printed 900 lines and you are seeing the last 50" --
    the distinction that decides whether re-running with ``--llm-full`` is worth the tokens.

    ``max_lines=None`` (``--llm-full``) still goes through ``splitlines``/``join`` rather than
    returning *text* untouched, and that is the point of the ``None`` branch existing at all:
    it makes the **normalisation** unconditional.  Otherwise a capture of 3 lines would keep
    its trailing newline and the same capture at 300 lines would lose it, so whether
    ``stdout`` ended in ``\\n`` would depend on how chatty the test was -- a difference no
    consumer could use and every golden would have to encode.  The round trip also folds
    ``\\r\\n`` to ``\\n``, so a Windows worker and a Linux one emit the same bytes.
    """
    lines = text.splitlines()
    if max_lines is None or len(lines) <= max_lines:
        return ("\n".join(lines), 0)
    return ("\n".join(lines[-max_lines:]), len(lines) - max_lines)


def _file_of(node_id: str) -> str:
    """The path half of a node id -- everything before the first ``::``.

    Split out into its own field on every ``fail``/``skip`` line even though it is derivable,
    because the point of this mode is that a consumer does no string work: a tool that wants to
    group failures by file should not have to know that ``::`` is the separator and that a
    parametrised id (``test_x[a::b]``) can contain another one.
    """
    return node_id.partition("::")[0]


def _package_version() -> str:
    """The installed ``rustest`` version, or ``0.0.0`` from a source tree with no dist-info.

    ``importlib.metadata`` is imported here rather than at module scope for the reason
    everything else in this package defers its imports: this module is itself only imported
    when ``--llm`` is passed, and the metadata scan is the expensive half of it.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("rustest")
    except PackageNotFoundError:  # pragma: no cover - a source checkout with no install
        return "0.0.0"


class _Emitter:
    """Writes compact JSON objects, one per line, to a stream.

    A class rather than a closure so the stream is named once and the ``separators``/
    ``ensure_ascii`` choice lives in one place.  Both are load-bearing:

    * ``separators=(",", ":")`` -- no space after ``,`` or ``:``.  A 200-failure run saves a
      few hundred tokens, and every JSON reader is indifferent.
    * ``ensure_ascii=True`` -- a test may legally be named ``def test_測試():``, and this
      output is routinely redirected on Windows, where a redirected stdout uses the locale
      encoding.  Escaping at encode time is what stops a non-ASCII node id from killing the
      process with ``UnicodeEncodeError`` *after* the run has finished, which would lose the
      whole report to a naming choice.
    """

    def __init__(self, stream: IO[str]) -> None:
        super().__init__()
        self._stream: IO[str] = stream

    def __call__(self, obj: dict[str, Any]) -> None:
        _ = self._stream.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=True) + "\n")


def _meta(report: _RunReport) -> dict[str, Any]:
    """The header line.

    ``total`` is here as well as in the summary, and that is not redundancy: a consumer reading
    the stream head-first (or one whose read was cut short) learns the scale of the run from
    the first line rather than only from the last.  The summary's copy is the one that is
    *authoritative* -- it is the engine's own count -- and they are the same value read from
    the same field.
    """
    return {
        "t": "meta",
        "schema_version": SCHEMA_VERSION,
        "tool": "rustest",
        "version": _package_version(),
        "rootdir": report["rootdir"],
        "total": report["summary"]["total"],
    }


def _fail_object(test: _ReportTest, *, captures: bool, full: bool) -> dict[str, Any]:
    """One ``fail`` line: everything about a red test that is not already in the summary.

    ``msg`` is the worker's message **whole** -- the frame-filtered traceback whose last line
    is the assertion-rewritten comparison.  ``main`` shredded the same string into
    ``error``/``msg``/``expected``/``actual``/``code``/``frames`` and lost the rewriting in the
    process: ``{"error":"AssertionError","msg":"assert 41 == 42"}`` is four fields to say what
    the one string already said, and the ``expected``/``actual`` pair only ever appeared for
    the comparisons a regex recognised.  The whole message is both cheaper to produce and
    strictly more informative, and it is the text a coding agent is best at reading.
    """
    message = test.get("message", "")
    obj: dict[str, Any] = {
        "t": "fail",
        "id": test["id"],
        "file": _file_of(test["id"]),
    }
    line = failing_line(message)
    if line is not None:
        obj["line"] = line
    obj["status"] = test["status"]
    obj["msg"] = message
    if captures:
        _attach_captures(obj, test, full=full)
    return obj


def _attach_captures(obj: dict[str, Any], test: _ReportTest, *, full: bool) -> None:
    """Attach ``stdout``/``stderr`` (and their ``*_omitted`` counts) when non-empty.

    Omitted-when-empty rather than ``""``, matching the engine's own wire rule
    (``ResultResponse``): an unremarkable failure should not carry two empty strings, and a
    consumer testing ``"stdout" in obj`` gets the same answer as one testing truthiness.
    """
    max_lines = None if full else CAPTURE_MAX_LINES
    for stream in ("stdout", "stderr"):
        raw = test.get(stream)
        if not raw:
            continue
        kept, dropped = truncate_tail(raw, max_lines)
        obj[stream] = kept
        if dropped:
            obj[f"{stream}_omitted"] = dropped


def _skip_object(test: _ReportTest) -> dict[str, Any]:
    """One ``skip`` line.  ``message`` is a *reason* for these three statuses, never a traceback.

    Guaranteed by the engine, not assumed: ``core._REASON_STATUSES`` is the same set, and it is
    what lets ``-v`` print ``SKIPPED (not ready)`` on the human ladder.
    """
    return {
        "t": "skip",
        "id": test["id"],
        "file": _file_of(test["id"]),
        "status": test["status"],
        "reason": test.get("message", ""),
    }


def _summary_object(report: _RunReport) -> dict[str, Any]:
    """The sentinel.  Six buckets, ``deselected``, and the exit code the process will return.

    The counts are copied from ``report["summary"]`` rather than tallied from the lines above,
    which matters at ``-q`` (no ``skip`` lines were emitted, and ``skipped`` is still right) and
    under ``--maxfail`` (``total`` is the *selection*, not what ran).

    ``exit_code`` is included because it is the answer to the question an agent actually has --
    "is the suite green?" -- and reading it off the summary line is one parse instead of a
    process-status round trip through whatever shell wrapper the agent is using.  It is
    **reported**, never decided, here: ``--llm`` changes no exit code, which
    ``test_llm_does_not_change_the_exit_code`` pins across all five shapes.

    ``collection_errors`` is its own count and is deliberately *not* folded into ``error``.
    The human summary line folds them (a reader wants one number); a machine reader can afford
    the distinction, and it needs it -- ``error`` counts tests that ran, ``collection_errors``
    counts files whose tests never existed, and only the second means the report is incomplete.
    """
    summary = report["summary"]
    obj: dict[str, Any] = {
        "t": "summary",
        "total": summary["total"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "skipped": summary["skipped"],
        "xfailed": summary["xfailed"],
        "xpassed": summary["xpassed"],
        "error": summary["error"],
        "deselected": summary["deselected"],
        "collection_errors": len(report["collection_errors"]),
        "duration": round(summary["duration"], 3),
        "exit_code": report["exit_code"],
    }
    if report.get("stopped_early"):
        # Omitted when false, so an ordinary run's sentinel is byte-identical whether or not
        # `--maxfail` was on the command line -- and present, loudly, exactly when the counts
        # above describe a partial run. An agent that reads `2 failed` off a `-x` run and
        # concludes the rest passed has been misled by a line that was true.
        obj["stopped_early"] = True
    return obj


def render(
    report: _RunReport,
    *,
    verbosity: int = 0,
    full: bool = False,
    output: IO[str] | None = None,
) -> None:
    """Write the whole report as JSONL.  The entirety of ``--llm``.

    Line order is the contract and it is fixed here, not sorted: ``meta``, then ``error``
    lines, then ``fail`` lines, then ``skip`` lines, then ``summary``.  Errors come first
    because a file that did not import is a *different and larger* problem than a test that
    failed -- the tests in it were never even attempted -- and an agent triaging a red run
    should read it before it starts fixing assertions.

    The verbosity ladder, which is this port's own and is documented in
    ``docs/guide/llm-output.md``:

    * ``verbosity < 0`` (``-q``) -- failures **without captures**.  The cheapest useful mode:
      an agent that only wants the assertions pays no tokens for a chatty fixture's output.
    * ``verbosity == 0`` -- captures attached, tail-truncated to
      :data:`CAPTURE_MAX_LINES`.
    * ``verbosity > 0`` (``-v``) -- plus one ``skip`` line per skipped/xfailed/xpassed test.

    ``--llm-full`` is orthogonal to the ladder: it removes the truncation cap wherever captures
    are emitted at all, so ``-q --llm-full`` is a contradiction that resolves the way the ladder
    says (no captures, hence nothing to un-truncate) rather than an error.

    Args:
        report: The parsed schema-v2 run report.
        verbosity: ``-1`` for ``-q``, ``0`` default, ``1`` for ``-v`` -- the same ladder
            :func:`rustest.core.v2_run` uses for the human output.
        full: ``--llm-full``; keep captures whole instead of tail-truncating them.
        output: Where to write.  Defaults to ``sys.stdout``, which is the contract: stdout is
            pure JSONL under ``--llm`` and every diagnostic goes to stderr.
    """
    emit = _Emitter(sys.stdout if output is None else output)
    captures = verbosity >= 0

    emit(_meta(report))

    for error in report["collection_errors"]:
        emit({"t": "error", "scope": "collection", "file": error["path"], "msg": error["message"]})
    for failure in report.get("teardown_errors", []):
        # No `file`: an unattributable teardown failure is one the engine could not tie to a
        # test *or* to a file (`RunReport::teardown_errors`). Inventing a path here would be a
        # guess, and this is a mode whose whole value is that its fields are not guesses.
        emit({"t": "error", "scope": "teardown", "msg": failure})

    tests = report["tests"]
    for test in tests:
        if test["status"] in _FAIL:
            emit(_fail_object(test, captures=captures, full=full))
    if verbosity > 0:
        for test in tests:
            if test["status"] in _SKIP:
                emit(_skip_object(test))

    emit(_summary_object(report))
