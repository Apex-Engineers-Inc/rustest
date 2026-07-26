"""Matches the second default `python_files` pattern (`*_test.py`), not the first.

Its name also sorts after `test_a.py`, so it anchors the tail of the walk order.
"""


def test_gamma():
    assert True
