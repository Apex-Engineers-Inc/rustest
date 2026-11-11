"""The terminal display: live ``rich`` progress, failure blocks with code context, a glyph summary.

``core.run`` uses this when stdout is a terminal; anything else (a pipe, a file) gets
:class:`rustest._progress.Progress`'s pytest-format lines, which tools parse. It consumes the
same engine events (``collected`` / ``results`` / ``done``):

* while running, one live row per unfinished file plus an overall bar; a file's row becomes
  a permanent ``✓ path (time)`` / ``✗ path (time)`` line above the live region the moment its
  last test reports;
* ``-v`` lists each test under its file line, ``-q`` keeps only the overall bar;
* after the run, :meth:`RichProgress.failures` prints each failure as a header with its
  ``path:line``, a rule, ``✗ Type: message``, and the source around the line that raised,
  then the ``FAILED <node id>`` list; :meth:`RichProgress.summary` prints the glyph summary
  on stderr.

Imported only on that path, so ``rich`` stays off ``import rustest``, the workers, ``--llm``
and piped runs.
"""

from __future__ import annotations

import os
import re
import textwrap
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, TextIO, cast

from rich.cells import cell_len
from rich.console import Console, RenderableType
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.status import Status
from rich.style import Style
from rich.table import Column
from rich.text import Text

from ._progress import display_path, use_color

#: status -> (glyph, ``--ascii`` glyph, style).
_MARK: Final[dict[str, tuple[str, str, str]]] = {
    "passed": ("✓", "PASS", "green"),
    "failed": ("✗", "FAIL", "red"),
    "error": ("✗", "ERROR", "red"),
    "skipped": ("⊘", "SKIP", "yellow"),
    "xfailed": ("x", "XFAIL", "yellow"),
    "xpassed": ("X", "XPASS", "yellow"),
}

#: Characters the display draws; a stream that cannot encode them gets ``--ascii``.
_UNICODE_PROBE: Final = "✓✗⊘→─━⠋"

_TRACEBACK: Final = "Traceback (most recent call last):"
_FRAME: Final = re.compile(r'^  File "(.+)", line (\d+), in ')
#: A doc block's code runs under a filename of `<file>.md:L<fence line>`.
_DOC_FRAME: Final = re.compile(r"^(.*):L(\d+)$")
#: Python's chaining separators -> the label an earlier exception is printed under.
_CHAINED: Final = {
    "The above exception was the direct cause of the following exception:": "Caused by:",
    "During handling of the above exception, another exception occurred:": "While handling:",
}

_CONTEXT_LINES: Final = 3

_Result = tuple[int, str, str | None, float]


def make_console(stream: TextIO, color: str) -> Console:
    """A console on *stream* honouring ``--color`` with pytest's precedence."""
    return Console(
        file=stream,
        no_color=not use_color(stream, color),
        highlight=False,
        emoji=False,
    )


def wants_ascii(stream: TextIO, ascii: bool) -> bool:
    """``--ascii``, or a stream whose encoding cannot carry the glyphs."""
    if ascii:
        return True
    try:
        _ = _UNICODE_PROBE.encode(getattr(stream, "encoding", None) or "ascii")
    except (UnicodeEncodeError, LookupError):
        return True
    return False


