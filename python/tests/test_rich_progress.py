"""The terminal display (`rustest._rich_progress`), asserted on what the screen ends up showing.

Each test drives :class:`RichProgress` with engine-shaped events into a forced-terminal
console over a ``StringIO``, then replays the bytes through a minimal terminal (``\\r``,
``\\n``, cursor-up, erase-line; colour codes dropped) -- so a live region that was not erased
would show up as leftover lines, exactly as it would in a real terminal.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

import pytest
from rich.console import Console

from rustest._rich_progress import RichProgress, wants_ascii


def _screen(raw: str) -> list[str]:
    lines = [""]
    row = col = 0
    for token in re.split(r"(\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r|\n)", raw):
        if not token or token.startswith("\x1b]"):  # an OSC hyperlink: nothing visible
            continue
        if token == "\n":
            row, col = row + 1, 0
            lines.extend([""] * (row + 1 - len(lines)))
        elif token == "\r":
            col = 0
        elif token.startswith("\x1b["):
            if token[-1] == "A":
                row = max(0, row - int(token[2:-1] or 1))
            elif token[-1] == "K":
                lines[row] = "" if token[2:-1] == "2" else lines[row][:col]
        else:
            line = lines[row].ljust(col)
            lines[row] = line[:col] + token + line[col + len(token) :]
            col += len(token)
    while lines and not lines[-1].strip():
        lines.pop()
    return [line.rstrip() for line in lines]


def _console(*, no_color: bool = False) -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    console = Console(
        file=buffer, force_terminal=True, legacy_windows=False, width=80, no_color=no_color
    )
    return console, buffer


def _display(
    root: Path, *, verbosity: int = 0, ascii: bool = False, no_color: bool = False
) -> tuple[RichProgress, io.StringIO]:
    console, buffer = _console(no_color=no_color)
    display = RichProgress(console, console, verbosity=verbosity, ascii=ascii, cwd=str(root))
    return display, buffer


_IDS = ["test_a.py::test_one", "test_a.py::test_two", "sub/test_b.py::test_three"]


def _run(display: RichProgress, root: Path) -> None:
    """A two-file run whose results arrive out of order and in two bursts."""
    display("collected", (_IDS, str(root)))
    display("results", [(2, "passed", None, 0.012), (0, "failed", None, 0.25)])
    display("results", [(1, "skipped", "not today", 0.0)])
    display("done", False)


def test_a_run_leaves_the_collected_line_and_one_line_per_file(tmp_path: Path) -> None:
    display, buffer = _display(tmp_path)
    _run(display, tmp_path)

    screen = _screen(buffer.getvalue())
    assert re.fullmatch(r"✓ Collected 3 tests from 2 files \(\d+ms\)", screen[0]), screen
    # Finished files in the order they finished, each with its tests' summed time; nothing
    # of the live region is left.
    assert screen[1:] == [f"✓ {os.path.join('sub', 'test_b.py')} (12ms)", "✗ test_a.py (250ms)"]


def test_verbose_lists_every_test_with_its_outcome_and_reason(tmp_path: Path) -> None:
    display, buffer = _display(tmp_path, verbosity=1)
    _run(display, tmp_path)

    assert _screen(buffer.getvalue())[-3:] == [
        "✗ test_a.py (250ms)",
        "    ✗ test_one",
        "    ⊘ test_two skipped (not today)",
    ]


def test_quiet_leaves_nothing_but_what_comes_after(tmp_path: Path) -> None:
    display, buffer = _display(tmp_path, verbosity=-1)
    _run(display, tmp_path)

    assert _screen(buffer.getvalue()) == []


def test_a_stopped_run_shows_the_half_done_file(tmp_path: Path) -> None:
    display, buffer = _display(tmp_path)
    display("collected", (_IDS, str(tmp_path)))
    display("results", [(0, "failed", None, 1.5)])
    display("done", True)

    assert _screen(buffer.getvalue())[-1] == "✗ test_a.py (1/2 run, 1.50s)"


def test_an_interrupt_leaves_no_live_region(tmp_path: Path) -> None:
    display, buffer = _display(tmp_path)
    display("collected", (_IDS, str(tmp_path)))
    display("results", [(0, "passed", None, 0.0)])  # test_a.py is now a live row
    display.close()  # what core.run does on KeyboardInterrupt
    display.close()  # and it is safe twice

    screen = _screen(buffer.getvalue())
    assert len(screen) == 1 and screen[0].startswith("✓ Collected 3 tests"), screen
    assert "\x1b[?25h" in buffer.getvalue()  # the cursor is shown again


def _failure_screen(
    tmp_path: Path, node_id: str, message: str, status: str = "failed"
) -> list[str]:
    """The block :meth:`RichProgress.failures` prints for one test, from the header down."""
    display, buffer = _display(tmp_path)
    display("collected", ([node_id], str(tmp_path)))
    display("done", False)
    display.failures([{"id": node_id, "status": status, "message": message}])
    screen = _screen(buffer.getvalue())
    return screen[screen.index("ERRORS" if status == "error" else "FAILURES") + 2 :]


def _frame(path: Path | str, line: int, function: str, code: str) -> str:
    return f'  File "{path}", line {line}, in {function}\n    {code}\n'


def test_a_failure_shows_where_it_failed_the_exception_and_the_source(tmp_path: Path) -> None:
    source = tmp_path / "test_a.py"
    _ = source.write_text(
        "def test_eq():\n    value = 4\n    assert value == 5\n\n\ndef test_other():\n    pass\n",
        encoding="utf-8",
    )
    message = (
        "Traceback (most recent call last):\n"
        + _frame(source, 3, "test_eq", "assert value == 5")
        + "AssertionError: assert 4 == 5\n"
    )

    assert _failure_screen(tmp_path, "test_a.py::test_eq", message) == [
        "test_eq  test_a.py:3",
        "─" * 70,
        "✗ AssertionError: assert 4 == 5",
        "",
        "    def test_eq():",
        "        value = 4",
        "  →     assert value == 5",
        "",
        "FAILED test_a.py::test_eq",
    ]


def test_an_embedded_traceback_stays_part_of_the_message(tmp_path: Path) -> None:
    """``assert proc.returncode == 0, proc.stderr``: the child's traceback is the message."""
    source = tmp_path / "test_a.py"
    _ = source.write_text("def test_child():\n    assert proc.returncode == 0, proc.stderr\n")
    message = (
        "Traceback (most recent call last):\n"
        + _frame(source, 2, "test_child", "assert proc.returncode == 0, proc.stderr")
        + "AssertionError: Traceback (most recent call last):\n"
        '    File "<string>", line 1, in <module>\n'
        "      1/0\n"
        "  ZeroDivisionError: division by zero\n"
        "  \n"
        "assert 1 == 0\n"
    )

    block = _failure_screen(tmp_path, "test_a.py::test_child", message)
    assert block[:7] == [
        "test_child  test_a.py:2",
        "─" * 70,
        "✗ AssertionError: Traceback (most recent call last):",
        '    File "<string>", line 1, in <module>',
        "      1/0",
        "  ZeroDivisionError: division by zero",
        "",
    ]
    assert "assert 1 == 0" in block
    assert "  →     assert proc.returncode == 0, proc.stderr" in block


