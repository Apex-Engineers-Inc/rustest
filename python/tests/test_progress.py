"""Unit tests for the plain progress renderer, driven with synthetic events.

The byte-for-byte claim against real pytest lives in ``test_progress_oracle.py``; these pin
the renderer's own contract: modes, wrapping, ordering, and the ``done`` rules.
"""

from __future__ import annotations

import io
import os
import random

import pytest

from rustest._progress import Progress

CWD = "/proj"
ROOT = "/proj"
Result = tuple[int, str, str | None]


def render(
    results: list[Result],
    ids: list[str] | None = None,
    *,
    verbosity: int = 0,
    width: int = 80,
    capture: bool = True,
    stopped: bool = False,
    bursts: list[list[Result]] | None = None,
) -> str:
    ids = ids if ids is not None else [f"test_a.py::t{i}" for i in range(len(results))]
    out = io.StringIO()
    p = Progress(out, verbosity=verbosity, width=width, cwd=CWD, capture=capture)
    p("collected", (ids, ROOT))
    for burst in bursts if bursts is not None else [[r] for r in results]:
        p("results", burst)
    p("done", stopped)
    return out.getvalue()


def passes(n: int) -> list[Result]:
    return [(i, "passed", None) for i in range(n)]


@pytest.mark.parametrize("width", [80, 50])
def test_default_lines_are_one_column_short_of_the_width(width: int):
    first = render(passes(3), width=width).split("\n")[0]
    assert len(first) == width - 1
    assert first.startswith("test_a.py ...") and first.endswith("[100%]")


def test_default_layout_exact():
    ids = ["test_a.py::t0", "test_a.py::t1", "test_b.py::t0"]
    got = render([(0, "passed", None), (1, "failed", None), (2, "skipped", "why")], ids, width=40)
    assert got == ("test_a.py .F" + " " * 20 + " [ 66%]\ntest_b.py s" + " " * 21 + " [100%]\n\n")


def test_letters_map_every_status():
    statuses = ["passed", "failed", "error", "skipped", "xfailed", "xpassed"]
    got = render([(i, s, "r") for i, s in enumerate(statuses)], verbosity=-1)
    assert got.startswith(".FEsxX")


def test_verbose_words_and_reasons():
    results: list[Result] = [
        (0, "passed", None),
        (1, "failed", "traceback text"),
        (2, "skipped", "nope"),
        (3, "xfailed", "known"),
        (4, "xpassed", ""),
        (5, "error", None),
    ]
    lines = render(results, verbosity=1, width=60).split("\n")
    assert lines[0] == "test_a.py::t0 PASSED" + " " * 33 + "[ 16%]"
    assert lines[1].startswith("test_a.py::t1 FAILED ") and "traceback" not in lines[1]
    assert lines[2].startswith("test_a.py::t2 SKIPPED (nope)")
    assert lines[3].startswith("test_a.py::t3 XFAIL (known)")
    assert lines[4].startswith("test_a.py::t4 XPASS ") and "()" not in lines[4]
    assert lines[5].startswith("test_a.py::t5 ERROR") and lines[5].endswith("[100%]")
    assert lines[6:] == ["", ""]
    assert all(len(line) == 59 for line in lines[:6])


def test_verbose_trims_a_long_reason_with_an_ellipsis():
    line = render([(0, "skipped", "x" * 100)], verbosity=1, width=50).split("\n")[0]
    assert line == "test_a.py::t0 SKIPPED (" + "x" * 15 + "...) [100%]"


def test_verbose_drops_a_reason_that_cannot_fit_even_an_ellipsis():
    got = render([(0, "skipped", "why")], ids=["t" * 20 + "::" + "a" * 12], verbosity=1, width=50)
    assert "(" not in got


def test_quiet_is_letters_only_and_ends_with_one_newline():
    assert render(passes(5), verbosity=-1, width=40) == "....." + " " * 27 + " [100%]\n"


def test_quiet_wraps_with_the_percent_at_the_edge():
    first, second, tail = render(passes(60), verbosity=-1, width=50).split("\n")
    assert first == "." * 42 + " [ 70%]" and len(first) == 49
    assert second.endswith(" [100%]") and len(second) == 49
    assert tail == ""


def test_default_wrap_has_no_path_on_the_continuation_line():
    first, second, *_ = render(passes(60), width=50).split("\n")
    assert first.startswith("test_a.py ") and first.endswith(" [ 53%]")
    assert second.startswith("....") and "test_a" not in second


def test_stopped_has_no_final_percent_and_still_ends_the_line():
    ids = ["test_a.py::t0", "test_a.py::t1", "test_a.py::t2"]
    got = render([(0, "passed", None), (1, "failed", None)], ids, stopped=True)
    assert got == "test_a.py .F\n\n"
    assert render([(0, "passed", None)], ids, stopped=True, verbosity=-1) == ".\n"