def _duration(seconds: float) -> str:
    return f"{seconds * 1000:.0f}ms" if seconds < 1 else f"{seconds:.2f}s"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _row_cap(width: int) -> int:
    """Cells a live row's file name may take: about 40% of the terminal, less on a narrow one,
    so the bar, percent, count and elapsed time always fit beside it."""
    return max(10, min(width * 2 // 5, width - 36))


class _Bar(ProgressColumn):
    """The bar, sized at each refresh to what its row leaves (so a resize or a narrow
    terminal shrinks it rather than wrapping the row), and drawn with ``#``/``-`` under
    ``--ascii`` -- rich only switches to ASCII bars on a non-UTF stream."""

    def __init__(self, console: Console, *, ascii: bool) -> None:
        super().__init__()
        self._console: Console = console
        self._ascii: bool = ascii
        self._bar: BarColumn = BarColumn()

    def render(self, task: Task) -> RenderableType:
        # The rest of the row: the (capped) file name plus about 30 cells of spinner,
        # percent, count, elapsed time and padding.
        width = self._console.width
        cells = max(5, min(40, width - _row_cap(width) - 30))
        if not self._ascii:
            self._bar.bar_width = cells
            return self._bar.render(task)
        filled = int(cells * task.percentage / 100) if task.total else 0
        return Text("#" * filled + "-" * (cells - filled), style="bar.complete")


class _File:
    """One test file's share of the run."""

    __slots__: tuple[str, ...] = ("total", "results", "task")

    def __init__(self, total: int) -> None:
        super().__init__()
        self.total: int = total
        self.results: list[_Result] = []
        self.task: TaskID | None = None


class RichProgress:
    """The live display, fed by the engine's events; then failures and the summary."""

    def __init__(
        self,
        out: Console,
        err: Console,
        *,
        verbosity: int,
        ascii: bool,
        cwd: str,
    ) -> None:
        super().__init__()
        self.out: Console = out
        self.err: Console = err
        self._ascii: bool = ascii
        self._quiet: bool = verbosity < 0
        self._verbose: bool = verbosity > 0
        self._cwd: str = cwd
        self._root: str = ""
        self._ids: list[str] = []
        self._files: dict[str, _File] = {}
        self._paths: dict[str, str] = {}
        self._started: float = time.perf_counter()
        self._spoke: bool = False  # anything above the summary, which then gets a blank line
        self._live: Progress = Progress(
            SpinnerColumn("line" if ascii else "dots"),
            TextColumn(
                "{task.description}",
                markup=False,
                style="cyan",
                table_column=Column(no_wrap=True, overflow="ellipsis"),
            ),
            _Bar(out, ascii=ascii),
            TaskProgressColumn(),
            TextColumn("•"),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=out,
            transient=True,
        )
        self._overall: TaskID = self._live.add_task("Running", total=None)
        # Until `collected`, a spinner alone: there is nothing to count yet.
        self._status: Status = out.status("Collecting tests", spinner="line" if ascii else "dots")
        self._status.start()

    # -- events -------------------------------------------------------------------------

    def __call__(self, kind: str, payload: object) -> None:
        if kind == "collected":
            self._collected(*cast("tuple[list[str], str]", payload))
        elif kind == "results":
            self._results(cast("list[_Result]", payload))
        elif kind == "done":
            self._done()

    def close(self) -> None:
        """Stop the live region and erase it; safe to call more than once."""
        self._status.stop()
        self._live.stop()

    def _collected(self, ids: list[str], root: str) -> None:
        self._ids, self._root = ids, root
        counts = Counter(node_id.partition("::")[0] for node_id in ids)
        self._files = {file: _File(total) for file, total in counts.items()}
        self._status.stop()
        # Nothing selected (an empty tree, `-k` matching nothing, a collection error): the
        # summary, and any ERRORS above it, say why.
        if not ids:
            return
        if not self._quiet:
            took = _duration(time.perf_counter() - self._started)
            self.out.print(
                Text.assemble(
                    (self._glyph("[OK]", "✓"), "green"),
                    f" Collected {_plural(len(ids), 'test')} from {_plural(len(counts), 'file')} ",
                    (f"({took})", "dim"),
                )
            )
            self._spoke = True
        self._live.update(self._overall, total=len(ids))
        self._live.start()

    def _results(self, results: list[_Result]) -> None:
        touched: dict[str, _File] = {}
        for result in results:
            file = self._ids[result[0]].partition("::")[0]
            touched[file] = state = self._files[file]
            state.results.append(result)
        self._live.advance(self._overall, len(results))
        if self._quiet:
            return
        # A file gets a live row only while it is unfinished after a burst, so one whose
        # tests all land together goes straight to its finished line without flashing.
        finished: list[Text] = []
        for file, state in touched.items():
            if len(state.results) < state.total:
                if state.task is None:
                    state.task = self._live.add_task(
                        self._fit(self._path(file), _row_cap(self.out.width)),
                        total=state.total,
                        completed=len(state.results),
                    )
                else:
                    self._live.update(state.task, completed=len(state.results))
                continue
            if state.task is not None:
                self._live.remove_task(state.task)
            finished.extend(self._file_lines(file, state))
        if finished:
            self._live.console.print(Text("\n").join(finished))

    def _done(self) -> None:
        # Files a stopped run left half done (`-x`, `pytest.exit`, a dead worker).
        unfinished = [
            line
            for file, state in self._files.items()
            if state.results and len(state.results) < state.total and not self._quiet
            for line in self._file_lines(file, state)
        ]
        self.close()
        if unfinished:
            self.out.print(Text("\n").join(unfinished))

    # -- lines ---------------------------------------------------------------------------

    def _glyph(self, ascii: str, unicode: str) -> str:
        return ascii if self._ascii else unicode

    def _mark(self, status: str) -> tuple[str, str]:
        glyph, ascii, style = _MARK.get(status, ("?", status.upper(), "yellow"))
        return (ascii if self._ascii else glyph), style

    def _path(self, file: str) -> str:
        path = self._paths.get(file)
        if path is None:
            path = self._paths[file] = self._shorter(
                display_path(self._root, file, self._cwd), os.path.join(self._root, file)
            )
        return path

    @staticmethod
    def _shorter(relative: str, absolute: str) -> str:
        """*relative*, unless it climbs out of the invocation dir and *absolute* is shorter."""
        absolute = os.path.normpath(absolute)
        if relative.startswith(os.pardir) and cell_len(absolute) < cell_len(relative):
            return absolute
        return relative

    def _fit(self, text: str, cells: int) -> str:
        """*text* in at most *cells* terminal cells, cut from the left so that its end -- a
        file name and its nearest directories -- survives: ``…\\bars\\test_slow_7.py``."""
        if cell_len(text) <= cells:
            return text
        ellipsis = self._glyph("...", "…")
        room = cells - cell_len(ellipsis)
        kept: list[str] = []
        for char in reversed(text):
            room -= cell_len(char)
            if room < 0:
                break
            kept.append(char)
        return ellipsis + "".join(reversed(kept))

    def _file_lines(self, file: str, state: _File) -> list[Text]:
        failed = any(result[1] in ("failed", "error") for result in state.results)
        done = len(state.results)
        if failed:
            glyph, style = self._mark("failed")
        elif done < state.total:
            glyph, style = self._mark("skipped")
        else:
            glyph, style = self._mark("passed")
        # The tests' own time, summed: fixtures shared across the file are not in it.
        took = _duration(sum(result[3] for result in state.results))
        tail = f" ({done}/{state.total} run, {took})" if done < state.total else f" ({took})"
        # One physical line: the path gives way, from the left, to the glyph and the time.
        room = self.out.width - cell_len(glyph) - 1 - cell_len(tail)
        path = self._fit(self._path(file), room)
        lines = [Text.assemble((glyph, style), " ", (path, style), (tail, "dim"))]
        if self._verbose:
            for slot, status, reason, _ in sorted(state.results):
                mark, mark_style = self._mark(status)
                name = self._ids[slot].partition("::")[2]
                line = Text.assemble("    ", (mark, mark_style), " ", name)
                if status not in ("passed", "failed"):
                    line.append(f" {status}", style=mark_style)
                if reason:
                    line.append(f" ({reason.splitlines()[0]})", style="dim")
                line.truncate(self.out.width, overflow="ellipsis")
                lines.append(line)
        return lines

    # -- after the run -------------------------------------------------------------------

    def failures(self, tests: Sequence[Mapping[str, object]]) -> None:
        """ERRORS, then FAILURES, then pytest's ``FAILED``/``ERROR`` node-id list, on stdout."""
        failed = [test for test in tests if test["status"] == "failed"]
        errored = [test for test in tests if test["status"] == "error"]
        torn = [test for test in tests if "teardown_error" in test]  # an error of its own
        for title, group, key in (
            ("ERRORS", errored, "message"),
            ("ERRORS", torn, "teardown_error"),
            ("FAILURES", failed, "message"),
        ):
            if not group:
                continue
            if not (title == "ERRORS" and key == "teardown_error" and errored):
                self._heading(self.out, title)
            for test in group:
                file, _, domain = str(test["id"]).partition("::")
                prefix = "at teardown of " if key == "teardown_error" else ""
                name = Text(prefix + (domain.replace("::", ".") or file), style="bold")
                self._block(self.out, name, file, str(test.get(key) or ""))
        if failed or errored or torn:
            # What to paste after `rustest` to run them again.
            self.out.print()
            for word, group in (("FAILED", failed), ("ERROR", [*errored, *torn])):
                for test in group:
                    self.out.print(
                        Text.assemble((word, "red"), " ", str(test["id"])), soft_wrap=True
                    )

    def warnings(self, groups: Sequence[tuple[str, str]]) -> None:
        """pytest's warnings summary (`core._warning_groups`), on stdout after the failures."""
        if not groups:
            return
        self._heading(self.out, "WARNINGS", style="bold yellow")
        for where, message in groups:
            self.out.print()
            self.out.print(Text(where, style="yellow"), soft_wrap=True)
            self.out.print(
                Text("\n".join("  " + line for line in message.splitlines()).rstrip()),
                soft_wrap=True,
            )

    def collection_errors(self, errors: Sequence[Mapping[str, object]]) -> None:
        if not errors:
            return
        self._heading(self.err, "ERRORS")
        for error in errors:
            path = str(error["path"])
            shown = self._fit(self._path(path), max(10, (self.out.width - 19) // 2))
            name = Text(f"ERROR collecting {shown}", style="bold red")
            self._block(self.err, name, path, str(error["message"]))

    def summary(self, line: Sequence[tuple[int, str]], seconds: float) -> None:
        """v1's glyph summary over pytest's buckets: ``(count, label)`` pairs, zeros dropped.

        v1's order -- passed, failed, skipped -- then pytest's remaining buckets in pytest's.
        """
        first = ("passed", "failed", "skipped")
        ordered = sorted(line, key=lambda item: first.index(item[1]) if item[1] in first else 3)
        parts: list[Text] = []
        for count, label in ordered:
            status = "error" if label.startswith("error") else label
            glyph, style = self._mark(status)
            text = f"{count} {label}"
            if label in ("passed", "failed", "skipped"):
                text = f"{glyph} {text}"
            parts.append(Text(text, style="dim" if label == "deselected" else style))
        if not parts:
            parts.append(Text("no tests ran", style="yellow"))
        if self._spoke:
            self.err.print()
        summary = Text(", ").join(parts) + Text(f" in {_duration(seconds)}", style="dim")
        self.err.print(summary, soft_wrap=True)

    def _heading(self, console: Console, title: str, style: str = "bold red") -> None:
        if self._spoke:
            console.print()
        console.print(title, style=style)
        self._spoke = True

    def _block(self, console: Console, name: Text, file: str, message: str) -> None:
        console.print()
        # Unwrapped: a traceback line longer than the terminal is the terminal's to wrap, not
        # split by rich in the middle of a path.
        console.print(Text("\n").join(self._failure(name, file, message)), soft_wrap=True)

    def _rule(self) -> Text:
        return Text(self._glyph("-", "─") * min(70, self.out.width), style="red")

    def _failure(self, name: Text, file: str, message: str) -> list[Text]:
        """One failure: *name* and where in *file* (rootdir-relative) it failed, a rule,
        ``✗ Type: message`` with the rest of the message, the source around the line that
        raised, then any earlier exceptions in the chain, raw.

        Built only from a traceback this can read with certainty (:func:`_sections`); any
        other message is printed exactly as sent, under the name and the rule.
        """
        text = message.rstrip("\n").splitlines()
        sections = _sections(text)
        if sections is None:
            return [name, self._rule(), *(Text(line) for line in text)]

        frames, exception, _, _ = sections[-1]
        spots = [_source_line(frame.group(1), int(frame.group(2))) for frame in frames]
        own = os.path.join(self._root, file)
        raised = spots[-1]
        anchor = ([spot for spot in spots if _same_file(spot[0], own)] or [raised])[-1]
        # The name stays whole; the location gives way, keeping its full path as the link.
        room = self.out.width - name.cell_len - 2
        lines = [Text.assemble(name, "  ", self._location(*anchor, room)), self._rule()]
        lines.append(Text.assemble((self._glyph("FAIL", "✗"), "red"), " ", (exception[0], "bold")))
        lines.extend(Text(line) for line in exception[1:])
        context = self._context(*raised)
        if context:
            lines.append(Text())
            if raised != anchor:  # a helper, a conftest fixture, a library: say which
                lines.append(Text.assemble("  at ", self._location(*raised, self.out.width - 5)))
            lines.extend(context)
        for _, _, raw, joined in reversed(sections[:-1]):
            lines.extend([Text(), Text(_CHAINED[joined], style="dim")])
            lines.extend(Text(line) for line in raw)
        return lines

    def _location(self, path: str, line: int, cells: int) -> Text:
        """``path:line`` in at most *cells* (the path shortened from the left), linked to the
        whole path where it can be."""
        try:
            shown = self._shorter(os.path.relpath(path, self._cwd), path)
        except ValueError:
            shown = path
        number = f":{line}"
        text = self._fit(shown, max(10, cells - cell_len(number))) + number
        try:
            link = Path(path).resolve().as_uri()
        except (OSError, ValueError):
            return Text(text, style="dim")
        return Text(text, style=Style(dim=True, link=link))

    def _context(self, path: str, number: int) -> list[Text]:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                source = handle.read().splitlines()
        except OSError:
            return []
        if not 0 < number <= len(source):
            return []
        # Up to three lines either side, stopping at a blank line so the window stays inside
        # the failing block; dedented as a whole.
        first = last = number
        while first > max(1, number - _CONTEXT_LINES) and source[first - 2].strip():
            first -= 1
        while last < min(len(source), number + _CONTEXT_LINES) and source[last].strip():
            last += 1
        window = textwrap.dedent("\n".join(source[first - 1 : last])).split("\n")
        arrow = self._glyph(">", "→")
        lines: list[Text] = []
        for current, code in enumerate(window, start=first):
            if current == number:
                lines.append(Text.assemble("  ", (arrow, "bold red"), " ", (code, "bold")))
            else:
                lines.append(Text("    " + code, style="dim"))
        return lines


_Section = tuple[list[re.Match[str]], list[str], list[str], str]


def _sections(lines: list[str]) -> list[_Section] | None:
    """A worker traceback split into its chained sections, or ``None`` when not certain.

    Each section is ``(frames, exception lines, raw lines, separator that follows it)``. A
    section's frames are the ``  File "...", line N, in ...`` lines between its
    ``Traceback (most recent call last):`` line and its first unindented line; **everything**
    from that line on is the exception's message, whatever it contains -- an assertion on a
    subprocess's stderr carries a whole second traceback, which belongs to the message. A new
    section starts only at Python's own chaining separator, and only while no section's
    message has itself contained a traceback: past that point a separator could be part of
    the embedded text, so the answer is ``None`` and the message is shown as sent.
    """
    sections: list[_Section] = []
    start = 0
    while True:
        if start >= len(lines) or lines[start] != _TRACEBACK:
            return None
        index = start + 1
        frames: list[re.Match[str]] = []
        while index < len(lines) and lines[index].startswith("  "):
            if frame := _FRAME.match(lines[index]):
                frames.append(frame)
            index += 1
        if not frames or index >= len(lines):
            return None
        embedded = _TRACEBACK in lines[index]
        for cut in range(index + 1, len(lines) - 2):
            chained = (
                lines[cut] in _CHAINED
                and lines[cut - 1] == ""
                and lines[cut + 1] == ""
                and lines[cut + 2] == _TRACEBACK
            )
            if chained:
                if embedded:
                    return None
                sections.append(
                    (frames, lines[index : cut - 1], lines[start : cut - 1], lines[cut])
                )
                start = cut + 2
                break
            embedded = embedded or _TRACEBACK in lines[cut]
        else:
            sections.append((frames, lines[index:], lines[start:], ""))
            return sections


def _source_line(path: str, line: int) -> tuple[str, int]:
    """A frame's file and line; a doc block's ``README.md:L12`` frame maps into the ``.md``."""
    doc = _DOC_FRAME.match(path)
    if doc is None:
        return path, line
    return doc.group(1), int(doc.group(2)) + line


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