def test_a_chained_exception_is_built_from_the_last_and_shows_the_cause_beneath(
    tmp_path: Path,
) -> None:
    source = tmp_path / "test_a.py"
    _ = source.write_text(
        "def test_chained():\n    try:\n        {}['k']\n"
        "    except KeyError as err:\n        raise ValueError('bad config') from err\n"
    )
    cause = "Traceback (most recent call last):\n" + _frame(source, 3, "test_chained", "{}['k']")
    message = (
        cause
        + "KeyError: 'k'\n\nThe above exception was the direct cause of the following exception:\n\n"
        + "Traceback (most recent call last):\n"
        + _frame(source, 5, "test_chained", "raise ValueError('bad config') from err")
        + "ValueError: bad config\n"
    )

    block = _failure_screen(tmp_path, "test_a.py::test_chained", message)
    assert block[:3] == ["test_chained  test_a.py:5", "─" * 70, "✗ ValueError: bad config"]
    assert "  →     raise ValueError('bad config') from err" in block
    below = block[block.index("Caused by:") + 1 :]
    assert below[0] == "Traceback (most recent call last):"
    assert "KeyError: 'k'" in below


def test_a_separator_after_an_embedded_traceback_is_not_trusted(tmp_path: Path) -> None:
    """Past an embedded traceback a chaining separator may be part of the text: show it raw."""
    source = tmp_path / "test_a.py"
    message = (
        "Traceback (most recent call last):\n"
        + _frame(source, 1, "test_x", "assert False, out")
        + "AssertionError: Traceback (most recent call last):\n"
        "KeyError: 'k'\n\nThe above exception was the direct cause of the following exception:\n\n"
        "Traceback (most recent call last):\n"
        + _frame(source, 9, "elsewhere", "boom()")
        + "ValueError: from the child\n"
    )

    block = _failure_screen(tmp_path, "test_a.py::test_x", message)
    assert block[:3] == ["test_x", "─" * 70, "Traceback (most recent call last):"]
    assert block[-3:-2] == ["ValueError: from the child"]