def test_stopped_verbose_keeps_the_per_line_percent():
    ids = ["test_a.py::t0", "test_a.py::t1"]
    got = render([(0, "failed", None)], ids, verbosity=1, stopped=True, width=40)
    assert got == "test_a.py::t0 FAILED" + " " * 12 + " [ 50%]\n\n"


def test_capture_disabled_drops_the_percent_and_the_wrapping():
    assert render(passes(100), capture=False, width=50) == "test_a.py " + "." * 100 + "\n\n"
    assert render(passes(2), capture=False, verbosity=1) == (
        "test_a.py::t0 PASSED\ntest_a.py::t1 PASSED\n\n"
    )
    assert render(passes(2), capture=False, verbosity=-1) == "..\n"


def test_capture_disabled_still_reserves_the_percent_column_when_trimming_reasons():
    got = render([(0, "skipped", "x" * 100)], verbosity=1, capture=False, width=50)
    assert got == "test_a.py::t0 SKIPPED (" + "x" * 15 + "...)\n\n"


def test_a_single_test():
    assert render(passes(1), width=40) == "test_a.py ." + " " * 21 + " [100%]\n\n"


def test_an_empty_session_prints_nothing():
    out = io.StringIO()
    p = Progress(out, verbosity=0, width=80, cwd=CWD)
    p("collected", ([], ROOT))
    p("done", False)
    assert out.getvalue() == ""


def test_paths_are_relative_to_the_cwd_with_the_os_separator():
    out = io.StringIO()
    p = Progress(out, verbosity=0, width=80, cwd=os.path.join(ROOT, "pkg"))
    p("collected", (["pkg/sub/test_a.py::t", "other/test_b.py::t"], ROOT))
    p("results", [(0, "passed", None), (1, "passed", None)])
    p("done", False)
    lines = out.getvalue().split("\n")
    assert lines[0].startswith(os.path.join("sub", "test_a.py") + " .")
    assert lines[1].startswith(os.path.join("..", "other", "test_b.py") + " .")


def test_verbose_ids_are_cwd_relative_only_when_cwd_is_not_the_rootdir():
    out = io.StringIO()
    p = Progress(out, verbosity=1, width=80, cwd=os.path.join(ROOT, "pkg"))
    p("collected", (["pkg/test_a.py::T::t[1]"], ROOT))
    p("results", [(0, "passed", None)])
    assert out.getvalue().startswith("test_a.py::T::t[1] PASSED")


@pytest.mark.parametrize("verbosity", [1, 0, -1])
@pytest.mark.parametrize("seed", range(5))
def test_output_is_identical_for_any_arrival_order_and_burst_split(verbosity: int, seed: int):
    rng = random.Random(seed)
    statuses = ["passed"] * 6 + ["failed", "skipped", "xfailed", "xpassed", "error"]
    files = ["a/test_1.py", "a/test_2.py", "test_3.py"]
    ids = [f"{files[i * 3 // 140]}::t{i}" for i in range(140)]
    results: list[Result] = []
    for i in range(140):
        status = rng.choice(statuses)
        reason = "why" if status in {"skipped", "xfailed", "xpassed"} else None
        results.append((i, status, reason))
    expected = render(results, ids, verbosity=verbosity, width=50)

    shuffled = results[:]
    rng.shuffle(shuffled)
    bursts: list[list[Result]] = []
    i = 0
    while i < len(shuffled):
        n = rng.randint(1, 20)
        bursts.append(shuffled[i : i + n])
        i += n
    assert render(shuffled, ids, verbosity=verbosity, width=50, bursts=bursts) == expected
    assert render(results[::-1], ids, verbosity=verbosity, width=50) == expected


def test_a_burst_spanning_files_prints_each_file_on_its_own_line():
    ids = ["a.py::t0", "a.py::t1", "b.py::t0", "c.py::t0"]
    got = render(passes(4), ids, width=40, bursts=[passes(4)])
    assert [line.split(" ")[0] for line in got.split("\n")[:3]] == ["a.py", "b.py", "c.py"]


def test_a_later_slot_waits_for_the_earlier_one():
    out = io.StringIO()
    p = Progress(out, verbosity=0, width=40, cwd=CWD)
    p("collected", (["a.py::0", "a.py::1"], ROOT))
    p("results", [(1, "passed", None)])
    assert out.getvalue() == ""
    p("results", [(0, "passed", None)])
    assert out.getvalue() == "a.py .."


def test_done_prints_what_arrived_in_slot_order_when_the_run_was_cut_short():
    ids = [f"a.py::{i}" for i in range(4)]
    got = render([], ids, stopped=True, bursts=[[(2, "failed", None), (0, "passed", None)]])
    assert got == "a.py .F\n\n"


def test_a_reason_that_starts_with_a_newline_prints_empty_parentheses():
    """pytest trims to the first line *after* testing the reason for truthiness."""
    got = render([(0, "skipped", "\nsecond line")], verbosity=1, width=50)
    assert got.split("\n")[0].startswith("test_a.py::t0 SKIPPED ()")
