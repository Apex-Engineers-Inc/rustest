# python/tests/test_llm_extract.py
"""Unit tests for the pure LLM extraction helpers."""

from __future__ import annotations

import os

from rustest.renderers import _llm_extract as ex

ROOT = os.path.normpath("/proj")


def _p(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


def test_normalize_path_makes_relative_and_forward_slashed() -> None:
    assert ex.normalize_path(_p("tests", "test_a.py"), root=ROOT) == "tests/test_a.py"


def test_normalize_path_strips_extended_length_prefix() -> None:
    raw = "\\\\?\\" + _p("tests", "test_a.py")
    assert ex.normalize_path(raw, root=ROOT) == "tests/test_a.py"


def test_normalize_path_leaves_synthetic_frames() -> None:
    assert ex.normalize_path("<string>", root=ROOT) == "<string>"


def test_node_id_normalizes_only_the_path_segment() -> None:
    tid = _p("tests", "t.py") + "::TestGroup::test_method"
    assert ex.node_id(tid, root=ROOT) == "tests/t.py::TestGroup::test_method"


def test_node_id_handles_params_with_colons() -> None:
    tid = _p("t.py") + "::test_x[a:b]"
    assert ex.node_id(tid, root=ROOT) == "t.py::test_x[a:b]"


def test_file_of_splits_on_first_double_colon() -> None:
    assert ex.file_of("tests/t.py::TestGroup::test_method") == "tests/t.py"


def test_extract_line_takes_last_file_frame() -> None:
    msg = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 11, in _wrap\n'
        '  File "/proj/t.py", line 9, in test_method\n'
        "    assert False\n"
        "AssertionError\n"
    )
    assert ex.extract_line(msg) == 9


def test_extract_line_returns_none_when_absent() -> None:
    assert ex.extract_line("boom") is None


def test_extract_error_and_msg_splits_on_first_colon() -> None:
    assert ex.extract_error_and_msg("AssertionError: expected 200, got 401") == (
        "AssertionError",
        "expected 200, got 401",
    )


def test_extract_error_and_msg_bare_exception() -> None:
    msg = (
        "Traceback (most recent call last):\n"
        '  File "/proj/t.py", line 5, in test\n'
        "    assert x == 1\n"
        "AssertionError\n"
    )
    assert ex.extract_error_and_msg(msg) == ("AssertionError", "")


def test_extract_error_and_msg_ignores_assertion_values_block() -> None:
    msg = "AssertionError\n\n__RUSTEST_ASSERTION_VALUES__\nExpected: 20\nReceived: 10"
    assert ex.extract_error_and_msg(msg) == ("AssertionError", "")


def test_extract_expected_actual_present() -> None:
    msg = "AssertionError\n\n__RUSTEST_ASSERTION_VALUES__\nExpected: 200\nReceived: 401"
    assert ex.extract_expected_actual(msg) == ("200", "401")


def test_extract_expected_actual_absent() -> None:
    assert ex.extract_expected_actual("AssertionError") is None


def test_extract_code_returns_last_frame_code_line() -> None:
    msg = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 11, in _wrap\n'
        '  File "/proj/t.py", line 9, in test_method\n'
        "    assert response.status == 200\n"
        "           ^^^^^^^^^^^^^^^^^^^^^^\n"
        "AssertionError\n"
    )
    assert ex.extract_code(msg) == "assert response.status == 200"


def test_extract_code_none_when_no_source() -> None:
    assert ex.extract_code("AssertionError") is None


def test_extract_frames_parses_chain_outermost_first() -> None:
    msg = (
        "Traceback (most recent call last):\n"
        '  File "\\\\?\\/proj/t.py", line 42, in test_login\n'
        "    get_status()\n"
        '  File "\\\\?\\/proj/app/client.py", line 88, in get_status\n'
        "    raise TimeoutError\n"
        "TimeoutError\n"
    )
    assert ex.extract_frames(msg, root=ROOT) == [
        {"file": "t.py", "line": 42, "fn": "test_login"},
        {"file": "app/client.py", "line": 88, "fn": "get_status"},
    ]


def test_truncate_tail_keeps_last_n_and_counts_dropped() -> None:
    text = "\n".join(str(i) for i in range(10))
    kept, dropped = ex.truncate_tail(text, 3)
    assert kept == "7\n8\n9"
    assert dropped == 7


def test_truncate_tail_no_truncation_returns_zero() -> None:
    assert ex.truncate_tail("a\nb", 5) == ("a\nb", 0)