def test_a_helper_frame_is_shown_with_its_own_location(tmp_path: Path) -> None:
    test = tmp_path / "test_a.py"
    helper = tmp_path / "helper.py"
    _ = test.write_text("from helper import check\n\n\ndef test_helper():\n    check(-1)\n")
    _ = helper.write_text('def check(x):\n    assert x > 0, "x must be positive"\n')
    message = (
        "Traceback (most recent call last):\n"
        + _frame(test, 5, "test_helper", "check(-1)")
        + _frame(helper, 2, "check", 'assert x > 0, "x must be positive"')
        + "AssertionError: x must be positive\n"
    )

    assert _failure_screen(tmp_path, "test_a.py::test_helper", message)[:7] == [
        "test_helper  test_a.py:5",
        "─" * 70,
        "✗ AssertionError: x must be positive",
        "",
        "  at helper.py:2",
        "    def check(x):",
        '  →     assert x > 0, "x must be positive"',
    ]


def test_a_conftest_fixture_error_points_at_the_fixture(tmp_path: Path) -> None:
    conftest = tmp_path / "conftest.py"
    _ = conftest.write_text(
        "import pytest\n\n\n@pytest.fixture\ndef db():\n    raise ConnectionError('db down')\n"
    )
    message = (
        "Traceback (most recent call last):\n"
        + _frame(conftest, 6, "db", "raise ConnectionError('db down')")
        + "ConnectionError: db down\n"
    )

    block = _failure_screen(tmp_path, "test_a.py::test_fixture", message, status="error")
    assert block[:3] == ["test_fixture  conftest.py:6", "─" * 70, "✗ ConnectionError: db down"]
    assert "  →     raise ConnectionError('db down')" in block
    assert block[-1] == "ERROR test_a.py::test_fixture"


def test_a_doc_block_failure_points_into_the_markdown(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    _ = doc.write_text(
        "# Doc\n\nSome text.\n\n```python\ndef test_in_doc():\n    value = 3\n"
        "    assert value == 4\n```\n"
    )
    message = (
        "Traceback (most recent call last):\n"
        f'  File "{doc}:L5", line 3, in test_in_doc\n'
        "AssertionError\n"
    )

    block = _failure_screen(tmp_path, "doc.md::codeblock_0_line_5::test_in_doc", message)
    assert block[:3] == ["codeblock_0_line_5.test_in_doc  doc.md:8", "─" * 70, "✗ AssertionError"]
    assert "  →     assert value == 4" in block


def test_odd_tracebacks_get_their_real_headline_or_are_shown_raw(tmp_path: Path) -> None:
    source = tmp_path / "test_a.py"
    recursion = (
        "Traceback (most recent call last):\n"
        + _frame(source, 2, "f", "return f()")
        + "  [Previous line repeated 986 more times]\n"
        "RecursionError: maximum recursion depth exceeded\n"
    )
    syntax = (
        "Traceback (most recent call last):\n"
        + _frame(source, 2, "test_syntax", 'compile("x x", "<string>", "exec")')
        + '  File "<string>", line 1\n    x x\n      ^\nSyntaxError: invalid syntax\n'
    )
    group = (
        "  + Exception Group Traceback (most recent call last):\n"
        f'  |   File "{source}", line 2, in test_group\n'
        "  | ExceptionGroup: many (2 sub-exceptions)\n"
    )

    assert _failure_screen(tmp_path, "test_a.py::f", recursion)[2] == (
        "✗ RecursionError: maximum recursion depth exceeded"
    )
    assert _failure_screen(tmp_path, "test_a.py::test_syntax", syntax)[2] == (
        "✗ SyntaxError: invalid syntax"
    )
    assert _failure_screen(tmp_path, "test_a.py::test_group", group)[2] == (
        "  + Exception Group Traceback (most recent call last):"
    )


def test_a_message_that_is_not_a_traceback_is_shown_as_sent(tmp_path: Path) -> None:
    block = _failure_screen(tmp_path, "test_a.py::test_x", "[XPASS(strict)] why")
    assert block == ["test_x", "─" * 70, "[XPASS(strict)] why", "", "FAILED test_a.py::test_x"]


def test_a_collection_error_gets_the_same_block_and_nothing_above_it(tmp_path: Path) -> None:
    source = tmp_path / "test_bad.py"
    _ = source.write_text("import nope\n", encoding="utf-8")
    message = (
        "Traceback (most recent call last):\n"
        + _frame(
            "/somewhere/rustest/_worker.py", 9, "collect_file", "module = import_test_module(path)"
        )
        + _frame(source, 1, "<module>", "import nope")
        + "ModuleNotFoundError: No module named 'nope'\n"
    )
    display, buffer = _display(tmp_path)
    display("collected", ([], str(tmp_path)))
    display("done", False)
    display.collection_errors([{"path": "test_bad.py", "message": message}])

    assert _screen(buffer.getvalue()) == [
        "ERRORS",
        "",
        "ERROR collecting test_bad.py  test_bad.py:1",
        "─" * 70,
        "✗ ModuleNotFoundError: No module named 'nope'",
        "",
        "  → import nope",
    ]


def test_quiet_failures_start_without_a_blank_line(tmp_path: Path) -> None:
    display, buffer = _display(tmp_path, verbosity=-1)
    display("collected", (["test_a.py::test_x"], str(tmp_path)))
    display("results", [(0, "failed", None, 0.0)])
    display("done", False)
    display.failures([{"id": "test_a.py::test_x", "status": "failed", "message": "boom"}])

    assert _screen(buffer.getvalue())[0] == "FAILURES"


def test_the_summary_leads_with_v1s_glyphs_and_keeps_pytests_buckets(tmp_path: Path) -> None:
    display, buffer = _display(tmp_path, verbosity=-1)
    display.close()
    display.summary(
        [
            (1, "failed"),
            (3, "passed"),
            (2, "skipped"),
            (1, "deselected"),
            (1, "xfailed"),
            (1, "xpassed"),
            (2, "errors"),
        ],
        0.42,
    )

    assert _screen(buffer.getvalue()) == [
        "✓ 3 passed, ✗ 1 failed, ⊘ 2 skipped, 1 deselected, 1 xfailed, 1 xpassed, 2 errors in 420ms"
    ]


def test_ascii_mode_draws_only_ascii(tmp_path: Path) -> None:
    display, buffer = _display(tmp_path, ascii=True)
    _run(display, tmp_path)
    display.close()
    display.summary([(1, "passed"), (1, "failed")], 1.5)

    screen = _screen(buffer.getvalue())
    assert screen[0].startswith("[OK] Collected 3 tests")
    assert screen[1:3] == [
        f"PASS {os.path.join('sub', 'test_b.py')} (12ms)",
        "FAIL test_a.py (250ms)",
    ]
    assert screen[-1] == "PASS 1 passed, FAIL 1 failed in 1.50s"
    assert all(line.isascii() for line in screen), screen


def test_ascii_is_chosen_for_a_stream_that_cannot_encode_the_glyphs() -> None:
    utf8 = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    assert wants_ascii(utf8, False) is False
    assert wants_ascii(utf8, True) is True
    assert wants_ascii(cp1252, False) is True


def test_no_color_keeps_the_layout_and_drops_the_colour(tmp_path: Path) -> None:
    coloured, coloured_buffer = _display(tmp_path)
    plain, plain_buffer = _display(tmp_path, no_color=True)
    _run(coloured, tmp_path)
    _run(plain, tmp_path)

    def layout(buffer: io.StringIO) -> list[str]:
        return [re.sub(r"\(\d+ms\)", "(…)", line) for line in _screen(buffer.getvalue())]

    assert layout(plain_buffer) == layout(coloured_buffer)
    assert re.search(r"\x1b\[[0-9;]*3[1-3]m", coloured_buffer.getvalue())
    assert not re.search(r"\x1b\[[0-9;]*3[1-3]m", plain_buffer.getvalue())


# -- core.run's choice and lifecycle of the display -----------------------------------------


def _terminal_run(
    monkeypatch: pytest.MonkeyPatch, fake_run: object
) -> tuple[int | None, BaseException | None, io.StringIO]:
    """``core.run`` as if stdout were a terminal, against a fake ``rust.run``."""
    from rustest import _progress, _rich_progress, core

    console, buffer = _console()
    monkeypatch.setattr(_progress, "is_terminal", lambda stream: True)
    monkeypatch.setattr(_rich_progress, "make_console", lambda stream, color: console)
    monkeypatch.setattr(core.rust, "run", fake_run)
    try:
        return core.run(paths=["."], workers=1), None, buffer
    except BaseException as exc:  # noqa: BLE001 - the test inspects it
        return None, exc, buffer


def test_a_terminal_gets_the_live_display_and_a_usage_error_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, on_event: object = None, **kwargs: object) -> str:
        del args, kwargs
        raise ValueError("Wrong expression passed to '-k'")

    code, raised, buffer = _terminal_run(monkeypatch, fake_run)

    assert (code, raised) == (4, None)
    raw = buffer.getvalue()
    assert "\x1b[?25l" in raw, "the collecting spinner (rich) was shown"
    assert raw.rfind("\x1b[?25h") > raw.rfind("\x1b[?25l"), "the cursor is shown again"
    assert _screen(raw) == []


def test_an_error_inside_the_display_is_not_a_usage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rustest import _rich_progress, core

    def broken(self: object, results: object) -> None:
        del self, results
        raise ValueError("a bug in the display")

    monkeypatch.setattr(_rich_progress.RichProgress, "_results", broken)

    def fake_run(*args: object, on_event: object = None, **kwargs: object) -> str:
        del args, kwargs
        assert callable(on_event)
        on_event("collected", (["t.py::a"], str(Path.cwd())))
        on_event("results", [(0, "passed", None, 0.0)])  # raises, as rust.run would re-raise
        return "{}"

    code, raised, buffer = _terminal_run(monkeypatch, fake_run)

    assert code is None
    assert isinstance(raised, core._DisplayError)  # pyright: ignore[reportPrivateUsage]
    assert isinstance(raised.__cause__, ValueError)
    raw = buffer.getvalue()
    assert raw.rfind("\x1b[?25h") > raw.rfind("\x1b[?25l"), "the display was still closed"
    screen = _screen(raw)  # the live region is erased; the collected line stays
    assert len(screen) == 1 and screen[0].startswith("✓ Collected 1 test from 1 file"), screen
